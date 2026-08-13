import io
import json
import wave
from dataclasses import dataclass

import httpx


ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"


class CallConfigurationError(RuntimeError):
    pass


class CallProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallAudioStatus:
    configured: bool
    voice_configured: bool
    tts_model: str
    stt_model: str
    sample_rate: int


def pcm16_to_wav(pcm: bytes, *, sample_rate: int = 16000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm)
    return output.getvalue()


class ElevenLabsAudioPipeline:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_key: str,
        voice_id: str,
        tts_model: str = "eleven_v3",
        stt_model: str = "scribe_v2",
        sample_rate: int = 16000,
        language_code: str = "",
    ):
        self.http = http
        self.api_key = str(api_key or "").strip()
        self.voice_id = str(voice_id or "").strip()
        self.tts_model = str(tts_model or "eleven_v3").strip()
        self.stt_model = str(stt_model or "scribe_v2").strip()
        self.sample_rate = int(sample_rate)
        self.language_code = str(language_code or "").strip()

    def status(self) -> CallAudioStatus:
        return CallAudioStatus(
            configured=bool(self.api_key and self.voice_id),
            voice_configured=bool(self.voice_id),
            tts_model=self.tts_model,
            stt_model=self.stt_model,
            sample_rate=self.sample_rate,
        )

    def require_ready(self) -> None:
        if not self.api_key:
            raise CallConfigurationError(
                "服务器还没有配置 ELEVENLABS_API_KEY（也可用 OMBRE_CALL_ELEVENLABS_API_KEY）。"
            )
        if not self.voice_id:
            raise CallConfigurationError(
                "服务器还没有配置 OMBRE_CALL_TTS_VOICE_ID。请先在 ElevenLabs 创建并保存音色。"
            )

    async def transcribe_pcm(self, pcm: bytes) -> str:
        self.require_ready()
        if not pcm:
            return ""
        data = {
            "model_id": self.stt_model,
            # Preserve useful non-verbal cues such as laughter or sighs in
            # the transcript. The dialogue model still receives text only;
            # the raw recording is never forwarded to it.
            "tag_audio_events": "true",
            "diarize": "false",
        }
        if self.language_code:
            data["language_code"] = self.language_code
        try:
            response = await self.http.post(
                f"{ELEVENLABS_BASE_URL}/speech-to-text",
                headers={"xi-api-key": self.api_key},
                data=data,
                files={"file": ("utterance.wav", pcm16_to_wav(pcm, sample_rate=self.sample_rate), "audio/wav")},
                timeout=90.0,
            )
        except httpx.RequestError as exc:
            raise CallProviderError(f"连接 ElevenLabs 语音识别失败：{exc}") from exc
        if not 200 <= response.status_code < 300:
            raise CallProviderError(self._provider_error("语音识别", response))
        try:
            body = response.json()
        except ValueError as exc:
            raise CallProviderError("ElevenLabs 语音识别返回了无法解析的数据。") from exc
        return str(body.get("text") or "").strip() if isinstance(body, dict) else ""

    async def synthesize_pcm(self, text: str) -> bytes:
        self.require_ready()
        spoken_text = str(text or "").strip()
        if not spoken_text:
            return b""
        url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{self.voice_id}/stream"
        params = {"output_format": f"pcm_{self.sample_rate}"}
        payload = {
            "text": spoken_text,
            "model_id": self.tts_model,
        }
        try:
            async with self.http.stream(
                "POST",
                url,
                params=params,
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=120.0,
            ) as response:
                if not 200 <= response.status_code < 300:
                    body = await response.aread()
                    preview = body.decode("utf-8", errors="replace")[:500]
                    raise CallProviderError(
                        f"ElevenLabs 语音合成返回 HTTP {response.status_code}：{preview}"
                    )
                chunks = [chunk async for chunk in response.aiter_bytes() if chunk]
        except CallProviderError:
            raise
        except httpx.RequestError as exc:
            raise CallProviderError(f"连接 ElevenLabs 语音合成失败：{exc}") from exc
        return b"".join(chunks)

    @staticmethod
    def _provider_error(action: str, response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = json.dumps(body, ensure_ascii=False)
        except ValueError:
            detail = response.text
        return f"ElevenLabs {action}返回 HTTP {response.status_code}：{detail[:500]}"
