import json
import os
import re
import uuid
from datetime import datetime
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
            "auth": "token" if os.environ.get("OMBRE_GATEWAY_TOKEN", "").strip() else "open",
            "embedding": bool(self.embedding_engine and self.embedding_engine.enabled),
            "include_legacy": self.include_legacy,
            "legacy_keyword_limit": self.legacy_keyword_limit,
            "natural_limit": self.natural_limit,
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
            domain=[GATEWAY_DOMAIN],
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

    async def recall(self, body: dict[str, Any]) -> dict[str, Any]:
        query = self._build_query(body)
        max_results = self._bounded_int(body.get("max_results", 5), 1, 8)
        keyword_limit = self._bounded_int(body.get("keyword_limit", 4), 0, max_results)
        semantic_limit = self._bounded_int(body.get("semantic_limit", 1), 0, max_results)

        if not query:
            buckets = await self._important_recent(max_results)
        else:
            buckets = await self._recall_buckets(query, keyword_limit, semantic_limit, max_results)

        index = self._load_memory_index()
        memories = [self._format_memory(b, index) for b in buckets]
        memories = [m for m in memories if m]
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

    async def _recall_buckets(self, query: str, keyword_limit: int, semantic_limit: int, max_results: int) -> list[dict]:
        seen = set()
        buckets = []
        keyword_terms = self._keyword_terms(query)
        keyword_query = " ".join(keyword_terms)
        natural_limit = min(self.natural_limit, max_results)
        semantic_target = min(semantic_limit, max(0, max_results - natural_limit))
        keyword_budget = max(0, max_results - semantic_target - natural_limit)

        if self.include_legacy and self.legacy_keyword_limit and keyword_limit and keyword_budget:
            legacy_target = min(self.legacy_keyword_limit, keyword_budget)
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

    async def _keyword_search(self, terms: list[str], limit: int, domain_filter: list[str] | None = None) -> list[dict]:
        merged = {}
        for term in terms[:6]:
            hits = await self.bucket_mgr.search(term, limit=max(limit, 4), domain_filter=domain_filter)
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
        ranked.sort(key=lambda bucket: float(bucket.get("score") or 0), reverse=True)
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
        score = 0.0
        for raw_term in terms:
            term = raw_term.lower()
            if not term:
                continue
            if term in content:
                score += 55.0
            if term in name:
                score += 35.0
            if term in tags:
                score += 28.0
            if term in domains:
                score += 18.0
            if len(term) >= 4:
                pieces = [p for p in re.split(r"\s+", term) if len(p) >= 2]
                if pieces and any(piece in content for piece in pieces):
                    score += 18.0
        if score <= 0:
            return 0.0
        try:
            importance = max(1, min(10, int(meta.get("importance", 5))))
        except (TypeError, ValueError):
            importance = 5
        return min(99.0, score + importance)

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
        importance = self._bounded_int(body.get("importance", 5), 1, 10)
        entry = {
            "summary_text": summary_text,
            "tags": tags,
            "importance": importance,
            "raw_ref": raw_ref,
        }

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

    def _memory_content(self, entry: dict[str, Any]) -> str:
        lines = [
            MEMORY_MARKER,
            f"summary_text: {entry['summary_text']}",
            f"tags: {', '.join(entry['tags'])}",
            f"importance: {entry['importance']}",
            f"raw_ref: {entry['raw_ref']}",
        ]
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
            "importance": record.get("importance"),
            "raw_ref": record.get("raw_ref", ""),
        }
        for field in ("feel_text", "valence", "arousal", "memory_id", "created"):
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
            elif key == "importance":
                record[key] = self._bounded_int(value, 1, 10)
            elif key in ("valence", "arousal"):
                record[key] = float(value)
            elif key in ("summary_text", "raw_ref", "feel_text"):
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
        return result

    def _injection_text(self, memories: list[dict[str, Any]]) -> str:
        if not memories:
            return ""
        parts = ["[Zeta memory gateway]"]
        for idx, memory in enumerate(memories, 1):
            parts.append(
                f"{idx}. {memory.get('summary_text', '')}\n"
                f"   tags: {', '.join(memory.get('tags') or [])}\n"
                f"   importance: {memory.get('importance')}\n"
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
        explicit_query = str(body.get("query") or "").strip()
        current_text = str(body.get("current_text") or body.get("user_message") or "").strip()
        recent_context = str(body.get("recent_context") or "").strip()
        if explicit_query:
            text = explicit_query
        elif len(current_text) >= 8:
            text = current_text
        else:
            text = "\n".join(p for p in (current_text, recent_context) if p)
        return text[:4000]

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
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
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

    def _raw_path(self, session_id: str) -> Path:
        return self.raw_dir / f"{self._safe_id(session_id)}.jsonl"

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
