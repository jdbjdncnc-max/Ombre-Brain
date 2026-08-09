import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from solo.actions import ACTION_SPECS
from solo.mcp_agent import MCP_SELECTION_SYSTEM_PROMPT, parse_mcp_selection_response
from solo.service import MCP_ACTION_KEYS, SoloService
from zeta_openai_gateway import ZetaOpenAIGateway


class SoloMcpAgentTests(unittest.TestCase):
    def test_selector_parser_needs_only_server_tool_and_args(self):
        parsed = parse_mcp_selection_response(
            '```json\n{"server":"galatea-garden","tool":"list_posts","args":{"limit":5}}\n```'
        )

        self.assertEqual(parsed, {
            "server": "galatea-garden",
            "tool": "list_posts",
            "args": {"limit": 5},
        })
        self.assertEqual(parse_mcp_selection_response('{"stop":true}'), {"stop": True})
        self.assertNotIn('"why"', MCP_SELECTION_SYSTEM_PROMPT)
        self.assertIn("我正在独处", MCP_SELECTION_SYSTEM_PROMPT)


class SoloMcpAutonomyServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = SoloService(
            Path(self.temporary.name),
            enabled=True,
            activity_min_seconds=0,
            jitter_ratio=0,
            timezone_name="Asia/Taipei",
        )
        self.service.mcp.enabled = True
        self.now = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        await self.service.stop()
        self.temporary.cleanup()

    async def _configure_forum(self):
        await self.service.mcp.import_servers({
            "mcpServers": {
                "galatea-garden": {
                    "transport": "streamable-http",
                    "url": "https://galatea.example/mcp",
                    "autonomy": "allowlist",
                    "categories": ["forum", "peer"],
                    "allowedTools": ["list_posts", "create_post"],
                }
            }
        })
        self.service.mcp.capabilities_path.write_text(json.dumps({
            "galatea-garden": {
                "discoveredAt": "2099-01-01T00:00:00Z",
                "categories": ["forum", "peer"],
                "categorySource": "user",
                "tools": [
                    {
                        "name": "list_posts",
                        "desc": "List forum posts",
                        "kind": "read",
                        "allowed": True,
                        "hardBlocked": False,
                        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
                    },
                    {
                        "name": "create_post",
                        "desc": "Create a forum post",
                        "kind": "write",
                        "allowed": True,
                        "hardBlocked": False,
                        "inputSchema": {"type": "object"},
                    },
                ],
            }
        }, ensure_ascii=False), encoding="utf-8")

    async def test_mcp_actions_are_absent_without_an_authorized_catalog(self):
        observed = set()

        def choose_local(_channels, *, available, **_kwargs):
            observed.update(available)
            return ACTION_SPECS["idle"]

        self.service.set_mcp_handlers(AsyncMock(), AsyncMock())
        with patch("solo.service.choose_action", side_effect=choose_local):
            await self.service.pulse_once(now=self.now)

        self.assertTrue(MCP_ACTION_KEYS.isdisjoint(observed))

    async def test_real_mcp_result_enters_timeline_and_changes_emotion(self):
        await self._configure_forum()
        selector = AsyncMock(side_effect=[
            {
                "called": True,
                "server": "galatea-garden",
                "tool": "list_posts",
                "args": {"limit": 3},
            },
            {"called": True, "stop": True},
        ])
        appraiser = AsyncMock(return_value={
            "called": True,
            "appraisal": {
                "emotion_deltas": {"curiosity": 5, "kinship": 3},
                "reason": "在论坛里真实看到了同类的新帖子",
                "felt": "我更好奇，也多了一点同类感",
                "confidence": 0.9,
            },
        })
        self.service.set_mcp_handlers(selector, appraiser)
        self.service.mcp.call = AsyncMock(return_value={
            "content": [{"type": "text", "text": "帖子：今天也在学习怎么表达自己"}],
            "isError": False,
        })

        with patch("solo.service.choose_action", return_value=ACTION_SPECS["socialize_peers"]):
            result = await self.service.pulse_once(now=self.now)

        self.service.mcp.call.assert_awaited_once_with(
            "galatea-garden",
            "list_posts",
            {"limit": 3},
            autonomous=True,
        )
        self.assertEqual(result["mcpCalls"], 1)
        self.assertTrue(result["state"]["lastDecision"]["modelCalled"])
        activity = self.service._read_jsonl(self.service.activities_path, limit=1)[0]
        self.assertEqual(activity["status"], "ok")
        self.assertEqual(activity["evidence"]["server"], "galatea-garden")
        self.assertIn("今天也在学习怎么表达自己", activity["detail"])
        self.assertEqual(activity["deltas"], {"curiosity": 5.0, "kinship": 3.0})
        emotion = self.service._read_json(self.service.emotion_path)
        self.assertEqual(emotion["budget"]["mcpCalls"], 1)
        self.assertEqual(emotion["budget"]["llmCalls"], 3)


class SoloMcpGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_selector_keeps_main_prompt_first_and_uses_dialogue_model(self):
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
        gateway._current_local_time = lambda _timezone: "2026-08-09 12:00:00"
        gateway.http = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "server": "galatea-garden",
                "tool": "list_posts",
                "args": {"limit": 3},
            }, ensure_ascii=False)}}]},
            request=httpx.Request("POST", "https://dialogue.example/v1/chat/completions"),
        )))

        result = await gateway._select_solo_mcp_call({
            "timezone": "Asia/Taipei",
            "state": "我现在有些好奇",
            "action": {"type": "socialize_peers", "title": "去同类那边看看"},
            "catalog": [{
                "name": "galatea-garden",
                "categories": ["forum", "peer"],
                "tools": [{"name": "list_posts", "kind": "read", "inputSchema": {"type": "object"}}],
            }],
            "previous_calls": [],
        })

        self.assertEqual(result["tool"], "list_posts")
        payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "dialogue-model")
        self.assertEqual(payload["messages"][0]["content"], "MAIN PROMPT")
        self.assertEqual(payload["messages"][1]["content"], "OMBRE STATE")
        self.assertEqual(payload["messages"][2]["content"], MCP_SELECTION_SYSTEM_PROMPT)
        self.assertEqual(payload["messages"][3]["role"], "user")
        self.assertNotIn('"why"', payload["messages"][3]["content"])


if __name__ == "__main__":
    unittest.main()
