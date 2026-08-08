import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.requests import Request

from zeta_openai_gateway import ZetaOpenAIGateway


class MessageTimeContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = object.__new__(ZetaOpenAIGateway)

    def test_keeps_message_bodies_clean_and_removes_custom_context_fields(self):
        messages = [
            {"role": "system", "content": "system text"},
            {
                "role": "user",
                "content": "今天好累",
                "context": {
                    "sentAt": "2026-08-06T13:36:00Z",
                    "timezone": "Asia/Taipei",
                },
            },
            {
                "role": "assistant",
                "content": "先休息一下。",
                "createdAt": "2026-08-06T13:37:00Z",
                "timezone": "Asia/Taipei",
            },
        ]

        result = self.gateway._inject_message_time_context(messages, "UTC")

        self.assertEqual(result[0], messages[0])
        self.assertEqual(result[1]["content"], "今天好累")
        self.assertEqual(result[2]["content"], "先休息一下。")
        self.assertNotIn("[Ombre 消息信息]", result[1]["content"])
        self.assertNotIn("context", result[1])
        self.assertNotIn("createdAt", result[2])
        self.assertNotIn("timezone", result[2])
        self.assertEqual(messages[1]["content"], "今天好累")

    def test_invalid_timestamp_keeps_message_body_clean(self):
        messages = [{
            "role": "user",
            "content": "正文",
            "context": {"sentAt": "not-a-date", "timezone": "Bad Zone"},
        }]

        result = self.gateway._inject_message_time_context(messages, "Asia/Taipei")

        self.assertEqual(result, [{"role": "user", "content": "正文"}])

    def test_removes_only_legacy_info_blocks_copied_at_start_of_assistant_message(self):
        messages = [{
            "role": "assistant",
            "content": (
                "[Ombre 消息信息]\n"
                "发送时间：2026-08-08 01:43:??\n"
                "时区：Asia/Taipei\n"
                "[/Ombre 消息信息]\n\n"
                "真正的回复"
            ),
        }]

        result = self.gateway._inject_message_time_context(messages, "Asia/Taipei")

        self.assertEqual(result[0]["content"], "真正的回复")

    def test_prepare_payload_builds_one_timeline_in_the_ombre_system_layer(self):
        payload = {
            "messages": [
                {"role": "system", "content": "外部场景补丁"},
                {
                    "role": "system",
                    "ombre_context_kind": "conversation_summary",
                    "content": "她此前说最近很忙。",
                },
                {
                    "role": "system",
                    "ombre_context_kind": "schedule",
                    "content": "1. 20:00｜todo｜交作业",
                },
                {
                    "role": "user",
                    "content": "今天好累",
                    "context": {
                        "sentAt": "2026-08-06T13:36:00Z",
                        "timezone": "Asia/Taipei",
                    },
                },
                {
                    "role": "assistant",
                    "content": "我还在生气。",
                    "context": {
                        "sentAt": "2026-08-06T13:37:00Z",
                        "timezone": "Asia/Taipei",
                    },
                },
            ]
        }
        layer = self.gateway._compose_ombre_system_layer(memory_context="一条召回记忆")

        forwarded = self.gateway._prepare_forward_payload(
            payload,
            layer,
            "主 Prompt 哨兵",
            "Asia/Taipei",
        )

        messages = forwarded["messages"]
        self.assertEqual([message["role"] for message in messages], [
            "system", "system", "system", "user", "assistant"
        ])
        self.assertEqual(messages[0]["content"], "主 Prompt 哨兵")
        self.assertEqual(messages[1]["content"], "外部场景补丁")
        system_layer = messages[2]["content"]
        self.assertIn("[Ombre 系统层｜内部资料]", system_layer)
        self.assertIn("1. 她｜2026-08-06 21:36:00｜Asia/Taipei", system_layer)
        self.assertIn("2. 我｜2026-08-06 21:37:00｜Asia/Taipei", system_layer)
        self.assertIn("她此前说最近很忙。", system_layer)
        self.assertIn("1. 20:00｜todo｜交作业", system_layer)
        self.assertIn("一条召回记忆", system_layer)
        self.assertNotIn("除非她主动询问时间", system_layer)
        self.assertNotIn("这一层由 Ombre 系统提供", system_layer)
        self.assertNotIn("位于我的主 Prompt 之后", system_layer)
        self.assertEqual(messages[3]["content"], "今天好累")
        self.assertEqual(messages[4]["content"], "我还在生气。")
        self.assertNotIn("[Ombre 消息信息]", messages[3]["content"])

    async def test_raw_turn_keeps_sent_and_received_metadata(self):
        self.gateway.memory_gateway = SimpleNamespace(
            save_raw=AsyncMock(return_value={"raw_refs": ["convo://main/turn_1"]})
        )

        refs = await self.gateway._save_turn(
            "main",
            "user",
            "你好",
            timestamp="2026-08-06T13:36:00Z",
            metadata={"timezone": "Asia/Taipei", "receivedAt": "2026-08-06T13:36:01Z"},
        )

        self.assertEqual(refs, ["convo://main/turn_1"])
        payload = self.gateway.memory_gateway.save_raw.await_args.args[0]
        message = payload["messages"][0]
        self.assertEqual(message["timestamp"], "2026-08-06T13:36:00Z")
        self.assertEqual(message["metadata"]["timezone"], "Asia/Taipei")
        self.assertEqual(message["metadata"]["receivedAt"], "2026-08-06T13:36:01Z")

    async def test_capture_user_turn_updates_solo_and_raw_log_together(self):
        self.gateway.solo = SimpleNamespace(
            timezone_name="UTC",
            note_user_message=AsyncMock(),
        )
        self.gateway._save_turn = AsyncMock(return_value=["convo://main/turn_2"])
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [(b"x-ombre-client-timezone", b"Asia/Taipei")],
        })
        messages = [{
            "role": "user",
            "content": "晚安",
            "context": {
                "sentAt": "2026-08-06T15:00:00Z",
                "timezone": "Asia/Taipei",
            },
        }]

        text, refs, client_timezone = await self.gateway._capture_user_turn(
            request,
            messages,
            "main",
        )

        self.assertEqual(text, "晚安")
        self.assertEqual(refs, ["convo://main/turn_2"])
        self.assertEqual(client_timezone, "Asia/Taipei")
        self.gateway.solo.note_user_message.assert_awaited_once_with(
            sent_at="2026-08-06T15:00:00Z",
            timezone_name="Asia/Taipei",
        )
        saved = self.gateway._save_turn.await_args
        self.assertEqual(saved.args[:3], ("main", "user", "晚安"))
        self.assertEqual(saved.kwargs["timestamp"], "2026-08-06T15:00:00Z")
        self.assertEqual(saved.kwargs["metadata"]["timezone"], "Asia/Taipei")


if __name__ == "__main__":
    unittest.main()
