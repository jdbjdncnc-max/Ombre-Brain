import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_PROMPT_BYTES = 256 * 1024


def inject_gateway_messages(
    source_messages: list[Any],
    memory_text: str,
    system_prompt: str,
) -> list[Any]:
    messages = deepcopy(source_messages)
    if system_prompt.strip():
        messages.insert(0, {"role": "system", "content": system_prompt})
    if memory_text.strip():
        insert_at = 0
        while (
            insert_at < len(messages)
            and isinstance(messages[insert_at], dict)
            and messages[insert_at].get("role") == "system"
        ):
            insert_at += 1
        messages.insert(insert_at, {"role": "system", "content": memory_text})
    return messages


class GatewaySystemPromptStore:
    def __init__(self, buckets_dir: str | os.PathLike[str]):
        prompt_dir = Path(buckets_dir) / "gateway"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_path = prompt_dir / "system_prompt.md"
        self.meta_path = prompt_dir / "system_prompt.meta.json"

    def read(self) -> str:
        try:
            return self.prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def status(self) -> dict[str, Any]:
        content = self.read()
        metadata: dict[str, Any] = {}
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                metadata = raw
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return {
            "ok": True,
            "configured": bool(content),
            "filename": str(metadata.get("filename") or ("system_prompt.md" if content else "")),
            "characters": len(content),
            "bytes": len(content.encode("utf-8")),
            "updated_at": str(metadata.get("updated_at") or ""),
        }

    def write(self, content: Any, filename: Any) -> dict[str, Any]:
        text = str(content or "").strip()
        safe_filename = Path(str(filename or "system_prompt.md")).name
        if not re.search(r"\.(md|markdown)$", safe_filename, flags=re.IGNORECASE):
            raise ValueError("Please choose a .md or .markdown file")
        if not text:
            raise ValueError("System prompt file is empty")
        if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("System prompt file must not exceed 256 KB")

        updated_at = datetime.now(timezone.utc).isoformat()
        prompt_tmp = self.prompt_path.with_suffix(".md.tmp")
        meta_tmp = self.meta_path.with_suffix(".json.tmp")
        prompt_tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(prompt_tmp, self.prompt_path)
        meta_tmp.write_text(
            json.dumps({"filename": safe_filename, "updated_at": updated_at}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(meta_tmp, self.meta_path)
        return self.status()
