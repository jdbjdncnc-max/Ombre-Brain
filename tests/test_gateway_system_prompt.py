import hashlib
import tempfile
import unittest
from pathlib import Path


from gateway_system_prompt import GatewaySystemPromptStore, inject_gateway_messages
from zeta_openai_gateway import ZetaOpenAIGateway


def prompt_gateway(base_dir: Path):
    return GatewaySystemPromptStore(base_dir)


class GatewaySystemPromptTests(unittest.TestCase):
    def test_system_prompt_is_saved_without_exposing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = prompt_gateway(Path(directory))
            status = gateway.write("# Canonical prompt\nKeep this private.", "../prompt.md")

            self.assertIs(status["configured"], True)
            self.assertEqual(status["filename"], "prompt.md")
            self.assertNotIn("content", status)
            self.assertEqual(
                status["sha256"],
                hashlib.sha256(b"# Canonical prompt\nKeep this private.").hexdigest(),
            )
            self.assertEqual(gateway.read(), "# Canonical prompt\nKeep this private.")

    def test_canonical_prompt_precedes_scene_patch_and_dynamic_memory(self):
        payload = {
            "messages": [
                {"role": "system", "content": "Duetto scene patch"},
                {"role": "user", "content": "Play something"},
            ]
        }

        forwarded = inject_gateway_messages(
            payload["messages"],
            "Dynamic memory",
            "Canonical persona",
        )

        self.assertEqual(
            [message["content"] for message in forwarded],
            [
                "Canonical persona",
                "Duetto scene patch",
                "Dynamic memory",
                "Play something",
            ],
        )
        self.assertEqual(payload["messages"][0]["content"], "Duetto scene patch")

    def test_debug_headers_prove_exact_prompt_without_exposing_its_content(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        prompt = "主 Prompt 哨兵"
        payload = {
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "system", "content": "[Ombre 系统层｜内部资料]\n测试"},
                {"role": "user", "content": "你好"},
            ]
        }

        headers = gateway._system_prompt_debug_headers(payload, prompt)

        self.assertEqual(headers["X-Ombre-System-Prompt-Included"], "1")
        self.assertEqual(headers["X-Ombre-System-Layer-Included"], "1")
        self.assertEqual(headers["X-Ombre-System-Message-Count"], "2")
        self.assertEqual(
            headers["X-Ombre-System-Prompt-SHA256"],
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(prompt, headers.values())


if __name__ == "__main__":
    unittest.main()
