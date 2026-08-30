import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    async def test_four_am_compactor_summarizes_previous_day_and_keeps_new_day_messages(self):
        gateway = _gateway({
            "model": "summary-provider-model",
            "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            "choices": [{"message": {"content": (
                "<<<OMBRE_EPISODE_MEMORY>>>\n昨晚聊完了实验和明天的安排。\n"
                "<<<OMBRE_HANDOFF>>>\n早上可以问她实验进展。\n"
                "<<<END_OMBRE_EPISODE_MEMORY>>>"
            )}}],
        })
        gateway.solo = SimpleNamespace(timezone_name="Asia/Taipei")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gateway.daily_conversation_memory_path = root / "daily.json"
            gateway.proactive_conversation_context_path = root / "context.json"
            gateway._write_proactive_conversation_context({
                "session_id": "session-1",
                "summary_prompt": "按一天整理经历",
                "summary_context": "昨晚较早的一段经历",
                "daily_dirty": True,
                "messages": [
                    {"role": "user", "content": "晚安", "context": {"sentAt": "2026-08-29T19:58:00Z"}},
                    {"role": "assistant", "content": "晚安，好梦", "context": {"sentAt": "2026-08-29T19:59:00Z"}},
                    {"role": "user", "content": "新一天醒了", "context": {"sentAt": "2026-08-29T20:01:00Z"}},
                ],
            })

            result = await gateway._compact_daily_conversation({
                "triggered_at": "2026-08-29T20:00:05Z",
                "timezone": "Asia/Taipei",
            })

            self.assertTrue(result["completed"])
            saved = gateway._read_daily_conversation_memory()
            self.assertEqual(saved["conversationDay"], "2026-08-29")
            self.assertEqual(saved["summarizedMessageCount"], 2)
            context = gateway._load_proactive_conversation_context()
            self.assertEqual([item["content"] for item in context["messages"]], ["新一天醒了"])
            self.assertFalse(context["daily_dirty"])

    async def test_can_skip_old_summary_coupled_emotion_appraisal(self):
        gateway = _gateway()
        gateway._schedule_emotion_appraisal = Mock(return_value=True)

        response = await gateway.conversation_summary(_request({
            "prompt": "总结",
            "skip_emotion_appraisal": True,
            "messages": [
                {"role": "user", "content": "新问题"},
                {"role": "assistant", "content": "新回答"},
            ],
        }))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.body)["emotion_appraisal_scheduled"])
        gateway._schedule_emotion_appraisal.assert_not_called()

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
        response_body = json.loads(response.body)
        self.assertEqual(response_body["model"], "summary-provider-model")
        self.assertEqual(response_body["summary"], "累计摘要内容")
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
                "mode": "episode",
                "user_reference": "她",
                "previous_summary": "旧摘要",
                "source_episodes": [],
                "new_messages": [
                    {"role": "user", "content": "新问题"},
                    {"role": "assistant", "content": "新回答"},
                ],
            },
        )

    async def test_splits_episode_memory_and_handoff_and_returns_usage(self):
        gateway = _gateway({
            "model": "summary-provider-model",
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
            "choices": [{"message": {"content": (
                "<<<OMBRE_EPISODE_MEMORY>>>\n【这段经历】\n一起聊完了。\n"
                "<<<OMBRE_HANDOFF>>>\n我可以从她最后的问题接下去。\n"
                "<<<END_OMBRE_EPISODE_MEMORY>>>"
            )}}],
        })

        response = await gateway.conversation_summary(_request({
            "mode": "daily",
            "prompt": "整理经历",
            "messages": [],
            "source_episodes": [{
                "memory": "早上的经历",
                "handoff": "停在早餐",
                "conversation_day": "2026-08-28",
            }],
        }))

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["summary"], "【这段经历】\n一起聊完了。")
        self.assertEqual(body["handoff"], "我可以从她最后的问题接下去。")
        self.assertEqual(body["usage"]["total_tokens"], 160)

    async def test_returns_model_summary_without_appending_current_transcript(self):
        gateway = _gateway({
            "choices": [{
                "message": {
                    "content": "【当前话题】\n温暖的概要"
                }
            }]
        })
        request = _request(
            {
                "prompt": "总结",
                "user_reference": "小舟",
                "messages": [
                    {"role": "user", "content": "第一行\n第二行"},
                    {"role": "assistant", "content": "好呀。"},
                ],
            }
        )

        response = await gateway.conversation_summary(request)

        summary = json.loads(response.body)["summary"]
        self.assertEqual(summary, "【当前话题】\n温暖的概要")
        self.assertNotIn("第一行", summary)
        self.assertNotIn("好呀。", summary)

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
