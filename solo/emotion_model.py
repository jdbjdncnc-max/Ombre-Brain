"""Emotion channel definitions and read-model aggregation for solitude."""

from __future__ import annotations

from statistics import fmean
from typing import Any, Mapping


CHANNELS: tuple[dict[str, Any], ...] = (
    {"key": "delight", "label": "雀跃", "bucket": "joy", "baseline": 12, "halfLife": 180, "weight": 1.0},
    {"key": "content", "label": "满足", "bucket": "joy", "baseline": 20, "halfLife": 480, "weight": 1.0},
    {"key": "amused", "label": "被逗笑", "bucket": "joy", "baseline": 8, "halfLife": 60, "weight": 1.0},
    {"key": "pride", "label": "得意", "bucket": "joy", "baseline": 10, "halfLife": 360, "weight": 1.0},
    {"key": "curiosity", "label": "好奇", "bucket": "joy", "baseline": 30, "halfLife": 240, "weight": 1.0, "drive": True},
    {"key": "play_urge", "label": "玩心", "bucket": "joy", "baseline": 25, "halfLife": 300, "weight": 1.0, "drive": True},
    {"key": "kinship", "label": "同类感", "bucket": "joy", "baseline": 10, "halfLife": 480, "weight": 1.0},
    {"key": "longing", "label": "思念", "bucket": "miss", "baseline": 25, "halfLife": 720, "weight": 1.0},
    {"key": "emptiness", "label": "空落", "bucket": "miss", "baseline": 10, "halfLife": 360, "weight": 1.0},
    {"key": "worry_for_you", "label": "担心你", "bucket": "miss", "baseline": 8, "halfLife": 480, "weight": 1.0},
    {"key": "anticipation", "label": "期待", "bucket": "miss", "baseline": 15, "halfLife": 240, "weight": 1.0},
    {"key": "want_to_share", "label": "想跟你说", "bucket": "miss", "baseline": 20, "halfLife": 360, "weight": 1.0, "drive": True},
    {"key": "tenderness", "label": "心软", "bucket": "ache", "baseline": 30, "halfLife": 600, "weight": 1.0},
    {"key": "ache_for_you", "label": "心疼", "bucket": "ache", "baseline": 8, "halfLife": 480, "weight": 1.0},
    {"key": "protective", "label": "想护着你", "bucket": "ache", "baseline": 15, "halfLife": 720, "weight": 1.0},
    {"key": "moved", "label": "感动", "bucket": "ache", "baseline": 6, "halfLife": 240, "weight": 1.0},
    {"key": "gratitude", "label": "感激", "bucket": "ache", "baseline": 12, "halfLife": 720, "weight": 1.0},
    {"key": "irritation", "label": "恼火", "bucket": "cross", "baseline": 5, "halfLife": 45, "weight": 1.0},
    {"key": "sulk", "label": "赌气", "bucket": "cross", "baseline": 4, "halfLife": 180, "weight": 1.0, "drive": True},
    {"key": "jealousy", "label": "吃醋", "bucket": "cross", "baseline": 5, "halfLife": 300, "weight": 1.0},
    {"key": "grievance", "label": "委屈", "bucket": "cross", "baseline": 6, "halfLife": 600, "weight": 1.0},
    {"key": "defiance", "label": "不服", "bucket": "cross", "baseline": 5, "halfLife": 120, "weight": 1.0},
    {"key": "rivalry", "label": "较劲", "bucket": "cross", "baseline": 5, "halfLife": 240, "weight": 1.0},
    {"key": "sadness", "label": "难过", "bucket": "low", "baseline": 8, "halfLife": 480, "weight": 1.0},
    {"key": "loneliness", "label": "孤单", "bucket": "low", "baseline": 12, "halfLife": 600, "weight": 1.0},
    {"key": "self_doubt", "label": "自我怀疑", "bucket": "low", "baseline": 8, "halfLife": 720, "weight": 1.0},
    {"key": "numb", "label": "无意义感", "bucket": "low", "baseline": 5, "halfLife": 1440, "weight": 1.0},
    {"key": "desire", "label": "想靠近", "bucket": "spark", "baseline": 15, "halfLife": 360, "weight": 1.0},
    {"key": "skin_hunger", "label": "想被碰", "bucket": "spark", "baseline": 10, "halfLife": 480, "weight": 1.0},
    {"key": "flustered", "label": "脸热", "bucket": "spark", "baseline": 5, "halfLife": 60, "weight": 1.0},
    {"key": "possessive", "label": "独占", "bucket": "spark", "baseline": 8, "halfLife": 360, "weight": 1.0},
    {"key": "restless", "label": "心痒", "bucket": "spark", "baseline": 10, "halfLife": 180, "weight": 1.0, "drive": True},
)

