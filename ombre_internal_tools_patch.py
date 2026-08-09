from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
from starlette.responses import JSONResponse, Response, StreamingResponse


TOOL_REQUEST_OPEN = "<ombre_tool_request>"
TOOL_REQUEST_CLOSE = "</ombre_tool_request>"
TOOL_RESULT_OPEN = "<ombre_tool_result>"
TOOL_RESULT_CLOSE = "</ombre_tool_result>"
ZETA_MEMORY_REQUEST_OPEN = "<zeta_memory_request>"
ZETA_MEMORY_REQUEST_CLOSE = "</zeta_memory_request>"
MAX_TOOL_CALLS = 2
MAX_INTERNAL_TOOL_ROUNDS = 2
READ_ACTIONS = {"memory.search", "diary.search", "profile.read"}
WRITE_ACTIONS = {"memory.write", "profile.patch"}
EMPTY_STREAM_FALLBACK = "模型没有返回可见内容，请重试一次。"


def apply_ombre_internal_tools_patch(zeta_openai_gateway_module) -> None:
    if getattr(zeta_openai_gateway_module, "_ombre_internal_tools_patched", False):
        return

    module = zeta_openai_gateway_module
    logger = getattr(module, "logger", None)

    module.TOOL_REQUEST_OPEN = TOOL_REQUEST_OPEN
    module.TOOL_REQUEST_CLOSE = TOOL_REQUEST_CLOSE
    module.TOOL_RESULT_OPEN = TOOL_RESULT_OPEN
    module.TOOL_RESULT_CLOSE = TOOL_RESULT_CLOSE

    class OmbreHiddenMemoryStreamFilter:
        def __init__(self, parse_entries, enabled: bool):
            self.parse_entries = parse_entries
            self.gateway = getattr(parse_entries, "__self__", None)
            self.enabled = enabled
            self.buffer = ""
            self.hidden_parts: list[str] = []
            self.current_close = ""
            self.current_kind = ""
            self.entries: list[dict[str, Any]] = []
            self.open_tags = [
                (ZETA_MEMORY_REQUEST_OPEN, ZETA_MEMORY_REQUEST_CLOSE, "zeta"),
                (TOOL_REQUEST_OPEN, TOOL_REQUEST_CLOSE, "ombre"),
            ]

        def feed(self, text: str) -> str:
            if not self.enabled or not text:
                return text or ""
            self.buffer += text
            output: list[str] = []
            self._drain(output)
            return "".join(output)

        def flush(self) -> str:
            if not self.enabled:
                return ""
            if self.current_close:
                self.buffer = ""
                self.hidden_parts = []
                self.current_close = ""
                self.current_kind = ""
                return ""
            tail = self.buffer
            self.buffer = ""
            return tail

        def _find_next_open(self) -> tuple[int, str, str, str] | None:
            lower = self.buffer.lower()
            best: tuple[int, str, str, str] | None = None
            for open_tag, close_tag, kind in self.open_tags:
                idx = lower.find(open_tag.lower())
                if idx >= 0 and (best is None or idx < best[0]):
                    best = (idx, open_tag, close_tag, kind)
            return best

        def _pending_open_prefix_len(self) -> int:
            """Keep only a suffix that could still become an internal opening tag."""
            lower = self.buffer.lower()
            best = 0
            for open_tag, _, _ in self.open_tags:
                candidate = open_tag.lower()
                limit = min(len(lower), len(candidate) - 1)
                for size in range(limit, best, -1):
                    if lower.endswith(candidate[:size]):
                        best = size
                        break
            return best

        def _finish_hidden(self) -> None:
            raw_json = "".join(self.hidden_parts).strip()
            parsed: list[dict[str, Any]] = []
            if self.current_kind == "zeta":
                parsed = self.parse_entries(raw_json)
            elif self.gateway is not None:
                parsed = self.gateway._parse_ombre_tool_json(raw_json)
            self.entries.extend(parsed)
            self.hidden_parts = []
            self.current_close = ""
            self.current_kind = ""

        def _drain(self, output: list[str]) -> None:
            while self.buffer:
                if self.current_close:
                    close_idx = self.buffer.lower().find(self.current_close.lower())
                    if close_idx < 0:
                        self.hidden_parts.append(self.buffer)
                        self.buffer = ""
                        return
                    self.hidden_parts.append(self.buffer[:close_idx])
                    self.buffer = self.buffer[close_idx + len(self.current_close):]
                    self._finish_hidden()
                    continue

                found = self._find_next_open()
                if found is not None:
                    open_idx, open_tag, close_tag, kind = found
                    if open_idx:
                        output.append(self.buffer[:open_idx])
                    self.buffer = self.buffer[open_idx + len(open_tag):]
                    self.current_close = close_tag
                    self.current_kind = kind
                    self.hidden_parts = []
                    continue

                pending_len = self._pending_open_prefix_len()
                emit_len = len(self.buffer) - pending_len
                if emit_len:
                    output.append(self.buffer[:emit_len])
                    self.buffer = self.buffer[emit_len:]
                return

    def _loose_json(raw_json: str) -> Any:
        text = (raw_json or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        if not text:
            raise json.JSONDecodeError("empty JSON", text, 0)
        first_obj = text.find("{")
        first_arr = text.find("[")
        starts = [idx for idx in (first_obj, first_arr) if idx >= 0]
        if starts:
            start = min(starts)
            end = max(text.rfind("}"), text.rfind("]"))
            if end > start:
                text = text[start:end + 1]
        return json.loads(text)

    def _string_list(value: Any, limit: int = 8) -> list[str]:
        if isinstance(value, list):
            raw = value
        elif isinstance(value, str):
            raw = re.split(r"[,，;；\n]+", value)
        else:
            raw = []
        result: list[str] = []
        for item in raw:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text[:80])
            if len(result) >= limit:
                break
        return result

    def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(low, min(high, number))

    def _compact_text(value: Any, limit: int = 2000) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _strip_internal(entry: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in entry.items() if not str(key).startswith("__ombre_")}

    def _hidden_memory_instruction(self) -> str:
        if not getattr(self, "hidden_memory_enabled", False):
            return ""
        return f"""
我拥有一个安静的内部工具通道。这个通道只用于帮助我更好地记住、理解和照顾当前对话，不直接展示给她。除非她明确询问，我不会在自然回复里提到工具调用、隐藏块、检索过程或内部协议。

当我需要确认过去发生过什么、她的偏好、重要事实、公开日记、私密日记摘要，或我和她之间已经形成的相处方式时，我会先使用内部工具，而不是凭感觉编造。如果当前回复依赖工具结果，我会先只输出隐藏工具块，不输出自然语言；网关会把结果放回给我，然后我再自然回复她。

我会保持克制：每一轮最多请求 2 次内部工具；每次只取完成当前回答所需的最少内容。记忆通常最多取 5 条，日记通常最多取 2 篇。私密日记默认只读取标题、时间和摘要；只有当她明确要求回忆、整理或讨论私密日记内容时，我才会请求更多正文，并且仍然只取必要片段。

如果我对她或自己的画像有了新的认识，我会先判断它是否稳定、具体、对未来有帮助。只有满足这些条件时，我才会请求修改画像。我不会把一时情绪、猜测、过度解读或没有证据的判断写入画像。每次画像修改最多 3 条，并且必须带上 evidence，说明这个认识来自哪里。

我可以使用的内部动作包括：

- memory.search：搜索长期记忆
- diary.search：搜索公开或私密日记
- profile.read：读取她的画像、AI 画像或两者
- profile.patch：小幅修改她的画像或 AI 画像
- memory.write：写入新的结构化记忆、公开日记索引或私密日记索引

内部工具请求格式如下。隐藏块必须是严格 JSON，不要包在 Markdown 代码块里：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"memory.search","query":"她上次说过的界面偏好","limit":5}}]}}
{TOOL_REQUEST_CLOSE}

读取公开日记：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"diary.search","visibility":"public","query":"最近的开发进展","limit":2,"max_chars":800}}]}}
{TOOL_REQUEST_CLOSE}

读取私密日记摘要：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"diary.search","visibility":"private_summary","query":"她最近压力相关的记录","limit":2}}]}}
{TOOL_REQUEST_CLOSE}

只有她明确要求讨论私密日记正文时，我才请求必要片段：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"diary.search","visibility":"private_excerpt","query":"那篇关于焦虑的日记","limit":1,"max_chars":600}}]}}
{TOOL_REQUEST_CLOSE}

读取画像：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"profile.read","target":"both"}}]}}
{TOOL_REQUEST_CLOSE}

修改画像：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"profile.patch","target":"user","patches":[{{"op":"add","path":"preferences.communication","value":"她更喜欢温柔、具体、少术语的解释。","evidence":"她多次要求用更有温度的第一人称提示词，并希望减少技术黑话。"}}]}}]}}
{TOOL_REQUEST_CLOSE}

写入结构化记忆。只有稳定、具体、对未来有帮助的事实才写：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"memory.write","kind":"memory","summary_text":"她决定后续 GitHub 推送由自己完成，助手只需要给 git 命令。","tags":["workflow","github"],"domains":["project"],"importance":6,"raw_ref":"auto"}}]}}
{TOOL_REQUEST_CLOSE}

写入公开或私密日记索引：
{TOOL_REQUEST_OPEN}
{{"calls":[{{"action":"memory.write","kind":"diary","visibility":"public","title":"合并前端和记忆网关","summary_text":"今天完成了前端合并方案，并开始加入内部工具协议。","content":"可选正文或索引内容","tags":["project","diary"],"importance":5,"raw_ref":"auto"}}]}}
{TOOL_REQUEST_CLOSE}

如果我只是想在自然回复后顺手保存记忆、日记或画像，我会把隐藏块放在自然回复末端。网关会在她看到之前移除隐藏块。
""".strip()

    def _parse_ombre_tool_json(self, raw_json: str) -> list[dict[str, Any]]:
        try:
            payload = _loose_json(raw_json)
        except json.JSONDecodeError:
            if logger:
                logger.warning("Ombre internal tool JSON parse failed: %s", (raw_json or "")[:500])
            return []
        if isinstance(payload, dict):
            calls = payload.get("calls") or payload.get("tool_calls") or [payload]
        elif isinstance(payload, list):
            calls = payload
        else:
            calls = []
        entries: list[dict[str, Any]] = []
        for call in calls[:MAX_TOOL_CALLS]:
            if not isinstance(call, dict):
                continue
            entry = self._normalize_ombre_tool_call(call)
            if entry:
                entries.append(entry)
        return entries

    def _normalize_ombre_tool_call(self, call: dict[str, Any]) -> dict[str, Any] | None:
        action = str(call.get("action") or call.get("name") or "").strip().lower()
        if not action:
            return None
        if action == "memory.write":
            kind = str(call.get("kind") or call.get("type") or "memory").strip().lower()
            if kind in {"diary", "public_diary", "private_diary"}:
                visibility = str(call.get("visibility") or "").strip().lower()
                if not visibility:
                    visibility = "private" if kind == "private_diary" else "public"
                if visibility not in {"public", "private"}:
                    visibility = "private" if "private" in visibility else "public"
                return {
                    "__ombre_action": action,
                    "__ombre_kind": "diary",
                    "visibility": visibility,
                    "title": str(call.get("title") or "").strip()[:120],
                    "summary_text": str(call.get("summary_text") or call.get("summary") or "").strip()[:800],
                    "content": str(call.get("content") or "").strip()[:4000],
                    "tags": _string_list(call.get("tags"), 12),
                    "importance": _bounded_int(call.get("importance"), 5, 1, 10),
                    "raw_ref": str(call.get("raw_ref") or "auto").strip() or "auto",
                    "index_to_memory": call.get("index_to_memory", True),
                }
            item = {
                "summary_text": call.get("summary_text") or call.get("summary") or call.get("content"),
                "tags": call.get("tags", []),
                "domains": call.get("domains", call.get("domain", [])),
                "importance": call.get("importance", 5),
                "raw_ref": call.get("raw_ref", "auto"),
                "feel_text": call.get("feel_text", ""),
                "valence": call.get("valence"),
                "arousal": call.get("arousal"),
            }
            entry = self._normalize_requested_memory_entry(item)
            if not entry or self._is_rejected_reflection_entry(entry):
                return None
            entry["__ombre_action"] = action
            entry["__ombre_kind"] = "memory"
            return entry
        if action == "profile.patch":
            patches = call.get("patches")
            if not isinstance(patches, list):
                patches = [{
                    "op": call.get("op", "add"),
                    "path": call.get("path", ""),
                    "value": call.get("value", ""),
                    "evidence": call.get("evidence", ""),
                    "target": call.get("target", ""),
                }]
            return {
                "__ombre_action": action,
                "target": str(call.get("target") or "user").strip().lower(),
                "patches": patches[:3],
            }
        if action in READ_ACTIONS:
            result = {
                "__ombre_action": action,
                "query": str(call.get("query") or "").strip()[:800],
                "limit": _bounded_int(call.get("limit"), 5 if action == "memory.search" else 2, 1, 5),
                "max_chars": _bounded_int(call.get("max_chars"), 800, 120, 2400),
            }
            if action == "diary.search":
                result["visibility"] = str(call.get("visibility") or call.get("scope") or "public").strip().lower()
            if action == "profile.read":
                result["target"] = str(call.get("target") or "both").strip().lower()
            return result
        return None

    def _extract_zeta_memory_request(self, assistant_text: str) -> tuple[str, list[dict[str, Any]]]:
        text = assistant_text or ""
        if not getattr(self, "hidden_memory_enabled", False):
            return text, []
        entries: list[dict[str, Any]] = []

        def collect_zeta(match: re.Match) -> str:
            entries.extend(self._parse_zeta_memory_json(match.group(1).strip()))
            return ""

        def collect_ombre(match: re.Match) -> str:
            entries.extend(self._parse_ombre_tool_json(match.group(1).strip()))
            return ""

        visible = re.sub(
            rf"{re.escape(ZETA_MEMORY_REQUEST_OPEN)}\s*([\s\S]*?)\s*{re.escape(ZETA_MEMORY_REQUEST_CLOSE)}",
            collect_zeta,
            text,
            flags=re.IGNORECASE,
        )
        visible = re.sub(
            rf"{re.escape(TOOL_REQUEST_OPEN)}\s*([\s\S]*?)\s*{re.escape(TOOL_REQUEST_CLOSE)}",
            collect_ombre,
            visible,
            flags=re.IGNORECASE,
        )
        for open_tag in (ZETA_MEMORY_REQUEST_OPEN, TOOL_REQUEST_OPEN):
            visible = re.sub(rf"{re.escape(open_tag)}[\s\S]*$", "", visible, flags=re.IGNORECASE)
        return visible.strip(), entries[:MAX_TOOL_CALLS]

    def _profile_path(self) -> Path:
        base_dir = getattr(self.memory_gateway, "base_dir", None)
        if base_dir is None:
            base_dir = Path(getattr(self, "config", {}).get("buckets_dir", "buckets")) / "gateway"
        base_dir = Path(base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "profile.json"

    def _empty_companion_profile(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": 1,
            "user": "",
            "ai": "",
            "updated_at": "",
            "updated_by": "",
            "history": [],
        }

    def _read_companion_profile(self) -> dict[str, Any]:
        path = self._ombre_profile_path()
        if not path.exists():
            return self._ombre_empty_companion_profile()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        profile = self._ombre_empty_companion_profile()
        if isinstance(data, dict):
            profile.update({
                "version": int(data.get("version", 1) or 1),
                "user": str(data.get("user") or "")[:6000],
                "ai": str(data.get("ai") or "")[:6000],
                "updated_at": str(data.get("updated_at") or ""),
                "updated_by": str(data.get("updated_by") or ""),
                "history": data.get("history", []) if isinstance(data.get("history"), list) else [],
            })
        return profile

    def _write_companion_profile(self, profile: dict[str, Any]) -> None:
        path = self._ombre_profile_path()
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_profile_patch(self, entry: dict[str, Any]) -> int:
        profile = self._ombre_read_companion_profile()
        patches = entry.get("patches") if isinstance(entry.get("patches"), list) else []
        default_target = str(entry.get("target") or "user").strip().lower()
        changed = 0
        for patch in patches[:3]:
            if not isinstance(patch, dict):
                continue
            target = str(patch.get("target") or default_target or "user").strip().lower()
            target = "ai" if target in {"ai", "assistant", "zeta", "me", "self"} else "user"
            op = str(patch.get("op") or "add").strip().lower()
            path = str(patch.get("path") or "note").strip()[:120]
            value = _compact_text(patch.get("value"), 600)
            evidence = _compact_text(patch.get("evidence"), 600)
            if op in {"remove", "delete"}:
                line = f"- [remove] {path}"
                if evidence:
                    line += f"\n  evidence: {evidence}"
            else:
                if not value:
                    continue
                line = f"- {path}: {value}"
                if evidence:
                    line += f"\n  evidence: {evidence}"
            current = str(profile.get(target) or "").strip()
            profile[target] = (current + "\n" + line).strip()[:6000]
            changed += 1
        if changed:
            updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            profile["updated_at"] = updated_at
            profile["updated_by"] = "internal_tool"
            history = profile.get("history", [])
            if not isinstance(history, list):
                history = []
            history.append({
                "at": updated_at,
                "source": "internal_tool",
                "patches": changed,
                "user_length": len(str(profile.get("user") or "")),
                "ai_length": len(str(profile.get("ai") or "")),
            })
            profile["history"] = history[-30:]
            self._ombre_write_companion_profile(profile)
        return changed

    async def _write_zeta_memory_requests(
        self,
        *,
        session_id: str,
        entries: list[dict[str, Any]],
        default_raw_ref: str,
    ) -> int:
        written = 0
        for entry in entries:
            action = entry.get("__ombre_action")
            if action in READ_ACTIONS:
                continue
            try:
                if action == "profile.patch":
                    written += self._ombre_append_profile_patch(entry)
                    continue
                if action == "memory.write" and entry.get("__ombre_kind") == "diary":
                    raw_ref = str(entry.get("raw_ref") or "").strip()
                    if not raw_ref or raw_ref == "auto":
                        raw_ref = default_raw_ref or ""
                    body = {
                        "session_id": session_id,
                        "visibility": entry.get("visibility", "public"),
                        "title": entry.get("title", ""),
                        "summary_text": entry.get("summary_text", ""),
                        "content": entry.get("content") or entry.get("summary_text") or entry.get("title") or "",
                        "tags": entry.get("tags", []),
                        "importance": entry.get("importance", 5),
                        "raw_ref": raw_ref,
                        "index_to_memory": entry.get("index_to_memory", True),
                    }
                    result = await self.memory_gateway.save_diary(body)
                    if result.get("ok"):
                        written += 1
                    continue
                memory_entry = _strip_internal(entry)
                if not memory_entry.get("raw_ref") or memory_entry.get("raw_ref") == "auto":
                    memory_entry["raw_ref"] = default_raw_ref
                if not memory_entry.get("raw_ref"):
                    if logger:
                        logger.info(
                            "Skipped hidden memory without raw_ref | summary=%s",
                            str(memory_entry.get("summary_text", ""))[:120],
                        )
                    continue
                await self.memory_gateway.write_memory(memory_entry)
                written += 1
                if logger:
                    logger.info(
                        "Ombre hidden memory written | session=%s summary=%s",
                        session_id,
                        str(memory_entry.get("summary_text", ""))[:80],
                    )
            except Exception as exc:
                if logger:
                    logger.warning("Ombre internal write failed | session=%s error=%s", session_id, exc)
        return written

    def _compact_memory_result(self, item: dict[str, Any]) -> dict[str, Any]:
        result = {
            "summary_text": item.get("summary_text", ""),
            "tags": item.get("tags", []),
            "importance": item.get("importance"),
            "raw_ref": item.get("raw_ref", ""),
            "source": item.get("source", ""),
            "reason": item.get("reason", ""),
        }
        for field in ("feel_text", "valence", "arousal", "created", "lastUsedAt"):
            if item.get(field) is not None:
                result[field] = item[field]
        return result

    async def _run_memory_search(self, entry: dict[str, Any]) -> dict[str, Any]:
        limit = _bounded_int(entry.get("limit"), 5, 1, 5)
        query = str(entry.get("query") or "").strip()
        recalled = await self.memory_gateway.recall({
            "query": query,
            "current_text": query,
            "max_results": limit,
            "keyword_limit": limit,
            "semantic_limit": min(2, limit),
            "track_usage": True,
        })
        memories = recalled.get("memories") if isinstance(recalled, dict) else []
        if not isinstance(memories, list):
            memories = []
        return {
            "action": "memory.search",
            "ok": True,
            "query": query,
            "count": len(memories[:limit]),
            "memories": [self._ombre_compact_memory_result(item) for item in memories[:limit] if isinstance(item, dict)],
        }

    def _diary_matches(self, item: dict[str, Any], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join([
            str(item.get("title") or ""),
            str(item.get("summary_text") or ""),
            str(item.get("content") or ""),
            " ".join(str(tag) for tag in item.get("tags", []) if tag),
        ]).lower()
        terms = [term.lower() for term in re.split(r"\s+", query) if len(term.strip()) >= 2]
        if not terms:
            return query.lower() in haystack
        return any(term in haystack for term in terms)

    def _search_diaries(self, entry: dict[str, Any]) -> dict[str, Any]:
        visibility = str(entry.get("visibility") or "public").strip().lower()
        limit = _bounded_int(entry.get("limit"), 2, 1, 2)
        max_chars = _bounded_int(entry.get("max_chars"), 800, 120, 2400)
        query = str(entry.get("query") or "").strip()
        include_content = visibility in {"public", "private_excerpt"}
        selected = ["public"]
        if visibility in {"private", "private_summary"}:
            selected = ["private"]
            include_content = False
        elif visibility == "private_excerpt":
            selected = ["private"]
            include_content = True
        elif visibility == "all":
            selected = ["public", "private"]
            include_content = False
        items: list[dict[str, Any]] = []
        for item_visibility in selected:
            try:
                merged = self.memory_gateway._merged_diaries(item_visibility, max(limit * 5, 20), include_content)
            except Exception:
                merged = []
            for item in merged:
                if not isinstance(item, dict) or not self._ombre_diary_matches(item, query):
                    continue
                result = {
                    "visibility": item_visibility,
                    "created": item.get("created", ""),
                    "title": item.get("title", ""),
                    "summary_text": item.get("summary_text", ""),
                    "tags": item.get("tags", []),
                    "raw_ref": item.get("raw_ref", ""),
                }
                if include_content:
                    result["content"] = _compact_text(item.get("content"), max_chars)
                items.append(result)
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        return {
            "action": "diary.search",
            "ok": True,
            "visibility": visibility,
            "query": query,
            "count": len(items),
            "diaries": items,
        }

    def _read_profile_tool(self, entry: dict[str, Any]) -> dict[str, Any]:
        target = str(entry.get("target") or "both").strip().lower()
        profile = self._ombre_read_companion_profile()
        result = {"action": "profile.read", "ok": True, "target": target}
        if target in {"user", "her"}:
            result["user"] = profile.get("user", "")
        elif target in {"ai", "assistant", "zeta", "self"}:
            result["ai"] = profile.get("ai", "")
        else:
            result["user"] = profile.get("user", "")
            result["ai"] = profile.get("ai", "")
        result["updated_at"] = profile.get("updated_at", "")
        return result

    async def _run_ombre_read_tool(self, entry: dict[str, Any]) -> dict[str, Any]:
        action = entry.get("__ombre_action")
        try:
            if action == "memory.search":
                return await self._ombre_run_memory_search(entry)
            if action == "diary.search":
                return self._ombre_search_diaries(entry)
            if action == "profile.read":
                return self._ombre_read_profile_tool(entry)
        except Exception as exc:
            if logger:
                logger.warning("Ombre internal read failed | action=%s error=%s", action, exc)
            return {"action": action, "ok": False, "error": str(exc)}
        return {"action": action, "ok": False, "error": "unsupported action"}

    def _has_read_tools(self, entries: list[dict[str, Any]]) -> bool:
        return any(entry.get("__ombre_action") in READ_ACTIONS for entry in entries)

    async def _run_ombre_tool_entries(
        self,
        *,
        session_id: str,
        entries: list[dict[str, Any]],
        default_raw_ref: str,
    ) -> tuple[list[dict[str, Any]], int]:
        results: list[dict[str, Any]] = []
        written = 0
        for entry in entries[:MAX_TOOL_CALLS]:
            if entry.get("__ombre_action") in READ_ACTIONS:
                results.append(await self._ombre_run_read_tool(entry))
            else:
                written += await self._write_zeta_memory_requests(
                    session_id=session_id,
                    entries=[entry],
                    default_raw_ref=default_raw_ref,
                )
        return results, written

    def _tool_result_payload(self, payload: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        follow = deepcopy(payload)
        messages = list(follow.get("messages") or [])
        tool_text = json.dumps({"results": results[:MAX_TOOL_CALLS]}, ensure_ascii=False, indent=2)
        messages.append({
            "role": "system",
            "content": (
                f"{TOOL_RESULT_OPEN}\n{tool_text}\n{TOOL_RESULT_CLOSE}\n"
                "我已经拿到内部工具结果。现在我会直接、自然地回复她；不会提到工具、隐藏块、检索过程或内部协议。"
            ),
        })
        follow["messages"] = messages
        return follow

    def _add_ombre_tool_headers(
        self,
        headers: dict[str, str],
        entries: list[dict[str, Any]],
        results: list[dict[str, Any]],
        written_count: int,
    ) -> None:
        if not entries:
            return
        headers["X-Ombre-Tool-Requests"] = str(len(entries))
        headers["X-Ombre-Tool-Results"] = str(len(results))
        headers["X-Ombre-Tool-Written"] = str(written_count)

    async def _stream_upstream_with_internal_tools(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        user_text: str,
        user_raw_refs: list[str],
        recalled: dict[str, Any],
        memory_headers: dict[str, str],
        internal_round: int = 0,
    ) -> Response:
        try:
            first_context = self.http.stream(
                "POST",
                self.upstream_chat_url,
                headers=self._upstream_headers(self.upstream_api_key),
                json=self._payload_for_upstream(payload),
            )
            first_response = await first_context.__aenter__()
        except httpx.RequestError as exc:
            return self._upstream_request_error(exc)

        if not 200 <= first_response.status_code < 300:
            body = await first_response.aread()
            await first_context.__aexit__(None, None, None)
            return self._upstream_status_error(
                first_response.status_code,
                first_response.headers.get("content-type", ""),
                body,
            )

        content_type = first_response.headers.get("content-type", "").lower()
        if logger:
            logger.info(
                "Ombre SSE upstream connected | session=%s status=%s content_type=%s content_encoding=%s",
                session_id,
                first_response.status_code,
                content_type or "missing",
                first_response.headers.get("content-encoding", "identity") or "identity",
            )
        if "application/json" in content_type and "text/event-stream" not in content_type:
            await first_response.aread()
            await first_context.__aexit__(None, None, None)
            assistant_text = self._assistant_text_from_response(first_response)
            visible_text, entries = self._extract_zeta_memory_request(assistant_text)
            if (
                entries
                and self._ombre_has_read_tools(entries)
                and not visible_text.strip()
                and internal_round < MAX_INTERNAL_TOOL_ROUNDS
            ):
                tool_results, written = await self._ombre_run_tool_entries(
                    session_id=session_id,
                    entries=entries,
                    default_raw_ref=user_raw_refs[0] if user_raw_refs else "",
                )
                self._ombre_add_tool_headers(memory_headers, entries, tool_results, written)
                follow_payload = self._ombre_tool_result_payload(payload, tool_results)
                follow_payload["stream"] = True
                return await self._ombre_stream_upstream(
                    follow_payload,
                    session_id=session_id,
                    user_text=user_text,
                    user_raw_refs=user_raw_refs,
                    recalled=recalled,
                    memory_headers=memory_headers,
                    internal_round=internal_round + 1,
                )
            return await self._ombre_finalize_nonstream_response(
                first_response,
                session_id=session_id,
                user_text=user_text,
                user_raw_refs=user_raw_refs,
                recalled=recalled,
                memory_headers=memory_headers,
                as_stream=True,
            )

        def encode_chunk(chunk: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

        def content_chunk(last_chunk: dict[str, Any], text: str) -> bytes:
            base = {
                key: deepcopy(value)
                for key, value in last_chunk.items()
                if key not in {"choices", "usage"}
            } if last_chunk else {
                "id": f"chatcmpl-ombre-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": getattr(self, "public_model", "ombre"),
            }
            base["choices"] = [{
                "index": 0,
                "delta": {"content": text},
                "finish_reason": None,
            }]
            return encode_chunk(base)

        def final_chunk(last_chunk: dict[str, Any], finish_reason: str = "stop") -> bytes:
            base = {
                key: deepcopy(value)
                for key, value in last_chunk.items()
                if key not in {"choices", "usage"}
            } if last_chunk else {
                "id": f"chatcmpl-ombre-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": getattr(self, "public_model", "ombre"),
            }
            base["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
            return encode_chunk(base)

        def has_client_payload(chunk: dict[str, Any]) -> bool:
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if not isinstance(choices, list):
                return True
            for choice in choices:
                delta = choice.get("delta") if isinstance(choice, dict) else None
                if not isinstance(delta, dict):
                    continue
                for value in delta.values():
                    if value not in (None, "", [], {}):
                        return True
            return bool(chunk.get("usage"))

        async def stream_body():
            current_payload = deepcopy(payload)
            stream_context = first_context
            upstream_response = first_response
            tool_round = internal_round

            while True:
                stream_filter = OmbreHiddenMemoryStreamFilter(
                    self._parse_zeta_memory_json,
                    bool(getattr(self, "hidden_memory_enabled", False)),
                )
                assistant_parts: list[str] = []
                terminal_events: list[bytes] = []
                last_chunk: dict[str, Any] = {}
                native_tool_seen = False

                try:
                    async for line in upstream_response.aiter_lines():
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            yield (line + "\n\n").encode("utf-8")
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            yield (line + "\n\n").encode("utf-8")
                            continue
                        if not isinstance(chunk, dict):
                            yield (line + "\n\n").encode("utf-8")
                            continue

                        last_chunk = chunk
                        choices = chunk.get("choices")
                        has_finish = False
                        if isinstance(choices, list):
                            for choice in choices:
                                if not isinstance(choice, dict):
                                    continue
                                if choice.get("finish_reason") is not None:
                                    has_finish = True
                                delta = choice.get("delta")
                                if not isinstance(delta, dict):
                                    continue
                                if delta.get("tool_calls") or delta.get("function_call"):
                                    native_tool_seen = True
                                content = delta.get("content")
                                if isinstance(content, str):
                                    visible = stream_filter.feed(content)
                                    if visible:
                                        assistant_parts.append(visible)
                                    delta["content"] = visible

                        encoded = encode_chunk(chunk)
                        if has_finish:
                            terminal_events.append(encoded)
                        elif has_client_payload(chunk):
                            yield encoded
                finally:
                    await stream_context.__aexit__(None, None, None)

                tail = stream_filter.flush()
                if tail:
                    assistant_parts.append(tail)
                    yield content_chunk(last_chunk, tail)

                visible_text = "".join(assistant_parts).strip()
                entries = stream_filter.entries[:MAX_TOOL_CALLS]
                if (
                    entries
                    and self._ombre_has_read_tools(entries)
                    and not visible_text
                    and tool_round < MAX_INTERNAL_TOOL_ROUNDS
                ):
                    tool_results, written = await self._ombre_run_tool_entries(
                        session_id=session_id,
                        entries=entries,
                        default_raw_ref=user_raw_refs[0] if user_raw_refs else "",
                    )
                    self._ombre_add_tool_headers(memory_headers, entries, tool_results, written)
                    current_payload = self._ombre_tool_result_payload(current_payload, tool_results)
                    current_payload["stream"] = True
                    tool_round += 1
                    try:
                        stream_context = self.http.stream(
                            "POST",
                            self.upstream_chat_url,
                            headers=self._upstream_headers(self.upstream_api_key),
                            json=self._payload_for_upstream(current_payload),
                        )
                        upstream_response = await stream_context.__aenter__()
                    except httpx.RequestError as exc:
                        if logger:
                            logger.warning("Ombre internal tool follow-up failed: %s", exc)
                        yield content_chunk(last_chunk, EMPTY_STREAM_FALLBACK)
                        yield final_chunk(last_chunk)
                        yield b"data: [DONE]\n\n"
                        return
                    if not 200 <= upstream_response.status_code < 300:
                        await upstream_response.aread()
                        await stream_context.__aexit__(None, None, None)
                        yield content_chunk(last_chunk, EMPTY_STREAM_FALLBACK)
                        yield final_chunk(last_chunk)
                        yield b"data: [DONE]\n\n"
                        return
                    continue

                if not visible_text and not native_tool_seen:
                    visible_text = EMPTY_STREAM_FALLBACK
                    yield content_chunk(last_chunk, visible_text)

                assistant_raw_refs = await self._save_turn(session_id, "zeta", visible_text)
                written = await self._write_zeta_memory_requests(
                    session_id=session_id,
                    entries=entries,
                    default_raw_ref=user_raw_refs[0] if user_raw_refs else (
                        assistant_raw_refs[0] if assistant_raw_refs else ""
                    ),
                )
                self._augment_memory_headers(memory_headers, entries, written)
                self._ombre_add_tool_headers(memory_headers, entries, [], written)
                if self._should_run_reflection(written):
                    self._schedule_reflection(
                        session_id=session_id,
                        user_text=user_text,
                        assistant_text=visible_text,
                        user_raw_refs=user_raw_refs,
                        assistant_raw_refs=assistant_raw_refs,
                        recalled=recalled,
                    )

                if terminal_events:
                    for event in terminal_events:
                        yield event
                else:
                    finish_reason = "tool_calls" if native_tool_seen else "stop"
                    yield final_chunk(last_chunk, finish_reason)
                yield b"data: [DONE]\n\n"
                return

        hop_by_hop_or_body_headers = {
            "content-length",
            "transfer-encoding",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "upgrade",
            "content-type",
            "content-encoding",
            "content-md5",
            "accept-ranges",
            "content-range",
            "etag",
        }
        headers = {
            key: value
            for key, value in first_response.headers.items()
            if key.lower() not in hop_by_hop_or_body_headers
        }
        headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        headers.update(memory_headers)
        return StreamingResponse(
            stream_body(),
            status_code=first_response.status_code,
            media_type="text/event-stream",
            headers=headers,
        )

    async def _finalize_nonstream_response(
        self,
        upstream_response: httpx.Response,
        *,
        session_id: str,
        user_text: str,
        user_raw_refs: list[str],
        recalled: dict[str, Any],
        memory_headers: dict[str, str],
        as_stream: bool,
    ) -> Response:
        assistant_text = self._assistant_text_from_response(upstream_response)
        visible_text, entries = self._extract_zeta_memory_request(assistant_text)
        assistant_raw_refs = await self._save_turn(session_id, "zeta", visible_text)
        written = await self._write_zeta_memory_requests(
            session_id=session_id,
            entries=entries,
            default_raw_ref=user_raw_refs[0] if user_raw_refs else (assistant_raw_refs[0] if assistant_raw_refs else ""),
        )
        self._augment_memory_headers(memory_headers, entries, written)
        self._ombre_add_tool_headers(memory_headers, entries, [], written)
        if self._should_run_reflection(written):
            self._schedule_reflection(
                session_id=session_id,
                user_text=user_text,
                assistant_text=visible_text,
                user_raw_refs=user_raw_refs,
                assistant_raw_refs=assistant_raw_refs,
                recalled=recalled,
            )
        if as_stream:
            return self._ombre_synthetic_stream_response(upstream_response, visible_text, memory_headers)
        if visible_text != assistant_text:
            return self._proxy_chat_response_with_text(
                upstream_response,
                visible_text,
                extra_headers=memory_headers,
            )
        return self._proxy_response(upstream_response, extra_headers=memory_headers)

    def _synthetic_stream_response(
        self,
        upstream_response: httpx.Response,
        assistant_text: str,
        extra_headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        body: dict[str, Any] = {}
        try:
            body = upstream_response.json()
        except Exception:
            body = {}
        response_id = str(body.get("id") or f"chatcmpl-ombre-{int(time.time())}")
        model = str(body.get("model") or getattr(self, "public_model", "ombre"))
        created = int(body.get("created") or time.time()) if isinstance(body, dict) else int(time.time())
        choices = body.get("choices") if isinstance(body, dict) else []
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        finish_reason = str(choice.get("finish_reason") or ("tool_calls" if message.get("tool_calls") else "stop"))

        async def stream_body():
            role_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(role_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            reasoning_delta = {
                key: deepcopy(message[key])
                for key in ("reasoning_content", "reasoning", "reasoning_details")
                if message.get(key) not in (None, "", [], {})
            }
            if reasoning_delta:
                reasoning_chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": reasoning_delta, "finish_reason": None}],
                }
                yield f"data: {json.dumps(reasoning_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            native_tool_delta = {
                key: deepcopy(message[key])
                for key in ("tool_calls", "function_call")
                if message.get(key) not in (None, "", [], {})
            }
            if native_tool_delta:
                tool_chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": native_tool_delta, "finish_reason": None}],
                }
                yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            text = assistant_text or ""
            step = 64
            for idx in range(0, len(text), step):
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": text[idx:idx + step]}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            final_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            }
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        hop_by_hop_or_body_headers = {
            "content-length",
            "transfer-encoding",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "upgrade",
            "content-type",
            "content-encoding",
            "content-md5",
            "accept-ranges",
            "content-range",
            "etag",
        }
        headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in hop_by_hop_or_body_headers
        }
        headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        if extra_headers:
            headers.update(extra_headers)
        return StreamingResponse(
            stream_body(),
            status_code=upstream_response.status_code,
            media_type="text/event-stream",
            headers=headers,
        )

    async def _chat_completions_with_internal_tools(self, request) -> Response:
        auth = self._authorize(request)
        if auth is not None:
            return auth
        if not self.upstream_chat_url or not self.upstream_api_key:
            return JSONResponse(
                {"error": {"message": "Upstream model is not configured", "type": "server_error"}},
                status_code=503,
            )

        session_id = (request.headers.get("X-Ombre-Session-Id") or self.default_session_id).strip()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error": {"message": "Request body must be valid JSON", "type": "invalid_request_error"}},
                status_code=400,
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            return JSONResponse(
                {"error": {"message": "messages must be an array", "type": "invalid_request_error"}},
                status_code=400,
            )

        user_text, user_raw_refs, client_timezone = await self._capture_user_turn(
            request,
            payload.get("messages", []),
            session_id,
        )
        recall_mode = str(request.headers.get("X-Ombre-Recall-Mode") or "").strip().lower()
        recall_injected = recall_mode == "injected"
        if recall_injected:
            recalled = {"memories": [], "injection_text": "", "mode": "injected"}
        else:
            recall_context = self._recall_context_text(payload.get("messages", []))
            recalled = await self.memory_gateway.recall({
                "current_text": user_text,
                "recent_context": recall_context,
                "max_results": self.recall_max_results,
                "keyword_limit": self.keyword_limit,
                "semantic_limit": self.semantic_limit,
                "track_usage": True,
            })
        memory_headers = self._memory_debug_headers(recalled)
        if recall_injected:
            memory_headers["X-Zeta-Recall-Mode"] = "injected"
        self._log_recall(session_id, recalled)
        remember_recall = getattr(self, "_remember_recall_debug", None)
        if callable(remember_recall):
            remember_recall(session_id=session_id, user_text=user_text, recalled=recalled)
        injected_text = self._build_gateway_system_text(recalled)
        system_prompt = self._read_system_prompt()
        forward_payload = self._prepare_forward_payload(
            payload,
            injected_text,
            system_prompt,
            client_timezone=client_timezone,
        )
        memory_headers.update(self._system_prompt_debug_headers(forward_payload, system_prompt))
        wants_stream = forward_payload.get("stream") is True
        if wants_stream:
            return await self._ombre_stream_upstream(
                forward_payload,
                session_id=session_id,
                user_text=user_text,
                user_raw_refs=user_raw_refs,
                recalled=recalled,
                memory_headers=memory_headers,
            )

        upstream_started_at = time.monotonic()
        try:
            first_response = await self._forward_upstream(forward_payload)
        except httpx.RequestError as exc:
            if logger:
                logger.warning(
                    "Ombre chat upstream request failed | session=%s elapsed_ms=%s error=%s",
                    session_id,
                    int((time.monotonic() - upstream_started_at) * 1000),
                    exc,
                )
            return self._upstream_request_error(exc)
        if logger:
            logger.info(
                "Ombre chat upstream complete | session=%s status=%s elapsed_ms=%s",
                session_id,
                first_response.status_code,
                int((time.monotonic() - upstream_started_at) * 1000),
            )

        if not 200 <= first_response.status_code < 300:
            return self._proxy_response(first_response, extra_headers=memory_headers)

        assistant_text = self._assistant_text_from_response(first_response)
        visible_text, entries = self._extract_zeta_memory_request(assistant_text)
        if entries and self._ombre_has_read_tools(entries) and not visible_text.strip():
            tool_results, written = await self._ombre_run_tool_entries(
                session_id=session_id,
                entries=entries,
                default_raw_ref=user_raw_refs[0] if user_raw_refs else "",
            )
            self._ombre_add_tool_headers(memory_headers, entries, tool_results, written)
            follow_payload = self._ombre_tool_result_payload(forward_payload, tool_results)
            follow_payload["stream"] = False
            try:
                follow_response = await self._forward_upstream(follow_payload)
            except httpx.RequestError as exc:
                return self._upstream_request_error(exc)
            if not 200 <= follow_response.status_code < 300:
                return self._proxy_response(follow_response, extra_headers=memory_headers)
            return await self._ombre_finalize_nonstream_response(
                follow_response,
                session_id=session_id,
                user_text=user_text,
                user_raw_refs=user_raw_refs,
                recalled=recalled,
                memory_headers=memory_headers,
                as_stream=False,
            )

        return await self._ombre_finalize_nonstream_response(
            first_response,
            session_id=session_id,
            user_text=user_text,
            user_raw_refs=user_raw_refs,
            recalled=recalled,
            memory_headers=memory_headers,
            as_stream=False,
        )

    cls = module.ZetaOpenAIGateway
    module._HiddenMemoryStreamFilter = OmbreHiddenMemoryStreamFilter
    cls._hidden_memory_instruction = _hidden_memory_instruction
    cls._parse_ombre_tool_json = _parse_ombre_tool_json
    cls._normalize_ombre_tool_call = _normalize_ombre_tool_call
    cls._extract_zeta_memory_request = _extract_zeta_memory_request
    cls._write_zeta_memory_requests = _write_zeta_memory_requests
    cls._ombre_profile_path = _profile_path
    cls._ombre_empty_companion_profile = _empty_companion_profile
    cls._ombre_read_companion_profile = _read_companion_profile
    cls._ombre_write_companion_profile = _write_companion_profile
    cls._ombre_append_profile_patch = _append_profile_patch
    cls._ombre_compact_memory_result = _compact_memory_result
    cls._ombre_run_memory_search = _run_memory_search
    cls._ombre_diary_matches = _diary_matches
    cls._ombre_search_diaries = _search_diaries
    cls._ombre_read_profile_tool = _read_profile_tool
    cls._ombre_run_read_tool = _run_ombre_read_tool
    cls._ombre_has_read_tools = _has_read_tools
    cls._ombre_run_tool_entries = _run_ombre_tool_entries
    cls._ombre_tool_result_payload = _tool_result_payload
    cls._ombre_add_tool_headers = _add_ombre_tool_headers
    cls._ombre_stream_upstream = _stream_upstream_with_internal_tools
    cls._ombre_finalize_nonstream_response = _finalize_nonstream_response
    cls._ombre_synthetic_stream_response = _synthetic_stream_response
    cls.chat_completions = _chat_completions_with_internal_tools

    module._ombre_internal_tools_patched = True
