import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import Request

from solo.actions import ACTION_SPECS, action_scores
from solo.proactive import PROACTIVE_SYSTEM_PROMPT, parse_proactive_response
from solo.service import SoloService
from zeta_openai_gateway import ZetaOpenAIGateway


def _request(method, path, *, payload=None, query="", token="secret"):
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("ascii"),
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }, receive)


class ProactivePromptTests(unittest.TestCase):
    def test_parser_bounds_model_messages(self):
        parsed = parse_proactive_response(
            "```json\n"
            + json.dumps({
                "title": "想找你" * 30,
                "messages": ["第一条", "第二条" * 200, "第三条", "第四条"],
            }, ensure_ascii=False)
            + "\n```"
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["messages"]), 3)
        self.assertLessEqual(len(parsed["title"]), 60)
        self.assertTrue(all(len(item) <= 240 for item in parsed["messages"]))

    def test_emotion_score_can_favor_reaching_out_without_erasing_sulk(self):
        eager = action_scores({"want_to_share": 90, "longing": 85, "delight": 50})
        upset = action_scores({
            "want_to_share": 90,
            "longing": 85,
            "delight": 50,
            "sulk": 90,
            "grievance": 90,
        })

        self.assertGreater(eager["message_user"], eager["idle"])
        self.assertLess(upset["message_user"], eager["message_user"])


class ProactiveServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = SoloService(
            Path(self.temporary.name),
            enabled=True,
            jitter_ratio=0,
            timezone_name="Asia/Taipei",
        )
        self.now = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        await self.service.stop()
        self.temporary.cleanup()

    async def test_model_messages_enter_outbox_and_ack_once(self):
        contexts = []
        dispatcher = AsyncMock(return_value={"configured": True, "sent": 2, "failed": 0})

        async def generate(context):
            contexts.append(context)
            return {"called": True, "title": "Zeta", "messages": ["第一条", "第二条"]}

        self.service.set_proactive_generator(generate)
        self.service.set_proactive_dispatcher(dispatcher)
        with patch("solo.service.choose_action", return_value=ACTION_SPECS["message_user"]):
            result = await self.service.pulse_once(now=self.now)

        self.assertEqual(result["proactiveQueued"], 2)
        self.assertTrue(result["state"]["lastDecision"]["modelCalled"])
        self.assertIn("下方内容来自独处系统", contexts[0]["state"])

        items = await self.service.get_proactive_outbox(limit=10)
        self.assertEqual([item["text"] for item in items], ["第一条", "第二条"])
        self.assertTrue(all(item["timezone"] == "Asia/Taipei" for item in items))
        dispatcher.assert_awaited_once()
        self.assertEqual(
            [item["id"] for item in dispatcher.await_args.args[0]],
            [item["id"] for item in items],
        )
        acked = await self.service.ack_proactive_outbox([items[0]["id"], "unknown"])
        self.assertEqual(acked["acked"], [items[0]["id"]])
        remaining = await self.service.get_proactive_outbox(limit=10)
        self.assertEqual([item["id"] for item in remaining], [items[1]["id"]])
        history = await self.service.get_proactive_messages(limit=10)
        self.assertEqual([item["id"] for item in history], [item["id"] for item in items])
        after_first = await self.service.get_proactive_messages(limit=10, after=items[0]["id"])
        self.assertEqual([item["id"] for item in after_first], [items[1]["id"]])

        emotion = self.service._read_json(self.service.emotion_path)
        self.assertEqual(emotion["budget"]["llmCalls"], 1)
        self.assertEqual(emotion["budget"]["proactive"], 2)

    async def test_daily_cost_budget_removes_model_action_without_limiting_other_actions(self):
        self.service.daily_llm_budget = 1
        self.service.solo_dir.mkdir(parents=True, exist_ok=True)
        emotion = self.service._new_emotion_state(self.now, self.now)
        emotion["budget"]["llmCalls"] = 1
        self.service._write_json(self.service.emotion_path, emotion)
        generator = AsyncMock(return_value={"called": True, "messages": ["不该生成"]})
        self.service.set_proactive_generator(generator)

        def choose_other(_channels, *, available, **_kwargs):
            self.assertNotIn("message_user", available)
            self.assertIn("idle", available)
            return ACTION_SPECS["idle"]

        with patch("solo.service.choose_action", side_effect=choose_other):
            result = await self.service.pulse_once(now=self.now)

        self.assertNotEqual(result["state"]["lastDecision"]["result"], "message_user")
        generator.assert_not_awaited()

    async def test_proactive_call_requires_silence_window_and_counts_once(self):
        self.service.proactive_call_enabled = True
        self.service.solo_dir.mkdir(parents=True, exist_ok=True)
        last_message = self.now - timedelta(hours=6)
        runtime = self.service._new_state(self.now)
        runtime["lastUserMessageAt"] = last_message.isoformat().replace("+00:00", "Z")
        emotion = self.service._new_emotion_state(self.now, last_message)
        self.service._write_json(self.service.state_path, runtime)
        self.service._write_json(self.service.emotion_path, emotion)
        generator = AsyncMock(return_value={"called": True, "invited": True, "pushSent": 1})
        self.service.set_call_invite_generator(generator)

        with patch("solo.service.choose_action", return_value=ACTION_SPECS["call_user"]):
            result = await self.service.pulse_once(now=self.now, force_decision=True)

        self.assertTrue(result["callInvited"])
        generator.assert_awaited_once()
        saved = self.service._read_json(self.service.emotion_path)
        self.assertEqual(saved["budget"]["calls"], 1)


class ProactiveGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_keeps_main_prompt_first_and_uses_dialogue_model(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        gateway.upstream_chat_url = "https://dialogue.example/v1/chat/completions"
        gateway.upstream_api_key = "dialogue-key"
        gateway.upstream_model = "dialogue-model"
        gateway.summary_timeout = 30
        gateway.openrouter_site_url = ""
        gateway.openrouter_app_name = ""
        gateway.solo = SimpleNamespace(timezone_name="Asia/Taipei")
        gateway._read_system_prompt = lambda: "MAIN PROMPT"
        gateway._compose_ombre_system_layer = lambda **_kwargs: "OMBRE STATE"
        gateway._current_local_time = lambda _timezone: "2026-08-08 12:00:00"
        gateway.http = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "title": "Zeta",
                "messages": ["突然有点想找你。"],
            }, ensure_ascii=False)}}]},
            request=httpx.Request("POST", "https://dialogue.example/v1/chat/completions"),
        )))

        result = await gateway._generate_proactive_messages({
            "timezone": "Asia/Taipei",
            "triggered_at": "2026-08-08T04:00:00Z",
            "state": "当前很想念她",
            "activity": {"title": "决定主动给她发消息"},
        })

        self.assertEqual(result["messages"], ["突然有点想找你。"])
        payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "dialogue-model")
        self.assertEqual(payload["messages"][0]["content"], "MAIN PROMPT")
        self.assertEqual(payload["messages"][1]["content"], "OMBRE STATE")
        self.assertEqual(payload["messages"][2]["content"], PROACTIVE_SYSTEM_PROMPT)
        self.assertEqual(payload["messages"][3]["role"], "user")

    async def test_generation_stops_when_main_prompt_is_missing(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        gateway.upstream_model = "dialogue-model"
        gateway.summary_timeout = 30
        gateway.solo = SimpleNamespace(timezone_name="Asia/Taipei")
        gateway._read_system_prompt = lambda: ""
        gateway._compose_ombre_system_layer = lambda **_kwargs: "OMBRE STATE"
        gateway._current_local_time = lambda _timezone: "2026-08-08 12:00:00"
        gateway.http = SimpleNamespace(post=AsyncMock())

        result = await gateway._generate_proactive_messages({"state": "想念", "activity": {}})

        self.assertFalse(result["called"])
        self.assertEqual(result["messages"], [])
        gateway.http.post.assert_not_awaited()

    async def test_outbox_endpoints_require_auth_and_forward_ack(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        gateway.gateway_token = "secret"
        gateway.solo = SimpleNamespace(
            get_proactive_outbox=AsyncMock(return_value=[{"id": "p1", "text": "找你"}]),
            get_proactive_messages=AsyncMock(return_value=[{"id": "p0", "text": "之前找过你"}]),
            ack_proactive_outbox=AsyncMock(return_value={"ok": True, "acked": ["p1"]}),
        )

        unauthorized = await gateway.solo_outbox(
            _request("GET", "/api/solo/outbox", token="")
        )
        response = await gateway.solo_outbox(
            _request("GET", "/api/solo/outbox", query="limit=7")
        )
        history = await gateway.solo_messages(
            _request("GET", "/api/solo/messages", query="limit=9&after=p-old")
        )
        ack = await gateway.solo_outbox_ack(
            _request("POST", "/api/solo/outbox/ack", payload={"ids": ["p1"]})
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(json.loads(response.body)["items"][0]["id"], "p1")
        gateway.solo.get_proactive_outbox.assert_awaited_once_with(limit=7)
        self.assertEqual(json.loads(history.body)["items"][0]["id"], "p0")
        gateway.solo.get_proactive_messages.assert_awaited_once_with(limit=9, after="p-old")
        self.assertEqual(json.loads(ack.body)["acked"], ["p1"])
        gateway.solo.ack_proactive_outbox.assert_awaited_once_with(["p1"])


if __name__ == "__main__":
    unittest.main()
