import asyncio
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bucket_manager import BucketManager
from dehydrator import Dehydrator
from embedding_engine import EmbeddingEngine
from utils import load_config, setup_logging
from zeta_gateway import ZetaMemoryGateway


logger = logging.getLogger("ombre_brain.zeta_openai_gateway")
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


def _falsy(value: str) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off"}


def _chat_completions_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def _preview_text(text: str, limit: int = 800) -> str:
    cleaned = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:limit]


class _HiddenMemoryStreamFilter:
    def __init__(self, parse_entries, enabled: bool):
        self.parse_entries = parse_entries
        self.enabled = enabled
        self.buffer = ""
        self.hidden_buffer = ""
        self.hidden = False
        self.entries: list[dict[str, Any]] = []
        self.tail_len = max(0, len(MEMORY_REQUEST_OPEN) - 1)

    def feed(self, text: str) -> str:
        if not self.enabled or not text:
            return text or ""
        output = []
        self._feed(text, output)
        return "".join(output)

    def flush(self) -> str:
        if not self.enabled:
            return ""
        if self.hidden:
            self.hidden_buffer = ""
            return ""
        tail = self.buffer
        self.buffer = ""
        return tail

    def _feed(self, text: str, output: list[str]) -> None:
        if self.hidden:
            self.hidden_buffer += text
            close_idx = self.hidden_buffer.lower().find(MEMORY_REQUEST_CLOSE.lower())
            if close_idx < 0:
                return
            raw_json = self.hidden_buffer[:close_idx].strip()
            self.entries.extend(self.parse_entries(raw_json))
            rest = self.hidden_buffer[close_idx + len(MEMORY_REQUEST_CLOSE):]
            self.hidden_buffer = ""
            self.hidden = False
            if rest:
                self._feed(rest, output)
            return

        self.buffer += text
        open_idx = self.buffer.lower().find(MEMORY_REQUEST_OPEN.lower())
        if open_idx >= 0:
            visible = self.buffer[:open_idx]
            if visible:
                output.append(visible)
            self.hidden = True
            hidden_rest = self.buffer[open_idx + len(MEMORY_REQUEST_OPEN):]
            self.buffer = ""
            self._feed(hidden_rest, output)
            return

        if len(self.buffer) > self.tail_len:
            emit_len = len(self.buffer) - self.tail_len
            output.append(self.buffer[:emit_len])
            self.buffer = self.buffer[emit_len:]


