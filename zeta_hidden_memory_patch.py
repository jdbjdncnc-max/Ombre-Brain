import json
import logging
import os
import re
import time
from copy import deepcopy
from typing import Any

import httpx
from starlette.responses import JSONResponse, Response, StreamingResponse


logger = logging.getLogger("ombre_brain.zeta_hidden_memory_patch")

MEMORY_REQUEST_OPEN = "<zeta_memory_request>"
MEMORY_REQUEST_CLOSE = "</zeta_memory_request>"


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _configure_instance(gateway: Any) -> None:
    mode = _env("OMBRE_MEMORY_WRITE_MODE", default="zeta").strip().lower()
    if mode not in {"zeta", "reflection", "both", "zeta_or_reflection"}:
        logger.warning("Unknown OMBRE_MEMORY_WRITE_MODE=%s, falling back to zeta", mode)
        mode = "zeta"
    gateway.memory_write_mode = mode
    gateway.hidden_memory_enabled = mode in {"zeta", "both", "zeta_or_reflection"}
    gateway.reflection_enabled = (
        not _truthy(_env("OMBRE_REFLECTION_DISABLED"))
        and mode in {"reflection", "both", "zeta_or_reflection"}
    )


def _hidden_memory_instruction(self: Any) -> str:
    if not getattr(self, "hidden_memory_enabled", False):
        return ""
    return f"""
Private memory-write instruction for Zeta:
- You may decide whether this exchange should become long-term memory.
- If nothing is worth remembering, do not output any memory request.
- If you want to remember something, append exactly one hidden block at the very end of your reply:
{MEMORY_REQUEST_OPEN}
{{"memories":[{{"summary_text":"...","tags":["..."],"importance":7,"raw_ref":"auto","feel_text":"...","valence":0.8,"arousal":0.4}}]}}
{MEMORY_REQUEST_CLOSE}
- The gateway will remove this block before the user sees it.
- Every memory must include summary_text, tags, importance, raw_ref. Use raw_ref "auto" if unsure.
- feel_text, valence, and arousal are optional; include them only when you genuinely feel something about the memory.
- Store stable personal facts, preferences, commitments, relationship moments, repeated patterns, or emotionally meaningful events.
- Do not store ordinary small talk.
- Do not store memories about prompts, hidden context, memory injection, the memory gateway, tools, MCP, Zeabur, OpenRouter, API keys, deployment, model settings, or debugging.
""".strip()


def _build_gateway_system_text(self: Any, recalled: dict[str, Any]) -> str:
    parts = []
    hidden_instruction = self._hidden_memory_instruction()
    if hidden_instruction:
        parts.append(hidden_instruction)
    memory_context = self._build_injection_text(recalled)
    if memory_context:
        parts.append(memory_context)
    return "\n\n".join(parts).strip()


def _extract_zeta_memory_request(self: Any, assistant_text: str) -> tuple[str, list[dict[str, Any]]]:
    text = assistant_text or ""
    if not getattr(self, "hidden_memory_enabled", False) or MEMORY_REQUEST_OPEN not in text:
        return text, []

    entries: list[dict[str, Any]] = []

    def collect(match: re.Match) -> str:
        raw_json = match.group(1).strip()
        entries.extend(self._parse_zeta_memory_json(raw_json))
        return ""

    pattern = re.compile(
        rf"{re.escape(MEMORY_REQUEST_OPEN)}\s*([\s\S]*?)\s*{re.escape(MEMORY_REQUEST_CLOSE)}",
        flags=re.IGNORECASE,
    )
    visible = pattern.sub(collect, text)
    visible = re.sub(
        rf"{re.escape(MEMORY_REQUEST_OPEN)}[\s\S]*$",
        "",
        visible,
        flags=re.IGNORECASE,
    )
    return visible.strip(), entries[:3]


