import gzip
import json
import unittest

import httpx

from zeta_openai_gateway import ZetaOpenAIGateway


class ProxyResponseHeaderTests(unittest.TestCase):
    def setUp(self):
        self.gateway = object.__new__(ZetaOpenAIGateway)
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


if __name__ == "__main__":
    unittest.main()