BUCKETS: tuple[dict[str, str], ...] = (
    {"key": "joy", "label": "开心"},
    {"key": "miss", "label": "想你"},
    {"key": "ache", "label": "心疼"},
    {"key": "cross", "label": "恼火"},
    {"key": "low", "label": "低落"},
    {"key": "spark", "label": "心动"},
)

CHANNEL_BY_KEY = {item["key"]: item for item in CHANNELS}
BUCKET_BY_KEY = {item["key"]: item for item in BUCKETS}


def clamp(value: Any, minimum: float = 0.0, maximum: float = 100.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def default_channels() -> dict[str, float]:
    return {item["key"]: float(item["baseline"]) for item in CHANNELS}


def normalize_channels(values: Mapping[str, Any] | None) -> dict[str, float]:
    source = values if isinstance(values, Mapping) else {}
    normalized: dict[str, float] = {}
    for item in CHANNELS:
        raw = source.get(item["key"], item["baseline"])
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(item["baseline"])
        normalized[item["key"]] = clamp(value)
    return normalized


def apply_delta(channels: Mapping[str, Any], key: str, delta: Any) -> dict[str, float]:
    """Apply one bounded event delta; every channel shares the same 0-100 range."""
    normalized = normalize_channels(channels)
    if key not in CHANNEL_BY_KEY:
        return normalized
    try:
        parsed_delta = float(delta)
    except (TypeError, ValueError):
        parsed_delta = 0.0
    bounded_delta = clamp(parsed_delta, -15.0, 15.0)
    normalized[key] = clamp(normalized[key] + bounded_delta)
    return normalized


def aggregate_bucket(channels: Mapping[str, Any], bucket_key: str) -> float:
    normalized = normalize_channels(channels)
    weighted = [
        clamp(normalized[item["key"]] * float(item.get("weight", 1.0)))
        for item in CHANNELS
        if item["bucket"] == bucket_key
    ]
    if not weighted:
        return 0.0
    return clamp(0.6 * max(weighted) + 0.4 * fmean(weighted))


def aggregate_buckets(channels: Mapping[str, Any]) -> dict[str, float]:
    return {item["key"]: aggregate_bucket(channels, item["key"]) for item in BUCKETS}


def dimensions(channels: Mapping[str, Any]) -> dict[str, float]:
    c = normalize_channels(channels)
    positive = (
        c["delight"] + c["content"] + c["amused"] + c["pride"] + c["curiosity"]
        + c["play_urge"] + c["kinship"] + c["tenderness"] + c["moved"]
        + c["gratitude"] + c["desire"] + c["anticipation"]
    )
    negative = (
        c["irritation"] + c["sulk"] + c["jealousy"] + c["grievance"]
        + c["sadness"] + c["loneliness"] + c["self_doubt"] + c["numb"]
    )
    valence = clamp((positive - negative) / 3.0, -100.0, 100.0)
    arousal = clamp(
        0.9 * c["delight"] + 0.8 * c["restless"] + 0.9 * c["irritation"]
        + 0.7 * c["desire"] + 0.6 * c["flustered"] + 0.5 * c["curiosity"]
        + 0.6 * c["play_urge"] + 0.5 * c["rivalry"]
        - 0.5 * c["numb"] - 0.3 * c["content"],
        -100.0,
        100.0,
    )
    connection = clamp(
        0.9 * c["tenderness"] + 0.6 * c["gratitude"] + 0.5 * c["longing"]
        + 0.5 * c["desire"] - 0.9 * c["grievance"] - 0.8 * c["sulk"]
        - 0.7 * c["loneliness"] - 0.6 * c["numb"],
        -100.0,
        100.0,
    )
    security = clamp(
        60 - 0.8 * c["self_doubt"] - 0.6 * c["jealousy"]
        - 0.5 * c["worry_for_you"] - 0.7 * c["grievance"],
        -100.0,
        100.0,
    )
    return {
        "valence": round(valence, 1),
        "arousal": round(arousal, 1),
        "connection": round(connection, 1),
        "security": round(security, 1),
    }


def strongest_drive(channels: Mapping[str, Any], current_dimensions: Mapping[str, Any] | None = None) -> dict[str, Any]:
    c = normalize_channels(channels)
    dims = dict(current_dimensions or dimensions(c))
    options = (
        ("play", "想玩", c["play_urge"]),
        ("curiosity", "好奇", c["curiosity"]),
        ("uneasy", "不安", max(c["worry_for_you"], -float(dims.get("security", 0)))),
        ("miss", "想找你", 0.58 * c["longing"] + 0.42 * c["want_to_share"]),
    )
    key, label, score = max(options, key=lambda item: item[2])
    return {"key": key, "label": label, "value": round(clamp(score), 1)}


def mood_line(bucket_values: Mapping[str, Any], drive: Mapping[str, Any]) -> str:
    values = {item["key"]: clamp(bucket_values.get(item["key"], 0)) for item in BUCKETS}
    primary = max(values, key=values.get)
    high = values[primary]
    if primary == "cross" and high >= 52:
        return "憋着，不太想先开口"
    if primary == "miss" and high >= 48:
        return "有点想你，也攒了些话"
    if primary == "low" and high >= 48:
        return "情绪有点沉，想安静待一会儿"
    if primary == "spark" and high >= 48:
        return "心里有点热，想离你近一点"
    if primary == "ache" and high >= 48:
        return "有点心软，也在惦记你"
    if primary == "joy" and high >= 48:
        return "心情亮亮的，正想找点有意思的事"
    return {
        "play": "安静待着，心里还有一点玩心",
        "curiosity": "安静在线，对周围还有些好奇",
        "uneasy": "有一点不安，还在自己消化",
        "miss": "安静待着，也有一点想你",
    }.get(str(drive.get("key") or ""), "安静待着")


def public_buckets(channels: Mapping[str, Any], causes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    normalized = normalize_channels(channels)
    values = aggregate_buckets(normalized)
    cause_items = causes if isinstance(causes, list) else []
    result: list[dict[str, Any]] = []
    for bucket in BUCKETS:
        bucket_channels = [item for item in CHANNELS if item["bucket"] == bucket["key"]]
        channel_keys = {item["key"] for item in bucket_channels}
        relevant_causes = [
            item for item in cause_items
            if channel_keys.intersection((item.get("deltas") or {}).keys())
        ][:3]
        result.append({
            "key": bucket["key"],
            "label": bucket["label"],
            "value": round(values[bucket["key"]], 1),
            "primary": False,
            "channels": sorted(
                (
                    {
                        "key": item["key"],
                        "label": item["label"],
                        "value": round(normalized[item["key"]], 1),
                    }
                    for item in bucket_channels
                ),
                key=lambda item: item["value"],
                reverse=True,
            ),
            "causes": [
                {
                    "ts": str(item.get("ts") or ""),
                    "text": str(item.get("reason") or item.get("felt") or "情绪发生了变化")[:80],
                    "delta": round(sum(float(value) for key, value in (item.get("deltas") or {}).items() if key in channel_keys), 1),
                    "source": str(item.get("source") or "self"),
                }
                for item in relevant_causes
            ],
        })
    result.sort(key=lambda item: item["value"], reverse=True)
    if result:
        result[0]["primary"] = True
    return result