class ZetaOpenAIGateway:
    def __init__(self, config: dict):
        self.config = config
        self.embedding_engine = EmbeddingEngine(config)
        self.bucket_mgr = BucketManager(config, embedding_engine=self.embedding_engine)
        self.dehydrator = Dehydrator(config)
        self.memory_gateway = ZetaMemoryGateway(config, self.bucket_mgr, self.embedding_engine)

        self.gateway_token = _env("OMBRE_GATEWAY_TOKEN")
        self.default_session_id = _env("OMBRE_GATEWAY_DEFAULT_SESSION_ID", default="zeta-main")
        self.upstream_base_url = _env(
            "OMBRE_UPSTREAM_BASE_URL",
            "OMBRE_GATEWAY_UPSTREAM_BASE_URL",
            "OMBRE_GATEWAY_UPSTREAM_URL",
        ).rstrip("/")
        self.upstream_chat_url = _chat_completions_url(self.upstream_base_url)
        self.upstream_api_key = _env(
            "OMBRE_UPSTREAM_API_KEY",
            "OMBRE_GATEWAY_UPSTREAM_API_KEY",
        )
        self.upstream_model = _env(
            "OMBRE_UPSTREAM_MODEL",
            "OMBRE_GATEWAY_UPSTREAM_MODEL",
            "OMBRE_MODEL",
            default="zeta-upstream",
        )
        self.public_model = _env("OMBRE_PUBLIC_MODEL", default=self.upstream_model)

        self.recall_max_results = int(_env("OMBRE_RECALL_MAX_RESULTS", default="5"))
        self.keyword_limit = int(_env("OMBRE_RECALL_KEYWORD_LIMIT", default="4"))
        self.semantic_limit = int(_env("OMBRE_RECALL_SEMANTIC_LIMIT", default="1"))

        self.reflection_enabled = not _truthy(_env("OMBRE_REFLECTION_DISABLED"))
        self.reflection_base_url = _env("OMBRE_REFLECTION_BASE_URL", default=self.upstream_base_url).rstrip("/")
        self.reflection_chat_url = _chat_completions_url(self.reflection_base_url)
        self.reflection_api_key = _env("OMBRE_REFLECTION_API_KEY", default=self.upstream_api_key)
        self.reflection_model = _env("OMBRE_REFLECTION_MODEL", default=self.upstream_model)
        self.reflection_timeout = float(_env("OMBRE_REFLECTION_TIMEOUT", default="30"))
        self.memory_write_mode = _env("OMBRE_MEMORY_WRITE_MODE", default="zeta").strip().lower()
        if self.memory_write_mode not in {"zeta", "reflection", "both", "zeta_or_reflection"}:
            logger.warning("Unknown OMBRE_MEMORY_WRITE_MODE=%s, falling back to zeta", self.memory_write_mode)
            self.memory_write_mode = "zeta"
        self.hidden_memory_enabled = self.memory_write_mode in {"zeta", "both", "zeta_or_reflection"}
        self.reflection_enabled = self.reflection_enabled and self.memory_write_mode in {
            "reflection",
            "both",
            "zeta_or_reflection",
        }
        self.openrouter_site_url = _env("OMBRE_OPENROUTER_SITE_URL", "OMBRE_SITE_URL")
        self.openrouter_app_name = _env(
            "OMBRE_OPENROUTER_APP_NAME",
            "OMBRE_APP_NAME",
            default="Zeta Memory Gateway",
        )
        self.reasoning_config = self._load_reasoning_config()
        self.reasoning_force = _truthy(_env(
            "OMBRE_REASONING_FORCE",
            "OMBRE_OPENROUTER_REASONING_FORCE",
        ))

        self.http = httpx.AsyncClient(timeout=120.0)

    async def close(self) -> None:
        await self.http.aclose()

    def _authorize(self, request: Request) -> Response | None:
        if not self.gateway_token:
            return None
        auth = request.headers.get("authorization", "")
        x_key = request.headers.get("x-api-key", "")
        provided = auth[7:].strip() if auth.lower().startswith("bearer ") else x_key.strip()
        if provided == self.gateway_token:
            return None
        return JSONResponse(
            {"error": {"message": "Unauthorized", "type": "invalid_api_key"}},
            status_code=401,
        )

    async def health(self, request: Request) -> JSONResponse:
        try:
            stats = await self.bucket_mgr.get_stats()
        except Exception as exc:
            logger.warning("Health stats failed: %s", exc)
            stats = {}
        return JSONResponse({
            "status": "ok",
            "gateway": "zeta_openai",
            "token_configured": bool(self.gateway_token),
            "upstream_ready": bool(self.upstream_base_url and self.upstream_api_key),
            "upstream_base_url": self.upstream_base_url,
            "upstream_chat_url": self.upstream_chat_url,
            "model": self.public_model,
            "reflection_enabled": self.reflection_enabled,
            "reflection_chat_url": self.reflection_chat_url if self.reflection_enabled else "",
            "memory_write_mode": self.memory_write_mode,
            "hidden_memory_request_enabled": self.hidden_memory_enabled,
            "openrouter_headers_configured": bool(self.openrouter_site_url or self.openrouter_app_name),
            "reasoning_configured": bool(self.reasoning_config),
            "reasoning_force": self.reasoning_force,
            "memory": self.memory_gateway.status(),
            "buckets": stats,
        })

    async def models(self, request: Request) -> JSONResponse:
        auth = self._authorize(request)
        if auth is not None:
            return auth
        return JSONResponse({
            "object": "list",
            "data": [{
                "id": self.public_model,
                "object": "model",
                "created": 0,
                "owned_by": "zeta-gateway",
            }],
        })

    async def chat_completions(self, request: Request) -> Response:
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
        recall_context = self._recall_context_text(payload.get("messages", []))
        recalled = await self.memory_gateway.recall({
            "current_text": user_text,
            "recent_context": recall_context,
            "max_results": self.recall_max_results,
            "keyword_limit": self.keyword_limit,
            "semantic_limit": self.semantic_limit,
            "track_usage": True,
        })
        memory_headers = self._memory_debug_headers(recalled)
        self._log_recall(session_id, recalled)
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
                return self._proxy_chat_response_with_text(
                    upstream_response,
                    visible_text,
                    extra_headers=memory_headers,
                )
        return self._proxy_response(upstream_response, extra_headers=memory_headers)

    async def _forward_upstream(self, payload: dict[str, Any]) -> httpx.Response:
        return await self.http.post(
            self.upstream_chat_url,
            headers=self._upstream_headers(self.upstream_api_key),
            json=self._payload_for_upstream(payload),
        )

    async def _stream_upstream(
        self,
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
            zeta_written = await self._write_zeta_memory_requests(
                session_id=session_id,
                entries=stream_filter.entries[:3],
                default_raw_ref=user_raw_refs[0] if user_raw_refs else (
                    assistant_raw_refs[0] if assistant_raw_refs else ""
                ),
            )
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

    def _payload_for_upstream(self, payload: dict[str, Any]) -> dict[str, Any]:
        upstream_payload = deepcopy(payload)
        upstream_payload["model"] = self.upstream_model
        self._apply_reasoning_config(upstream_payload)
        return upstream_payload

    def _load_reasoning_config(self) -> dict[str, Any]:
        configured: dict[str, Any] = {}
        enabled = _env(
            "OMBRE_REASONING_ENABLED",
            "OMBRE_OPENROUTER_REASONING_ENABLED",
        )
        effort = _env(
            "OMBRE_REASONING_EFFORT",
            "OMBRE_OPENROUTER_REASONING_EFFORT",
        ).lower()
        max_tokens = _env(
            "OMBRE_REASONING_MAX_TOKENS",
            "OMBRE_OPENROUTER_REASONING_MAX_TOKENS",
        )
        exclude = _env(
            "OMBRE_REASONING_EXCLUDE",
            "OMBRE_OPENROUTER_REASONING_EXCLUDE",
        )

        if max_tokens:
            try:
                parsed_max_tokens = int(max_tokens)
            except ValueError:
                logger.warning("Ignoring invalid OMBRE_REASONING_MAX_TOKENS=%s", max_tokens)
            else:
                if parsed_max_tokens > 0:
                    configured["max_tokens"] = parsed_max_tokens
                else:
                    logger.warning("Ignoring non-positive OMBRE_REASONING_MAX_TOKENS=%s", max_tokens)

        allowed_efforts = {"xhigh", "high", "medium", "low", "minimal", "none"}
        if effort:
            if effort in allowed_efforts:
                if "max_tokens" in configured and effort != "none":
                    logger.warning("OMBRE_REASONING_MAX_TOKENS is set; ignoring OMBRE_REASONING_EFFORT=%s", effort)
                elif "max_tokens" not in configured:
                    configured["effort"] = effort
            else:
                logger.warning("Ignoring invalid OMBRE_REASONING_EFFORT=%s", effort)

        if "effort" not in configured and "max_tokens" not in configured and _truthy(enabled):
            configured["enabled"] = True

        if exclude:
            if _truthy(exclude):
                configured["exclude"] = True
            elif _falsy(exclude):
                configured["exclude"] = False
            else:
                logger.warning("Ignoring invalid OMBRE_REASONING_EXCLUDE=%s", exclude)

        return configured

    def _apply_reasoning_config(self, upstream_payload: dict[str, Any]) -> None:
        existing = upstream_payload.get("reasoning")
        reasoning = deepcopy(existing) if isinstance(existing, dict) else {}

        incoming_effort = str(upstream_payload.get("reasoning_effort") or "").strip().lower()
        if incoming_effort and (self.reasoning_force or (
            "effort" not in reasoning and "max_tokens" not in reasoning
        )):
            reasoning["effort"] = incoming_effort

        if "include_reasoning" in upstream_payload and (self.reasoning_force or "exclude" not in reasoning):
            reasoning["exclude"] = not bool(upstream_payload.get("include_reasoning"))

        for key, value in self.reasoning_config.items():
            if self.reasoning_force or key not in reasoning:
                if key == "effort" and "max_tokens" in reasoning and not self.reasoning_force:
                    continue
                if key == "max_tokens" and "effort" in reasoning and not self.reasoning_force:
                    continue
                reasoning[key] = value

        if "effort" in reasoning and "max_tokens" in reasoning:
            if self.reasoning_force and "max_tokens" in self.reasoning_config:
                reasoning.pop("effort", None)
            else:
                reasoning.pop("max_tokens", None)

        if reasoning:
            upstream_payload["reasoning"] = reasoning

    def _memory_debug_headers(self, recalled: dict[str, Any]) -> dict[str, str]:
        memories = recalled.get("memories") if isinstance(recalled, dict) else []
        if not isinstance(memories, list):
            memories = []
        sources = sorted({
            str(item.get("source"))
            for item in memories
            if isinstance(item, dict) and item.get("source")
        })
        headers = {"X-Zeta-Memory-Count": str(len(memories))}
        if sources:
            headers["X-Zeta-Memory-Sources"] = ",".join(sources)[:200]
        return headers

    def _log_recall(self, session_id: str, recalled: dict[str, Any]) -> None:
        memories = recalled.get("memories") if isinstance(recalled, dict) else []
        if not isinstance(memories, list):
            memories = []
        sources: dict[str, int] = {}
        for item in memories:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "unknown")
            sources[source] = sources.get(source, 0) + 1
        logger.info(
            "Zeta recall | session=%s count=%s sources=%s",
            session_id,
            len(memories),
            sources,
        )

    def _upstream_headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-OpenRouter-Title"] = self.openrouter_app_name
            headers["X-Title"] = self.openrouter_app_name
        return headers

    def _prepare_forward_payload(self, payload: dict[str, Any], injected_text: str) -> dict[str, Any]:
        forward = deepcopy(payload)
        messages = list(forward.get("messages") or [])
        if injected_text.strip():
            memory_message = {"role": "system", "content": injected_text}
            insert_at = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(insert_at, memory_message)
        forward["messages"] = messages
        return forward

    def _build_gateway_system_text(self, recalled: dict[str, Any]) -> str:
        parts = []
        hidden_instruction = self._hidden_memory_instruction()
        if hidden_instruction:
            parts.append(hidden_instruction)
        memory_context = self._build_injection_text(recalled)
        if memory_context:
            parts.append(memory_context)
        return "\n\n".join(parts).strip()

    def _build_injection_text(self, recalled: dict[str, Any]) -> str:
        injection = str(recalled.get("injection_text") or "").strip()
        if not injection:
            return ""
        return (
            "Private memory context for Zeta. Use it quietly as background continuity. "
            "Do not mention that a memory gateway or hidden context exists unless the user asks.\n\n"
            f"{injection}"
        )

    def _hidden_memory_instruction(self) -> str:
        if not self.hidden_memory_enabled:
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

    def _extract_zeta_memory_request(self, assistant_text: str) -> tuple[str, list[dict[str, Any]]]:
        text = assistant_text or ""
        if not self.hidden_memory_enabled or MEMORY_REQUEST_OPEN not in text:
            return text, []

        entries: list[dict[str, Any]] = []

        def collect(match: re.Match) -> str:
            raw_json = match.group(1).strip()
            parsed = self._parse_zeta_memory_json(raw_json)
            entries.extend(parsed)
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

    def _parse_zeta_memory_json(self, raw_json: str) -> list[dict[str, Any]]:
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

    def _normalize_requested_memory_entry(self, item: dict[str, Any]) -> dict[str, Any] | None:
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
        self,
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
        self,
        headers: dict[str, str],
        requested_entries: list[dict[str, Any]],
        written_count: int,
    ) -> None:
        headers["X-Zeta-Memory-Requests"] = str(len(requested_entries))
        headers["X-Zeta-Memory-Written"] = str(written_count)

    def _should_run_reflection(self, zeta_written_count: int) -> bool:
        if not self.reflection_enabled:
            return False
        if self.memory_write_mode == "zeta_or_reflection" and zeta_written_count > 0:
            return False
        return True

    async def _save_turn(self, session_id: str, speaker: str, content: str) -> list[str]:
        if not str(content or "").strip():
            return []
        result = await self.memory_gateway.save_raw({
            "session_id": session_id,
            "source": "zeta_openai_gateway",
            "messages": [{
                "speaker": speaker,
                "content": content,
            }],
        })
        return list(result.get("raw_refs") or [])

    def _schedule_reflection(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        user_raw_refs: list[str],
        assistant_raw_refs: list[str],
        recalled: dict[str, Any],
    ) -> None:
        if not self.reflection_enabled:
            return
        if not user_text.strip() or not assistant_text.strip():
            return

        async def runner() -> None:
            try:
                await self._reflect_and_write_memory(
                    session_id=session_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    user_raw_refs=user_raw_refs,
                    assistant_raw_refs=assistant_raw_refs,
                    recalled=recalled,
                )
            except Exception as exc:
                logger.warning("Zeta reflection failed | session=%s error=%s", session_id, exc)

        asyncio.create_task(runner())

    async def _reflect_and_write_memory(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        user_raw_refs: list[str],
        assistant_raw_refs: list[str],
        recalled: dict[str, Any],
    ) -> None:
        if not self.reflection_chat_url or not self.reflection_api_key or not self.reflection_model:
            return
        raw_ref = user_raw_refs[0] if user_raw_refs else (assistant_raw_refs[0] if assistant_raw_refs else "")
        prompt = self._reflection_prompt(
            session_id=session_id,
            user_text=user_text,
            assistant_text=assistant_text,
            raw_ref=raw_ref,
            recalled=recalled,
        )
        response = await self.http.post(
            self.reflection_chat_url,
            headers=self._upstream_headers(self.reflection_api_key),
            json={
                "model": self.reflection_model,
                "messages": [
                    {"role": "system", "content": "You decide whether Zeta should store long-term memories. Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "stream": False,
            },
            timeout=self.reflection_timeout,
        )
        if not 200 <= response.status_code < 300:
            logger.warning("Reflection upstream failed | status=%s body=%s", response.status_code, response.text[:500])
            return
        text = self._assistant_text_from_response(response)
        entries = self._parse_reflection_entries(text)
        for entry in entries:
            if not entry.get("raw_ref"):
                entry["raw_ref"] = raw_ref
            await self.memory_gateway.write_memory(entry)
            logger.info("Zeta memory written | session=%s summary=%s", session_id, entry.get("summary_text", "")[:80])

    def _reflection_prompt(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        raw_ref: str,
        recalled: dict[str, Any],
    ) -> str:
        recalled_summaries = [
            item.get("summary_text", "")
            for item in recalled.get("memories", [])
            if isinstance(item, dict) and item.get("summary_text")
        ]
        return f"""
Session: {session_id}
Default raw_ref for any new memory: {raw_ref}

User message:
{user_text}

Zeta reply:
{assistant_text}

Memories already injected this turn:
{json.dumps(recalled_summaries, ensure_ascii=False)}

Decide whether Zeta should create long-term memory entries from this exchange.
Rules:
- If nothing is worth remembering, return {{"memories":[]}}.
- Store only stable preferences, relationship facts, commitments, identity-relevant events, or emotionally meaningful moments.
- Do not store trivial chatter.
- Do not store memories about the memory gateway, hidden memory injection, prompts, tools, MCP, Zeabur, OpenRouter, API keys, model settings, deployment, or debugging.
- Do not store that "memories were injected" or that Zeta explained how memory works.
- If the exchange is mainly testing or debugging memory behavior, return {{"memories":[]}} unless the user explicitly asks Zeta to remember a personal fact or preference.
- Every memory must include summary_text, tags, importance, raw_ref.
- tags may be a JSON array or comma-separated string.
- importance must be 1-10.
- Only include feel_text, valence, arousal when Zeta has a real emotional reaction or importance >= 5.
- valence and arousal must be 0-1.

Return strict JSON with this shape:
{{"memories":[{{"summary_text":"...","tags":["..."],"importance":7,"raw_ref":"{raw_ref}","feel_text":"...","valence":0.8,"arousal":0.4}}]}}
""".strip()

    def _parse_reflection_entries(self, text: str) -> list[dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return []
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Reflection JSON parse failed: %s", text[:500])
            return []
        memories = payload.get("memories", []) if isinstance(payload, dict) else []
        if not isinstance(memories, list):
            return []
        entries = []
        for item in memories:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary_text") or "").strip()
            raw_ref = str(item.get("raw_ref") or "").strip()
            if not summary:
                continue
            entry = {
                "summary_text": summary,
                "tags": item.get("tags", []),
                "importance": item.get("importance", 5),
                "raw_ref": raw_ref,
            }
            for field in ("feel_text", "valence", "arousal"):
                if item.get(field) is not None and item.get(field) != "":
                    entry[field] = item[field]
            if self._is_rejected_reflection_entry(entry):
                logger.info("Rejected meta memory from reflection | summary=%s", summary[:120])
                continue
            entries.append(entry)
        return entries[:3]

    def _is_rejected_reflection_entry(self, entry: dict[str, Any]) -> bool:
        tags = entry.get("tags", [])
        tag_text = ",".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
        text = " ".join([
            str(entry.get("summary_text") or ""),
            str(entry.get("feel_text") or ""),
            tag_text,
        ]).lower()
        blocked_terms = [
            "memory gateway",
            "zeta memory gateway",
            "hidden context",
            "injected",
            "injection",
            "long-term memories injected",
            "system prompt",
            "prompt",
            "mcp",
            "openrouter",
            "zeabur",
            "api key",
            "base url",
            "environment variable",
            "deployment",
            "debug",
            "debugging",
            "tool call",
        ]
        return any(term in text for term in blocked_terms)

    def _extract_last_user_text(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                return self._sanitize_user_text(self._message_content_to_text(message.get("content")))
        return ""

    def _recall_context_text(self, messages: list[dict[str, Any]]) -> str:
        if not isinstance(messages, list):
            return ""

        current_index = None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, dict) and message.get("role") == "user":
                current_index = index
                break
        if current_index is None:
            return ""

        previous_user: tuple[str, str] | None = None
        previous_assistant: tuple[str, str] | None = None
        for index in range(current_index - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            text = self._message_content_to_text(message.get("content")).strip()
            if role == "user":
                text = self._sanitize_user_text(text)
            text = self._recall_context_piece(text, 900)
            if not text:
                continue
            if role == "assistant" and previous_assistant is None:
                previous_assistant = (role, text)
                continue
            if role == "user":
                previous_user = (role, text)
                break

        selected: list[tuple[str, str]] = []
        if previous_user:
            selected.append(previous_user)
        if previous_assistant:
            selected.append(previous_assistant)

        current_user = self._sanitize_user_text(
            self._message_content_to_text(messages[current_index].get("content"))
        )
        current_user = self._recall_context_piece(current_user, 1800)
        if current_user:
            selected.append(("user", current_user))

        return "\n".join(f"{role}: {text}" for role, text in selected)[-2800:]

    def _recall_context_piece(self, text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

    def _recent_context_text(self, messages: list[dict[str, Any]]) -> str:
        texts = []
        for message in messages[-8:]:
            if not isinstance(message, dict):
                continue
            role = message.get("role", "")
            if role == "system":
                continue
            text = self._message_content_to_text(message.get("content")).strip()
            if role == "user":
                text = self._sanitize_user_text(text)
            if text:
                texts.append(f"{role}: {text}")
        return "\n".join(texts)[-4000:]

    def _sanitize_user_text(self, text: str) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""

        for pattern in (
            r"(?im)^\s*(?:query|user_message|message|text)\s*[:=]\s*(.+?)\s*$",
            r"(?im)^\s*(?:用户消息|消息正文|正文|问题)\s*[:：=]\s*(.+?)\s*$",
        ):
            matches = [m.group(1).strip() for m in re.finditer(pattern, raw) if m.group(1).strip()]
            if matches:
                return matches[-1]

        cleaned_lines = []
        metadata_prefixes = (
            "current time",
            "current_time",
            "timestamp",
            "timezone",
            "app uptime",
            "app runtime",
            "application uptime",
            "application runtime",
            "elapsed",
            "duration",
            "当前时间",
            "现在时间",
            "时间戳",
            "时区",
            "应用时长",
            "运行时长",
            "使用时长",
            "会话时长",
            "页面",
            "窗口",
        )
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            stripped = self._strip_operit_context_noise(stripped).strip()
            if not stripped:
                continue
            low = stripped.lower()
            if any(low.startswith(prefix) for prefix in metadata_prefixes):
                continue
            if re.match(r"^(?:query|user_message|message|text|filename|file|path|url|mime|type|size)\s*[:=]", low):
                continue
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines).strip() or raw

    @staticmethod
    def _strip_operit_context_noise(text: str) -> str:
        cleaned = str(text or "")
        if not cleaned.strip():
            return ""

        metadata_markers = [
            "【当前屏幕应用】",
            "【应用使用时长】",
            "统计窗口:",
            "最近使用:",
            "包名:",
            "Activity:",
            "来源: wttr.in",
            "source: wttr.in",
            "message_insert_extra_bundle",
            "keyword=",
            "terms=",
        ]
        positions = [cleaned.find(marker) for marker in metadata_markers if cleaned.find(marker) >= 0]
        if positions:
            prefix = cleaned[:min(positions)].strip()
            cleaned = prefix if prefix else ""

        cleaned = re.sub(r"\bkeyword\s*=\s*[^|\n]*", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bterms\s*=\s*.*$", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bmessage_insert_extra_bundle_\d+\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bcom\.[A-Za-z0-9_.-]+\b", " ", cleaned)
        cleaned = re.sub(r"\bActivity\s*:\s*\S+", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:package|pkg|包名)\s*[:：]\s*\S+", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:current\s+app|app\s+uptime|recent\s+use)\b.*", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:风速|湿度|来源\s*[:：]\s*wttr\.in|weather|thundery)[^。！？\n]*", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if "%" in cleaned and re.fullmatch(r"[\d\s%./:-]+", cleaned):
            return ""

        metadata_only_markers = (
            "当前屏幕应用",
            "应用使用时长",
            "最近使用",
            "统计窗口",
            "wttr.in",
            "风速",
            "包名",
            "activity",
            "keyword=",
            "terms=",
        )
        lowered = cleaned.lower()
        if any(marker in lowered for marker in metadata_only_markers):
            return ""
        return cleaned

    def _message_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif "text" in item:
                        parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        if content is None:
            return ""
        return str(content)

    def _assistant_text_from_response(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return ""
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices:
            return ""
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        return self._message_content_to_text(message.get("content")).strip()

    def _capture_openai_stream_chunk(self, chunk: bytes, assistant_parts: list[str]) -> None:
        text = chunk.decode("utf-8", errors="ignore")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = payload.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content")
            if isinstance(content, str):
                assistant_parts.append(content)

    def _proxy_response(self, response: httpx.Response, extra_headers: dict[str, str] | None = None) -> Response:
        if response.status_code >= 400:
            return self._upstream_status_error(
                response.status_code,
                response.headers.get("content-type", ""),
                response.content,
            )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding", "connection"}
        }
        if extra_headers:
            headers.update(extra_headers)
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=headers,
            media_type=response.headers.get("content-type"),
        )

    def _proxy_chat_response_with_text(
        self,
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

    def _stream_events_from_text(self, response: httpx.Response, assistant_text: str) -> list[bytes]:
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

    def _upstream_request_error(self, exc: httpx.RequestError) -> JSONResponse:
        logger.warning("Upstream request failed | url=%s error=%s", self.upstream_chat_url, exc)
        return JSONResponse(
            {
                "error": {
                    "message": "Gateway could not reach the upstream model provider",
                    "type": "upstream_connection_error",
                    "upstream_chat_url": self.upstream_chat_url,
                    "hint": "For OpenRouter use OMBRE_UPSTREAM_BASE_URL=https://openrouter.ai/api/v1, not the site URL.",
                    "detail": str(exc),
                }
            },
            status_code=502,
        )

    def _upstream_status_error(self, status_code: int, content_type: str, body: bytes) -> Response:
        if "json" in str(content_type).lower():
            return Response(content=body, status_code=status_code, media_type=content_type)
        text = body.decode("utf-8", errors="replace")
        return JSONResponse(
            {
                "error": {
                    "message": f"Upstream model provider returned HTTP {status_code}",
                    "type": "upstream_status_error",
                    "upstream_chat_url": self.upstream_chat_url,
                    "body_preview": _preview_text(text),
                }
            },
            status_code=status_code,
        )


startup_error = ""
gateway: ZetaOpenAIGateway | None = None
try:
    config = load_config()
    setup_logging(config.get("log_level", "INFO"))
    gateway = ZetaOpenAIGateway(config)
except Exception as exc:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    startup_error = f"{type(exc).__name__}: {exc}"
    logger.exception("Zeta OpenAI gateway startup failed")


async def health_route(request: Request) -> JSONResponse:
    if gateway is None:
        return JSONResponse(
            {
                "status": "startup_error",
                "gateway": "zeta_openai",
                "error": startup_error,
                "hint": "The web server is running, but memory gateway initialization failed. Check Zeabur logs and OMBRE_BUCKETS_DIR permissions.",
            },
            status_code=503,
        )
    return await gateway.health(request)


async def models_route(request: Request) -> Response:
    if gateway is None:
        return JSONResponse(
            {"error": {"message": f"Gateway startup failed: {startup_error}", "type": "server_error"}},
            status_code=503,
        )
    return await gateway.models(request)


async def chat_completions_route(request: Request) -> Response:
    if gateway is None:
        return JSONResponse(
            {"error": {"message": f"Gateway startup failed: {startup_error}", "type": "server_error"}},
            status_code=503,
        )
    return await gateway.chat_completions(request)


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        if gateway is not None:
            await gateway.close()


routes = [
    Route("/health", health_route, methods=["GET"]),
    Route("/v1/models", models_route, methods=["GET"]),
    Route("/v1/chat/completions", chat_completions_route, methods=["POST"]),
]

app = Starlette(routes=routes, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


if __name__ == "__main__":
    try:
        port = int(_env("PORT", "OMBRE_PORT", default="8000"))
    except ValueError:
        port = 8000
    logger.info("Starting Zeta OpenAI gateway on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