def _parse_zeta_memory_json(self: Any, raw_json: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("Zeta hidden memory JSON parse failed: %s", raw_json[:500])
        return []
    memories = payload.get("memories", []) if isinstance(payload, dict) else payload
    if not isinstance(memories, list):
        return []
    entries = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        entry = self._normalize_requested_memory_entry(item)
        if not entry:
            continue
        if self._is_rejected_reflection_entry(entry):
            logger.info("Rejected hidden meta memory | summary=%s", entry.get("summary_text", "")[:120])
            continue
        entries.append(entry)
    return entries[:3]


def _normalize_requested_memory_entry(self: Any, item: dict[str, Any]) -> dict[str, Any] | None:
    summary = str(item.get("summary_text") or "").strip()
    if not summary:
        return None
    raw_ref = str(item.get("raw_ref") or "auto").strip() or "auto"
    try:
        importance = max(1, min(10, int(item.get("importance", 5))))
    except (TypeError, ValueError):
        importance = 5
    entry: dict[str, Any] = {
        "summary_text": summary,
        "tags": item.get("tags", []),
        "importance": importance,
        "raw_ref": raw_ref,
    }
    if isinstance(entry["tags"], str):
        entry["tags"] = [tag.strip() for tag in entry["tags"].split(",") if tag.strip()]
    if not isinstance(entry["tags"], list):
        entry["tags"] = []
    feel_text = str(item.get("feel_text") or "").strip()
    if feel_text:
        entry["feel_text"] = feel_text
    for field in ("valence", "arousal"):
        if item.get(field) is None or item.get(field) == "":
            continue
        try:
            entry[field] = max(0.0, min(1.0, float(item[field])))
        except (TypeError, ValueError):
            continue
    return entry


async def _write_zeta_memory_requests(
    self: Any,
    *,
    session_id: str,
    entries: list[dict[str, Any]],
    default_raw_ref: str,
) -> int:
    written = 0
    for entry in entries:
        if not entry.get("raw_ref") or entry.get("raw_ref") == "auto":
            entry["raw_ref"] = default_raw_ref
        if not entry.get("raw_ref"):
            logger.info("Skipped hidden memory without raw_ref | summary=%s", entry.get("summary_text", "")[:120])
            continue
        try:
            await self.memory_gateway.write_memory(entry)
            written += 1
            logger.info(
                "Zeta hidden memory written | session=%s summary=%s",
                session_id,
                entry.get("summary_text", "")[:80],
            )
        except Exception as exc:
            logger.warning("Hidden memory write failed | session=%s error=%s", session_id, exc)
    return written


def _augment_memory_headers(
    self: Any,
    headers: dict[str, str],
    requested_entries: list[dict[str, Any]],
    written_count: int,
) -> None:
    headers["X-Zeta-Memory-Requests"] = str(len(requested_entries))
    headers["X-Zeta-Memory-Written"] = str(written_count)


def _should_run_reflection(self: Any, zeta_written_count: int) -> bool:
    if not getattr(self, "reflection_enabled", False):
        return False
    if getattr(self, "memory_write_mode", "zeta") == "zeta_or_reflection" and zeta_written_count > 0:
        return False
    return True


def _remember_recall_debug(
    self: Any,
    *,
    session_id: str,
    user_text: str,
    recalled: dict[str, Any],
) -> None:
    memories = recalled.get("memories") if isinstance(recalled, dict) else []
    if not isinstance(memories, list):
        memories = []
    self.last_recall_debug = {
        "session_id": session_id,
        "query": recalled.get("query", user_text) if isinstance(recalled, dict) else user_text,
        "user_text": user_text,
        "count": len(memories),
        "memories": memories,
        "injection_text": recalled.get("injection_text", "") if isinstance(recalled, dict) else "",
        "timestamp": int(time.time()),
    }


async def _hidden_chat_completions(self: Any, request: Any) -> Response:
    auth = self._authorize(request)
    if auth is not None:
        return auth
    if not self.upstream_chat_url or not self.upstream_api_key:
        return JSONResponse(
            {"error": {"message": "Upstream model is not configured", "type": "server_error"}},
            status_code=503,
        )

    session_id = (request.headers.get("X-Ombre-Session-Id") or self.default_session_id).strip()
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Request body must be valid JSON", "type": "invalid_request_error"}},
            status_code=400,
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return JSONResponse(
            {"error": {"message": "messages must be an array", "type": "invalid_request_error"}},
            status_code=400,
        )

    user_text = self._extract_last_user_text(payload.get("messages", []))
    user_raw_refs = await self._save_turn(session_id, "user", user_text)
    recalled = await self.memory_gateway.recall({
        "current_text": user_text,
        "recent_context": self._recent_context_text(payload.get("messages", [])),
        "max_results": self.recall_max_results,
        "keyword_limit": self.keyword_limit,
        "semantic_limit": self.semantic_limit,
    })
    memory_headers = self._memory_debug_headers(recalled)
    self._log_recall(session_id, recalled)
    self._remember_recall_debug(session_id=session_id, user_text=user_text, recalled=recalled)
    injected_text = self._build_gateway_system_text(recalled)
    forward_payload = self._prepare_forward_payload(payload, injected_text)

    if forward_payload.get("stream") is True:
        return await self._stream_upstream(
            forward_payload,
            session_id=session_id,
            user_text=user_text,
            user_raw_refs=user_raw_refs,
            recalled=recalled,
            memory_headers=memory_headers,
        )

    try:
        upstream_response = await self._forward_upstream(forward_payload)
    except httpx.RequestError as exc:
        return self._upstream_request_error(exc)
    if 200 <= upstream_response.status_code < 300:
        assistant_text = self._assistant_text_from_response(upstream_response)
        visible_text, zeta_entries = self._extract_zeta_memory_request(assistant_text)
        assistant_raw_refs = await self._save_turn(session_id, "zeta", visible_text)
        zeta_written = await self._write_zeta_memory_requests(
            session_id=session_id,
            entries=zeta_entries,
            default_raw_ref=user_raw_refs[0] if user_raw_refs else (assistant_raw_refs[0] if assistant_raw_refs else ""),
        )
        self._augment_memory_headers(memory_headers, zeta_entries, zeta_written)
        if self._should_run_reflection(zeta_written):
            self._schedule_reflection(
                session_id=session_id,
                user_text=user_text,
                assistant_text=visible_text,
                user_raw_refs=user_raw_refs,
                assistant_raw_refs=assistant_raw_refs,
                recalled=recalled,
            )
        if visible_text != assistant_text:
            return self._proxy_chat_response_with_text(upstream_response, visible_text, extra_headers=memory_headers)
    return self._proxy_response(upstream_response, extra_headers=memory_headers)


async def _hidden_stream_upstream(
    self: Any,
    payload: dict[str, Any],
    *,
    session_id: str,
    user_text: str,
    user_raw_refs: list[str],
    recalled: dict[str, Any],
    memory_headers: dict[str, str],
) -> Response:
    buffered_payload = deepcopy(payload)
    buffered_payload["stream"] = False
    try:
        upstream_response = await self._forward_upstream(buffered_payload)
    except httpx.RequestError as exc:
        return self._upstream_request_error(exc)
    if not 200 <= upstream_response.status_code < 300:
        return self._proxy_response(upstream_response, extra_headers=memory_headers)

    assistant_text = self._assistant_text_from_response(upstream_response)
    visible_text, zeta_entries = self._extract_zeta_memory_request(assistant_text)
    assistant_raw_refs = await self._save_turn(session_id, "zeta", visible_text)
    zeta_written = await self._write_zeta_memory_requests(
        session_id=session_id,
        entries=zeta_entries,
        default_raw_ref=user_raw_refs[0] if user_raw_refs else (assistant_raw_refs[0] if assistant_raw_refs else ""),
    )
    self._augment_memory_headers(memory_headers, zeta_entries, zeta_written)
    if self._should_run_reflection(zeta_written):
        self._schedule_reflection(
            session_id=session_id,
            user_text=user_text,
            assistant_text=visible_text,
            user_raw_refs=user_raw_refs,
            assistant_raw_refs=assistant_raw_refs,
            recalled=recalled,
        )

    async def stream_body():
        for event in self._stream_events_from_text(upstream_response, visible_text):
            yield event

    return StreamingResponse(
        stream_body(),
        status_code=200,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **memory_headers},
    )


