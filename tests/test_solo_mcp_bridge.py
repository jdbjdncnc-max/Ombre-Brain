import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult, TextContent, Tool

from solo.mcp_bridge import (
    MCP_CALL_TIMEOUT,
    MCP_JD_SHOPPING_CALL_TIMEOUT,
    MCP_MASK,
    McpConfigurationError,
    McpConnectionError,
    McpPermissionError,
    SoloMcpBridge,
    mcp_operation_timeout,
)


class SoloMcpConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.bridge = SoloMcpBridge(Path(self.temporary.name), enabled=True)

    async def asyncTearDown(self):
        await self.bridge.close_all()
        self.temporary.cleanup()

    async def test_import_preserves_unknown_fields_and_defaults_to_chat_only(self):
        result = await self.bridge.import_servers({
            "mcpServers": {
                "forum-alpha": {
                    "transport": "streamable-http",
                    "url": "https://forum.example/mcp?view=compact",
                    "headers": {"Authorization": "Bearer ${OMBRE_SOLO_MCP_TOKEN_ALPHA}"},
                    "vendorExtension": {"color": "blue"},
                }
            }
        })

        stored = json.loads(self.bridge.servers_path.read_text(encoding="utf-8"))
        server = stored["mcpServers"]["forum-alpha"]
        self.assertEqual(server["autonomy"], "chat_only")
        self.assertEqual(server["vendorExtension"], {"color": "blue"})
        self.assertEqual(result["servers"][0]["endpoint"], "https://forum.example/mcp")
        self.assertEqual(result["servers"][0]["credentialMask"], MCP_MASK)
        self.assertNotIn("Authorization", json.dumps(result, ensure_ascii=False))

    def test_only_jd_shopping_tool_calls_receive_the_long_timeout(self):
        self.assertEqual(
            mcp_operation_timeout("ombre-jd-shopping", "call_tool", {}),
            MCP_JD_SHOPPING_CALL_TIMEOUT,
        )
        self.assertEqual(
            mcp_operation_timeout(
                "shopping-alias",
                "call_tool",
                {"url": "https://example.test/api/jd-shopping/mcp"},
            ),
            MCP_JD_SHOPPING_CALL_TIMEOUT,
        )
        self.assertEqual(mcp_operation_timeout("forum", "call_tool", {}), MCP_CALL_TIMEOUT)
        self.assertEqual(
            mcp_operation_timeout("ombre-jd-shopping", "list_tools", {}),
            MCP_CALL_TIMEOUT,
        )

    async def test_plaintext_credentials_are_rejected(self):
        with self.assertRaisesRegex(McpConfigurationError, "plaintext credentials"):
            await self.bridge.import_servers({
                "mcpServers": {
                    "unsafe": {
                        "transport": "sse",
                        "url": "https://example.com/sse",
                        "headers": {"Authorization": "Bearer real-secret"},
                    }
                }
            })
        self.assertFalse(self.bridge.servers_path.exists())

    async def test_plaintext_credentials_in_url_or_vendor_fields_are_rejected(self):
        for config in (
            {
                "transport": "streamable-http",
                "url": "https://example.com/mcp?api_key=real-secret",
            },
            {
                "transport": "streamable-http",
                "url": "https://example.com/mcp",
                "vendorApiKey": "real-secret",
            },
        ):
            with self.subTest(config=config):
                with self.assertRaisesRegex(McpConfigurationError, "plaintext credentials"):
                    await self.bridge.import_servers({"mcpServers": {"unsafe": config}})

    async def test_secret_is_write_only_and_resolves_inside_connection_config(self):
        await self.bridge.import_servers({
            "mcpServers": {
                "private-forum": {
                    "transport": "streamable-http",
                    "url": "https://forum.example/mcp",
                    "headers": {"Authorization": "Bearer ${secret:private-forum.token}"},
                }
            }
        })
        await self.bridge.set_secret("private-forum", {"key": "token", "value": "super-private"})

        public = self.bridge.public_snapshot()
        resolved = self.bridge._resolved_config("private-forum")

        self.assertNotIn("super-private", json.dumps(public, ensure_ascii=False))
        self.assertEqual(public["servers"][0]["credentialMask"], MCP_MASK)
        self.assertEqual(resolved["headers"]["Authorization"], "Bearer super-private")
        self.assertEqual(json.loads(self.bridge.secrets_path.read_text(encoding="utf-8"))["private-forum"]["token"], "super-private")

    async def test_environment_placeholder_is_resolved_only_for_the_connection(self):
        await self.bridge.import_servers({
            "mcpServers": {
                "env-server": {
                    "transport": "sse",
                    "url": "https://example.com/sse",
                    "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                }
            }
        })
        with patch.dict(os.environ, {"MCP_TEST_TOKEN": "from-env"}):
            resolved = self.bridge._resolved_config("env-server")
        self.assertEqual(resolved["headers"]["Authorization"], "Bearer from-env")

    async def test_secret_cannot_be_borrowed_from_another_server(self):
        await self.bridge.import_servers({
            "mcpServers": {
                "alpha": {
                    "transport": "streamable-http",
                    "url": "https://alpha.example/mcp",
                    "headers": {"Authorization": "Bearer ${secret:beta.token}"},
                },
                "beta": {
                    "transport": "streamable-http",
                    "url": "https://beta.example/mcp",
                },
            }
        })
        await self.bridge.set_secret("beta", {"key": "token", "value": "beta-secret"})
        with self.assertRaisesRegex(McpConfigurationError, "stay inside server alpha"):
            self.bridge._resolved_config("alpha")

    async def test_invalid_json_reports_line_and_column(self):
        with self.assertRaisesRegex(McpConfigurationError, "第 3 行"):
            await self.bridge.import_servers({"config": "{\n  \"mcpServers\": {\n    broken\n  }\n}"})


class SoloMcpCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.bridge = SoloMcpBridge(Path(self.temporary.name), enabled=True)

    async def asyncTearDown(self):
        await self.bridge.close_all()
        self.temporary.cleanup()

    async def _configure(self):
        await self.bridge.import_servers({
            "mcpServers": {
                "peer-place": {
                    "transport": "streamable-http",
                    "url": "https://peer.example/mcp",
                    "autonomy": "full",
                }
            }
        })

    async def _discover_sample_tools(self):
        await self._configure()
        self.bridge._request = AsyncMock(return_value=type("ToolList", (), {"tools": [
            Tool(name="list_threads", description="List forum threads", inputSchema={"type": "object"}),
            Tool(name="post_reply", description="Post a reply to the AI forum", inputSchema={"type": "object"}),
            Tool(name="delete_thread", description="Delete forum thread data", inputSchema={"type": "object"}),
        ]})())
        return await self.bridge.discover("peer-place", force=True)

    async def test_discovery_classifies_read_write_and_hard_blocked_tools(self):
        result = await self._discover_sample_tools()
        tools = {tool["name"]: tool for tool in result["tools"]}

        self.assertIn("forum", result["categories"])
        self.assertEqual(tools["list_threads"]["kind"], "read")
        self.assertTrue(tools["list_threads"]["allowed"])
        self.assertEqual(tools["post_reply"]["kind"], "write")
        self.assertFalse(tools["post_reply"]["allowed"])
        self.assertTrue(tools["delete_thread"]["hardBlocked"])
        self.assertIn("删除数据", tools["delete_thread"]["blockedReason"])

    async def test_policy_rejects_hard_block_and_persists_explicit_write_permission(self):
        await self._discover_sample_tools()
        with self.assertRaisesRegex(McpPermissionError, "delete_thread"):
            await self.bridge.update_policy("peer-place", {
                "autonomy": "full",
                "allowedTools": ["list_threads", "delete_thread"],
            })

        result = await self.bridge.update_policy("peer-place", {
            "autonomy": "full",
            "categories": ["forum", "peer"],
            "allowedTools": ["list_threads", "post_reply"],
        })
        server = result["servers"][0]
        tools = {tool["name"]: tool for tool in server["tools"]}
        self.assertEqual(server["categories"], ["forum", "peer"])
        self.assertTrue(tools["post_reply"]["allowed"])

    async def test_autonomous_catalog_contains_only_explicitly_authorized_safe_tools(self):
        await self._discover_sample_tools()
        await self.bridge.update_policy("peer-place", {
            "autonomy": "allowlist",
            "categories": ["forum", "peer"],
            "allowedTools": ["list_threads", "post_reply"],
        })

        catalog = self.bridge.autonomous_catalog()

        self.assertEqual(catalog[0]["name"], "peer-place")
        self.assertEqual(
            [tool["name"] for tool in catalog[0]["tools"]],
            ["list_threads", "post_reply"],
        )
        serialized = json.dumps(catalog, ensure_ascii=False)
        self.assertNotIn("delete_thread", serialized)
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("Authorization", serialized)

        await self.bridge.update_policy("peer-place", {"autonomy": "chat_only"})
        self.assertEqual(self.bridge.autonomous_catalog(), [])

    async def test_chat_catalog_supports_chat_only_without_exposing_secrets_or_blocked_tools(self):
        await self._discover_sample_tools()
        await self.bridge.update_policy("peer-place", {
            "autonomy": "chat_only",
            "categories": ["forum", "peer"],
            "allowedTools": ["list_threads", "post_reply"],
        })

        catalog = self.bridge.chat_catalog()

        self.assertEqual(catalog[0]["name"], "peer-place")
        self.assertEqual(
            [tool["name"] for tool in catalog[0]["tools"]],
            ["list_threads", "post_reply"],
        )
        serialized = json.dumps(catalog, ensure_ascii=False)
        self.assertNotIn("delete_thread", serialized)
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertEqual(self.bridge.autonomous_catalog(), [])

    async def test_hard_blocked_tool_requires_explicit_user_confirmation(self):
        await self._discover_sample_tools()
        self.bridge._request.reset_mock()
        self.bridge._request.return_value = CallToolResult(
            content=[TextContent(type="text", text="deleted")],
            isError=False,
        )

        with self.assertRaisesRegex(McpPermissionError, "删除数据"):
            await self.bridge.call("peer-place", "delete_thread", autonomous=False)
        self.bridge._request.assert_not_awaited()

        result = await self.bridge.call(
            "peer-place",
            "delete_thread",
            autonomous=False,
            user_confirmed=True,
        )
        self.assertEqual(result["content"][0]["text"], "deleted")

    async def test_call_validates_schema_and_truncates_unknown_arguments(self):
        await self._configure()
        capabilities = {
            "discoveredAt": "2099-01-01T00:00:00Z",
            "categories": ["forum"],
            "categorySource": "auto",
            "tools": [{
                "name": "list_threads",
                "desc": "List threads",
                "kind": "read",
                "allowed": True,
                "risk": "safe",
                "hardBlocked": False,
                "blockedReason": "",
                "inputSchema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                    "required": ["limit"],
                    "additionalProperties": False,
                },
            }],
        }
        self.bridge.capabilities_path.write_text(json.dumps({"peer-place": capabilities}), encoding="utf-8")
        self.bridge._request = AsyncMock(return_value=CallToolResult(
            content=[TextContent(type="text", text="two threads")],
            isError=False,
        ))

        result = await self.bridge.call(
            "peer-place",
            "list_threads",
            {"limit": 2, "ignored": "value"},
            autonomous=True,
        )

        request = self.bridge._request.await_args
        self.assertEqual(request.args[2]["arguments"], {"limit": 2})
        self.assertEqual(result["content"][0]["text"], "two threads")

    async def test_disabled_bridge_keeps_config_but_refuses_connection(self):
        disabled = SoloMcpBridge(Path(self.temporary.name) / "disabled", enabled=False)
        await disabled.import_servers({
            "mcpServers": {"saved": {"transport": "sse", "url": "https://example.com/sse"}}
        })
        with self.assertRaisesRegex(McpConnectionError, "OMBRE_SOLO_MCP_ENABLED"):
            await disabled.discover("saved", force=True)
        self.assertEqual(disabled.public_snapshot()["servers"][0]["name"], "saved")


class SoloMcpRouteTests(unittest.TestCase):
    def test_gateway_exposes_mcp_management_routes(self):
        from zeta_openai_gateway import app

        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/api/solo/mcp/servers", paths)
        self.assertIn("/api/solo/mcp/servers/{name}/test", paths)
        self.assertIn("/api/solo/mcp/servers/{name}/autonomy", paths)
        self.assertIn("/api/solo/mcp/servers/{name}/secret", paths)
        self.assertIn("/api/solo/mcp/status", paths)


if __name__ == "__main__":
    unittest.main()
