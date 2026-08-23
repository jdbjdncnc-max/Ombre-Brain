from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("ombre_brain.call_delivery")


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


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


class CallDeliveryStore:
    """Persist private device registrations and short-lived incoming-call invites."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "call_delivery.json"
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            changed = self._expire_invites(data, datetime.now(timezone.utc))
            if changed:
                self._write(data)
            devices = [item for item in data["devices"] if self._device_is_fresh(item)]
            pending = [item for item in data["invites"] if item.get("state") == "pending"]
            return {
                "registered_devices": len(devices),
                "pending_invites": len(pending),
                "latest_invite": self._public_invite(pending[-1]) if pending else None,
            }

    def register_device(
        self,
        token: Any,
        *,
        platform: Any = "android",
        app_version: Any = "",
    ) -> dict[str, Any]:
        clean_token = _text(token, 4096)
        if len(clean_token) < 20:
            raise ValueError("FCM registration token is missing or too short")
        now = datetime.now(timezone.utc)
        device_id = hashlib.sha256(clean_token.encode("utf-8")).hexdigest()[:20]
        with self._lock:
            data = self._load()
            devices = [
                item for item in data["devices"]
                if str(item.get("id") or "") != device_id and str(item.get("token") or "") != clean_token
            ]
            devices.append({
                "id": device_id,
                "token": clean_token,
                "platform": _text(platform, 24) or "android",
                "appVersion": _text(app_version, 48),
                "registeredAt": _iso(now),
                "lastSeenAt": _iso(now),
            })
            data["devices"] = devices[-8:]
            self._write(data)
        return {"id": device_id, "platform": _text(platform, 24) or "android", "registered": True}

    def remove_device_token(self, token: Any) -> bool:
        clean_token = _text(token, 4096)
        if not clean_token:
            return False
        with self._lock:
            data = self._load()
            remaining = [item for item in data["devices"] if str(item.get("token") or "") != clean_token]
            changed = len(remaining) != len(data["devices"])
            if changed:
                data["devices"] = remaining
                self._write(data)
            return changed

    def device_tokens(self) -> list[str]:
        with self._lock:
            data = self._load()
            fresh = [item for item in data["devices"] if self._device_is_fresh(item)]
            if len(fresh) != len(data["devices"]):
                data["devices"] = fresh
                self._write(data)
            return [str(item.get("token") or "") for item in fresh if str(item.get("token") or "")]

    def create_invite(
        self,
        reason: Any,
        *,
        source: Any = "manual",
        session_id: Any = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        invite = {
            "id": f"call_{uuid.uuid4().hex[:20]}",
            "state": "pending",
            "reason": _text(reason, 100) or "突然有点想听听你的声音",
            "source": _text(source, 32) or "manual",
            "sessionId": _text(session_id, 160),
            "createdAt": _iso(current),
            "ringUntil": _iso(current + timedelta(seconds=90)),
            "expiresAt": _iso(current + timedelta(hours=2)),
            "push": {"sent": 0, "failed": 0, "at": ""},
        }
        with self._lock:
            data = self._load()
            self._expire_invites(data, current)
            data["invites"].append(invite)
            data["invites"] = data["invites"][-100:]
            self._write(data)
        return self._public_invite(invite)

    def pending_invite(self, invite_id: Any = "") -> dict[str, Any] | None:
        wanted = _text(invite_id, 80)
        current = datetime.now(timezone.utc)
        with self._lock:
            data = self._load()
            changed = self._expire_invites(data, current)
            candidates = [item for item in data["invites"] if item.get("state") == "pending"]
            if wanted:
                candidates = [item for item in candidates if str(item.get("id") or "") == wanted]
            if changed:
                self._write(data)
            return self._public_invite(candidates[-1]) if candidates else None

    def respond(self, invite_id: Any, action: Any, *, note: Any = "") -> dict[str, Any] | None:
        wanted = _text(invite_id, 80)
        normalized_action = _text(action, 20).lower()
        state = {"answer": "answered", "decline": "declined", "missed": "missed"}.get(normalized_action)
        if not wanted or state is None:
            raise ValueError("action must be answer, decline, or missed")
        current = datetime.now(timezone.utc)
        with self._lock:
            data = self._load()
            self._expire_invites(data, current)
            matched = None
            for item in data["invites"]:
                if str(item.get("id") or "") == wanted:
                    matched = item
                    if item.get("state") == "pending" or state == "answered":
                        item["state"] = state
                        item["respondedAt"] = _iso(current)
                        item["note"] = _text(note, 120)
                    break
            if matched is None:
                return None
            self._write(data)
            return self._public_invite(matched)

    def mark_push(self, invite_id: Any, *, sent: int, failed: int, error: Any = "") -> None:
        wanted = _text(invite_id, 80)
        with self._lock:
            data = self._load()
            for item in data["invites"]:
                if str(item.get("id") or "") != wanted:
                    continue
                item["push"] = {
                    "sent": max(0, int(sent)),
                    "failed": max(0, int(failed)),
                    "at": _iso(datetime.now(timezone.utc)),
                    "error": _text(error, 240),
                }
                self._write(data)
                return

    @staticmethod
    def _device_is_fresh(item: dict[str, Any]) -> bool:
        seen = _parse_time(item.get("lastSeenAt") or item.get("registeredAt"))
        return bool(seen and datetime.now(timezone.utc) - seen <= timedelta(days=60))

    @staticmethod
    def _public_invite(item: dict[str, Any]) -> dict[str, Any]:
        current = datetime.now(timezone.utc)
        ring_until = _parse_time(item.get("ringUntil"))
        return {
            "id": str(item.get("id") or ""),
            "state": str(item.get("state") or ""),
            "reason": str(item.get("reason") or ""),
            "source": str(item.get("source") or ""),
            "sessionId": str(item.get("sessionId") or ""),
            "createdAt": str(item.get("createdAt") or ""),
            "ringUntil": str(item.get("ringUntil") or ""),
            "expiresAt": str(item.get("expiresAt") or ""),
            "ringable": bool(ring_until and current <= ring_until and item.get("state") == "pending"),
            "push": dict(item.get("push") or {}),
            **({"respondedAt": str(item.get("respondedAt") or "")} if item.get("respondedAt") else {}),
        }

    @staticmethod
    def _expire_invites(data: dict[str, Any], now: datetime) -> bool:
        changed = False
        for item in data["invites"]:
            expires_at = _parse_time(item.get("expiresAt"))
            if item.get("state") == "pending" and expires_at and now >= expires_at:
                item["state"] = "missed"
                item["respondedAt"] = _iso(now)
                changed = True
        return changed

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            value = {}
        raw_devices = value.get("devices") if isinstance(value, dict) else []
        raw_invites = value.get("invites") if isinstance(value, dict) else []
        return {
            "version": 1,
            "devices": [dict(item) for item in (raw_devices or []) if isinstance(item, dict)],
            "invites": [dict(item) for item in (raw_invites or []) if isinstance(item, dict)],
        }

    def _write(self, value: dict[str, Any]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class FirebaseCallPush:
    """Lazy Firebase Admin wrapper; missing credentials never break the chat gateway."""

    def __init__(self) -> None:
        self._credentials_b64 = (
            os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_B64", "").strip()
            or os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_BASE64", "").strip()
        )
        self._credentials_json = os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        self._project_id = os.environ.get("OMBRE_FIREBASE_PROJECT_ID", "").strip()
        self._adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        self._app: Any = None
        self._messaging: Any = None
        self._error = ""
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._credentials_b64 or self._credentials_json or self._adc_path)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "ready": bool(self._app),
            "last_error": self._error,
        }

    def send_invite(self, tokens: list[str], invite: dict[str, Any]) -> dict[str, Any]:
        unique_tokens = list(dict.fromkeys(str(value or "").strip() for value in tokens if str(value or "").strip()))
        if not unique_tokens:
            return {"sent": 0, "failed": 0, "invalid_tokens": [], "error": "no_registered_device"}
        if not self._initialize():
            return {
                "sent": 0,
                "failed": len(unique_tokens),
                "invalid_tokens": [],
                "error": self._error or "firebase_not_configured",
            }

        sent = 0
        failures: list[str] = []
        invalid_tokens: list[str] = []
        data = {
            "type": "call_invite",
            "inviteId": str(invite.get("id") or ""),
            "reason": str(invite.get("reason") or "")[:100],
            "caller": "Zeta",
            "createdAt": str(invite.get("createdAt") or ""),
            "ringUntil": str(invite.get("ringUntil") or ""),
            "expiresAt": str(invite.get("expiresAt") or ""),
        }
        for token in unique_tokens:
            try:
                message = self._messaging.Message(
                    data=data,
                    token=token,
                    android=self._messaging.AndroidConfig(
                        priority="high",
                        ttl=timedelta(seconds=120),
                        collapse_key=f"ombre_call_{data['inviteId']}",
                    ),
                )
                self._messaging.send(message, app=self._app)
                sent += 1
            except Exception as exc:
                failures.append(exc.__class__.__name__)
                if exc.__class__.__name__ in {
                    "UnregisteredError",
                    "SenderIdMismatchError",
                    "InvalidArgumentError",
                }:
                    invalid_tokens.append(token)
        failed = len(unique_tokens) - sent
        error = ",".join(sorted(set(failures)))[:240]
        self._error = error
        return {"sent": sent, "failed": failed, "invalid_tokens": invalid_tokens, "error": error}

    def send_proactive(self, tokens: list[str], items: list[dict[str, Any]]) -> dict[str, Any]:
        unique_tokens = list(dict.fromkeys(str(value or "").strip() for value in tokens if str(value or "").strip()))
        clean_items = [dict(item) for item in items if isinstance(item, dict)]
        if not unique_tokens:
            return {"sent": 0, "failed": 0, "invalid_tokens": [], "error": "no_registered_device"}
        if not clean_items:
            return {"sent": 0, "failed": 0, "invalid_tokens": [], "error": ""}
        if not self._initialize():
            return {
                "sent": 0,
                "failed": len(unique_tokens) * len(clean_items),
                "invalid_tokens": [],
                "error": self._error or "firebase_not_configured",
            }

        sent = 0
        failures: list[str] = []
        invalid_tokens: list[str] = []
        for item in clean_items:
            data = {
                "kind": "ombre_proactive",
                "id": _text(item.get("id"), 100),
                "title": _text(item.get("title"), 60) or "Entangle",
                "text": _text(item.get("text"), 1200),
                "ts": _text(item.get("ts"), 80),
                "timezone": _text(item.get("timezone"), 80),
            }
            if not data["id"] or not data["text"]:
                continue
            for token in unique_tokens:
                try:
                    message = self._messaging.Message(
                        data=data,
                        notification=self._messaging.Notification(
                            title=data["title"],
                            body=data["text"],
                        ),
                        token=token,
                        android=self._messaging.AndroidConfig(
                            priority="high",
                            ttl=timedelta(days=1),
                            collapse_key=f"ombre_proactive_{data['id']}",
                            notification=self._messaging.AndroidNotification(
                                channel_id="ombre_proactive_messages",
                                tag=data["id"],
                            ),
                        ),
                    )
                    self._messaging.send(message, app=self._app)
                    sent += 1
                except Exception as exc:
                    name = exc.__class__.__name__
                    failures.append(name)
                    if name in {"UnregisteredError", "SenderIdMismatchError", "InvalidArgumentError"}:
                        invalid_tokens.append(token)
        failed = len(unique_tokens) * len(clean_items) - sent
        error = ",".join(sorted(set(failures)))[:240]
        self._error = error
        return {
            "configured": self.configured,
            "sent": sent,
            "failed": failed,
            "invalid_tokens": list(dict.fromkeys(invalid_tokens)),
            "error": error,
        }

    def _initialize(self) -> bool:
        if self._app is not None:
            return True
        if not self.configured:
            self._error = "Firebase service account is not configured"
            return False
        with self._lock:
            if self._app is not None:
                return True
            try:
                import firebase_admin
                from firebase_admin import credentials, messaging

                options = {"projectId": self._project_id} if self._project_id else None
                try:
                    self._app = firebase_admin.get_app("ombre-call-push")
                except ValueError:
                    if self._credentials_b64 or self._credentials_json:
                        raw = self._credentials_json
                        if self._credentials_b64:
                            raw = base64.b64decode(self._credentials_b64).decode("utf-8")
                        info = json.loads(raw)
                        credential = credentials.Certificate(info)
                        self._app = firebase_admin.initialize_app(
                            credential,
                            options=options,
                            name="ombre-call-push",
                        )
                    else:
                        self._app = firebase_admin.initialize_app(
                            options=options,
                            name="ombre-call-push",
                        )
                self._messaging = messaging
                self._error = ""
                return True
            except Exception as exc:
                self._error = f"{exc.__class__.__name__}: {_text(exc, 180)}"
                logger.warning("Firebase call push initialization failed: %s", self._error)
                return False