def _proxy_chat_response_with_text(
    self: Any,
    response: httpx.Response,
    assistant_text: str,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    try:
        body = response.json()
    except ValueError:
        return self._proxy_response(response, extra_headers=extra_headers)
    choices = body.get("choices") if isinstance(body, dict) else None
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            message["content"] = assistant_text
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "transfer-encoding", "connection"}
    }
    if extra_headers:
        headers.update(extra_headers)
    return Response(
        content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        status_code=response.status_code,
        headers=headers,
        media_type="application/json",
    )


def _stream_events_from_text(self: Any, response: httpx.Response, assistant_text: str) -> list[bytes]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    choice = {}
    choices = body.get("choices") if isinstance(body, dict) else None
    if choices and isinstance(choices[0], dict):
        choice = choices[0]
    chunk_base = {
        "id": body.get("id", f"chatcmpl-zeta-{int(time.time())}") if isinstance(body, dict) else f"chatcmpl-zeta-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": body.get("created", int(time.time())) if isinstance(body, dict) else int(time.time()),
        "model": self.public_model,
    }
    first = {
        **chunk_base,
        "choices": [{
            "index": choice.get("index", 0),
            "delta": {"role": "assistant", "content": assistant_text},
            "finish_reason": None,
        }],
    }
    final = {
        **chunk_base,
        "choices": [{
            "index": choice.get("index", 0),
            "delta": {},
            "finish_reason": choice.get("finish_reason", "stop") or "stop",
        }],
    }
    return [
        f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode("utf-8"),
        f"data: {json.dumps(final, ensure_ascii=False)}\n\n".encode("utf-8"),
        b"data: [DONE]\n\n",
    ]


