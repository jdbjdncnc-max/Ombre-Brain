import gzip
import json
import types
import unittest

import httpx

from zeta_openai_gateway import ZetaOpenAIGateway
from zeta_hidden_memory_patch import apply_hidden_memory_patch
from ombre_internal_tools_patch import apply_ombre_internal_tools_patch


class FakeStreamResponse:
    def __init__(self):
        self.status_code = 200
        self.headers = {
            "content-type": "text/event-stream; charset=utf-8",
            "content-encoding": "gzip",
            "content-length": "999",
            "content-md5": "stale-digest",
            "accept-ranges": "bytes",
            "content-range": "bytes 0-998/999",
            "etag": '"stale-etag"',
            "keep-alive": "timeout=5",
            "x-upstream-request-id": "stream-request-1",
        }

    async def aiter_lines(self):
        yield "data: [DONE]"


class FakeStreamContext:
    def __init__(self, response):
        self.response = response
        self.closed = False

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class FakeHttpClient:
    def __init__(self, context):
        self.context = context

    def stream(self, *args, **kwargs):
        return self.context


class ProxyResponseHeaderTests(unittest.TestCase):
    def setUp(self):
        self.gateway = object.__new__(ZetaOpenAIGateway)
        hidden_gateway_class = type(
            "HiddenPatchedGateway",
            (ZetaOpenAIGateway,),
            {"_zeta_hidden_memory_patch_applied": False},
        )
        hidden_module = types.SimpleNamespace(
            ZetaOpenAIGateway=hidden_gateway_class,
            gateway=None,
        )
        apply_hidden_memory_patch(hidden_module)
        apply_ombre_internal_tools_patch(hidden_module)
        self.hidden_gateway = object.__new__(hidden_gateway_class)
        compressed_body = gzip.compress(json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "原始回答"},
            }],
        }).encode("utf-8"))
        self.upstream = httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": "999",
                "content-md5": "stale-digest",
                "accept-ranges": "bytes",
                "etag": '"stale-etag"',
                "x-upstream-request-id": "request-1",
            },
            content=compressed_body,
        )

    def assert_body_headers_removed(self, response):
        for header in (
            "content-encoding",
            "content-md5",
            "accept-ranges",
            "etag",
        ):
            self.assertNotIn(header, response.headers)
        self.assertEqual(response.headers["x-upstream-request-id"], "request-1")
        self.assertEqual(response.headers["content-type"], "application/json")

    def test_plain_proxy_does_not_forward_stale_compression_headers(self):
        response = self.gateway._proxy_response(self.upstream)

        self.assert_body_headers_removed(response)
        self.assertEqual(json.loads(response.body)["choices"][0]["message"]["content"], "原始回答")

    def test_rewritten_chat_proxy_does_not_forward_stale_compression_headers(self):
        response = self.gateway._proxy_chat_response_with_text(self.upstream, "可见回答")

        self.assert_body_headers_removed(response)
        self.assertEqual(json.loads(response.body)["choices"][0]["message"]["content"], "可见回答")

    def test_zeabur_patch_order_does_not_restore_stale_body_headers(self):
        response = self.hidden_gateway._proxy_chat_response_with_text(self.upstream, "补丁后的可见回答")

        self.assert_body_headers_removed(response)
        self.assertEqual(json.loads(response.body)["choices"][0]["message"]["content"], "补丁后的可见回答")


class HiddenStreamResponseHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_stream_rebuilds_sse_headers_after_httpx_decoding(self):
        hidden_gateway_class = type(
            "HiddenStreamGateway",
            (ZetaOpenAIGateway,),
            {"_zeta_hidden_memory_patch_applied": False},
        )
        hidden_module = types.SimpleNamespace(
            ZetaOpenAIGateway=hidden_gateway_class,
            gateway=None,
        )
        apply_hidden_memory_patch(hidden_module)
        gateway = object.__new__(hidden_gateway_class)
        upstream = FakeStreamResponse()
        context = FakeStreamContext(upstream)
        gateway.http = FakeHttpClient(context)
        gateway.upstream_chat_url = "https://example.invalid/v1/chat/completions"
        gateway.upstream_api_key = "test-key"
        gateway.public_model = "test-model"
        gateway.hidden_memory_enabled = False
        gateway._upstream_headers = lambda _: {}
        gateway._payload_for_upstream = lambda payload: payload

        response = await gateway._stream_upstream(
            {"stream": True},
            session_id="test-session",
            user_text="hello",
            user_raw_refs=[],
            recalled={},
            memory_headers={},
        )
        output = b"".join([chunk async for chunk in response.body_iterator])

        for header in (
            "content-encoding",
            "content-length",
            "content-md5",
            "accept-ranges",
            "content-range",
            "etag",
            "keep-alive",
        ):
            self.assertNotIn(header, response.headers)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["x-upstream-request-id"], "stream-request-1")
        self.assertEqual(output, b"data: [DONE]\n\n")
        self.assertTrue(context.closed)


class BaseStreamResponseHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_base_stream_rebuilds_sse_headers_after_httpx_decoding(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        upstream = FakeStreamResponse()
        context = FakeStreamContext(upstream)
        gateway.http = FakeHttpClient(context)
        gateway.upstream_chat_url = "https://example.invalid/v1/chat/completions"
        gateway.upstream_api_key = "test-key"
        gateway.public_model = "test-model"
        gateway.hidden_memory_enabled = False
        gateway._upstream_headers = lambda _: {}
        gateway._payload_for_upstream = lambda payload: payload
        gateway._should_run_reflection = lambda _: False

        async def save_turn(*args, **kwargs):
            return []

        async def write_memory_requests(*args, **kwargs):
            return 0

        gateway._save_turn = save_turn
        gateway._write_zeta_memory_requests = write_memory_requests

        response = await gateway._stream_upstream(
            {"stream": True},
            session_id="test-session",
            user_text="hello",
            user_raw_refs=[],
            recalled={},
            memory_headers={},
        )
        output = b"".join([chunk async for chunk in response.body_iterator])

        for header in (
            "content-encoding",
            "content-length",
            "content-md5",
            "accept-ranges",
            "content-range",
            "etag",
            "keep-alive",
        ):
            self.assertNotIn(header, response.headers)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["x-upstream-request-id"], "stream-request-1")
        self.assertEqual(output, b"data: [DONE]\n\n")
        self.assertTrue(context.closed)


if __name__ == "__main__":
    unittest.main()
