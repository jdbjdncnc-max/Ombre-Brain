from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


try:
    import httpx  # noqa: F401
    from starlette.responses import StreamingResponse  # noqa: F401
except ModuleNotFoundError:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.RequestError = type("RequestError", (Exception,), {})
    httpx_stub.Response = object
    sys.modules.setdefault("httpx", httpx_stub)

    class Response:
        def __init__(self, content=b"", status_code=200, headers=None, media_type=None):
            self.content = content
            self.status_code = status_code
            self.headers = {str(key).lower(): value for key, value in (headers or {}).items()}
            self.media_type = media_type

    class JSONResponse(Response):
        pass

    class StreamingResponse(Response):
        def __init__(self, body_iterator, status_code=200, headers=None, media_type=None):
            super().__init__(b"", status_code=status_code, headers=headers, media_type=media_type)
            self.body_iterator = body_iterator

    starlette_stub = types.ModuleType("starlette")
    responses_stub = types.ModuleType("starlette.responses")
    responses_stub.Response = Response
    responses_stub.JSONResponse = JSONResponse
    responses_stub.StreamingResponse = StreamingResponse
    starlette_stub.responses = responses_stub
    sys.modules.setdefault("starlette", starlette_stub)
    sys.modules.setdefault("starlette.responses", responses_stub)


PATCH_PATH = Path(__file__).resolve().parents[1] / "ombre_internal_tools_patch.py"
SPEC = importlib.util.spec_from_file_location("ombre_internal_tools_patch_under_test", PATCH_PATH)
assert SPEC and SPEC.loader
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def sse_chunk(delta: dict, finish_reason=None, *, chunk_id: str = "chatcmpl-test") -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}"


class FakeResponse:
    def __init__(self, lines=None, *, body=None, status_code=200, headers=None):
        self.lines = list(lines or [])
        self.body = body
        self.status_code = status_code
        self.headers = headers or {
            "content-type": "text/event-stream",
            "content-encoding": "gzip",
            "content-length": "999",
            "content-md5": "stale-digest",
            "accept-ranges": "bytes",
            "content-range": "bytes 0-998/999",
            "etag": '"stale-etag"',
            "keep-alive": "timeout=5",
        }

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aread(self):
        return json.dumps(self.body or {}, ensure_ascii=False).encode("utf-8")

    def json(self):
        return self.body or {}


class FakeStreamContext:
    def __init__(self, response):
        self.response = response
        self.closed = False

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.contexts = []

    def stream(self, method, url, *, headers, json):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        context = FakeStreamContext(self.responses.pop(0))
        self.contexts.append(context)
        return context


class FakeGateway:
    def _parse_zeta_memory_json(self, raw_json):
        return []

    def _payload_for_upstream(self, payload):
        return payload

    def _upstream_headers(self, api_key):
        return {"authorization": f"Bearer {api_key}"}

    def _upstream_request_error(self, exc):
        raise AssertionError(f"unexpected request error: {exc}")

    def _upstream_status_error(self, status_code, content_type, body):
        raise AssertionError(f"unexpected upstream status: {status_code}")

    def _assistant_text_from_response(self, response):
        choices = response.json().get("choices") or []
        if not choices:
            return ""
        return str((choices[0].get("message") or {}).get("content") or "")


PATCH.apply_ombre_internal_tools_patch(SimpleNamespace(ZetaOpenAIGateway=FakeGateway))


async def consume(response):
    parts = []
    async for chunk in response.body_iterator:
        parts.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(parts)


def event_payloads(stream_text):
    payloads = []
    for block in stream_text.split("\n\n"):
        if not block.startswith("data:"):
            continue
        data = block[5:].strip()
        if data and data != "[DONE]":
            payloads.append(json.loads(data))
    return payloads


def delta_values(stream_text, key):
    values = []
    for payload in event_payloads(stream_text):
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            if key in delta:
                values.append(delta[key])
    return values


class StreamingRegressionTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self, responses):
        gateway = FakeGateway()
        gateway.http = FakeHttp(responses)
        gateway.upstream_chat_url = "https://upstream.test/chat/completions"
        gateway.upstream_api_key = "test-key"
        gateway.public_model = "public-model"
        gateway.hidden_memory_enabled = True
        gateway.saved_turns = []

        async def save_turn(session_id, role, text):
            gateway.saved_turns.append((session_id, role, text))
            return ["raw:test"]

        async def write_entries(**kwargs):
            return 0

        gateway._save_turn = save_turn
        gateway._write_zeta_memory_requests = write_entries
        gateway._augment_memory_headers = lambda headers, entries, written: None
        gateway._should_run_reflection = lambda written: False
        gateway._schedule_reflection = lambda **kwargs: None
        return gateway

    async def call_stream(self, gateway):
        return await gateway._ombre_stream_upstream(
            {"model": "public-model", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            session_id="session-test",
            user_text="hi",
            user_raw_refs=["raw:user"],
            recalled={},
            memory_headers={"X-Zeta-Memory-Count": "0"},
        )

    async def test_preserves_real_stream_and_reasoning(self):
        response = FakeResponse([
            sse_chunk({"role": "assistant"}),
            sse_chunk({"reasoning": "先想"}),
            sse_chunk({"content": "你"}),
            sse_chunk({"content": "好"}),
            sse_chunk({}, "stop"),
            "data: [DONE]",
        ])
        gateway = self.make_gateway([response])

        proxied = await self.call_stream(gateway)
        output = await consume(proxied)

        self.assertTrue(gateway.http.requests[0]["json"]["stream"])
        self.assertEqual(delta_values(output, "reasoning"), ["先想"])
        self.assertEqual(delta_values(output, "content"), ["你", "好"])
        self.assertEqual(output.count("data: [DONE]"), 1)
        for header in (
            "content-encoding",
            "content-length",
            "content-md5",
            "accept-ranges",
            "content-range",
            "etag",
            "keep-alive",
        ):
            self.assertNotIn(header, proxied.headers)
        self.assertTrue(proxied.headers["content-type"].startswith("text/event-stream"))

    async def test_preserves_incremental_native_tool_calls_without_empty_fallback(self):
        first_tool_delta = [{
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "weather", "arguments": "{\"city\":"},
        }]
        second_tool_delta = [{
            "index": 0,
            "function": {"arguments": "\"Shanghai\"}"},
        }]
        response = FakeResponse([
            sse_chunk({"role": "assistant"}),
            sse_chunk({"tool_calls": first_tool_delta}),
            sse_chunk({"tool_calls": second_tool_delta}),
            sse_chunk({}, "tool_calls"),
            "data: [DONE]",
        ])
        gateway = self.make_gateway([response])

        output = await consume(await self.call_stream(gateway))

        self.assertEqual(delta_values(output, "tool_calls"), [first_tool_delta, second_tool_delta])
        self.assertNotIn(PATCH.EMPTY_STREAM_FALLBACK, output)
        self.assertIn('"finish_reason": "tool_calls"', output)

    async def test_executes_hidden_read_tool_then_streams_follow_up(self):
        hidden = (
            '<ombre_tool_request>{"calls":[{"action":"memory.search",'
            '"query":"project","limit":1}]}</ombre_tool_request>'
        )
        first = FakeResponse([
            sse_chunk({"role": "assistant"}),
            sse_chunk({"content": hidden[:17]}),
            sse_chunk({"content": hidden[17:]}),
            sse_chunk({}, "stop"),
            "data: [DONE]",
        ])
        second = FakeResponse([
            sse_chunk({"role": "assistant"}, chunk_id="chatcmpl-follow"),
            sse_chunk({"content": "查到了。"}, chunk_id="chatcmpl-follow"),
            sse_chunk({}, "stop", chunk_id="chatcmpl-follow"),
            "data: [DONE]",
        ])
        gateway = self.make_gateway([first, second])
        calls = []

        async def run_entries(**kwargs):
            calls.append(kwargs["entries"])
            return ([{"action": "memory.search", "ok": True, "count": 1}], 0)

        gateway._ombre_run_tool_entries = run_entries

        output = await consume(await self.call_stream(gateway))

        self.assertEqual(len(gateway.http.requests), 2)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("ombre_tool_request", output)
        self.assertEqual(delta_values(output, "content"), ["查到了。"])
        self.assertEqual(output.count("data: [DONE]"), 1)
        follow_messages = gateway.http.requests[1]["json"]["messages"]
        self.assertIn("<ombre_tool_result>", follow_messages[-1]["content"])

    async def test_replaces_truly_empty_success_with_visible_fallback(self):
        response = FakeResponse([
            sse_chunk({"role": "assistant"}),
            sse_chunk({}, "stop"),
            "data: [DONE]",
        ])
        gateway = self.make_gateway([response])

        output = await consume(await self.call_stream(gateway))

        self.assertIn(PATCH.EMPTY_STREAM_FALLBACK, output)
        self.assertEqual(output.count("data: [DONE]"), 1)

    async def test_json_fallback_keeps_reasoning_and_native_tool_calls(self):
        tool_calls = [{
            "index": 0,
            "id": "call_json",
            "type": "function",
            "function": {"name": "clock", "arguments": "{}"},
        }]
        response = FakeResponse(
            body={
                "id": "chatcmpl-json",
                "model": "test-model",
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning": "需要查时间",
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": "tool_calls",
                }],
            },
            headers={"content-type": "application/json", "content-length": "123"},
        )
        gateway = self.make_gateway([response])

        output = await consume(await self.call_stream(gateway))

        self.assertEqual(delta_values(output, "reasoning"), ["需要查时间"])
        self.assertEqual(delta_values(output, "tool_calls"), [tool_calls])
        self.assertNotIn(PATCH.EMPTY_STREAM_FALLBACK, output)


if __name__ == "__main__":
    unittest.main()