def apply_hidden_memory_patch(gateway_module: Any) -> None:
    gateway_class = gateway_module.ZetaOpenAIGateway
    if getattr(gateway_class, "_zeta_hidden_memory_patch_applied", False):
        if getattr(gateway_module, "gateway", None) is not None:
            _configure_instance(gateway_module.gateway)
        return

    original_init = gateway_class.__init__
    original_health = gateway_class.health

    def patched_init(self: Any, config: dict) -> None:
        original_init(self, config)
        _configure_instance(self)

    async def patched_health(self: Any, request: Any) -> JSONResponse:
        response = await original_health(self, request)
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except Exception:
            payload = {}
        payload["memory_write_mode"] = getattr(self, "memory_write_mode", "zeta")
        payload["hidden_memory_request_enabled"] = bool(getattr(self, "hidden_memory_enabled", False))
        payload["reflection_enabled"] = bool(getattr(self, "reflection_enabled", False))
        return JSONResponse(payload, status_code=response.status_code)

    gateway_class.__init__ = patched_init
    gateway_class.health = patched_health
    gateway_class.chat_completions = _hidden_chat_completions
    gateway_class._stream_upstream = _hidden_stream_upstream
    gateway_class._build_gateway_system_text = _build_gateway_system_text
    gateway_class._hidden_memory_instruction = _hidden_memory_instruction
    gateway_class._extract_zeta_memory_request = _extract_zeta_memory_request
    gateway_class._parse_zeta_memory_json = _parse_zeta_memory_json
    gateway_class._normalize_requested_memory_entry = _normalize_requested_memory_entry
    gateway_class._write_zeta_memory_requests = _write_zeta_memory_requests
    gateway_class._augment_memory_headers = _augment_memory_headers
    gateway_class._should_run_reflection = _should_run_reflection
    gateway_class._remember_recall_debug = _remember_recall_debug
    gateway_class._proxy_chat_response_with_text = _proxy_chat_response_with_text
    gateway_class._stream_events_from_text = _stream_events_from_text
    gateway_class._zeta_hidden_memory_patch_applied = True

    if getattr(gateway_module, "gateway", None) is not None:
        _configure_instance(gateway_module.gateway)
    logger.info("Zeta hidden memory request patch applied")
