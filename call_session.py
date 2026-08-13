import asyncio
import json
import logging
import uuid
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from call_audio_pipeline import CallConfigurationError, CallProviderError, ElevenLabsAudioPipeline


logger = logging.getLogger("ombre_brain.call")
MAX_CONTEXT_ITEMS = 64
MAX_CONTEXT_CHARACTERS = 240_000
MAX_UTTERANCE_BYTES = 16_000 * 2 * 90
AUDIO_CHUNK_BYTES = 16_000


def sanitize_call_context(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    characters = 0
    for item in reversed(value[-MAX_CONTEXT_ITEMS:]):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        remaining = MAX_CONTEXT_CHARACTERS - characters
        if remaining <= 0:
            break
        text = text[-remaining:]
        message: dict[str, Any] = {"role": role, "content": text}
        kind = str(item.get("ombre_context_kind") or "").strip()
        if kind in {"conversation_summary", "schedule", "call_private"}:
            message["ombre_context_kind"] = kind
        raw_context = item.get("context")
        if isinstance(raw_context, dict):
            message["context"] = raw_context
        for key in ("createdAt", "timezone"):
            if item.get(key):
                message[key] = str(item[key])[:80]
        cleaned.append(message)
        characters += len(text)
    cleaned.reverse()
    return cleaned


def latest_call_private_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    private_context: dict[str, Any] = {}
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        raw_context = message.get("context")
        if not isinstance(raw_context, dict):
            continue
        for key in ("health", "device"):
            if key not in private_context and isinstance(raw_context.get(key), dict):
                private_context[key] = raw_context[key]
        if "health" in private_context and "device" in private_context:
            break
    return private_context


class CallSession:
    def __init__(self, gateway: Any, websocket: WebSocket, pipeline: ElevenLabsAudioPipeline):
        self.gateway = gateway
        self.websocket = websocket
        self.pipeline = pipeline
        self.session_id = gateway.default_session_id
        self.timezone_name = gateway.solo.timezone_name
        self.context_messages: list[dict[str, Any]] = []
        self.call_turns: list[dict[str, str]] = []
        self.private_context: dict[str, Any] = {}
        self.call_id = uuid.uuid4().hex
        self.end_reason = "disconnect"
        self.audio = bytearray()
        self.speech_active = False
        self.started = False
        self.closed = False
        self.response_task: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        self.appraisal_finalized = False

    async def run(self) -> None:
        await self.websocket.accept()
        await self.send_json({"type": "status", "state": "connected"})
        try:
            while True:
                event = await self.websocket.receive()
                event_type = event.get("type")
                if event_type == "websocket.disconnect":
                    break
                raw_bytes = event.get("bytes")
                if raw_bytes is not None:
                    self._append_audio(raw_bytes)
                    continue
                raw_text = event.get("text")
                if raw_text is not None:
                    await self._handle_text(raw_text)
        except WebSocketDisconnect:
            pass
        finally:
            self.closed = True
            await self._cancel_response()
            await self._finalize_appraisal()

    async def _handle_text(self, raw_text: str) -> None:
        try:
            message = json.loads(raw_text)
        except json.JSONDecodeError:
            await self.send_error("通话控制消息不是有效 JSON。")
            return
        if not isinstance(message, dict):
            await self.send_error("通话控制消息格式不正确。")
            return
        message_type = str(message.get("type") or "").strip().lower()
        if message_type == "start":
            await self._start(message)
        elif message_type == "speech_start":
            await self._speech_start()
        elif message_type == "speech_end":
            await self._speech_end()
        elif message_type == "barge_in":
            await self._cancel_response()
            await self.send_json({"type": "audio_stop"})
            await self.send_json({"type": "status", "state": "listening"})
        elif message_type == "hangup":
            await self._close("user")
        elif message_type == "ping":
            await self.send_json({"type": "pong"})

    async def _start(self, message: dict[str, Any]) -> None:
        if self.started:
            return
        try:
            self.pipeline.require_ready()
        except CallConfigurationError as exc:
            await self.send_error(str(exc), code="call_not_configured")
            await self._close("configuration", notify=False)
            return
        self.session_id = str(message.get("sessionId") or self.session_id).strip()[:160]
        invite_id = str(message.get("inviteId") or "").strip()[:80]
        if invite_id:
            self.call_id = invite_id
        if invite_id and hasattr(self.gateway, "call_delivery"):
            self.gateway.call_delivery.respond(invite_id, "answer", note="websocket_connected")
        self.timezone_name = str(message.get("timezone") or self.timezone_name).strip()[:80]
        self.context_messages = sanitize_call_context(message.get("contextMessages"))
        self.private_context = latest_call_private_context(self.context_messages)
        self.started = True
        await self.send_json({
            "type": "ready",
            "sampleRate": self.pipeline.sample_rate,
            "ttsModel": self.pipeline.tts_model,
            "sttModel": self.pipeline.stt_model,
        })
        await self.send_json({"type": "status", "state": "listening"})

    async def _speech_start(self) -> None:
        if not self.started:
            await self.send_error("通话尚未初始化。")
            return
        await self._cancel_response()
        await self.send_json({"type": "audio_stop"})
        self.audio.clear()
        self.speech_active = True
        await self.send_json({"type": "status", "state": "listening"})

    async def _speech_end(self) -> None:
        if not self.started or not self.speech_active:
            return
        self.speech_active = False
        pcm = bytes(self.audio)
        self.audio.clear()
        if len(pcm) < self.pipeline.sample_rate * 2 // 5:
            await self.send_json({"type": "status", "state": "listening"})
            return
        await self._cancel_response()
        self.response_task = asyncio.create_task(self._answer(pcm))

    def _append_audio(self, frame: bytes) -> None:
        if not self.started or not self.speech_active or self.closed:
            return
        if len(self.audio) + len(frame) > MAX_UTTERANCE_BYTES:
            return
        self.audio.extend(frame)

    async def _answer(self, pcm: bytes) -> None:
        try:
            await self.send_json({"type": "status", "state": "transcribing"})
            user_text = await self.pipeline.transcribe_pcm(pcm)
            if not user_text:
                await self.send_json({"type": "status", "state": "listening"})
                return
            await self.send_json({"type": "transcript", "speaker": "user", "text": user_text})
            self.call_turns.append({"role": "user", "content": user_text})
            self.call_turns = sanitize_call_context(self.call_turns)
            await self.send_json({"type": "status", "state": "thinking"})
            answer = await self.gateway.generate_call_reply(
                context_messages=self.context_messages,
                user_text=user_text,
                session_id=self.session_id,
                client_timezone=self.timezone_name,
            )
            visible_text = str(answer.get("text") or "").strip()
            hangup = bool(answer.get("hangup"))
            self.context_messages.extend([
                {
                    "role": "user",
                    "content": user_text,
                    **({"context": self.private_context} if self.private_context else {}),
                },
                {"role": "assistant", "content": visible_text},
            ])
            self.context_messages = sanitize_call_context(self.context_messages)
            if visible_text:
                self.call_turns.append({"role": "assistant", "content": visible_text})
                self.call_turns = sanitize_call_context(self.call_turns)
                await self.send_json({"type": "transcript", "speaker": "assistant", "text": visible_text})
                await self.send_json({"type": "status", "state": "speaking"})
                pcm_reply = await self.pipeline.synthesize_pcm(visible_text)
                if pcm_reply:
                    await self.send_json({
                        "type": "audio_start",
                        "sampleRate": self.pipeline.sample_rate,
                        "bytes": len(pcm_reply),
                    })
                    for offset in range(0, len(pcm_reply), AUDIO_CHUNK_BYTES):
                        await self.send_bytes(pcm_reply[offset:offset + AUDIO_CHUNK_BYTES])
                        await asyncio.sleep(0)
                    await self.send_json({"type": "audio_end"})
            if hangup:
                self.end_reason = "assistant"
                await self.send_json({"type": "marker", "name": "hangup", "immediate": True})
                return
            await self.send_json({"type": "status", "state": "listening"})
        except asyncio.CancelledError:
            raise
        except (CallConfigurationError, CallProviderError) as exc:
            await self.send_error(str(exc), code="voice_provider_error")
            await self.send_json({"type": "status", "state": "listening"})
        except Exception as exc:
            logger.exception("Call turn failed")
            await self.send_error(f"这一轮通话失败：{exc}", code="call_turn_error")
            await self.send_json({"type": "status", "state": "listening"})

    async def _cancel_response(self) -> None:
        task = self.response_task
        self.response_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _finalize_appraisal(self) -> None:
        if self.appraisal_finalized:
            return
        self.appraisal_finalized = True
        if not self.started or not self.call_turns:
            return
        finalizer = getattr(self.gateway, "finalize_call_appraisal", None)
        if not callable(finalizer):
            return
        try:
            await finalizer(
                call_id=self.call_id,
                session_id=self.session_id,
                messages=list(self.call_turns),
                end_reason=self.end_reason,
            )
        except Exception as exc:
            logger.warning("Unable to finalize voice call appraisal | error=%s", exc)

    async def _close(self, reason: str, *, notify: bool = True) -> None:
        if self.closed:
            return
        self.end_reason = str(reason or "disconnect")[:40]
        if notify:
            await self.send_json({"type": "ended", "reason": reason})
        self.closed = True
        try:
            await self.websocket.close(code=1000)
        except RuntimeError:
            pass

    async def send_error(self, message: str, *, code: str = "call_error") -> None:
        await self.send_json({"type": "error", "code": code, "message": str(message)})

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def send_bytes(self, payload: bytes) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.websocket.send_bytes(payload)
