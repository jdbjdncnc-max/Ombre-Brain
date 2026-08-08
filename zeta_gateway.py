import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


MEMORY_MARKER = "ZETA_MEMORY_V1"
GATEWAY_DOMAIN = "zeta_gateway"


class ZetaMemoryGateway:
    def __init__(self, config: dict, bucket_mgr, embedding_engine=None):
        self.config = config
        self.bucket_mgr = bucket_mgr
        self.embedding_engine = embedding_engine
        self.base_dir = Path(config["buckets_dir"]) / "gateway"
        self.raw_dir = self.base_dir / "raw"
        self.memory_index_path = self.base_dir / "memories.jsonl"
        self.public_diary_path = self.base_dir / "public_diary.jsonl"
        self.private_diary_path = self.base_dir / "private_diary.jsonl"
        self.include_legacy = os.environ.get("OMBRE_RECALL_INCLUDE_LEGACY", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.legacy_keyword_limit = self._bounded_int(
            os.environ.get("OMBRE_RECALL_LEGACY_KEYWORD_LIMIT", "2"),
            0,
            8,
        )
        self.natural_limit = self._bounded_int(
            os.environ.get("OMBRE_RECALL_NATURAL_LIMIT", "1"),
            0,
            8,
        )
        self.use_boost_threshold = self._bounded_int(
            os.environ.get("OMBRE_RECALL_USECOUNT_BOOST_THRESHOLD", "3"),
            1,
            50,
        )
        self.use_boost_importance_max = self._bounded_int(
            os.environ.get("OMBRE_RECALL_USECOUNT_IMPORTANCE_MAX", "7"),
            1,
            10,
        )
        self.recall_strategy = os.environ.get("OMBRE_RECALL_STRATEGY", "hybrid").strip().lower() or "hybrid"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def require_auth(self, request):
        token = os.environ.get("OMBRE_GATEWAY_TOKEN", "").strip()
        if not token:
            return None

        bearer = request.headers.get("authorization", "")
        header_token = request.headers.get("x-zeta-gateway-token", "")
        if bearer.lower().startswith("bearer "):
            provided = bearer[7:].strip()
        else:
            provided = header_token.strip()

        if provided == token:
            return None
        from starlette.responses import JSONResponse
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "gateway": "zeta",
            "raw_dir": str(self.raw_dir),
            "memory_index": str(self.memory_index_path),
            "public_diary": str(self.public_diary_path),
            "private_diary": str(self.private_diary_path),
            "auth": "token" if os.environ.get("OMBRE_GATEWAY_TOKEN", "").strip() else "open",
            "embedding": bool(self.embedding_engine and self.embedding_engine.enabled),
            "embedding_status": self.embedding_engine.status() if self.embedding_engine else {"enabled": False},
            "recall_strategy": self.recall_strategy,
            "include_legacy": self.include_legacy,
            "legacy_keyword_limit": self.legacy_keyword_limit,
            "natural_limit": self.natural_limit,
            "use_boost_threshold": self.use_boost_threshold,
            "use_boost_importance_max": self.use_boost_importance_max,
        }

    async def save_raw(self, body: dict[str, Any]) -> dict[str, Any]:
        session_id = self._safe_id(body.get("session_id") or f"session_{uuid.uuid4().hex[:12]}")
        source = str(body.get("source") or "operit").strip() or "operit"
        messages = body.get("messages")
        if not isinstance(messages, list):
            messages = [{
                "speaker": body.get("speaker", "unknown"),
                "content": body.get("content", ""),
                "timestamp": body.get("timestamp"),
            }]

        records = []
        next_turn = self._next_turn_number(session_id)
        for offset, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            content = str(message.get("content") or "")
            if not content.strip():
                continue
            turn_id = self._safe_id(message.get("turn_id") or f"turn_{next_turn + offset:06d}")
            record = {
                "raw_ref": f"convo://{session_id}/{turn_id}",
                "session_id": session_id,
                "turn_id": turn_id,
                "speaker": str(message.get("speaker") or "unknown"),
                "content": content,
                "timestamp": str(message.get("timestamp") or _now_iso()),
                "source": str(message.get("source") or source),
                "metadata": message.get("metadata") if isinstance(message.get("metadata"), dict) else {},
            }
            records.append(record)

        if records:
            self._append_jsonl(self._raw_path(session_id), records)

        return {
            "ok": True,
            "session_id": session_id,
            "stored": len(records),
            "raw_refs": [r["raw_ref"] for r in records],
        }

    async def write_memory(self, body: dict[str, Any]) -> dict[str, Any]:
        entry = self._normalize_memory_entry(body)
        content = self._memory_content(entry)
        bucket_id = await self.bucket_mgr.create(
            content=content,
            tags=entry["tags"],
            importance=entry["importance"],
            domain=self._memory_domains(entry),
            valence=entry.get("valence", 0.5) if entry.get("valence") is not None else 0.5,
            arousal=entry.get("arousal", 0.3) if entry.get("arousal") is not None else 0.3,
            name=entry["summary_text"][:48],
        )

        if self.embedding_engine:
            await self.embedding_engine.generate_and_store(bucket_id, content)

        record = {
            "memory_id": f"mem_{uuid.uuid4().hex[:12]}",
            "bucket_id": bucket_id,
            "created": _now_iso(),
            **entry,
        }
        self._append_jsonl(self.memory_index_path, [record])
        return {"ok": True, "memory_id": record["memory_id"], "bucket_id": bucket_id, "memory": record}

    async def save_diary(self, body: dict[str, Any]) -> dict[str, Any]:
        content = str(body.get("content") or "").strip()
        visibility = self._normalize_diary_visibility(body)
        created = str(body.get("created") or body.get("created_at") or _now_iso()).strip()
        prefix = "diary" if visibility == "public" else "pdiary"
        diary_id = self._safe_id(body.get("id") or body.get("diary_id") or f"{prefix}_{uuid.uuid4().hex[:12]}")
        summary = str(body.get("summary_text") or body.get("summary") or "").strip()
        if not summary:
            summary = self._compact_text(content, 160) if content else ""
        title = str(body.get("title") or "").strip()
        if not content and not summary and not title:
            return {"ok": False, "error": "content, summary_text, or title is required"}
        if not summary:
            summary = title
        title = title or self._compact_text(summary, 48)
        raw_ref = str(body.get("raw_ref") or "").strip() or f"zeta-diary://{visibility}/{diary_id}"
        tags = self._normalize_tags(body.get("tags", []))
        base_tags = ["diary", f"diary:{visibility}"]
        if visibility == "private":
            base_tags.append("zeta_private")
        for tag in base_tags:
            if tag not in tags:
                tags.append(tag)

        record = {
            "diary_id": diary_id,
            "raw_ref": raw_ref,
            "visibility": visibility,
            "session_id": str(body.get("session_id") or "").strip(),
            "created": created,
            "title": title,
            "summary_text": summary,
            "content": content,
            "mood": str(body.get("mood") or "").strip(),
            "tags": tags,
        }
        self._append_jsonl(self._diary_path(visibility), [record])

        if self._truthy(body.get("index_to_memory", True)):
            try:
                importance = self._bounded_int(body.get("importance", 6), 1, 10)
                label = "Public diary" if visibility == "public" else "Private diary"
                await self.write_memory({
                    "summary_text": f"{label}: {summary}",
                    "tags": tags,
                    "importance": importance,
                    "raw_ref": raw_ref,
                    "feel_text": str(body.get("feel_text") or "").strip(),
                    "valence": body.get("valence"),
                    "arousal": body.get("arousal"),
                })
            except Exception:
                pass

        return {
            "ok": True,
            "diary_id": diary_id,
            "raw_ref": raw_ref,
            "created": created,
            "title": title,
            "summary_text": summary,
            "visibility": visibility,
            "content_stored": bool(content),
        }

    async def save_private_diary(self, body: dict[str, Any]) -> dict[str, Any]:
        if not str(body.get("content") or "").strip():
            return {"ok": False, "error": "content is required"}
        body = dict(body)
        body["visibility"] = "private"
        return await self.save_diary(body)

    def list_diaries(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        visibility = str(body.get("visibility") or "all").strip().lower()
        limit = self._bounded_int(body.get("limit", 100), 1, 1000)
        include_content = self._truthy(body.get("include_content", True))

        selected = []
        if visibility in {"public", "all", ""}:
            selected.append("public")
        if visibility in {"private", "all", ""}:
            selected.append("private")

        result = {"public": [], "private": []}
        for item_visibility in selected:
            if item_visibility == "private":
                result[item_visibility] = []
            else:
                result[item_visibility] = self._merged_diaries(item_visibility, limit, include_content)

        counts = {
            "public": self._count_diaries("public"),
            "private": self._count_diaries("private"),
        }
        counts["total"] = counts["public"] + counts["private"]
        return {"ok": True, "counts": counts, **result}

    async def active_recall(self, body: dict[str, Any]) -> dict[str, Any]:
        query = self._clean_recall_text(body.get("query") or body.get("current_text") or "")
        session_id = self._safe_id(body.get("session_id") or "")
        max_turns = self._bounded_int(body.get("max_turns", 8), 1, 16)
        max_memories = self._bounded_int(body.get("max_memories", 6), 1, 12)
        max_diaries = self._bounded_int(body.get("max_diaries", 3), 0, 8)
        label, start, end = self._active_recall_window(query, body)

        raw_turns = self._raw_turns_in_window(session_id, start, end, max_turns)
        memories = await self._memories_in_window(start, end, max_memories)
        private_diaries = self._private_diaries_in_window(start, end, max_diaries)
        injection_text = self._active_recall_text(label, start, end, raw_turns, memories, private_diaries)
        return {
            "ok": True,
            "mode": "active_recall",
            "query": query,
            "label": label,
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "raw_turns": raw_turns,
            "memories": memories,
            "private_diaries": private_diaries,
            "injection_text": injection_text,
        }

    async def recall(self, body: dict[str, Any]) -> dict[str, Any]:
        query = self._build_query(body)
        max_results = self._bounded_int(body.get("max_results", 5), 1, 8)
        keyword_limit = self._bounded_int(body.get("keyword_limit", 4), 0, max_results)
        semantic_limit = self._bounded_int(body.get("semantic_limit", 1), 0, max_results)
        track_usage = self._truthy(body.get("track_usage", False))

        if not query:
            buckets = await self._important_recent(max_results)
        else:
            buckets = await self._recall_buckets(query, keyword_limit, semantic_limit, max_results)

        index = self._load_memory_index()
        memories = [self._format_memory(b, index) for b in buckets]
        memories = [m for m in memories if m]
        if track_usage:
            memories = await self._track_recalled_usage(memories, index)
        return {
            "ok": True,
            "query": query,
            "keyword_query": self._keyword_query(query),
            "keyword_terms": self._keyword_terms(query),
            "count": len(memories),
            "memories": memories,
            "injection_text": self._injection_text(memories),
        }

    async def lookup_raw(self, raw_ref: str) -> dict[str, Any]:
        parsed = self._parse_raw_ref(raw_ref)
        if not parsed:
            return {"ok": False, "error": "Unsupported raw_ref", "raw_ref": raw_ref}
        session_id, turn_id = parsed
        path = self._raw_path(session_id)
        if not path.exists():
            return {"ok": False, "error": "Session not found", "raw_ref": raw_ref}

        records = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("turn_id") == turn_id:
                    records.append(record)
        return {"ok": bool(records), "raw_ref": raw_ref, "records": records}

    def _active_recall_window(self, query: str, body: dict[str, Any]) -> tuple[str, datetime, datetime]:
        explicit_start = str(body.get("start") or "").strip()
        explicit_end = str(body.get("end") or "").strip()
        if explicit_start or explicit_end:
            start = self._parse_time(explicit_start) or (datetime.now() - timedelta(days=3))
            end = self._parse_time(explicit_end) or (datetime.now() + timedelta(hours=1))
            return "explicit range", start, end

        now = datetime.now()
        today = datetime(now.year, now.month, now.day)
        text = str(query or "").lower()
        if any(word in text for word in ("昨晚", "昨天晚上", "昨夜")):
            return "last night", today - timedelta(days=1) + timedelta(hours=18), today + timedelta(hours=6)
        if "前天" in text:
            return "the day before yesterday", today - timedelta(days=2), today - timedelta(days=1)
        if "昨天" in text:
            return "yesterday", today - timedelta(days=1), today
        if any(word in text for word in ("今晚", "今天晚上")):
            return "tonight", today + timedelta(hours=18), now + timedelta(hours=2)
        if "今天" in text:
            return "today", today, now + timedelta(hours=1)
        if any(word in text for word in ("刚刚", "刚才", "前面")):
            return "recent hours", now - timedelta(hours=4), now + timedelta(hours=1)
        if any(word in text for word in ("最近", "这几天", "这两天")):
            return "recent days", now - timedelta(days=3), now + timedelta(hours=1)
        if "上周" in text:
            return "last week", now - timedelta(days=10), now + timedelta(hours=1)
        return "recent week", now - timedelta(days=7), now + timedelta(hours=1)

    def _raw_turns_in_window(
        self,
        session_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        paths = []
        if session_id:
            path = self._raw_path(session_id)
            if path.exists():
                paths = [path]
        else:
            paths = sorted(self.raw_dir.glob("*.jsonl")) if self.raw_dir.exists() else []

        turns: list[dict[str, Any]] = []
        for path in paths:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = self._parse_time(record.get("timestamp"))
                    if not timestamp or timestamp < start or timestamp > end:
                        continue
                    turns.append({
                        "raw_ref": record.get("raw_ref", ""),
                        "timestamp": record.get("timestamp", ""),
                        "speaker": record.get("speaker", ""),
                        "content": self._compact_text(record.get("content", ""), 520),
                    })
        turns.sort(key=lambda item: str(item.get("timestamp") or ""))
        return turns[-limit:]

    async def _memories_in_window(self, start: datetime, end: datetime, limit: int) -> list[dict[str, Any]]:
        index = self._load_memory_index()
        candidates = []
        for bucket in await self.bucket_mgr.list_all(include_archive=False):
            meta = bucket.get("metadata", {})
            record = index.get(bucket["id"], {})
            created = self._parse_time(record.get("created")) or self._parse_time(meta.get("created"))
            if not created or created < start or created > end:
                continue
            bucket = dict(bucket)
            bucket["gateway_source"] = "active_time"
            bucket["gateway_reason"] = "created in active recall window"
            formatted = self._format_memory(bucket, index)
            if formatted:
                candidates.append(formatted)
        candidates.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
        return candidates[:limit]

    def _private_diaries_in_window(self, start: datetime, end: datetime, limit: int) -> list[dict[str, Any]]:
        return self._diaries_in_window("private", start, end, limit, include_content=False)

    def _diaries_in_window(
        self,
        visibility: str,
        start: datetime,
        end: datetime,
        limit: int,
        *,
        include_content: bool,
    ) -> list[dict[str, Any]]:
        path = self._diary_path(visibility)
        if limit <= 0 or not path.exists():
            return []
        diaries = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                created = self._parse_time(record.get("created"))
                if not created or created < start or created > end:
                    continue
                diaries.append({
                    "diary_id": record.get("diary_id", ""),
                    "raw_ref": record.get("raw_ref", ""),
                    "created": record.get("created", ""),
                    "title": record.get("title", ""),
                    "summary_text": record.get("summary_text", ""),
                    "mood": record.get("mood", ""),
                    "tags": record.get("tags", []),
                })
                if include_content:
                    diaries[-1]["content"] = record.get("content", "")
        diaries.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
        return diaries[:limit]

    def _load_diaries(self, visibility: str, limit: int, include_content: bool) -> list[dict[str, Any]]:
        path = self._diary_path(visibility)
        if not path.exists():
            return []
        latest_by_id: dict[str, dict[str, Any]] = {}
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                diary_id = str(record.get("diary_id") or record.get("raw_ref") or uuid.uuid4().hex)
                item = {
                    "diary_id": record.get("diary_id", ""),
                    "raw_ref": record.get("raw_ref", ""),
                    "visibility": record.get("visibility", visibility),
                    "session_id": record.get("session_id", ""),
                    "created": record.get("created", ""),
                    "title": record.get("title", ""),
                    "summary_text": record.get("summary_text", ""),
                    "mood": record.get("mood", ""),
                    "tags": record.get("tags", []),
                }
                if include_content:
                    item["content"] = record.get("content", "")
                latest_by_id[diary_id] = item
        diaries = list(latest_by_id.values())
        diaries.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
        return diaries[:limit]

    def _merged_diaries(self, visibility: str, limit: int, include_content: bool) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        for item in self._indexed_diaries(visibility, include_content):
            by_key[self._diary_entry_key(item)] = item
        for item in self._load_diaries(visibility, max(limit, 1000), include_content):
            key = self._diary_entry_key(item)
            existing = by_key.get(key, {})
            merged = {**existing, **item}
            if include_content and not merged.get("content") and existing.get("content"):
                merged["content"] = existing["content"]
            merged["source"] = "diary_store" if item.get("content") else existing.get("source", "diary_store")
            by_key[key] = merged
        diaries = list(by_key.values())
        diaries.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
        return diaries[:limit]

    def _indexed_diaries(self, visibility: str, include_content: bool) -> list[dict[str, Any]]:
        diaries = []
        for record in self._load_memory_index().values():
            if not self._is_diary_memory(record, visibility):
                continue
            raw_ref = str(record.get("raw_ref") or "").strip()
            summary = str(record.get("summary_text") or "").strip()
            item = {
                "diary_id": self._diary_id_from_ref(raw_ref) or str(record.get("memory_id") or record.get("bucket_id") or ""),
                "raw_ref": raw_ref,
                "visibility": visibility,
                "session_id": "",
                "created": record.get("created", ""),
                "title": self._diary_title_from_summary(summary),
                "summary_text": summary,
                "mood": "",
                "tags": record.get("tags", []),
                "source": "memory_index",
            }
            if include_content:
                item["content"] = summary
            diaries.append(item)
        diaries.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
        return diaries

    def _active_recall_text(
        self,
        label: str,
        start: datetime,
        end: datetime,
        raw_turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        private_diaries: list[dict[str, Any]],
    ) -> str:
        if not raw_turns and not memories and not private_diaries:
            return ""
        parts = [
            "[Zeta active recall]",
            f"time window: {label} ({start.isoformat(timespec='seconds')} to {end.isoformat(timespec='seconds')})",
            "Use this only to answer explicit recall/timeline questions. Do not quote private diary text; only use its title/summary as a private orientation.",
        ]
        if raw_turns:
            parts.append("conversation turns:")
            for turn in raw_turns:
                parts.append(
                    f"- [{turn.get('timestamp', '')}] {turn.get('speaker', '')}: "
                    f"{turn.get('content', '')} ({turn.get('raw_ref', '')})"
                )
        if memories:
            parts.append("memories created in this window:")
            for memory in memories:
                parts.append(
                    f"- [{memory.get('created', '')}] {memory.get('summary_text', '')} "
                    f"tags={', '.join(memory.get('tags') or [])} raw_ref={memory.get('raw_ref', '')}"
                )
        if private_diaries:
            parts.append("private diary indexes in this window:")
            for diary in private_diaries:
                parts.append(
                    f"- [{diary.get('created', '')}] {diary.get('title', '')}: "
                    f"{diary.get('summary_text', '')} raw_ref={diary.get('raw_ref', '')}"
                )
        return "\n".join(parts)

    async def _recall_buckets(self, query: str, keyword_limit: int, semantic_limit: int, max_results: int) -> list[dict]:
        if self.recall_strategy != "slots":
            return await self._hybrid_recall_buckets(query, keyword_limit, semantic_limit, max_results)

        seen = set()
        buckets = []
        keyword_terms = self._keyword_terms(query)
        keyword_query = " ".join(keyword_terms)
        natural_limit = min(self.natural_limit, max_results)
        semantic_target = min(semantic_limit, max(0, max_results - natural_limit))
        keyword_budget = max(0, max_results - semantic_target - natural_limit)

        if self.include_legacy and self.legacy_keyword_limit and keyword_limit and keyword_budget:
            legacy_target = min(self.legacy_keyword_limit, max(1, keyword_budget // 3), keyword_budget)
            legacy_hits = await self._keyword_search(keyword_terms, limit=max(max_results * 12, legacy_target * 8))
            legacy_added = 0
            for bucket in legacy_hits:
                if bucket["id"] in seen or not self._is_legacy_memory(bucket):
                    continue
                bucket["gateway_source"] = "legacy_keyword"
                bucket["gateway_reason"] = "keyword slot: old Ombre bucket"
                buckets.append(bucket)
                seen.add(bucket["id"])
                legacy_added += 1
                if legacy_added >= legacy_target or len(buckets) >= keyword_budget:
                    break

        if keyword_limit and len(buckets) < keyword_budget:
            gateway_target = max(0, keyword_budget - len(buckets))
            keyword_hits = await self._keyword_search(
                keyword_terms,
                limit=max(keyword_limit * 4, gateway_target * 4, keyword_limit),
                domain_filter=[GATEWAY_DOMAIN],
            )
            gateway_added = 0
            for bucket in keyword_hits:
                if self._is_gateway_memory(bucket) and bucket["id"] not in seen:
                    bucket["gateway_source"] = "keyword"
                    bucket["gateway_reason"] = "keyword slot: structured gateway memory"
                    buckets.append(bucket)
                    seen.add(bucket["id"])
                    gateway_added += 1
                if gateway_added >= gateway_target or len(buckets) >= keyword_budget:
                    break

        if semantic_target and self.embedding_engine and self.embedding_engine.enabled:
            semantic_hits = await self.embedding_engine.search_similar(query, top_k=semantic_target * 10 + 10)
            semantic_added = 0
            for bucket_id, semantic_score in semantic_hits:
                if bucket_id in seen:
                    continue
                bucket = await self.bucket_mgr.get(bucket_id)
                if not bucket or not (
                    self._is_gateway_memory(bucket) or (self.include_legacy and self._is_legacy_memory(bucket))
                ):
                    continue
                bucket["score"] = round(float(semantic_score) * 100, 2)
                bucket["gateway_source"] = "semantic" if self._is_gateway_memory(bucket) else "legacy_semantic"
                bucket["gateway_reason"] = "semantic slot"
                buckets.append(bucket)
                seen.add(bucket_id)
                semantic_added += 1
                if semantic_added >= semantic_target or len(buckets) >= max_results:
                    break

        if natural_limit and len(buckets) < max_results:
            natural_hits = await self._natural_float(seen, natural_limit)
            for bucket in natural_hits:
                buckets.append(bucket)
                seen.add(bucket["id"])
                if len(buckets) >= max_results:
                    break

        if len(buckets) < max_results:
            fill_hits = await self._keyword_search(keyword_terms, limit=max_results * 4, domain_filter=[GATEWAY_DOMAIN])
            for bucket in fill_hits:
                if self._is_gateway_memory(bucket) and bucket["id"] not in seen:
                    bucket["gateway_source"] = "fill"
                    bucket["gateway_reason"] = "keyword backfill"
                    buckets.append(bucket)
                    seen.add(bucket["id"])
                if len(buckets) >= max_results:
                    break

        if self.include_legacy and len(buckets) < max_results:
            legacy_hits = await self._keyword_search(keyword_terms, limit=max_results * 8)
            for bucket in legacy_hits:
                if bucket["id"] in seen or not self._is_legacy_memory(bucket):
                    continue
                bucket["gateway_source"] = "legacy_fill"
                bucket["gateway_reason"] = "old Ombre backfill"
                buckets.append(bucket)
                seen.add(bucket["id"])
                if len(buckets) >= max_results:
                    break

        return buckets[:max_results]

    async def _hybrid_recall_buckets(
        self,
        query: str,
        keyword_limit: int,
        semantic_limit: int,
        max_results: int,
    ) -> list[dict]:
        keyword_terms = self._keyword_terms(query)
        candidates: dict[str, dict[str, Any]] = {}

        def allowed(bucket: dict[str, Any]) -> bool:
            return self._is_gateway_memory(bucket) or (self.include_legacy and self._is_legacy_memory(bucket))

        def remember(bucket: dict[str, Any], source: str, reason: str, score: float) -> None:
            if not bucket or not allowed(bucket):
                return
            bucket_id = bucket.get("id")
            if not bucket_id:
                return
            current = candidates.get(bucket_id)
            if current is None:
                copy = dict(bucket)
                copy["_hybrid_scores"] = {}
                copy["_hybrid_sources"] = []
                copy["_hybrid_reasons"] = []
                candidates[bucket_id] = copy
                current = copy
            current["_hybrid_scores"][source] = max(
                float(current["_hybrid_scores"].get(source, 0.0)),
                float(score or 0.0),
            )
            if source not in current["_hybrid_sources"]:
                current["_hybrid_sources"].append(source)
            if reason and reason not in current["_hybrid_reasons"]:
                current["_hybrid_reasons"].append(reason)

        if semantic_limit and self.embedding_engine and self.embedding_engine.enabled and query.strip():
            semantic_top_k = max(max_results * 8, semantic_limit * 10, 20)
            semantic_hits = await self.embedding_engine.search_similar(query, top_k=semantic_top_k)
            for bucket_id, semantic_score in semantic_hits:
                bucket = await self.bucket_mgr.get(bucket_id)
                if not bucket:
                    continue
                source = "semantic" if self._is_gateway_memory(bucket) else "legacy_semantic"
                remember(bucket, source, "semantic similarity", float(semantic_score) * 100.0)

        if keyword_limit and keyword_terms:
            keyword_hits = await self._keyword_search(
                keyword_terms,
                limit=max(max_results * 8, keyword_limit * 8, 20),
            )
            for bucket in keyword_hits:
                source = "keyword" if self._is_gateway_memory(bucket) else "legacy_keyword"
                remember(bucket, source, "keyword/fuzzy match", float(bucket.get("score") or 0.0))

        natural_target = max(self.natural_limit, max_results if not candidates else self.natural_limit)
        if natural_target:
            natural_hits = await self._natural_float(set(), min(max_results * 2, max(natural_target, 1)))
            for bucket in natural_hits:
                natural_score = float(bucket.get("natural_score") or self._natural_score(bucket.get("metadata", {})))
                remember(bucket, "natural", "recent/emotional/important memory", natural_score * 100.0)

        if not candidates and keyword_terms:
            fill_hits = await self._keyword_search(keyword_terms, limit=max_results * 8, domain_filter=[GATEWAY_DOMAIN])
            for bucket in fill_hits:
                remember(bucket, "fill", "gateway keyword fallback", float(bucket.get("score") or 0.0))

        ranked = list(candidates.values())
        ranked.sort(key=self._hybrid_ranking_score, reverse=True)
        for bucket in ranked:
            scores = bucket.pop("_hybrid_scores", {})
            sources = bucket.pop("_hybrid_sources", [])
            reasons = bucket.pop("_hybrid_reasons", [])
            bucket["score"] = round(self._hybrid_ranking_score_from_scores(bucket, scores), 2)
            bucket["gateway_source"] = "+".join(sources) if sources else "hybrid"
            bucket["gateway_reason"] = "; ".join(reasons[:3])
            bucket["gateway_scores"] = {k: round(float(v), 2) for k, v in scores.items()}
        return ranked[:max_results]

    def _hybrid_ranking_score(self, bucket: dict[str, Any]) -> float:
        return self._hybrid_ranking_score_from_scores(bucket, bucket.get("_hybrid_scores", {}))

    def _hybrid_ranking_score_from_scores(self, bucket: dict[str, Any], scores: dict[str, float]) -> float:
        values = [float(v or 0.0) for v in scores.values()]
        if values:
            base = max(values)
            spread = sum(v for v in values if v != base) * 0.22
        else:
            base = 0.0
            spread = 0.0
        semantic_presence = 6.0 if any("semantic" in str(key) for key in scores) else 0.0
        multi_source_bonus = min(8.0, max(0, len(scores) - 1) * 3.0)
        return base + spread + semantic_presence + multi_source_bonus + self._importance_bonus(bucket)

    async def _keyword_search(self, terms: list[str], limit: int, domain_filter: list[str] | None = None) -> list[dict]:
        merged = {}
        for term in terms[:6]:
            # Hybrid recall already performs one semantic search for the full
            # query. Keyword expansion must stay local; otherwise every term
            # triggers another paid embedding request (up to 12 per turn when
            # fallback search runs as well).
            hits = await self.bucket_mgr.search(
                term,
                limit=max(limit, 4),
                domain_filter=domain_filter,
                use_embedding=False,
            )
            for bucket in hits:
                existing = merged.get(bucket["id"])
                if not existing or float(bucket.get("score") or 0) > float(existing.get("score") or 0):
                    merged[bucket["id"]] = bucket
        content_hits = await self._content_search(terms, limit=max(limit, 4), domain_filter=domain_filter)
        for bucket in content_hits:
            existing = merged.get(bucket["id"])
            if not existing or float(bucket.get("score") or 0) > float(existing.get("score") or 0):
                merged[bucket["id"]] = bucket
        ranked = list(merged.values())
        ranked.sort(key=self._ranking_score, reverse=True)
        return ranked[:limit]

    async def _content_search(self, terms: list[str], limit: int, domain_filter: list[str] | None = None) -> list[dict]:
        terms = [str(term).strip() for term in terms if str(term).strip()]
        if not terms:
            return []
        all_buckets = await self.bucket_mgr.list_all(include_archive=False)
        domain_set = {d.lower() for d in domain_filter or []}
        scored = []
        for bucket in all_buckets:
            meta = bucket.get("metadata", {})
            if domain_set and not ({str(d).lower() for d in meta.get("domain", [])} & domain_set):
                continue
            score = self._content_relevance(terms, bucket)
            if score <= 0:
                continue
            bucket["score"] = max(float(bucket.get("score") or 0), score)
            scored.append(bucket)
        scored.sort(key=lambda bucket: float(bucket.get("score") or 0), reverse=True)
        return scored[:limit]

    def _content_relevance(self, terms: list[str], bucket: dict) -> float:
        meta = bucket.get("metadata", {})
        name = str(meta.get("name") or "").lower()
        tags = " ".join(str(t) for t in meta.get("tags", [])).lower()
        domains = " ".join(str(d) for d in meta.get("domain", [])).lower()
        content = str(bucket.get("content") or "").lower()
        record = self._parse_memory_content(bucket.get("content", "")) if MEMORY_MARKER.lower() in content else {}
        summary = str(record.get("summary_text") or "").lower()
        record_tags = " ".join(str(t) for t in record.get("tags", [])).lower()
        feel_text = str(record.get("feel_text") or "").lower()
        score = 0.0
        matched_terms = 0
        for raw_term in terms:
            term = raw_term.lower()
            if not term:
                continue
            term_score = 0.0
            if term in summary:
                term_score += 90.0
            if term in record_tags:
                term_score += 70.0
            if term in name:
                term_score += 45.0
            if term in tags:
                term_score += 38.0
            if term in content:
                term_score += 30.0
            if term in feel_text:
                term_score += 24.0
            if term in name:
                term_score += 15.0
            if term in tags:
                term_score += 12.0
            if term in domains:
                term_score += 4.0
            if len(term) >= 4:
                pieces = [p for p in re.split(r"\s+", term) if len(p) >= 2]
                if pieces and any(piece in summary or piece in record_tags for piece in pieces):
                    term_score += 26.0
                elif pieces and any(piece in content for piece in pieces):
                    term_score += 12.0
            if term_score > 0:
                matched_terms += 1
                score += term_score
        if score <= 0:
            return 0.0
        return min(99.0, score + matched_terms * 8.0)

    def _importance_bonus(self, bucket: dict[str, Any]) -> float:
        meta = bucket.get("metadata", {}) if isinstance(bucket, dict) else {}
        try:
            importance = max(1, min(10, int(meta.get("importance", 5))))
        except (TypeError, ValueError):
            importance = 5
        return importance * 0.75

    def _ranking_score(self, bucket: dict[str, Any]) -> float:
        try:
            base_score = float(bucket.get("score") or 0)
        except (TypeError, ValueError):
            base_score = 0.0
        return base_score + self._importance_bonus(bucket)

    async def _natural_float(self, seen: set[str], limit: int) -> list[dict]:
        all_buckets = await self.bucket_mgr.list_all(include_archive=False)
        candidates = []
        for bucket in all_buckets:
            if bucket["id"] in seen:
                continue
            if not (self._is_gateway_memory(bucket) or (self.include_legacy and self._is_legacy_memory(bucket))):
                continue
            meta = bucket.get("metadata", {})
            bucket["gateway_source"] = "recent_emotional"
            bucket["gateway_reason"] = "natural slot: emotion/time/importance"
            bucket["natural_score"] = self._natural_score(meta)
            candidates.append(bucket)
        candidates.sort(key=lambda b: b.get("natural_score", 0), reverse=True)
        return candidates[:limit]

    async def _important_recent(self, limit: int) -> list[dict]:
        all_buckets = await self.bucket_mgr.list_all(include_archive=False)
        candidates = [
            b for b in all_buckets
            if self._is_gateway_memory(b) or (self.include_legacy and self._is_legacy_memory(b))
        ]
        candidates.sort(
            key=lambda b: (
                int(b.get("metadata", {}).get("importance", 0)),
                str(b.get("metadata", {}).get("last_active", "")),
            ),
            reverse=True,
        )
        return candidates[:limit]

    def _normalize_memory_entry(self, body: dict[str, Any]) -> dict[str, Any]:
        summary_text = str(body.get("summary_text") or "").strip()
        raw_ref = str(body.get("raw_ref") or "").strip()
        if not summary_text:
            raise ValueError("summary_text is required")
        if not raw_ref:
            raise ValueError("raw_ref is required")

        tags = self._normalize_tags(body.get("tags"))
        domains = self._normalize_tags(body.get("domains") or body.get("domain"))
        importance = self._bounded_int(body.get("importance", 5), 1, 10)
        entry = {
            "summary_text": summary_text,
            "tags": tags,
            "domains": domains,
            "importance": importance,
            "raw_ref": raw_ref,
            "useCount": self._bounded_int(body.get("useCount", 0), 0, 1000000),
        }
        last_used_at = str(body.get("lastUsedAt") or "").strip()
        if last_used_at:
            entry["lastUsedAt"] = last_used_at

        feel_text = str(body.get("feel_text") or "").strip()
        if feel_text:
            entry["feel_text"] = feel_text
        for field in ("valence", "arousal"):
            if body.get(field) is not None and body.get(field) != "":
                value = float(body.get(field))
                if not 0 <= value <= 1:
                    raise ValueError(f"{field} must be between 0 and 1")
                entry[field] = value
        return entry

    def _memory_domains(self, entry: dict[str, Any]) -> list[str]:
        domains = [GATEWAY_DOMAIN]
        for domain in self._normalize_tags(entry.get("domains", [])):
            if domain and domain not in domains:
                domains.append(domain)
        return domains

    def _memory_content(self, entry: dict[str, Any]) -> str:
        lines = [
            MEMORY_MARKER,
            f"summary_text: {entry['summary_text']}",
            f"tags: {', '.join(entry['tags'])}",
            f"domains: {', '.join(entry.get('domains') or [])}",
            f"importance: {entry['importance']}",
            f"useCount: {entry.get('useCount', 0)}",
            f"raw_ref: {entry['raw_ref']}",
        ]
        if entry.get("lastUsedAt"):
            lines.append(f"lastUsedAt: {entry['lastUsedAt']}")
        if entry.get("feel_text"):
            lines.append(f"feel_text: {entry['feel_text']}")
        if entry.get("valence") is not None:
            lines.append(f"valence: {entry['valence']}")
        if entry.get("arousal") is not None:
            lines.append(f"arousal: {entry['arousal']}")
        lines.append("")
        lines.append(json.dumps(entry, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def _format_memory(self, bucket: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        record = index.get(bucket["id"], {})
        if not record:
            record = self._parse_memory_content(bucket.get("content", ""))
        if not record:
            return self._format_legacy_memory(bucket)
        result = {
            "bucket_id": bucket["id"],
            "score": bucket.get("score"),
            "source": bucket.get("gateway_source", "search"),
            "reason": bucket.get("gateway_reason", ""),
            "summary_text": record.get("summary_text", ""),
            "tags": record.get("tags", []),
            "domains": record.get("domains", []),
            "importance": record.get("importance"),
            "raw_ref": record.get("raw_ref", ""),
        }
        for field in ("feel_text", "valence", "arousal", "memory_id", "created", "useCount", "lastUsedAt"):
            if record.get(field) is not None:
                result[field] = record[field]
        return result

    def _parse_memory_content(self, content: str) -> dict[str, Any]:
        if MEMORY_MARKER not in content:
            return {}
        marker_pos = content.find("{")
        if marker_pos >= 0:
            try:
                return json.loads(content[marker_pos:])
            except json.JSONDecodeError:
                pass
        record = {}
        for line in content.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "tags":
                record[key] = self._normalize_tags(value)
            elif key in {"domain", "domains"}:
                record["domains"] = self._normalize_tags(value)
            elif key == "importance":
                record[key] = self._bounded_int(value, 1, 10)
            elif key == "useCount":
                record[key] = self._bounded_int(value, 0, 1000000)
            elif key in ("valence", "arousal"):
                record[key] = float(value)
            elif key in ("summary_text", "raw_ref", "feel_text", "lastUsedAt"):
                record[key] = value
        return record

    def _format_legacy_memory(self, bucket: dict[str, Any]) -> dict[str, Any] | None:
        content = str(bucket.get("content") or "").strip()
        meta = bucket.get("metadata", {})
        name = str(meta.get("name") or "").strip()
        compact = self._compact_text(content, 260)
        summary = name or compact
        if name and compact and compact not in name:
            summary = f"{name}: {compact}"
        if not summary:
            return None
        result = {
            "bucket_id": bucket["id"],
            "score": bucket.get("score"),
            "source": bucket.get("gateway_source", "legacy"),
            "reason": bucket.get("gateway_reason", ""),
            "summary_text": summary,
            "tags": self._normalize_tags(meta.get("tags", [])),
            "importance": self._bounded_int(meta.get("importance", 5), 1, 10),
            "raw_ref": f"bucket://{bucket['id']}",
        }
        if meta.get("valence") is not None:
            result["valence"] = meta.get("valence")
        if meta.get("arousal") is not None:
            result["arousal"] = meta.get("arousal")
        if meta.get("created") is not None:
            result["created"] = meta.get("created")
        if meta.get("useCount") is not None:
            result["useCount"] = meta.get("useCount")
        if meta.get("lastUsedAt") is not None:
            result["lastUsedAt"] = meta.get("lastUsedAt")
        if meta.get("domain") is not None:
            result["domains"] = self._normalize_tags(meta.get("domain", []))
        return result

    async def _track_recalled_usage(
        self,
        memories: list[dict[str, Any]],
        index: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        now = _now_iso()
        tracked = []
        for memory in memories:
            updated = dict(memory)
            bucket_id = str(updated.get("bucket_id") or "").strip()
            if not bucket_id:
                tracked.append(updated)
                continue

            try:
                bucket = await self.bucket_mgr.get(bucket_id)
            except Exception:
                bucket = None
            meta = bucket.get("metadata", {}) if bucket else {}
            record = dict(index.get(bucket_id, {}))

            current_count = self._count_value(
                record.get("useCount", updated.get("useCount", meta.get("useCount", meta.get("activation_count", 0))))
            ) + 1
            old_importance = self._bounded_int(
                record.get("importance", updated.get("importance", meta.get("importance", 5))),
                1,
                10,
            )
            new_importance = self._boosted_importance(old_importance, current_count)

            updated["useCount"] = current_count
            updated["lastUsedAt"] = now
            updated["importance"] = new_importance

            if bucket:
                bucket_updates: dict[str, Any] = {
                    "useCount": current_count,
                    "lastUsedAt": now,
                    "activation_count": current_count,
                }
                if new_importance != old_importance:
                    bucket_updates["importance"] = new_importance

                if self._is_gateway_memory(bucket):
                    full_record = self._usage_record(record, updated, bucket_id, now)
                    self._append_jsonl(self.memory_index_path, [full_record])
                    bucket_updates["content"] = self._memory_content(full_record)

                try:
                    await self.bucket_mgr.update(bucket_id, **bucket_updates)
                except Exception:
                    pass

            tracked.append(updated)
        return tracked

    def _boosted_importance(self, importance: int, use_count: int) -> int:
        if use_count < self.use_boost_threshold:
            return importance
        if use_count % self.use_boost_threshold != 0:
            return importance
        if importance >= self.use_boost_importance_max:
            return importance
        return min(self.use_boost_importance_max, importance + 1)

    def _usage_record(
        self,
        record: dict[str, Any],
        memory: dict[str, Any],
        bucket_id: str,
        now: str,
    ) -> dict[str, Any]:
        merged = dict(record)
        merged["bucket_id"] = bucket_id
        for field in (
            "memory_id",
            "created",
            "summary_text",
            "tags",
            "domains",
            "importance",
            "raw_ref",
            "feel_text",
            "valence",
            "arousal",
            "useCount",
            "lastUsedAt",
        ):
            if memory.get(field) is not None:
                merged[field] = memory[field]
        if not merged.get("memory_id"):
            merged["memory_id"] = f"mem_{uuid.uuid4().hex[:12]}"
        if not merged.get("created"):
            merged["created"] = now
        if not isinstance(merged.get("tags"), list):
            merged["tags"] = self._normalize_tags(merged.get("tags", []))
        if not isinstance(merged.get("domains"), list):
            merged["domains"] = self._normalize_tags(merged.get("domains", []))
        return merged

    def _injection_text(self, memories: list[dict[str, Any]]) -> str:
        if not memories:
            return ""
        parts = ["[Zeta memory gateway]"]
        for idx, memory in enumerate(memories, 1):
            parts.append(
                f"{idx}. {memory.get('summary_text', '')}\n"
                f"   tags: {', '.join(memory.get('tags') or [])}\n"
                f"   domains: {', '.join(memory.get('domains') or [])}\n"
                f"   importance: {memory.get('importance')}\n"
                f"   created: {memory.get('created', '')}\n"
                f"   reason: {memory.get('reason') or memory.get('source', '')}\n"
                f"   raw_ref: {memory.get('raw_ref', '')}"
            )
            if memory.get("feel_text"):
                parts.append(f"   Zeta feel: {memory['feel_text']}")
            if memory.get("valence") is not None and memory.get("arousal") is not None:
                parts.append(f"   emotion: V{memory['valence']}/A{memory['arousal']}")
        return "\n".join(parts)

    def _load_memory_index(self) -> dict[str, dict[str, Any]]:
        index = {}
        if not self.memory_index_path.exists():
            return index
        with self.memory_index_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bucket_id = record.get("bucket_id")
                if bucket_id:
                    index[bucket_id] = record
        return index

    def _build_query(self, body: dict[str, Any]) -> str:
        explicit_query = self._clean_recall_text(body.get("query") or "")
        current_text = self._clean_recall_text(body.get("current_text") or body.get("user_message") or "")
        recent_context = self._clean_recall_text(body.get("recent_context") or "")
        if explicit_query:
            text = explicit_query
        else:
            parts = []
            if current_text:
                parts.append(current_text)
            if recent_context:
                context = recent_context
                if current_text:
                    context = context.replace(f"user: {current_text}", "")
                    context = context.replace(current_text, "")
                    context = re.sub(r"\n{2,}", "\n", context).strip()
                if context:
                    parts.append(context)
            text = "\n".join(parts)
        return text[:3000]

    def _keyword_query(self, text: str) -> str:
        return " ".join(self._keyword_terms(text))

    def _keyword_terms(self, text: str, limit: int = 5) -> list[str]:
        text = self._clean_keyword_text(text)
        if not text:
            return []
        terms = []
        for quoted in re.findall(r"[\"'\u201c\u2018\u300c\u300e\u300a](.{2,32}?)[\"'\u201d\u2019\u300d\u300f\u300b]", text):
            self._add_keyword_candidate(terms, quoted, limit)
        text_without_quotes = re.sub(
            r"[\"'\u201c\u2018\u300c\u300e\u300a].{2,32}?[\"'\u201d\u2019\u300d\u300f\u300b]",
            " ",
            text,
        )
        ascii_stop = {
            "filename",
            "file",
            "image",
            "attachment",
            "content",
            "message",
            "user",
            "assistant",
            "system",
            "http",
            "https",
            "true",
            "false",
            "null",
            "none",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "json",
            "keyword",
            "terms",
            "codex",
            "id",
            "operit",
            "activity",
            "package",
            "pkg",
            "bundle",
            "message_insert_extra_bundle",
            "wttr",
            "thundery",
        }
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,30}", text_without_quotes):
            lowered = token.lower()
            if lowered in ascii_stop:
                continue
            self._add_keyword_candidate(terms, token, limit)
        stop_words = [
            "\u4f60\u8fd8\u8bb0\u5f97",
            "\u8fd8\u8bb0\u5f97",
            "\u8bb0\u4e0d\u8bb0\u5f97",
            "\u77e5\u4e0d\u77e5\u9053",
            "\u80fd\u4e0d\u80fd",
            "\u53ef\u4ee5",
            "\u5e2e\u6211",
            "\u6211\u4eec",
            "\u4e4b\u524d",
            "\u4ee5\u524d",
            "\u521a\u521a",
            "\u90a3\u4e2a",
            "\u8fd9\u4e2a",
            "\u4e8b\u60c5",
            "\u5185\u5bb9",
            "\u56de\u5fc6",
            "\u8bb0\u5fc6",
            "\u76f8\u5173",
            "\u4ec0\u4e48",
            "\u600e\u4e48",
            "\u4e3a\u4ec0\u4e48",
            "\u662f\u4e0d\u662f",
            "\u6709\u6ca1\u6709",
            "\u5417",
            "\u5462",
            "\u554a",
            "\u5440",
            "\u7684",
            "\u4e86",
            "\u63d0\u5230",
            "\u804a\u5230",
            "\u8bf4\u5230",
            "\u4e00\u4e0b",
            "\u6211\u521a\u521a",
            "\u6211\u60f3\u804a",
            "\u60f3\u804a",
            "\u4e00\u53e5\u8bdd\u91cc",
            "\u7b49\u7b49\u540d\u8bcd",
            "\u7b49\u7b49",
            "\u540d\u8bcd",
        ]
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,32}", text_without_quotes):
            cleaned = chunk.strip()
            for word in stop_words:
                cleaned = cleaned.replace(word, " ")
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" _-")
            for piece in re.split(r"[\u548c\u4e0e\u3001,，/]+", cleaned):
                piece = piece.strip()
                if 2 <= len(piece) <= 18:
                    self._add_keyword_candidate(terms, piece, limit)
        if terms:
            return terms[:limit]
        fallback = text.strip(" _-")
        return [fallback[:80]] if fallback else []

    @staticmethod
    def _clean_keyword_text(text: Any) -> str:
        cleaned = ZetaMemoryGateway._clean_recall_text(text)
        query_matches = re.findall(
            r"(?im)^\s*(?:query|user_message|message|text)\s*[:=]\s*(.+?)\s*$",
            str(text or ""),
        )
        if query_matches:
            cleaned = query_matches[-1].strip()
        cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", cleaned)
        cleaned = re.sub(r"https?://\S+", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:file_?name|filename|name)\s*=\s*[^,\s;，。]+", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:mime|type|size|path|url)\s*=\s*[^,\s;，。]+", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bfilename\b", " ", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _clean_recall_text(text: Any) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not raw.strip():
            return ""

        metadata_prefixes = (
            "current time",
            "current_time",
            "timestamp",
            "timezone",
            "app uptime",
            "app runtime",
            "application uptime",
            "application runtime",
            "elapsed",
            "duration",
            "当前时间",
            "现在时间",
            "时间戳",
            "时区",
            "应用时长",
            "运行时长",
            "使用时长",
            "会话时长",
            "页面",
            "窗口",
        )
        metadata_markers = [
            "【当前屏幕应用】",
            "【应用使用时长】",
            "统计窗口:",
            "最近使用:",
            "包名:",
            "Activity:",
            "来源: wttr.in",
            "source: wttr.in",
            "message_insert_extra_bundle",
            "keyword=",
            "terms=",
        ]
        metadata_only_markers = (
            "当前屏幕应用",
            "应用使用时长",
            "最近使用",
            "统计窗口",
            "wttr.in",
            "风速",
            "包名",
            "activity",
            "keyword=",
            "terms=",
        )

        cleaned_lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if any(low.startswith(prefix) for prefix in metadata_prefixes):
                continue
            positions = [line.find(marker) for marker in metadata_markers if line.find(marker) >= 0]
            if positions:
                line = line[:min(positions)].strip()
            line = re.sub(r"\bkeyword\s*=\s*[^|\n]*", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\bterms\s*=\s*.*$", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\bmessage_insert_extra_bundle_\d+\b", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\bcom\.[A-Za-z0-9_.-]+\b", " ", line)
            line = re.sub(r"\bActivity\s*:\s*\S+", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\b(?:package|pkg|包名)\s*[:：]\s*\S+", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\b(?:current\s+app|app\s+uptime|recent\s+use)\b.*", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"(?:风速|湿度|来源\s*[:：]\s*wttr\.in|weather|thundery)[^。！？\n]*", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\s+", " ", line).strip()
            low = line.lower()
            if "%" in line and re.fullmatch(r"[\d\s%./:-]+", line):
                continue
            if not line or any(marker in low for marker in metadata_only_markers):
                continue
            cleaned_lines.append(line)

        return re.sub(r"\s+", " ", "\n".join(cleaned_lines)).strip()

    @staticmethod
    def _add_keyword_candidate(terms: list[str], value: str, limit: int) -> None:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" _-:：,，.。;；")
        if not cleaned or cleaned == "=" or len(cleaned) > 32:
            return
        if cleaned.lower() in {term.lower() for term in terms}:
            return
        terms.append(cleaned)
        if len(terms) > limit:
            del terms[limit:]

    def _natural_score(self, meta: dict[str, Any]) -> float:
        importance = self._bounded_int(meta.get("importance", 5), 1, 10) / 10.0
        try:
            arousal = max(0.0, min(1.0, float(meta.get("arousal", 0.3))))
        except (TypeError, ValueError):
            arousal = 0.3
        try:
            activation = max(1.0, float(meta.get("activation_count", 1)))
        except (TypeError, ValueError):
            activation = 1.0
        recency = self._recency_score(meta.get("last_active") or meta.get("created"))
        unresolved = 0.15 if not meta.get("resolved", False) else -0.2
        pinned = 0.5 if meta.get("pinned") or meta.get("protected") else 0.0
        return importance * 0.35 + arousal * 0.2 + min(1.0, activation / 8.0) * 0.15 + recency * 0.2 + unresolved + pinned

    def _is_gateway_memory(self, bucket: dict[str, Any]) -> bool:
        meta = bucket.get("metadata", {})
        domains = {str(d).lower() for d in meta.get("domain", [])}
        return GATEWAY_DOMAIN in domains or MEMORY_MARKER in bucket.get("content", "")

    def _is_legacy_memory(self, bucket: dict[str, Any]) -> bool:
        if self._is_gateway_memory(bucket):
            return False
        meta = bucket.get("metadata", {})
        if str(meta.get("type", "")).lower() == "feel":
            return False
        return bool(str(bucket.get("content") or "").strip())

    def _parse_raw_ref(self, raw_ref: str) -> tuple[str, str] | None:
        match = re.fullmatch(r"convo://([^/]+)/([^/]+)", str(raw_ref).strip())
        if not match:
            return None
        return self._safe_id(match.group(1)), self._safe_id(match.group(2))

    def _parse_time(self, value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed

    def _raw_path(self, session_id: str) -> Path:
        return self.raw_dir / f"{self._safe_id(session_id)}.jsonl"

    def _diary_path(self, visibility: str) -> Path:
        return self.public_diary_path if visibility == "public" else self.private_diary_path

    def _normalize_diary_visibility(self, body: dict[str, Any]) -> str:
        raw = str(
            body.get("visibility")
            or body.get("privacy")
            or body.get("access")
            or body.get("scope")
            or body.get("type")
            or body.get("kind")
            or body.get("source")
            or ""
        ).strip().lower()
        if raw in {"public", "open", "shared", "share", "visible", "public_diary", "diary_public"}:
            return "public"
        if raw in {"private", "secret", "hidden", "personal", "private_diary", "diary_private"}:
            return "private"

        for key in ("public", "is_public", "isPublic", "share_public", "sharePublic", "public_diary", "publicDiary"):
            value = str(body.get(key) or "").strip().lower()
            if self._truthy(body.get(key)) or value in {"public", "open", "shared"}:
                return "public"
        for key in ("private", "is_private", "isPrivate", "private_diary", "privateDiary"):
            value = str(body.get(key) or "").strip().lower()
            if self._truthy(body.get(key)) or value in {"private", "secret", "hidden"}:
                return "private"

        raw_ref = str(body.get("raw_ref") or "").strip().lower()
        if raw_ref.startswith(("diary://public/", "zeta-diary://public/")):
            return "public"
        if raw_ref.startswith(("diary://private/", "zeta-diary://private/")):
            return "private"
        return "private"

    def _count_diaries(self, visibility: str) -> int:
        return len(self._merged_diaries(visibility, 100000, include_content=False))

    def _is_diary_memory(self, record: dict[str, Any], visibility: str) -> bool:
        tags = {str(tag).lower() for tag in self._normalize_tags(record.get("tags", []))}
        raw_ref = str(record.get("raw_ref") or "").strip().lower()
        return (
            f"diary:{visibility}" in tags
            or raw_ref.startswith(f"diary://{visibility}/")
            or raw_ref.startswith(f"zeta-diary://{visibility}/")
        )

    @staticmethod
    def _diary_entry_key(item: dict[str, Any]) -> str:
        raw_ref = str(item.get("raw_ref") or "").strip()
        if raw_ref:
            return raw_ref
        visibility = str(item.get("visibility") or "").strip()
        diary_id = str(item.get("diary_id") or "").strip()
        return f"{visibility}:{diary_id or uuid.uuid4().hex}"

    @staticmethod
    def _diary_id_from_ref(raw_ref: str) -> str:
        text = str(raw_ref or "").strip().rstrip("/")
        if not text or "/" not in text:
            return ""
        return text.rsplit("/", 1)[-1]

    @staticmethod
    def _diary_title_from_summary(summary: str) -> str:
        text = str(summary or "").strip()
        for prefix in ("Public diary:", "Private diary:", "Diary note:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                break
        return text[:48] or "Diary"

    def _next_turn_number(self, session_id: str) -> int:
        path = self._raw_path(session_id)
        if not path.exists():
            return 1
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f) + 1

    def _append_jsonl(self, path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _safe_id(value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
        return text[:120] or uuid.uuid4().hex[:12]

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if isinstance(value, list):
            raw_tags = value
        else:
            raw_tags = str(value or "").split(",")
        tags = []
        for tag in raw_tags:
            tag = str(tag).strip()
            if tag and tag not in tags:
                tags.append(tag)
        return tags

    @staticmethod
    def _bounded_int(value: Any, low: int, high: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = low
        return max(low, min(high, number))

    @staticmethod
    def _count_value(value: Any) -> int:
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _truthy(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _compact_text(text: str, limit: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit].rstrip() + "..."

    @staticmethod
    def _recency_score(value: Any) -> float:
        raw = str(value or "").strip()
        if not raw:
            return 0.2
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            days = max(0.0, (now - dt).total_seconds() / 86400.0)
        except ValueError:
            return 0.2
        return 1.0 / (1.0 + days / 14.0)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
