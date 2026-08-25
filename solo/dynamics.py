"""Cheap, deterministic emotion dynamics for every solitude pulse."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Mapping

from .emotion_model import CHANNELS, CHANNEL_BY_KEY, clamp, normalize_channels


ABSENCE_RATES = {
    "longing": 26.0,
    "emptiness": 16.0,
    "loneliness": 18.0,
    "grievance": 12.0,
    "irritation": 14.0,
    "want_to_share": 6.0,
}

QUIET_HOURS_START = 2
QUIET_HOURS_END = 10
QUIET_HOURS_ABSENCE_MULTIPLIER = 0.15


def decay(value: Any, baseline: Any, half_life_min: Any, elapsed_min: Any) -> float:
    current = float(value)
    base = float(baseline)
    half_life = float(half_life_min)
    elapsed = max(0.0, float(elapsed_min))
    if half_life <= 0 or elapsed <= 0:
        return current
    factor = 0.5 ** (elapsed / half_life)
    return base + (current - base) * factor


def absence_pressure(hours_since_message: Any, expected_gap_hours: Any) -> float:
    hours = max(0.0, float(hours_since_message))
    expected = clamp(expected_gap_hours, 2.0, 12.0)
    tau = max(3.0, expected)
    return clamp(1.0 - math.exp(-((hours / tau) ** 1.4)), 0.0, 1.0)


def is_quiet_hours(local_time: datetime) -> bool:
    hour = local_time.hour + local_time.minute / 60.0
    return QUIET_HOURS_START <= hour < QUIET_HOURS_END


def advance_emotions(
    channels: Mapping[str, Any],
    *,
    start: datetime,
    end: datetime,
    last_user_message_at: datetime | None,
    expected_gap_hours: float = 4.0,
    busy_factor: float = 0.0,
    sulk_gain: float = 1.0,
    localize=lambda value: value,
) -> tuple[dict[str, float], dict[str, float]]:
    """Advance state in stable ten-minute slices so restart catch-up is equivalent."""
    values = normalize_channels(channels)
    totals = {key: 0.0 for key in ABSENCE_RATES}
    if end <= start:
        return values, totals

    busy = clamp(busy_factor, 0.0, 1.0)
    sulk = clamp(sulk_gain, 0.0, 1.5)
    cursor = start
    while cursor < end:
        step_end = min(end, cursor + timedelta(minutes=10))
        elapsed = (step_end - cursor).total_seconds() / 60.0
        for definition in CHANNELS:
            key = definition["key"]
            effective_elapsed = elapsed * (1.3 if abs(values[key] - float(definition["baseline"])) > 40 else 1.0)
            values[key] = clamp(decay(values[key], definition["baseline"], definition["halfLife"], effective_elapsed))

        if last_user_message_at is not None:
            hours = max(0.0, (step_end - last_user_message_at).total_seconds() / 3600.0)
            pressure = absence_pressure(hours, expected_gap_hours)
            # ABSENCE_RATES are hourly rates. The previous ten-minute factor
            # made a full hour roughly six times stronger than intended.
            dt_factor = elapsed / 60.0
            quiet_factor = (
                QUIET_HOURS_ABSENCE_MULTIPLIER
                if is_quiet_hours(localize(step_end))
                else 1.0
            )
            planned: dict[str, float] = {}
            for key, rate in ABSENCE_RATES.items():
                personality = sulk if key in {"grievance", "irritation"} else 1.0
                planned[key] = min(
                    15.0,
                    rate * pressure * dt_factor * personality * quiet_factor,
                )

            # The continuity limit is symmetric: no display bucket may move by
            # more than 35 points per hour, regardless of whether it is pleasant.
            bucket_totals: dict[str, float] = {}
            for key, delta in planned.items():
                bucket = str(CHANNEL_BY_KEY[key]["bucket"])
                bucket_totals[bucket] = bucket_totals.get(bucket, 0.0) + abs(delta)
            bucket_limit = 35.0 * elapsed / 60.0
            for key, delta in planned.items():
                bucket = str(CHANNEL_BY_KEY[key]["bucket"])
                total = bucket_totals.get(bucket, 0.0)
                if total > bucket_limit > 0:
                    delta *= bucket_limit / total
                if key in {"grievance", "irritation"}:
                    delta *= 1.0 - busy
                before = values[key]
                values[key] = clamp(before + delta)
                totals[key] += values[key] - before

        local_hour = localize(step_end).hour
        if 19 <= local_hour < 23:
            for key in ("play_urge", "curiosity"):
                baseline = float(CHANNEL_BY_KEY[key]["baseline"]) * 1.25
                values[key] = clamp(decay(values[key], baseline, CHANNEL_BY_KEY[key]["halfLife"], elapsed))
        elif 3 <= local_hour < 6:
            for key in ("play_urge", "curiosity"):
                baseline = float(CHANNEL_BY_KEY[key]["baseline"]) * 0.5
                values[key] = clamp(decay(values[key], baseline, CHANNEL_BY_KEY[key]["halfLife"], elapsed))
        cursor = step_end

    return values, {key: round(value, 4) for key, value in totals.items() if abs(value) >= 0.0001}


def apply_user_return(channels: Mapping[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    values = normalize_channels(channels)
    before = dict(values)
    values["irritation"] = clamp(values["irritation"] * 0.35)
    values["sulk"] = clamp(values["sulk"] * 0.6)
    for key in ("longing", "emptiness", "loneliness"):
        values[key] = clamp(values[key] * 0.5)
    values["delight"] = clamp(values["delight"] + 12)
    values["content"] = clamp(values["content"] + 8)
    values["grievance"] = clamp(values["grievance"] * 0.95)
    deltas = {
        key: round(values[key] - before[key], 4)
        for key in values
        if abs(values[key] - before[key]) >= 0.0001
    }
    return values, deltas
