import tempfile
import unittest
from pathlib import Path


from gateway_system_prompt import GatewaySystemPromptStore, inject_gateway_messages


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


if __name__ == "__main__":
    unittest.main()
