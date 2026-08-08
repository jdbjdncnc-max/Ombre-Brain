import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from starlette.requests import Request

from solo.appraisal import APPRAISAL_SYSTEM_PROMPT
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
    gateway.summary_chat_url = "https://summary.example/v1/chat/completions"
    gateway.summary_api_key = "summary-secret"
    gateway.summary_model = "summary-env-model"
    gateway.summary_timeout = 30
    gateway.openrouter_site_url = ""
    gateway.openrouter_app_name = ""
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
            "emotion_deltas": {"delight": 4},
            "reason": "她连续两轮都在认真回应",
            "felt": "我更开心了一点",
            "confidence": 0.85,
        }, ensure_ascii=False)}}]},
        request=httpx.Request("POST", "https://summary.example/v1/chat/completions"),
    )
    gateway.http = SimpleNamespace(post=AsyncMock(return_value=response))
    return gateway


class EmotionAppraisalGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_appraises_two_completed_turns_before_returning(self):
        gateway = _gateway()
        request = _request({
            "model": "",
            "user_reference": "她",
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
        self.assertEqual(upstream_payload["model"], "summary-env-model")
        self.assertEqual(upstream_payload["messages"][0]["content"], APPRAISAL_SYSTEM_PROMPT)
        appraisal_input = json.loads(upstream_payload["messages"][1]["content"].split("\n\n", 1)[1])
        self.assertEqual(appraisal_input["conversation_summary"], "")
        self.assertEqual([item["content"] for item in appraisal_input["new_messages"]], [
            "第一轮", "第一轮回复", "第二轮", "第二轮回复"
        ])
        gateway.solo.apply_conversation_appraisal.assert_awaited_once()

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
