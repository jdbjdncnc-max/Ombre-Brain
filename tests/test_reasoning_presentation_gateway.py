import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from starlette.requests import Request

from zeta_openai_gateway import ZetaOpenAIGateway


def _request(payload, *, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [
        (str(name).lower().encode("latin-1"), str(value).encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/reasoning-presentation",
        "raw_path": b"/api/reasoning-presentation",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope, receive)


def _gateway(*, gateway_token="", stored_system_prompt=""):
    gateway = object.__new__(ZetaOpenAIGateway)
    gateway.gateway_token = gateway_token
    gateway.upstream_chat_url = "https://dialog.example/v1/chat/completions"
    gateway.upstream_api_key = "dialog-secret"
    gateway.upstream_model = "real-dialog-model"
    gateway.public_model = "zeta-gateway"
    gateway.reasoning_config = {}
    gateway.reasoning_force = False
    gateway.openrouter_site_url = ""
    gateway.openrouter_app_name = ""
    gateway.system_prompt_store = SimpleNamespace(read=lambda: stored_system_prompt)
    upstream_response = httpx.Response(
        200,
        json={
            "model": "real-dialog-model",
            "choices": [{"message": {"content": "你不必担心啦！我一直都在哦。"}}],
        },
        request=httpx.Request("POST", "https://dialog.example/v1/chat/completions"),
    )
    gateway.http = SimpleNamespace(post=AsyncMock(return_value=upstream_response))
    return gateway


class ReasoningPresentationGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_dialog_model_with_gateway_system_prompt_and_context(self):
        gateway = _gateway(stored_system_prompt="这是网关保存的完整对话系统提示词。")
        request = _request(
            {
                "model": "must-not-override-dialog-model",
                "prompt": "把思考写成我直接对你说的话。",
                "conversation_summary": "此前的累计摘要",
                "messages": [
                    {"role": "user", "content": "我有点担心。"},
                    {"role": "assistant", "content": "最终回复"},
                ],
                "source_reasoning": "I should tell her not to worry.",
            }
        )

        response = await gateway.reasoning_presentation(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.body),
            {"reasoning": "你不必担心啦！我一直都在哦。", "model": "real-dialog-model"},
        )
        call = gateway.http.post.await_args
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer dialog-secret")
        upstream_payload = call.kwargs["json"]
        self.assertEqual(upstream_payload["model"], "real-dialog-model")
        self.assertEqual(
            upstream_payload["messages"][:2],
            [
                {"role": "system", "content": "这是网关保存的完整对话系统提示词。"},
                {"role": "system", "content": "把思考写成我直接对你说的话。"},
            ],
        )
        self.assertEqual(upstream_payload["reasoning"], {"exclude": True})
        presentation_input = json.loads(
            upstream_payload["messages"][2]["content"].split("\n\n", 1)[1]
        )
        self.assertEqual(
            presentation_input,
            {
                "conversation_summary": "此前的累计摘要",
                "related_conversation": [
                    {"role": "user", "content": "我有点担心。"},
                    {"role": "assistant", "content": "最终回复"},
                ],
                "source_reasoning": "I should tell her not to worry.",
            },
        )

    async def test_requires_system_prompt_when_gateway_and_request_are_empty(self):
        gateway = _gateway()
        request = _request(
            {
                "system_prompt": "",
                "prompt": "覆写",
                "source_reasoning": "reasoning",
            }
        )

        response = await gateway.reasoning_presentation(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.body)["error"]["message"],
            "Full conversation system prompt is required",
        )
        gateway.http.post.assert_not_awaited()

    async def test_accepts_request_prompt_as_legacy_fallback(self):
        gateway = _gateway()
        request = _request(
            {
                "system_prompt": "旧网页仍然发送的完整提示词",
                "prompt": "覆写",
                "source_reasoning": "reasoning",
            }
        )

        response = await gateway.reasoning_presentation(request)

        self.assertEqual(response.status_code, 200)
        upstream_payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(
            upstream_payload["messages"][0],
            {"role": "system", "content": "旧网页仍然发送的完整提示词"},
        )

    async def test_honors_gateway_authentication(self):
        gateway = _gateway(gateway_token="gateway-secret")
        request = _request(
            {
                "system_prompt": "完整提示词",
                "prompt": "覆写",
                "source_reasoning": "reasoning",
            },
            headers={"authorization": "Bearer wrong-secret"},
        )

        response = await gateway.reasoning_presentation(request)

        self.assertEqual(response.status_code, 401)
        gateway.http.post.assert_not_awaited()
