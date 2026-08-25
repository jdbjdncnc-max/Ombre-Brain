import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from starlette.requests import Request

from gateway_system_prompt import GatewaySystemPromptStore
from solo.actions import ACTION_SPECS, action_scores
from solo.proactive import parse_proactive_response
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


class ProactivePromptSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_endpoint_persists_custom_text_and_returns_default(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = object.__new__(ZetaOpenAIGateway)
            gateway.gateway_token = "secret"
            gateway.proactive_prompt_store = GatewaySystemPromptStore(directory, stem="proactive_prompt")

            initial = await gateway.proactive_prompt(_request("GET", "/api/proactive-prompt"))
            saved = await gateway.proactive_prompt(_request(
                "PUT",
                "/api/proactive-prompt",
                payload={"prompt": "看到我现在的状态后，你想说什么？"},
            ))

            self.assertEqual(json.loads(initial.body)["prompt"], "现在有什么想说的吗？")
            self.assertFalse(json.loads(initial.body)["configured"])
            self.assertEqual(json.loads(saved.body)["prompt"], "看到我现在的状态后，你想说什么？")
            self.assertTrue(json.loads(saved.body)["configured"])
            self.assertEqual(gateway._read_proactive_prompt(), "看到我现在的状态后，你想说什么？")

    async def test_prompt_endpoint_rejects_empty_text(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = object.__new__(ZetaOpenAIGateway)
            gateway.gateway_token = "secret"
            gateway.proactive_prompt_store = GatewaySystemPromptStore(directory, stem="proactive_prompt")

            response = await gateway.proactive_prompt(_request(
                "PUT",
                "/api/proactive-prompt",
                payload={"prompt": "   "},
            ))

            self.assertEqual(response.status_code, 400)


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
            return {
                "called": True,
                "title": "Zeta",
                "messages": ["第一条", "第二条"],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 18,
                    "prompt_tokens_details": {"cached_tokens": 80},
                    "cost": 0.0012,
                },
            }

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
        self.assertTrue(all(item["usage"]["prompt_tokens"] == 120 for item in items))
        self.assertTrue(all(item["usage"]["prompt_tokens_details"]["cached_tokens"] == 80 for item in items))
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

    async def test_ack_retention_follows_recent_message_order(self):
        self.service.solo_dir.mkdir(parents=True, exist_ok=True)
        ids = [f"proactive_b{index:04d}" for index in range(1000)]
        ids[-1] = "proactive_aaaa"
        for index, item_id in enumerate(ids):
            self.service._append_jsonl(self.service.proactive_path, {
                "id": item_id,
                "ts": (self.now + timedelta(seconds=index)).isoformat(),
                "text": item_id,
            })
        for start in range(0, len(ids), 100):
            await self.service.ack_proactive_outbox(ids[start:start + 100])

        newest_id = "proactive_zzzz"
        self.service._append_jsonl(self.service.proactive_path, {
            "id": newest_id,
            "ts": (self.now + timedelta(seconds=1001)).isoformat(),
            "text": newest_id,
        })
        await self.service.ack_proactive_outbox([newest_id])

        self.assertEqual(await self.service.get_proactive_outbox(limit=50), [])

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

    async def test_proactive_messages_are_spread_across_the_day(self):
        self.service.proactive_daily_limit = 6
        self.service.proactive_min_gap_minutes = 120
        self.service.proactive_window_hours = 6
        self.service.proactive_window_limit = 2
        self.service.solo_dir.mkdir(parents=True, exist_ok=True)
        runtime = self.service._new_state(self.now)
        runtime["lastActionAt"] = {"message_user": (self.now - timedelta(minutes=30)).isoformat()}
        emotion = self.service._new_emotion_state(self.now, self.now)
        self.assertFalse(self.service._proactive_message_allowed(runtime, emotion, self.now))

        runtime["lastActionAt"] = {"message_user": (self.now - timedelta(hours=3)).isoformat()}
        self.service._append_jsonl(self.service.proactive_path, {
            "id": "proactive_a",
            "ts": (self.now - timedelta(hours=5)).isoformat(),
            "text": "a",
        })
        self.service._append_jsonl(self.service.proactive_path, {
            "id": "proactive_b",
            "ts": (self.now - timedelta(hours=3)).isoformat(),
            "text": "b",
        })
        self.assertFalse(self.service._proactive_message_allowed(runtime, emotion, self.now))

    async def test_model_context_includes_current_screen_app(self):
        self.service.solo_dir.mkdir(parents=True, exist_ok=True)
        self.service._write_json(self.service.emotion_path, self.service._new_emotion_state(self.now, self.now))
        self.service._write_json(self.service.state_path, self.service._new_state(self.now))
        await self.service.note_device_context({
            "capturedAt": self.now.isoformat(),
            "appUsage": {"currentScreenApp": {"appName": "微信", "observedAt": self.now.isoformat()}},
        })
        self.service._append_jsonl(self.service.proactive_path, {
            "id": "proactive_recent",
            "ts": (self.now - timedelta(hours=1)).isoformat(),
            "text": "刚才已经说过这一句",
        })
        context = self.service.model_context_text(now=self.now)
        self.assertIn("当前屏幕应用：微信", context)
        self.assertIn("最近主动说过（不要重复原话）：刚才已经说过这一句", context)

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
    async def test_proactive_push_reuses_registered_native_fcm_tokens(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        removed = []
        gateway.call_delivery = SimpleNamespace(
            device_tokens=lambda: ["native-token"],
            remove_device_token=removed.append,
        )
        gateway.call_push = SimpleNamespace(send_proactive=lambda tokens, items: {
            "configured": True,
            "sent": 1,
            "failed": 0,
            "invalid_tokens": ["expired-token"],
            "tokens": tokens,
            "items": items,
        })

        result = await gateway._dispatch_proactive_push([{"id": "p1", "text": "找你"}])

        self.assertEqual(result["tokens"], ["native-token"])
        self.assertEqual(result["items"][0]["id"], "p1")
        self.assertEqual(removed, ["expired-token"])

    async def test_generation_keeps_main_prompt_first_and_uses_dialogue_model(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        gateway.upstream_chat_url = "https://dialogue.example/v1/chat/completions"
        gateway.upstream_api_key = "dialogue-key"
        gateway.upstream_model = "dialogue-model"
        gateway.public_model = "zeta-gateway"
        gateway.summary_timeout = 30
        gateway.openrouter_site_url = ""
        gateway.openrouter_app_name = ""
        gateway.default_session_id = "zeta-main"
        gateway.recall_max_results = 5
        gateway.keyword_limit = 4
        gateway.semantic_limit = 1
        gateway.solo = SimpleNamespace(timezone_name="Asia/Taipei")
        gateway._read_system_prompt = lambda: "MAIN PROMPT"
        gateway._read_proactive_prompt = lambda: "看到现在的情况，你想说什么？"
        gateway._load_proactive_conversation_context = lambda: {
            "session_id": "phone-session",
            "temperature": 0.7,
            "summary_context": "之前聊到她明天要考试",
            "messages": [
                {"role": "user", "content": "我有点担心明天的考试"},
                {"role": "assistant", "content": "先陪你把最担心的部分理清。"},
            ],
        }
        gateway._recent_raw_dialogue_context = lambda: {}
        gateway._recall_context_text = lambda _messages: "RECENT CHAT"
        gateway.memory_gateway = SimpleNamespace(recall=AsyncMock(return_value={
            "injection_text": "RELATED MEMORY",
            "memories": [],
        }))
        gateway._hidden_memory_instruction = lambda: "MEMORY RULE"
        gateway._build_injection_text = lambda recalled: recalled["injection_text"]
        gateway._compose_ombre_system_layer = lambda **kwargs: (
            f"OMBRE STATE\n{kwargs['solo_context']}\n{kwargs['memory_context']}"
        )
        gateway._prepare_forward_payload = lambda payload, injected, system, _timezone, **_kwargs: {
            **payload,
            "messages": [
                {"role": "system", "content": system},
                {"role": "system", "content": injected},
                *payload["messages"],
            ],
        }
        gateway._payload_for_upstream = lambda payload: {**payload, "model": gateway.upstream_model}
        gateway._extract_zeta_memory_request = lambda text: (text, [])
        gateway._save_turn = AsyncMock(return_value=[])
        gateway._remember_proactive_conversation_context = Mock()
        gateway.http = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "先别一个人吓自己。\n\n要不要把最担心的题型发给我？"}}],
                "usage": {
                    "prompt_tokens": 321,
                    "completion_tokens": 24,
                    "prompt_tokens_details": {"cached_tokens": 256},
                    "cost": 0.0042,
                },
            },
            request=httpx.Request("POST", "https://dialogue.example/v1/chat/completions"),
        )))

        result = await gateway._generate_proactive_messages({
            "timezone": "Asia/Taipei",
            "triggered_at": "2026-08-08T04:00:00Z",
            "state": "当前很想念她",
            "activity": {"title": "决定主动给她发消息"},
        })

        self.assertEqual(result["messages"], ["先别一个人吓自己。\n\n要不要把最担心的题型发给我？"])
        self.assertEqual(result["usage"]["prompt_tokens"], 321)
        self.assertEqual(result["usage"]["prompt_tokens_details"]["cached_tokens"], 256)
        self.assertEqual(result["usage"]["cost"], 0.0042)
        payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "dialogue-model")
        self.assertEqual(payload["messages"][0]["content"], "MAIN PROMPT")
        self.assertIn("当前很想念她", payload["messages"][1]["content"])
        self.assertEqual(payload["messages"][2]["ombre_context_kind"], "conversation_summary")
        self.assertEqual(payload["messages"][3]["content"], "我有点担心明天的考试")
        self.assertEqual(payload["messages"][4]["content"], "先陪你把最担心的部分理清。")
        self.assertEqual(payload["messages"][5]["content"], "看到现在的情况，你想说什么？")
        self.assertEqual(payload["temperature"], 0.7)
        self.assertNotIn("max_tokens", payload)
        gateway.memory_gateway.recall.assert_awaited_once()
        gateway._save_turn.assert_awaited_once()
        saved_context = gateway._remember_proactive_conversation_context.call_args.kwargs
        self.assertEqual(saved_context["messages"][-2]["content"], "看到现在的情况，你想说什么？")
        self.assertEqual(saved_context["messages"][-1]["content"], "先别一个人吓自己。\n\n要不要把最担心的题型发给我？")

    def test_proactive_message_text_preserves_paragraph_breaks(self):
        self.assertEqual(
            SoloService._message_text("第一段。\r\n\r\n第二段。", 1200),
            "第一段。\n\n第二段。",
        )

    def test_dialogue_context_snapshot_keeps_summary_recent_turns_and_latest_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = object.__new__(ZetaOpenAIGateway)
            gateway.proactive_conversation_context_path = Path(directory) / "proactive-context.json"
            gateway._remember_proactive_conversation_context(
                session_id="phone-session",
                messages=[
                    {"role": "user", "content": "我明天考试", "context": {"sentAt": "2026-08-22T12:00:00Z"}},
                    {"role": "assistant", "content": "我记得。"},
                ],
                summary_context="正在聊明天的考试",
                schedule_context="明天 09:00 考试",
                temperature=0.6,
            )
            gateway._append_proactive_context_assistant("phone-session", "先早点休息。")

            snapshot = gateway._load_proactive_conversation_context()
            self.assertEqual(snapshot["session_id"], "phone-session")
            self.assertEqual(snapshot["summary_context"], "正在聊明天的考试")
            self.assertEqual(snapshot["schedule_context"], "明天 09:00 考试")
            self.assertEqual(snapshot["temperature"], 0.6)
            self.assertEqual(snapshot["messages"][-1], {"role": "assistant", "content": "先早点休息。"})

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
