import asyncio
import io
import wave

import httpx
import pytest

from call_audio_pipeline import CallConfigurationError, ElevenLabsAudioPipeline, pcm16_to_wav
from call_markers import extract_call_markers
from call_session import MAX_CONTEXT_CHARACTERS, sanitize_call_context
from zeta_openai_gateway import CALL_EMPTY_RETRY_PROMPT, CALL_SYSTEM_PROMPT, ZetaOpenAIGateway


def test_hangup_marker_is_private_and_immediate_signal():
    result = extract_call_markers("晚安，明天见。\n⟪挂断⟫")

    assert result.text == "晚安，明天见。"
    assert result.hangup is True


def test_similar_plain_language_does_not_accidentally_hang_up():
    result = extract_call_markers("我还不想挂断，我们再说一会儿。")

    assert result.text == "我还不想挂断，我们再说一会儿。"
    assert result.hangup is False


def test_pcm_is_wrapped_as_mono_16_bit_wav():
    pcm = b"\x01\x02" * 160
    wrapped = pcm16_to_wav(pcm)

    with wave.open(io.BytesIO(wrapped), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 16000
        assert source.readframes(160) == pcm


def test_audio_pipeline_reports_missing_server_configuration():
    pipeline = ElevenLabsAudioPipeline(httpx.AsyncClient(), api_key="", voice_id="")
    try:
        try:
            pipeline.require_ready()
        except CallConfigurationError as exc:
            assert "ELEVENLABS_API_KEY" in str(exc)
        else:
            raise AssertionError("missing credentials must be rejected")
    finally:
        asyncio.run(pipeline.http.aclose())


@pytest.mark.asyncio
async def test_audio_pipeline_keeps_scribe_audio_event_tags():
    class FakeHttp:
        def __init__(self):
            self.data = None

        async def post(self, *args, **kwargs):
            self.data = kwargs.get("data")
            return httpx.Response(200, json={"text": "（轻笑）你好"})

    fake_http = FakeHttp()
    pipeline = ElevenLabsAudioPipeline(
        fake_http,
        api_key="secret",
        voice_id="voice",
    )

    text = await pipeline.transcribe_pcm(b"\x00\x00" * 8000)

    assert text == "（轻笑）你好"
    assert fake_http.data["tag_audio_events"] == "true"


def test_call_context_keeps_summary_recent_messages_and_private_device_context():
    messages = [
        {
            "role": "system",
            "ombre_context_kind": "conversation_summary",
            "content": "此前的累计摘要",
        },
        {"role": "user", "content": "上一句话"},
        {
            "role": "user",
            "content": "现在的话",
            "context": {"device": {"currentApp": {"label": "地图"}}},
        },
    ]

    cleaned = sanitize_call_context(messages)

    assert [item["role"] for item in cleaned] == ["system", "user", "user"]
    assert cleaned[0]["ombre_context_kind"] == "conversation_summary"
    assert cleaned[-1]["context"]["device"]["currentApp"]["label"] == "地图"


def test_call_context_has_hard_character_budget():
    cleaned = sanitize_call_context([
        {"role": "user", "content": "甲" * (MAX_CONTEXT_CHARACTERS + 1000)}
    ])

    assert len(cleaned) == 1
    assert len(cleaned[0]["content"]) == MAX_CONTEXT_CHARACTERS


def test_call_private_carrier_is_removed_before_upstream_prompt():
    gateway = ZetaOpenAIGateway.__new__(ZetaOpenAIGateway)
    kept, summary, schedule = gateway._extract_ombre_context_messages([
        {
            "role": "system",
            "ombre_context_kind": "call_private",
            "content": "Ombre 通话开始资料",
            "context": {"device": {"currentApp": {"label": "地图"}}},
        },
        {"role": "user", "content": "你好"},
    ])

    assert kept == [{"role": "user", "content": "你好"}]
    assert summary == ""
    assert schedule == ""


@pytest.mark.asyncio
async def test_call_reply_reuses_context_and_hides_hangup_marker():
    gateway = ZetaOpenAIGateway.__new__(ZetaOpenAIGateway)
    gateway.upstream_chat_url = "https://example.test/v1/chat/completions"
    gateway.upstream_api_key = "secret"
    gateway.public_model = "zeta"
    gateway.call_max_tokens = 420
    gateway.call_reasoning_effort = "minimal"
    gateway.recall_max_results = 5
    gateway.keyword_limit = 2
    gateway.semantic_limit = 3
    gateway.hidden_memory_enabled = False
    gateway.solo = _FakeSolo()
    gateway.memory_gateway = _FakeMemory()
    gateway._log_recall = lambda *args, **kwargs: None
    gateway._remember_recall_debug = lambda *args, **kwargs: None
    gateway._build_gateway_system_text = lambda recalled: "gateway rules"
    gateway._read_system_prompt = lambda: "persona"
    captured = {}

    def prepare(payload, *args, **kwargs):
        captured["payload"] = payload
        return payload

    async def save_turn(*args, **kwargs):
        return ["raw:1"]

    async def write_memories(**kwargs):
        return 0

    async def forward(payload):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "晚安，明天见。⟪挂断⟫"}}]
        })

    gateway._prepare_forward_payload = prepare
    gateway._save_turn = save_turn
    gateway._write_zeta_memory_requests = write_memories
    gateway._should_run_reflection = lambda count: False
    gateway._forward_upstream = forward

    result = await gateway.generate_call_reply(
        context_messages=[{
            "role": "system",
            "ombre_context_kind": "call_private",
            "content": "Ombre 通话开始资料",
            "context": {"device": {"currentApp": {"label": "地图"}}},
        }],
        user_text="那先这样，晚安",
        session_id="session-1",
        client_timezone="Asia/Taipei",
    )

    assert result == {"text": "晚安，明天见。", "hangup": True}
    messages = captured["payload"]["messages"]
    assert messages[0]["content"] == CALL_SYSTEM_PROMPT
    assert messages[-1]["context"]["device"]["currentApp"]["label"] == "地图"
    assert captured["payload"]["reasoning"] == {"effort": "minimal", "exclude": True}


