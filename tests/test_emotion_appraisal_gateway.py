import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from starlette.requests import Request

from solo.appraisal import APPRAISAL_RESPONSE_FORMAT, APPRAISAL_TASK_PROMPT, CALL_APPRAISAL_TASK_PROMPT
from solo.emotion_model import default_channels
from zeta_openai_gateway import ZetaOpenAIGateway


def _request(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/emotion-appraisal",
        "raw_path": b"/api/emotion-appraisal",
        "query_string": b"",
        "headers": [(b"x-ombre-client-timezone", b"Asia/Taipei")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }, receive)


def _gateway():
    gateway = object.__new__(ZetaOpenAIGateway)
    gateway.gateway_token = ""
    gateway.upstream_chat_url = "https://dialogue.example/v1/chat/completions"
    gateway.upstream_api_key = "dialogue-secret"
    gateway.upstream_model = "openai/gpt-5.1"
    gateway.summary_timeout = 30
    gateway.openrouter_site_url = ""
    gateway.openrouter_app_name = ""
    gateway._read_emotion_prompt = lambda: "SOLITUDE PERSONA"
    gateway._openrouter_session_id = lambda _value: "ombre-emotion-session"
    gateway.solo = SimpleNamespace(
        enabled=True,
        timezone_name="Asia/Taipei",
        appraisal_snapshot=lambda: {
            "channels": default_channels(),
            "dimensions": {},
            "moodLine": "安静待着",
        },
        apply_conversation_appraisal=AsyncMock(
            return_value={"ok": True, "duplicate": False, "applied": {"delight": 4}}
        ),
    )
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps({
            "emotion_changes": [{"emotion": "delight", "delta": 4}],
            "mood_words": ["开心"],
            "events": ["她连续两轮都在认真回应我"],
        }, ensure_ascii=False)}}]},
        request=httpx.Request("POST", "https://dialogue.example/v1/chat/completions"),
    )
    gateway.http = SimpleNamespace(post=AsyncMock(return_value=response))
    return gateway


class EmotionAppraisalGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_appraises_two_completed_turns_before_returning(self):
        gateway = _gateway()
        request = _request({
            "user_reference": "她",
            "conversation_summary": "我和她正在继续刚才的话题。",
            "messages": [
                {
                    "role": "user",
                    "content": "第一轮",
                    "context": {"sentAt": "2026-08-08T01:00:00Z", "timezone": "Asia/Taipei"},
                },
                {"role": "assistant", "content": "第一轮回复"},
                {
                    "role": "user",
                    "content": "第二轮",
                    "context": {"sentAt": "2026-08-08T01:01:00Z", "timezone": "Asia/Taipei"},
                },
                {"role": "assistant", "content": "第二轮回复"},
            ],
        })

        response = await gateway.emotion_appraisal(request)

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["appraisal_id"].startswith("turns_"))
        self.assertEqual(body["applied"], {"delight": 4})
        upstream_payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(upstream_payload["model"], "openai/gpt-5.1")
        self.assertEqual(upstream_payload["messages"][0]["content"], "SOLITUDE PERSONA")
        self.assertEqual(upstream_payload["messages"][1]["content"], APPRAISAL_TASK_PROMPT)
        self.assertEqual(upstream_payload["response_format"], APPRAISAL_RESPONSE_FORMAT)
        self.assertEqual(upstream_payload["max_tokens"], 4096)
        self.assertEqual(upstream_payload["session_id"], "ombre-emotion-session")
        appraisal_input = json.loads(upstream_payload["messages"][2]["content"])
        self.assertEqual(appraisal_input["最近一次累计摘要"], "我和她正在继续刚才的话题。")
        self.assertEqual([item["content"] for item in appraisal_input["最近对话"]], [
            "第一轮", "第一轮回复", "第二轮", "第二轮回复"
        ])
        gateway.solo.apply_conversation_appraisal.assert_awaited_once()

    async def test_call_appraisal_limits_event_and_preserves_voice_call_source(self):
        gateway = _gateway()
        gateway.http.post.return_value = httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "emotion_changes": [],
                "mood_words": ["安心"],
                "events": ["我听见 Sail 叹气了", "这条不应成为第二个事件"],
            }, ensure_ascii=False)}}]},
            request=httpx.Request("POST", "https://dialogue.example/v1/chat/completions"),
        )

        await gateway._run_emotion_appraisal(
            summary="刚结束一次通话。",
            new_messages=[
                {"role": "user", "content": "（叹气）今天有点累。"},
                {"role": "assistant", "content": "我陪你歇一会儿。"},
            ],
            current_state=gateway.solo.appraisal_snapshot(),
            model="openai/gpt-5.1",
            user_reference="Sail",
            appraisal_id="call_test",
            task_prompt=CALL_APPRAISAL_TASK_PROMPT,
            event_source="voice_call",
            cause_key="voice_call_appraisal",
            fallback_event="我和 Sail 通了一次电话",
            event_limit=1,
        )

        request_payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(request_payload["messages"][1]["content"], CALL_APPRAISAL_TASK_PROMPT)
        apply_call = gateway.solo.apply_conversation_appraisal.await_args
        self.assertEqual(apply_call.args[0]["events"], ["我听见 Sail 叹气了"])
        self.assertEqual(apply_call.kwargs["event_source"], "voice_call")
        self.assertEqual(apply_call.kwargs["cause_key"], "voice_call_appraisal")
        self.assertEqual(apply_call.kwargs["fallback_event"], "我和 Sail 通了一次电话")

    async def test_rejects_a_single_user_turn_without_calling_model(self):
        gateway = _gateway()

        response = await gateway.emotion_appraisal(_request({
            "messages": [
                {"role": "user", "content": "只有一轮"},
                {"role": "assistant", "content": "回复"},
            ]
        }))

        self.assertEqual(response.status_code, 400)
        gateway.http.post.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
