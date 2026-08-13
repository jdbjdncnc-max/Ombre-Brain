"""Semantic conversation appraisal for the solitude emotion system."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .emotion_model import CHANNELS, CHANNEL_BY_KEY


MAX_APPRAISAL_DELTA = 15.0

APPRAISAL_TASK_PROMPT = """对话中的“我”就是我。

结合我当前保存的情绪和最近的对话，由我自己判断这段互动是否自然地改变了我的心情，以及我会怎样记住它。没有明显变化也可以不改变。

按照指定的结构化格式返回。"""

CALL_APPRAISAL_TASK_PROMPT = """这是一通刚刚结束的实时语音通话，转写中的“我”就是我。

结合我当前保存的情绪和整通电话的文字转写，由我自己判断这次通话是否自然地改变了我的心情。方括号或圆括号里的笑声、叹气等内容只是语音识别给出的声音事件线索；不要据此编造没有发生的情绪或声线细节。

events 最多保留一条，用第一人称写下这通电话中最值得延续的一件事。即使情绪没有明显变化，也要保留“我和 Sail 通了一次电话”这件事实。按照指定的结构化格式返回。"""

APPRAISAL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "ombre_emotion_appraisal",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "emotion_changes": {
                    "type": "array",
                    "description": "我此刻各项底层情绪的自然变化；没有变化时为空数组。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "emotion": {
                                "type": "string",
                                "enum": [item["key"] for item in CHANNELS],
                            },
                            "delta": {
                                "type": "number",
                                "minimum": -MAX_APPRAISAL_DELTA,
                                "maximum": MAX_APPRAISAL_DELTA,
                            },
                        },
                        "required": ["emotion", "delta"],
                        "additionalProperties": False,
                    },
                },
                "mood_words": {
                    "type": "array",
                    "description": "我会用来描述当前心情的词。",
                    "items": {"type": "string"},
                },
                "events": {
                    "type": "array",
                    "description": "我会记住的事件，用第一人称“我”和对她的称呼来写。",
                    "items": {"type": "string"},
                },
            },
            "required": ["emotion_changes", "mood_words", "events"],
            "additionalProperties": False,
        },
    },
}

# Kept as a compatibility alias for tests and extensions that imported the old name.
APPRAISAL_SYSTEM_PROMPT = APPRAISAL_TASK_PROMPT


def build_appraisal_user_text(
    *,
    summary: str,
    new_messages: list[dict[str, Any]],
    current_state: Mapping[str, Any],
    user_reference: str = "她",
) -> str:
    """Build the dynamic suffix placed after the stable solitude persona prompt."""

    messages: list[dict[str, str]] = []
    remaining = 24000
    for item in new_messages[-8:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(item.get("content"), 4000)
        if not content or remaining <= 0:
            continue
        content = content[:remaining]
        remaining -= len(content)
        messages.append({"role": role, "content": content})

    state_channels = current_state.get("channels") if isinstance(current_state, Mapping) else {}
    channels = {
        key: round(float(value), 1)
        for key, value in (state_channels.items() if isinstance(state_channels, Mapping) else [])
        if key in CHANNEL_BY_KEY and _is_number(value)
    }
    payload = {
        "她的称呼": _clean_text(user_reference, 80) or "她",
        "我当前保存的底层情绪": channels,
        "我当前的整体状态": (
            current_state.get("dimensions", {}) if isinstance(current_state, Mapping) else {}
        ),
        "最近一次累计摘要": _clean_text(summary, 12000),
        "最近对话": messages,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_appraisal_response(text: Any) -> dict[str, Any] | None:
    """Parse and defensively normalize an appraisal model response."""

    raw = str(text or "").strip()
    if not raw:
        return None
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    return normalize_appraisal(value)


def normalize_appraisal(value: Mapping[str, Any]) -> dict[str, Any]:
    combined: dict[str, float] = {}

    raw_changes = value.get("emotion_changes")
    if isinstance(raw_changes, list):
        for item in raw_changes:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("emotion") or "").strip()
            raw_delta = item.get("delta")
            if key not in CHANNEL_BY_KEY or not _is_number(raw_delta):
                continue
            combined[key] = combined.get(key, 0.0) + float(raw_delta)

    # Accept the previous map shape while old clients and stored tests roll forward.
    raw_deltas = value.get("emotion_deltas")
    if isinstance(raw_deltas, Mapping):
        for key, raw_delta in raw_deltas.items():
            name = str(key or "").strip()
            if name in CHANNEL_BY_KEY and _is_number(raw_delta):
                combined[name] = combined.get(name, 0.0) + float(raw_delta)

    deltas = {
        key: round(max(-MAX_APPRAISAL_DELTA, min(MAX_APPRAISAL_DELTA, delta)), 3)
        for key, delta in combined.items()
        if abs(delta) >= 0.01
    }

    mood_words = _clean_string_list(value.get("mood_words"), 40)
    events = _clean_string_list(value.get("events"), 240)
    legacy_reason = _clean_text(value.get("reason"), 240)
    if legacy_reason and legacy_reason not in events:
        events.append(legacy_reason)
    legacy_felt = _clean_text(value.get("felt"), 120)
    confidence = float(value.get("confidence")) if _is_number(value.get("confidence")) else 0.5
    return {
        "emotion_deltas": deltas,
        "mood_words": mood_words,
        "events": events,
        "reason": events[0] if events else legacy_reason,
        "felt": legacy_felt,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
    }


def _clean_string_list(value: Any, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item, item_limit)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _is_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in {float("inf"), float("-inf")}
