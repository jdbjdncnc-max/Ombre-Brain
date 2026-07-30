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
        "path": "/api/conversation-summary",
        "raw_path": b"/api/conversation-summary",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope, receive)


def _gateway(response_body=None, *, gateway_token=""):
    gateway = object.__new__(ZetaOpenAIGateway)
    gateway.gateway_token = gateway_token
    gateway.summary_chat_url = "https://summary.example/v1/chat/completions"
    gateway.summary_api_key = "summary-secret"
    gateway.summary_model = "summary-env-model"
    gateway.summary_timeout = 30
    gateway.openrouter_site_url = ""
    gateway.openrouter_app_name = ""
    upstream_response = httpx.Response(
        200,
        json=response_body or {
            "model": "summary-provider-model",
            "choices": [{"message": {"content": "累计摘要内容"}}],
        },
        request=httpx.Request("POST", "https://summary.example/v1/chat/completions"),
    )
    gateway.http = SimpleNamespace(post=AsyncMock(return_value=upstream_response))
    return gateway


class ConversationSummaryGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_frontend_prompt_and_environment_model(self):
        gateway = _gateway()
        request = _request(
            {
                "model": "",
                "prompt": "只按前端提示词总结。",
                "previous_summary": "旧摘要",
                "messages": [
                    {"role": "system", "content": "不应转发"},
                    {"role": "user", "content": "新问题"},
                    {"role": "assistant", "content": "新回答"},
                ],
            }
        )

        response = await gateway.conversation_summary(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.body),
            {"summary": "累计摘要内容", "model": "summary-provider-model"},
        )
        call = gateway.http.post.await_args
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer summary-secret")
        self.assertEqual(call.kwargs["json"]["model"], "summary-env-model")
        self.assertEqual(
            call.kwargs["json"]["messages"][0],
            {"role": "system", "content": "只按前端提示词总结。"},
        )
        summary_input = json.loads(call.kwargs["json"]["messages"][1]["content"].split("\n\n", 1)[1])
        self.assertEqual(
            summary_input,
            {
                "previous_summary": "旧摘要",
                "new_messages": [
                    {"role": "user", "content": "新问题"},
                    {"role": "assistant", "content": "新回答"},
                ],
            },
        )

    async def test_allows_frontend_model_override(self):
        gateway = _gateway()
        request = _request(
            {
                "model": "frontend-summary-model",
                "prompt": "总结",
                "messages": [{"role": "user", "content": "你好"}],
            }
        )

        response = await gateway.conversation_summary(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            gateway.http.post.await_args.kwargs["json"]["model"],
            "frontend-summary-model",
        )

    async def test_rejects_empty_frontend_prompt_without_upstream_call(self):
        gateway = _gateway()
        request = _request(
            {
                "prompt": "",
                "messages": [{"role": "user", "content": "你好"}],
            }
        )

        response = await gateway.conversation_summary(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.body)["error"]["message"],
            "Summary prompt must not be empty",
        )
        gateway.http.post.assert_not_awaited()

    async def test_honors_gateway_authentication(self):
        gateway = _gateway(gateway_token="gateway-secret")
        request = _request(
            {
                "prompt": "总结",
                "messages": [{"role": "user", "content": "你好"}],
            },
            headers={"authorization": "Bearer wrong-secret"},
        )

        response = await gateway.conversation_summary(request)

        self.assertEqual(response.status_code, 401)
        gateway.http.post.assert_not_awaited()
