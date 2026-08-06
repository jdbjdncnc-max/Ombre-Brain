"""Lightweight runtime clock and wake scheduler for the solitude system."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import socket
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .dynamics import advance_emotions, apply_user_return
from .emotion_model import (
    aggregate_buckets,
    default_channels,
    dimensions,
    mood_line,
    normalize_channels,
    public_buckets,
    strongest_drive,
)


logger = logging.getLogger("ombre_brain.solo")
_TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_FIXED_TIMEZONES = {
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
    "Asia/Taipei": timezone(timedelta(hours=8)),
    "Asia/Shanghai": timezone(timedelta(hours=8)),
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_timezone_name(value: Any, fallback: str = "UTC") -> str:
    candidate = str(value or "").strip()
    if not candidate or not _TIMEZONE_RE.fullmatch(candidate):
        candidate = fallback
    try:
        _timezone_info(candidate)
        return candidate
    except (ZoneInfoNotFoundError, ValueError):
        try:
            _timezone_info(fallback)
            return fallback
        except (ZoneInfoNotFoundError, ValueError):
            return "UTC"


def timezone_info(value: Any, fallback: str = "UTC"):
    name = normalize_timezone_name(value, fallback)
    try:
        return _timezone_info(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _timezone_info(name: str):
    if name in _FIXED_TIMEZONES:
        return _FIXED_TIMEZONES[name]
    return ZoneInfo(name)


class SoloService:
    """Runs cheap clock pulses and records when an AI decision becomes due.

    This first-stage service intentionally does not call a model. Later action
    modules can consume ``lastDecision`` and decide whether a model-backed
    action is warranted.
    """

    def __init__(
        self,
        base_dir: str | Path,
        *,
        enabled: bool | None = None,
        pulse_seconds: int | None = None,
        decision_seconds: int | None = None,
        jitter_ratio: float = 0.2,
        timezone_name: str | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.solo_dir = self.base_dir / "solo"
        self.state_path = self.solo_dir / "runtime_state.json"
        self.emotion_path = self.solo_dir / "emotion_state.json"
        self.emotion_events_path = self.solo_dir / "emotion_events.jsonl"
        self.emotion_series_path = self.solo_dir / "emotion_series.jsonl"
        self.lease_path = self.solo_dir / "heartbeat.lock"
        self.enabled = _truthy(os.environ.get("OMBRE_SOLO_ENABLED", "0")) if enabled is None else bool(enabled)
        self.pulse_seconds = pulse_seconds if pulse_seconds is not None else _bounded_int(
            os.environ.get("OMBRE_SOLO_PULSE_SECONDS"), 60, 10, 3600
        )
        self.decision_seconds = decision_seconds if decision_seconds is not None else _bounded_int(
            os.environ.get("OMBRE_SOLO_DECISION_SECONDS"), 180, 30, 86400
        )
        self.jitter_ratio = max(0.0, min(0.5, float(jitter_ratio)))
        configured_timezone = timezone_name or os.environ.get("OMBRE_SOLO_TIMEZONE", "Asia/Taipei")
        self.timezone_name = normalize_timezone_name(configured_timezone, "Asia/Taipei")
        self._rng = rng or random.Random()
        self._owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task | None = None
        self._state_lock = asyncio.Lock()

    @classmethod
    def from_gateway(cls, gateway: Any) -> "SoloService":
        memory_base = getattr(getattr(gateway, "memory_gateway", None), "base_dir", None)
        if memory_base is None:
            memory_base = Path(gateway.config["buckets_dir"]) / "gateway"
        return cls(memory_base)

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": bool(self._task and not self._task.done()),
            "pulseSeconds": self.pulse_seconds,
            "decisionSeconds": self.decision_seconds,
            "timezone": self.timezone_name,
        }

    async def start(self) -> bool:
        if not self.enabled:
            return False
        if self._task and not self._task.done():
            return True
        self.solo_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run(), name="ombre-solo-heartbeat")
        logger.info(
            "Solo service started | pulse=%ss decision=%ss timezone=%s",
            self.pulse_seconds,
            self.decision_seconds,
            self.timezone_name,
        )
        return True

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await self._expire_lease()
        except Exception:
            logger.exception("Unable to expire solo heartbeat lease cleanly")

    async def _run(self) -> None:
        while True:
            try:
                await self.pulse_once(trigger="timer")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Solo heartbeat pulse failed")
            await asyncio.sleep(self._jittered(self.pulse_seconds))

    async def get_state(self) -> dict[str, Any]:
        async with self._state_lock:
            now = datetime.now(timezone.utc)
            runtime = self._read_json(self.state_path) or self._new_state(now)
            emotion = self._read_json(self.emotion_path) or self._new_emotion_state(
                now,
                _parse_time(runtime.get("lastUserMessageAt")) or now,
            )
            runtime["enabled"] = self.enabled
            runtime["running"] = bool(self._task and not self._task.done())
            runtime["config"] = {
                "pulseSeconds": self.pulse_seconds,
                "decisionSeconds": self.decision_seconds,
                "timezone": self.timezone_name,
            }
            return self._public_state(runtime, emotion)

    async def note_user_message(self, *, sent_at: Any = None, timezone_name: Any = None) -> None:
        if not self.enabled:
            return
        now = datetime.now(timezone.utc)
        sent = _parse_time(sent_at) or now
        user_timezone = normalize_timezone_name(timezone_name, self.timezone_name)
        async with self._state_lock:
            runtime = self._read_json(self.state_path) or self._new_state(now)
            emotion = self._advance_emotion_state(runtime, now)
            previous_message = _parse_time(emotion.get("lastUserMessageAt"))
            gaps = [
                float(value)
                for value in emotion.get("messageGapsHours", [])
                if isinstance(value, (int, float)) and 0 < float(value) <= 24 * 30
            ]
            if previous_message and sent > previous_message:
                gaps.append((sent - previous_message).total_seconds() / 3600.0)
            gaps = gaps[-40:]
            expected_gap = max(2.0, min(12.0, median(gaps))) if gaps else float(emotion.get("expectedGapHours") or 4.0)

            channels, deltas = apply_user_return(emotion.get("channels") or {})
            emotion["channels"] = channels
            emotion["lastUserMessageAt"] = _iso(sent)
            emotion["messageGapsHours"] = [round(value, 3) for value in gaps]
            emotion["expectedGapHours"] = round(expected_gap, 3)
            emotion["updatedAt"] = _iso(now)
            self._refresh_emotion_derived(emotion)
            self._record_emotion_event(
                emotion,
                ts=now,
                source="you",
                cause_key="user_returned",
                deltas=deltas,
                reason="你回来了，短期的恼火散掉了一些",
                felt="被回应了，但没把所有情绪一笔勾销",
            )
            self._write_json(self.emotion_path, emotion)

            runtime["lastUserMessageAt"] = _iso(sent)
            runtime["userTimezone"] = user_timezone
            runtime["updatedAt"] = _iso(now)
            self._write_json(self.state_path, runtime)

    async def wake(self, reason: str = "manual") -> dict[str, Any]:
        if not self.enabled:
            return {
                "ok": False,
                "enabled": False,
                "error": "Solitude service is disabled. Set OMBRE_SOLO_ENABLED=1 first.",
            }
        return await self.pulse_once(force_decision=True, trigger=reason or "manual")

    async def pulse_once(
        self,
        *,
        force_decision: bool = False,
        trigger: str = "timer",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "error": "Solitude service is disabled."}

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        async with self._state_lock:
            self.solo_dir.mkdir(parents=True, exist_ok=True)
            if not self._claim_lease(current):
                lease = self._read_json(self.lease_path)
                return {
                    "ok": False,
                    "enabled": True,
                    "reason": "lock_not_owned",
                    "lockOwner": lease.get("owner", "") if isinstance(lease, dict) else "",
                }

            state = self._read_json(self.state_path) or self._new_state(current)
            state["pulseCount"] = int(state.get("pulseCount") or 0) + 1
            state["lastPulseAt"] = _iso(current)
            state["updatedAt"] = _iso(current)
            state["status"] = "running"
            state["lockOwner"] = self._owner
            state["clock"] = self._clock_payload(current, state.get("userTimezone"))

            next_decision = _parse_time(state.get("nextDecisionAt"))
            decision_due = force_decision or next_decision is None or current >= next_decision
            if decision_due:
                wake_id = f"wake_{current.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
                state["decisionCount"] = int(state.get("decisionCount") or 0) + 1
                state["lastDecisionAt"] = _iso(current)
                state["lastWakeId"] = wake_id
                state["lastDecision"] = {
                    "id": wake_id,
                    "at": _iso(current),
                    "trigger": str(trigger or "timer")[:80],
                    "result": "idle",
                    "modelCalled": False,
                }
                state["nextDecisionAt"] = _iso(
                    current + timedelta(seconds=self._jittered(self.decision_seconds))
                )
                logger.info(
                    "Solo decision due | wake=%s trigger=%s result=idle model_called=false",
                    wake_id,
                    trigger,
                )

            emotion = self._advance_emotion_state(state, current)
            self._write_json(self.state_path, state)
            return {
                "ok": True,
                "enabled": True,
                "decisionDue": decision_due,
                "state": self._public_state(state, emotion),
            }

    def _new_state(self, now: datetime) -> dict[str, Any]:
        return {
            "version": 1,
            "enabled": self.enabled,
            "status": "idle",
            "updatedAt": _iso(now),
            "lastPulseAt": "",
            "lastDecisionAt": "",
            "nextDecisionAt": "",
            "lastUserMessageAt": "",
            "userTimezone": self.timezone_name,
            "pulseCount": 0,
            "decisionCount": 0,
            "lastWakeId": "",
            "lastDecision": None,
            "clock": self._clock_payload(now, self.timezone_name),
            "lockOwner": "",
        }

    def _new_emotion_state(self, now: datetime, last_user_message_at: datetime) -> dict[str, Any]:
        state: dict[str, Any] = {
            "version": 2,
            "updatedAt": _iso(now),
            "channels": default_channels(),
            "habituation": {},
            "dimensions": {},
            "buckets": {},
            "mode": "idle",
            "drive": "",
            "moodLine": "",
            "sensitivity": 1.0,
            "lastUserMessageAt": _iso(last_user_message_at),
            "expectedGapHours": 4.0,
            "messageGapsHours": [],
            "lastAbsenceCauseAt": "",
            "lastSeriesAt": "",
            "budget": {
                "date": now.date().isoformat(),
                "llmCalls": 0,
                "fetches": 0,
                "mcpCalls": 0,
                "proactive": 0,
            },
            "lockOwner": "",
        }
        self._refresh_emotion_derived(state)
        return state

    def _advance_emotion_state(self, runtime: dict[str, Any], now: datetime) -> dict[str, Any]:
        last_runtime_message = _parse_time(runtime.get("lastUserMessageAt"))
        emotion = self._read_json(self.emotion_path) or self._new_emotion_state(
            now,
            last_runtime_message or now,
        )
        updated_at = _parse_time(emotion.get("updatedAt")) or now
        last_user_message = _parse_time(emotion.get("lastUserMessageAt")) or last_runtime_message
        timezone_name = normalize_timezone_name(runtime.get("userTimezone"), self.timezone_name)
        timezone_value = timezone_info(timezone_name, self.timezone_name)
        channels, absence_deltas = advance_emotions(
            emotion.get("channels") or {},
            start=min(updated_at, now),
            end=now,
            last_user_message_at=last_user_message,
            expected_gap_hours=float(emotion.get("expectedGapHours") or 4.0),
            busy_factor=0.0,
            sulk_gain=1.0,
            localize=lambda value: value.astimezone(timezone_value),
        )
        emotion["channels"] = channels
        emotion["updatedAt"] = _iso(now)
        emotion["lastUserMessageAt"] = _iso(last_user_message) if last_user_message else ""
        emotion["lockOwner"] = str(runtime.get("lockOwner") or "")
        self._refresh_emotion_derived(emotion)

        last_cause_at = _parse_time(emotion.get("lastAbsenceCauseAt"))
        significant = any(abs(value) >= 0.25 for value in absence_deltas.values())
        cause_due = last_cause_at is None or now - last_cause_at >= timedelta(minutes=30)
        if last_user_message and significant and cause_due:
            hours = max(0.0, (now - last_user_message).total_seconds() / 3600.0)
            self._record_emotion_event(
                emotion,
                ts=now,
                source="you",
                cause_key="absence",
                deltas=absence_deltas,
                reason=self._absence_reason(hours),
                felt="想念和空落在慢慢累积",
            )
            emotion["lastAbsenceCauseAt"] = _iso(now)

        self._record_hourly_series(emotion, now)
        self._write_json(self.emotion_path, emotion)
        return emotion

    def _refresh_emotion_derived(self, emotion: dict[str, Any]) -> None:
        channels = normalize_channels(emotion.get("channels") or {})
        bucket_values = aggregate_buckets(channels)
        current_dimensions = dimensions(channels)
        drive = strongest_drive(channels, current_dimensions)
        emotion["channels"] = {key: round(value, 4) for key, value in channels.items()}
        emotion["buckets"] = {key: round(value, 2) for key, value in bucket_values.items()}
        emotion["dimensions"] = current_dimensions
        emotion["drive"] = drive["label"]
        emotion["moodLine"] = mood_line(bucket_values, drive)
        negative_load = max(bucket_values.get("cross", 0.0), bucket_values.get("low", 0.0))
        emotion["sensitivity"] = round(1.0 + 0.4 * negative_load / 100.0, 3)

    def _public_state(self, runtime: dict[str, Any], emotion: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(runtime)
        channels = normalize_channels(emotion.get("channels") or {})
        current_dimensions = dimensions(channels)
        drive = strongest_drive(channels, current_dimensions)
        events = self._read_recent_events(120)
        result.update({
            "updatedAt": str(emotion.get("updatedAt") or runtime.get("updatedAt") or ""),
            "mode": {"key": "idle", "label": "安静待着"},
            "drive": drive,
            "moodLine": str(emotion.get("moodLine") or mood_line(aggregate_buckets(channels), drive)),
            "buckets": public_buckets(channels, events),
            "dimensions": current_dimensions,
            "sensitivity": float(emotion.get("sensitivity") or 1.0),
            "lastUserMessageAt": str(emotion.get("lastUserMessageAt") or runtime.get("lastUserMessageAt") or ""),
            "expectedGapHours": float(emotion.get("expectedGapHours") or 4.0),
            "budget": deepcopy(emotion.get("budget") or {}),
        })
        return result

    def _record_emotion_event(
        self,
        emotion: dict[str, Any],
        *,
        ts: datetime,
        source: str,
        cause_key: str,
        deltas: dict[str, float],
        reason: str,
        felt: str,
    ) -> None:
        clean_deltas = {
            key: round(max(-15.0, min(15.0, float(value))), 3)
            for key, value in deltas.items()
            if key in (emotion.get("channels") or {}) and abs(float(value)) >= 0.01
        }
        if not clean_deltas:
            return
        self._append_jsonl(self.emotion_events_path, {
            "ts": _iso(ts),
            "source": source,
            "causeKey": cause_key,
            "deltas": clean_deltas,
            "reason": str(reason)[:120],
            "felt": str(felt)[:120],
            "activityId": "",
        })

    def _record_hourly_series(self, emotion: dict[str, Any], now: datetime) -> None:
        last_series = _parse_time(emotion.get("lastSeriesAt"))
        if last_series and now - last_series < timedelta(hours=1):
            return
        self._append_jsonl(self.emotion_series_path, {
            "ts": _iso(now),
            "buckets": deepcopy(emotion.get("buckets") or {}),
        })
        emotion["lastSeriesAt"] = _iso(now)

    def _read_recent_events(self, limit: int) -> list[dict[str, Any]]:
        try:
            lines = self.emotion_events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in reversed(lines[-max(1, limit):]):
            try:
                value = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                events.append(value)
        return events

    @staticmethod
    def _absence_reason(hours: float) -> str:
        if hours < 1:
            return f"你有 {max(1, round(hours * 60))} 分钟没有说话"
        if hours < 24:
            return f"你有 {hours:.1f} 小时没有说话"
        return f"你有 {hours / 24.0:.1f} 天没有说话"

    def _clock_payload(self, now: datetime, timezone_name: Any) -> dict[str, str]:
        name = normalize_timezone_name(timezone_name, self.timezone_name)
        local = now.astimezone(timezone_info(name, self.timezone_name))
        return {
            "utc": _iso(now),
            "local": local.isoformat(timespec="seconds"),
            "timezone": name,
        }

    def _jittered(self, seconds: int) -> float:
        if self.jitter_ratio <= 0:
            return float(seconds)
        factor = self._rng.uniform(1.0 - self.jitter_ratio, 1.0 + self.jitter_ratio)
        return max(1.0, float(seconds) * factor)

    def _claim_lease(self, now: datetime) -> bool:
        lease = self._read_json(self.lease_path)
        if lease and lease.get("owner") != self._owner:
            expires_at = _parse_time(lease.get("expiresAt"))
            if expires_at and expires_at > now:
                return False

        lease_seconds = max(60, int(self.pulse_seconds * 3))
        self._write_json(
            self.lease_path,
            {
                "owner": self._owner,
                "updatedAt": _iso(now),
                "expiresAt": _iso(now + timedelta(seconds=lease_seconds)),
            },
        )
        confirmed = self._read_json(self.lease_path)
        return confirmed.get("owner") == self._owner

    async def _expire_lease(self) -> None:
        if not self.enabled or not self.lease_path.exists():
            return
        async with self._state_lock:
            lease = self._read_json(self.lease_path)
            if lease.get("owner") != self._owner:
                return
            now = datetime.now(timezone.utc)
            lease["updatedAt"] = _iso(now)
            lease["expiresAt"] = _iso(now)
            self._write_json(self.lease_path, lease)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{self._owner.replace(':', '_')}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _append_jsonl(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            previous = path.read_text(encoding="utf-8")
        except OSError:
            previous = ""
        temporary = path.with_name(f".{path.name}.{self._owner.replace(':', '_')}.tmp")
        temporary.write_text(
            previous + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
