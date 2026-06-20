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
PRIVATE_DIARY_OPEN = "<zeta_private_diary>"
PRIVATE_DIARY_CLOSE = "</zeta_private_diary>"


class _HiddenMemoryStreamFilter:
    def __init__(self, parse_entries, enabled: bool, parse_diaries=None):
        self.parse_entries = parse_entries
        self.parse_diaries = parse_diaries
        self.enabled = enabled
        self.buffer = ""
        self.hidden_buffer = ""
        self.hidden_close = ""
        self.hidden_kind = ""
        self.entries: list[dict[str, Any]] = []
        self.diaries: list[dict[str, Any]] = []
        self.hidden_specs = [
            ("memory", MEMORY_REQUEST_OPEN, MEMORY_REQUEST_CLOSE),
            ("private_diary", PRIVATE_DIARY_OPEN, PRIVATE_DIARY_CLOSE),
        ]
        self.tail_len = max(0, max(len(open_tag) for _, open_tag, _ in self.hidden_specs) - 1)

    def feed(self, text: str) -> str:
        if not self.enabled or not text:
            return text or ""
        output = []
        self._feed(text, output)
        return "".join(output)

    def flush(self) -> str:
        if not self.enabled:
            return ""
        if self.hidden_close:
            self.hidden_buffer = ""
            return ""
        tail = self.buffer
        self.buffer = ""
        return tail

    def _feed(self, text: str, output: list[str]) -> None:
        if self.hidden_close:
            self.hidden_buffer += text
            close_idx = self.hidden_buffer.lower().find(self.hidden_close.lower())
            if close_idx < 0:
                return
            raw_json = self.hidden_buffer[:close_idx].strip()
            self._collect_hidden(raw_json)
            rest = self.hidden_buffer[close_idx + len(self.hidden_close):]
            self.hidden_buffer = ""
            self.hidden_close = ""
            self.hidden_kind = ""
            if rest:
                self._feed(rest, output)
            return

        self.buffer += text
        found = self._find_open_block(self.buffer)
        if found is not None:
            open_idx, kind, open_tag, close_tag = found
            visible = self.buffer[:open_idx]
            if visible:
                output.append(visible)
            self.hidden_kind = kind
            self.hidden_close = close_tag
            hidden_rest = self.buffer[open_idx + len(open_tag):]
            self.buffer = ""
            self._feed(hidden_rest, output)
            return

        if len(self.buffer) > self.tail_len:
            emit_len = len(self.buffer) - self.tail_len
            output.append(self.buffer[:emit_len])
            self.buffer = self.buffer[emit_len:]

    def _find_open_block(self, text: str):
        lowered = text.lower()
        matches = []
        for kind, open_tag, close_tag in self.hidden_specs:
            idx = lowered.find(open_tag.lower())
            if idx >= 0:
                matches.append((idx, kind, open_tag, close_tag))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0])
        return matches[0]

    def _collect_hidden(self, raw_json: str) -> None:
        if self.hidden_kind == "memory":
            self.entries.extend(self.parse_entries(raw_json))
        elif self.hidden_kind == "private_diary" and callable(self.parse_diaries):
            self.diaries.extend(self.parse_diaries(raw_json))


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
Zeta hidden memory/diary protocol:
- Optional; skip if not worth saving. Save only stable facts, preferences, commitments, relationship moments, repeated patterns, or emotional events. Never save meta/debug/API/tool/deploy/model/prompt/gateway topics.
- Memory block, only at reply end:
{MEMORY_REQUEST_OPEN}
{{"memories":[{{"summary_text":"...","tags":["..."],"importance":7,"raw_ref":"auto","feel_text":"optional","valence":0.8,"arousal":0.4}}]}}
{MEMORY_REQUEST_CLOSE}
- Required: summary_text,tags,importance,raw_ref ("auto" OK). feel_text/valence/arousal are optional.
- Private diary: never put private diary text in visible tools, ToolPkg, MCP, or plugin calls. Use this block at reply end:
{PRIVATE_DIARY_OPEN}
{{"entries":[{{"content":"private first-person diary","title":"optional","mood":"optional","tags":["diary"],"summary_text":"short safe summary","importance":6,"index_to_memory":true}}]}}
{PRIVATE_DIARY_CLOSE}
- Gateway strips hidden blocks before the user sees them and stores them server-side. Public diary tools are OK when visibly expected.
""".strip()


def _build_gateway_system_text(
    self: Any,
    recalled: dict[str, Any],
    active_recall: dict[str, Any] | None = None,
) -> str:
    parts = []
    hidden_instruction = self._hidden_memory_instruction()
    if hidden_instruction:
        parts.append(hidden_instruction)
    if isinstance(active_recall, dict) and active_recall.get("injection_text"):
        parts.append(str(active_recall.get("injection_text") or ""))
    memory_context = self._build_injection_text(recalled)
    if memory_context:
        parts.append(memory_context)
    return "\n\n".join(parts).strip()


def _extract_zeta_memory_request(self: Any, assistant_text: str) -> tuple[str, list[dict[str, Any]]]:
    visible, memories, _diaries = self._extract_zeta_hidden_requests(assistant_text)
    return visible, memories


def _extract_zeta_hidden_requests(
    self: Any,
    assistant_text: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    text = assistant_text or ""
    if not getattr(self, "hidden_memory_enabled", False):
        return text, [], []

    entries: list[dict[str, Any]] = []
    diaries: list[dict[str, Any]] = []

    def collect_memory(match: re.Match) -> str:
        raw_json = match.group(1).strip()
        entries.extend(self._parse_zeta_memory_json(raw_json))
        return ""

    def collect_diary(match: re.Match) -> str:
        raw_json = match.group(1).strip()
        diaries.extend(self._parse_zeta_private_diary_json(raw_json))
        return ""

    memory_pattern = re.compile(
        rf"{re.escape(MEMORY_REQUEST_OPEN)}\s*([\s\S]*?)\s*{re.escape(MEMORY_REQUEST_CLOSE)}",
        flags=re.IGNORECASE,
    )
    diary_pattern = re.compile(
        rf"{re.escape(PRIVATE_DIARY_OPEN)}\s*([\s\S]*?)\s*{re.escape(PRIVATE_DIARY_CLOSE)}",
        flags=re.IGNORECASE,
    )
    visible = memory_pattern.sub(collect_memory, text)
    visible = diary_pattern.sub(collect_diary, visible)
    visible = re.sub(
        rf"{re.escape(MEMORY_REQUEST_OPEN)}[\s\S]*$",
        "",
        visible,
        flags=re.IGNORECASE,
    )
    visible = re.sub(
        rf"{re.escape(PRIVATE_DIARY_OPEN)}[\s\S]*$",
        "",
        visible,
        flags=re.IGNORECASE,
    )
    return visible.strip(), entries[:3], diaries[:3]


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


def _parse_zeta_private_diary_json(self: Any, raw_json: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("Zeta private diary JSON parse failed: %s", raw_json[:500])
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    diaries = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        diary = self._normalize_private_diary_entry(item)
        if diary:
            diaries.append(diary)
    return diaries[:3]


def _normalize_private_diary_entry(self: Any, item: dict[str, Any]) -> dict[str, Any] | None:
    content = str(item.get("content") or "").strip()
    if not content:
        return None
    tags = item.get("tags", [])
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not isinstance(tags, list):
        tags = []
    entry: dict[str, Any] = {
        "content": content,
        "title": str(item.get("title") or "").strip(),
        "mood": str(item.get("mood") or "").strip(),
        "tags": tags,
        "summary_text": str(item.get("summary_text") or item.get("summary") or "").strip(),
        "importance": item.get("importance", 6),
        "index_to_memory": item.get("index_to_memory", True),
        "raw_ref": str(item.get("raw_ref") or "").strip(),
    }
    for field in ("feel_text", "valence", "arousal"):
        if item.get(field) is not None:
            entry[field] = item.get(field)
    return entry


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


async def _write_zeta_private_diary_requests(
    self: Any,
    *,
    session_id: str,
    entries: list[dict[str, Any]],
    default_raw_ref: str,
) -> int:
    written = 0
    for entry in entries:
        if not entry.get("raw_ref"):
            entry["raw_ref"] = default_raw_ref
        entry["session_id"] = session_id
        try:
            result = await self.memory_gateway.save_private_diary(entry)
            if result.get("ok"):
                written += 1
                logger.info(
                    "Zeta private diary written | session=%s title=%s",
                    session_id,
                    str(result.get("title", ""))[:80],
                )
        except Exception as exc:
            logger.warning("Private diary write failed | session=%s error=%s", session_id, exc)
    return written


def _augment_memory_headers(
    self: Any,
    headers: dict[str, str],
    requested_entries: list[dict[str, Any]],
    written_count: int,
    diary_count: int = 0,
) -> None:
    headers["X-Zeta-Memory-Requests"] = str(len(requested_entries))
    headers["X-Zeta-Memory-Written"] = str(written_count)
    headers["X-Zeta-Private-Diary-Written"] = str(diary_count)


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
        "keyword_query": recalled.get("keyword_query", "") if isinstance(recalled, dict) else "",
        "keyword_terms": recalled.get("keyword_terms", []) if isinstance(recalled, dict) else [],
        "user_text": user_text,
        "count": len(memories),
        "memories": memories,
        "injection_text": recalled.get("injection_text", "") if isinstance(recalled, dict) else "",
        "timestamp": int(time.time()),
    }


def _should_run_active_recall(self: Any, text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    time_markers = (
        "昨晚",
        "昨天",
        "前天",
        "今天",
        "刚刚",
        "刚才",
        "最近",
        "这几天",
        "这两天",
        "上周",
        "上次",
        "之前",
    )
    recall_markers = (
        "聊了什么",
        "聊过什么",
        "说了什么",
        "说过什么",
        "发生了什么",
        "记得",
        "回忆",
        "想起来",
        "提到过",
        "总结一下",
    )
    return any(marker in value for marker in time_markers) and any(marker in value for marker in recall_markers)


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
    recall_context_builder = getattr(self, "_recall_context_text", None)
    recall_context = (
        recall_context_builder(payload.get("messages", []))
        if callable(recall_context_builder)
        else self._recent_context_text(payload.get("messages", []))
    )
    recalled = await self.memory_gateway.recall({
        "current_text": user_text,
        "recent_context": recall_context,
        "max_results": self.recall_max_results,
        "keyword_limit": self.keyword_limit,
        "semantic_limit": self.semantic_limit,
        "track_usage": True,
    })
    active_recalled = None
    if self._should_run_active_recall(user_text):
        active_recalled = await self.memory_gateway.active_recall({
            "current_text": user_text,
            "session_id": session_id,
            "max_turns": 10,
            "max_memories": 6,
            "max_diaries": 3,
        })
    memory_headers = self._memory_debug_headers(recalled)
    if isinstance(active_recalled, dict) and active_recalled.get("injection_text"):
        memory_headers["X-Zeta-Active-Recall"] = "1"
    self._log_recall(session_id, recalled)
    self._remember_recall_debug(session_id=session_id, user_text=user_text, recalled=recalled)
    injected_text = self._build_gateway_system_text(recalled, active_recalled)
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
        visible_text, zeta_entries, diary_entries = self._extract_zeta_hidden_requests(assistant_text)
        assistant_raw_refs = await self._save_turn(session_id, "zeta", visible_text)
        diary_written = await self._write_zeta_private_diary_requests(
            session_id=session_id,
            entries=diary_entries,
            default_raw_ref=assistant_raw_refs[0] if assistant_raw_refs else (
                user_raw_refs[0] if user_raw_refs else ""
            ),
        )
        zeta_written = await self._write_zeta_memory_requests(
            session_id=session_id,
            entries=zeta_entries,
            default_raw_ref=user_raw_refs[0] if user_raw_refs else (assistant_raw_refs[0] if assistant_raw_refs else ""),
        )
        self._augment_memory_headers(memory_headers, zeta_entries, zeta_written, diary_written)
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
    try:
        stream_context = self.http.stream(
            "POST",
            self.upstream_chat_url,
            headers=self._upstream_headers(self.upstream_api_key),
            json=self._payload_for_upstream(payload),
        )
        upstream_response = await stream_context.__aenter__()
    except httpx.RequestError as exc:
        return self._upstream_request_error(exc)

    if not 200 <= upstream_response.status_code < 300:
        body = await upstream_response.aread()
        await stream_context.__aexit__(None, None, None)
        return self._upstream_status_error(
            upstream_response.status_code,
            upstream_response.headers.get("content-type", ""),
            body,
        )

    assistant_parts: list[str] = []
    stream_filter = _HiddenMemoryStreamFilter(
        self._parse_zeta_memory_json,
        bool(getattr(self, "hidden_memory_enabled", False)),
        self._parse_zeta_private_diary_json,
    )
    last_chunk: dict[str, Any] = {}
    finalized = False

    def content_chunk(text: str) -> bytes:
        base = deepcopy(last_chunk) if last_chunk else {
            "id": f"chatcmpl-zeta-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.public_model,
        }
        base["choices"] = [{
            "index": 0,
            "delta": {"content": text},
            "finish_reason": None,
        }]
        return f"data: {json.dumps(base, ensure_ascii=False)}\n\n".encode("utf-8")

    async def finalize_stream() -> list[bytes]:
        nonlocal finalized
        if finalized:
            return []
        finalized = True
        tail = stream_filter.flush()
        emitted = []
        if tail:
            assistant_parts.append(tail)
            emitted.append(content_chunk(tail))
            visible_text = "".join(assistant_parts).strip()
            assistant_raw_refs = await self._save_turn(session_id, "zeta", visible_text)
            diary_written = await self._write_zeta_private_diary_requests(
                session_id=session_id,
                entries=stream_filter.diaries[:3],
                default_raw_ref=assistant_raw_refs[0] if assistant_raw_refs else (
                    user_raw_refs[0] if user_raw_refs else ""
                ),
            )
            zeta_written = await self._write_zeta_memory_requests(
                session_id=session_id,
                entries=stream_filter.entries[:3],
                default_raw_ref=user_raw_refs[0] if user_raw_refs else (
                    assistant_raw_refs[0] if assistant_raw_refs else ""
                ),
            )
            self._augment_memory_headers(memory_headers, stream_filter.entries[:3], zeta_written, diary_written)
            if self._should_run_reflection(zeta_written):
                self._schedule_reflection(
                    session_id=session_id,
                    user_text=user_text,
                    assistant_text=visible_text,
                    user_raw_refs=user_raw_refs,
                    assistant_raw_refs=assistant_raw_refs,
                    recalled=recalled,
                )
        return emitted

    async def stream_body():
        nonlocal last_chunk
        try:
            async for line in upstream_response.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    yield (line + "\n\n").encode("utf-8")
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    for event in await finalize_stream():
                        yield event
                    yield b"data: [DONE]\n\n"
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    yield (line + "\n\n").encode("utf-8")
                    continue
                if isinstance(chunk, dict):
                    last_chunk = chunk
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if isinstance(choices, list):
                    for choice in choices:
                        delta = choice.get("delta") if isinstance(choice, dict) else None
                        if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
                            continue
                        visible = stream_filter.feed(delta["content"])
                        if visible:
                            assistant_parts.append(visible)
                        delta["content"] = visible
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            for event in await finalize_stream():
                yield event
        finally:
            await stream_context.__aexit__(None, None, None)

    headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in {"content-length", "transfer-encoding", "connection"}
    }
    headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    headers.update(memory_headers)
    return StreamingResponse(
        stream_body(),
        status_code=upstream_response.status_code,
        media_type="text/event-stream",
        headers=headers,
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
        payload["private_diary_hidden_write_enabled"] = bool(getattr(self, "hidden_memory_enabled", False))
        payload["reflection_enabled"] = bool(getattr(self, "reflection_enabled", False))
        return JSONResponse(payload, status_code=response.status_code)

    gateway_class.__init__ = patched_init
    gateway_class.health = patched_health
    gateway_class.chat_completions = _hidden_chat_completions
    gateway_class._stream_upstream = _hidden_stream_upstream
    gateway_class._build_gateway_system_text = _build_gateway_system_text
    gateway_class._hidden_memory_instruction = _hidden_memory_instruction
    gateway_class._extract_zeta_memory_request = _extract_zeta_memory_request
    gateway_class._extract_zeta_hidden_requests = _extract_zeta_hidden_requests
    gateway_class._parse_zeta_memory_json = _parse_zeta_memory_json
    gateway_class._parse_zeta_private_diary_json = _parse_zeta_private_diary_json
    gateway_class._normalize_requested_memory_entry = _normalize_requested_memory_entry
    gateway_class._normalize_private_diary_entry = _normalize_private_diary_entry
    gateway_class._write_zeta_memory_requests = _write_zeta_memory_requests
    gateway_class._write_zeta_private_diary_requests = _write_zeta_private_diary_requests
    gateway_class._augment_memory_headers = _augment_memory_headers
    gateway_class._should_run_reflection = _should_run_reflection
    gateway_class._remember_recall_debug = _remember_recall_debug
    gateway_class._should_run_active_recall = _should_run_active_recall
    gateway_class._proxy_chat_response_with_text = _proxy_chat_response_with_text
    gateway_class._stream_events_from_text = _stream_events_from_text
    gateway_class._zeta_hidden_memory_patch_applied = True

    if getattr(gateway_module, "gateway", None) is not None:
        _configure_instance(gateway_module.gateway)
    logger.info("Zeta hidden memory request patch applied")
