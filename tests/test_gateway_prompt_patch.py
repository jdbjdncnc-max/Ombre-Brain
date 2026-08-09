import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ombre_internal_tools_patch import apply_ombre_internal_tools_patch


class _Request:
    def __init__(self, recall_mode=""):
        self.headers = {
            "X-Ombre-Session-Id": "test-session",
            "X-Ombre-Client-Timezone": "Asia/Taipei",
        }
        if recall_mode:
            self.headers["X-Ombre-Recall-Mode"] = recall_mode

    async def json(self):
        return {
            "messages": [{"role": "user", "content": "测试消息"}],
            "stream": False,
        }


class _UpstreamResponse:
    status_code = 503


class _Gateway:
    pass


_module = SimpleNamespace(ZetaOpenAIGateway=_Gateway)
apply_ombre_internal_tools_patch(_module)


class GatewayPromptPatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_tools_handler_passes_gateway_prompt_to_final_payload(self):
        gateway = _Gateway()
        gateway.upstream_chat_url = "https://upstream.test/chat/completions"
        gateway.upstream_api_key = "test-key"
        gateway.default_session_id = "default-session"
        gateway.recall_max_results = 5
        gateway.keyword_limit = 4
        gateway.semantic_limit = 1
        gateway.memory_gateway = SimpleNamespace(
            recall=AsyncMock(return_value={"memories": [], "injection_text": ""})
        )
        gateway._authorize = lambda request: None
        gateway._capture_user_turn = AsyncMock(
            return_value=("测试消息", ["convo://test/1"], "Asia/Taipei")
        )
        gateway._recall_context_text = lambda messages: ""
        gateway._memory_debug_headers = lambda recalled: {}
        gateway._log_recall = lambda session_id, recalled: None
        gateway._build_gateway_system_text = lambda recalled: "第二层哨兵"
        gateway._read_system_prompt = lambda: "主 Prompt 哨兵"
        captured = {}

        def prepare(
            payload,
            injected_text,
            system_prompt="",
            client_timezone="UTC",
            *,
            session_id="",
        ):
            captured.update({
                "injected_text": injected_text,
                "system_prompt": system_prompt,
                "client_timezone": client_timezone,
                "session_id": session_id,
            })
            return {"messages": [], "stream": False}

        gateway._prepare_forward_payload = prepare
        gateway._system_prompt_debug_headers = lambda payload, prompt: {
            "X-Ombre-System-Prompt-Included": "1" if prompt == "主 Prompt 哨兵" else "0"
        }
        gateway._forward_upstream = AsyncMock(return_value=_UpstreamResponse())
        gateway._proxy_response = lambda response, extra_headers=None: extra_headers

        response_headers = await gateway.chat_completions(_Request())

        self.assertEqual(captured["injected_text"], "第二层哨兵")
        self.assertEqual(captured["system_prompt"], "主 Prompt 哨兵")
        self.assertEqual(captured["client_timezone"], "Asia/Taipei")
        self.assertEqual(captured["session_id"], "test-session")
        self.assertEqual(response_headers["X-Ombre-System-Prompt-Included"], "1")

    async def test_injected_duetto_recall_skips_second_gateway_recall(self):
        gateway = _Gateway()
        gateway.upstream_chat_url = "https://upstream.test/chat/completions"
        gateway.upstream_api_key = "test-key"
        gateway.default_session_id = "default-session"
        gateway.recall_max_results = 6
        gateway.keyword_limit = 4
        gateway.semantic_limit = 2
        gateway.memory_gateway = SimpleNamespace(
            recall=AsyncMock(return_value={"memories": [{"id": "duplicate"}], "injection_text": "duplicate"})
        )
        gateway._authorize = lambda request: None
        gateway._capture_user_turn = AsyncMock(
            return_value=("测试消息", ["convo://test/1"], "Asia/Taipei")
        )
        gateway._recall_context_text = lambda messages: "should not be used"
        gateway._memory_debug_headers = lambda recalled: {"X-Zeta-Memory-Count": "0"}
        gateway._log_recall = lambda session_id, recalled: None
        gateway._build_gateway_system_text = lambda recalled: "独处状态由网关注入"
        gateway._read_system_prompt = lambda: "主 Prompt 哨兵"
        gateway._prepare_forward_payload = lambda *args, **kwargs: {"messages": [], "stream": False}
        gateway._system_prompt_debug_headers = lambda payload, prompt: {}
        gateway._forward_upstream = AsyncMock(return_value=_UpstreamResponse())
        gateway._proxy_response = lambda response, extra_headers=None: extra_headers

        response_headers = await gateway.chat_completions(_Request("injected"))

        gateway.memory_gateway.recall.assert_not_awaited()
        self.assertEqual(response_headers["X-Zeta-Recall-Mode"], "injected")


if __name__ == "__main__":
    unittest.main()
