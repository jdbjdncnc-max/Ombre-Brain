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
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
            state = self._read_json(self.state_path)
            if not state:
                state = self._new_state(datetime.now(timezone.utc))
            state["enabled"] = self.enabled
            state["running"] = bool(self._task and not self._task.done())
            state["config"] = {
                "pulseSeconds": self.pulse_seconds,
                "decisionSeconds": self.decision_seconds,
                "timezone": self.timezone_name,
            }
            return deepcopy(state)

    async def note_user_message(self, *, sent_at: Any = None, timezone_name: Any = None) -> None:
        if not self.enabled:
            return
        now = datetime.now(timezone.utc)
        sent = _parse_time(sent_at) or now
        user_timezone = normalize_timezone_name(timezone_name, self.timezone_name)
        async with self._state_lock:
            state = self._read_json(self.state_path) or self._new_state(now)
            state["lastUserMessageAt"] = _iso(sent)
            state["userTimezone"] = user_timezone
            state["updatedAt"] = _iso(now)
            self._write_json(self.state_path, state)

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

            self._write_json(self.state_path, state)
            return {
                "ok": True,
                "enabled": True,
                "decisionDue": decision_due,
                "state": deepcopy(state),
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
