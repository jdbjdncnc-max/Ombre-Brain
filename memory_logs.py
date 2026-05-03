import json
import os
import uuid
from datetime import datetime, timezone


class MemoryLogStore:
    def __init__(self, base_dir: str):
        self.path = os.path.join(base_dir, "memory_logs.jsonl")
        os.makedirs(base_dir, exist_ok=True)

    def append(self, *, action: str, memory_id: str, memory_title: str, bucket_id: str | None, old_content: str, new_content: str | None = None) -> dict:
        entry = {
            "log_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "operator": "Sail",
            "action": action,
            "memory_id": memory_id,
            "memory_title": memory_title or memory_id,
            "bucket_id": bucket_id,
            "old_content": old_content or "",
            "new_content": new_content if action == "update" else "",
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def query(self, *, since: str | None = None, memory_id: str | None = None, bucket_id: str | None = None,
              action: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        items = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if since and str(row.get("timestamp", "")) < since:
                    continue
                if memory_id and row.get("memory_id") != memory_id:
                    continue
                if bucket_id and row.get("bucket_id") != bucket_id:
                    continue
                if action and row.get("action") != action:
                    continue
                items.append(row)
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return items[offset: offset + max(1, min(limit, 500))]
