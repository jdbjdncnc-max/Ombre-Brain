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


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
        self.openrouter_site_url = _env("OMBRE_OPENROUTER_SITE_URL", "OMBRE_SITE_URL")
        self.openrouter_app_name = _env(
            "OMBRE_OPENROUTER_APP_NAME",
            "OMBRE_APP_NAME",
            default="Zeta Memory Gateway",
        )

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
            "openrouter_headers_configured": bool(self.openrouter_site_url or self.openrouter_app_name),
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
        recalled = await self.memory_gateway.recall({
            "current_text": user_text,
            "recent_context": self._recent_context_text(payload.get("messages", [])),
            "max_results": self.recall_max_results,
            "keyword_limit": self.keyword_limit,
            "semantic_limit": self.semantic_limit,
        })
        memory_headers = self._memory_debug_headers(recalled)
        self._log_recall(session_id, recalled)
        injected_text = self._build_injection_text(recalled)
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
            assistant_raw_refs = await self._save_turn(session_id, "zeta", assistant_text)
            self._schedule_reflection(
                session_id=session_id,
                user_text=user_text,
                assistant_text=assistant_text,
                user_raw_refs=user_raw_refs,
                assistant_raw_refs=assistant_raw_refs,
                recalled=recalled,
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
        request = self.http.build_request(
            "POST",
            self.upstream_chat_url,
            headers=self._upstream_headers(self.upstream_api_key),
            json=self._payload_for_upstream(payload),
        )
        try:
            upstream_response = await self.http.send(request, stream=True)
        except httpx.RequestError as exc:
            return self._upstream_request_error(exc)
        content_type = upstream_response.headers.get("content-type", "text/event-stream")
        if not 200 <= upstream_response.status_code < 300:
            body = await upstream_response.aread()
            await upstream_response.aclose()
            return self._upstream_status_error(upstream_response.status_code, content_type, body)

        async def stream_body():
            assistant_parts: list[str] = []
            try:
                async for chunk in upstream_response.aiter_bytes():
                    self._capture_openai_stream_chunk(chunk, assistant_parts)
                    yield chunk
            finally:
                await upstream_response.aclose()
                assistant_text = "".join(assistant_parts).strip()
                assistant_raw_refs = await self._save_turn(session_id, "zeta", assistant_text)
                self._schedule_reflection(
                    session_id=session_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    user_raw_refs=user_raw_refs,
                    assistant_raw_refs=assistant_raw_refs,
                    recalled=recalled,
                )

        return StreamingResponse(
            stream_body(),
            status_code=upstream_response.status_code,
            media_type=content_type,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **memory_headers},
        )

    def _payload_for_upstream(self, payload: dict[str, Any]) -> dict[str, Any]:
        upstream_payload = deepcopy(payload)
        upstream_payload["model"] = self.upstream_model
        return upstream_payload

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

    def _build_injection_text(self, recalled: dict[str, Any]) -> str:
        injection = str(recalled.get("injection_text") or "").strip()
        if not injection:
            return ""
        return (
            "Private memory context for Zeta. Use it quietly as background continuity. "
            "Do not mention that a memory gateway or hidden context exists unless the user asks.\n\n"
            f"{injection}"
        )

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
                return self._message_content_to_text(message.get("content"))
        return ""

    def _recent_context_text(self, messages: list[dict[str, Any]]) -> str:
        texts = []
        for message in messages[-8:]:
            if not isinstance(message, dict):
                continue
            role = message.get("role", "")
            if role == "system":
                continue
            text = self._message_content_to_text(message.get("content")).strip()
            if text:
                texts.append(f"{role}: {text}")
        return "\n".join(texts)[-4000:]

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