@pytest.mark.asyncio
async def test_call_reply_retries_once_when_model_returns_no_speakable_text():
    gateway = ZetaOpenAIGateway.__new__(ZetaOpenAIGateway)
    gateway.upstream_chat_url = "https://example.test/v1/chat/completions"
    gateway.upstream_api_key = "secret"
    gateway.public_model = "zeta"
    gateway.call_max_tokens = 600
    gateway.call_reasoning_effort = "minimal"
    gateway.recall_max_results = 5
    gateway.keyword_limit = 2
    gateway.semantic_limit = 3
    gateway.hidden_memory_enabled = False
    gateway.solo = _FakeSolo()
    gateway.memory_gateway = _FakeMemory()
    gateway._log_recall = lambda *args, **kwargs: None
    gateway._remember_recall_debug = lambda *args, **kwargs: None
    gateway._build_gateway_system_text = lambda recalled: "gateway rules"
    gateway._read_system_prompt = lambda: "persona"
    gateway._prepare_forward_payload = lambda payload, *args, **kwargs: payload
    gateway._save_turn = _fake_save_turn
    gateway._write_zeta_memory_requests = _fake_write_memories
    gateway._should_run_reflection = lambda count: False
    payloads = []

    async def forward(payload):
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "我在，刚才走神了一下。"}, "finish_reason": "stop"}]
        })

    gateway._forward_upstream = forward

    result = await gateway.generate_call_reply(
        context_messages=[],
        user_text="你听得到吗？",
        session_id="session-retry",
        client_timezone="Asia/Taipei",
    )

    assert result == {"text": "我在，刚才走神了一下。", "hangup": False}
    assert len(payloads) == 2
    assert payloads[1]["messages"][-2] == {"role": "system", "content": CALL_EMPTY_RETRY_PROMPT}
    assert payloads[1]["messages"][-1]["role"] == "user"
    assert payloads[1]["reasoning"] == {"effort": "minimal", "exclude": True}


class _FakeSolo:
    timezone_name = "Asia/Taipei"

    async def note_user_message(self, **kwargs):
        return None


class _FakeMemory:
    async def recall(self, payload):
        return {"memories": [], "injection_text": ""}


async def _fake_save_turn(*args, **kwargs):
    return ["raw:1"]


async def _fake_write_memories(**kwargs):
    return 0
