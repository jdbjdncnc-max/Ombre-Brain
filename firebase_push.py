"""Firebase Cloud Messaging delivery for solitude proactive messages."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("ombre_brain.firebase_push")


class FirebasePushService:
    def __init__(self, buckets_dir: str | os.PathLike[str]) -> None:
        gateway_dir = Path(buckets_dir) / "gateway"
        gateway_dir.mkdir(parents=True, exist_ok=True)
        self.devices_path = gateway_dir / "firebase_devices.json"
        self._lock = asyncio.Lock()
        self._firebase_app: Any = None

    def status(self) -> dict[str, Any]:
        devices = self._read_devices()
        return {
            "ok": True,
            "credentialsConfigured": self._credentials_configured(),
            "registeredDevices": len(devices),
        }

    async def register(
        self,
        fid: Any,
        *,
        device_id: Any = "",
        platform: Any = "android",
    ) -> dict[str, Any]:
        clean_fid = self._clean_identifier(fid, 512)
        if not clean_fid:
            raise ValueError("firebase installation id must not be empty")
        clean_device_id = self._clean_identifier(device_id, 160)
        clean_platform = self._clean_identifier(platform, 40) or "android"
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            devices = self._read_devices()
            devices[clean_fid] = {
                "fid": clean_fid,
                "deviceId": clean_device_id,
                "platform": clean_platform,
                "updatedAt": now,
            }
            self._write_devices(devices)
        return {"ok": True, "registered": True, **self.status()}

    async def unregister(self, fid: Any) -> dict[str, Any]:
        clean_fid = self._clean_identifier(fid, 512)
        async with self._lock:
            devices = self._read_devices()
            removed = bool(clean_fid and devices.pop(clean_fid, None))
            self._write_devices(devices)
        return {"ok": True, "removed": removed, **self.status()}

    async def send_proactive(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        clean_items = [deepcopy(item) for item in items if isinstance(item, dict)]
        if not clean_items:
            return {"configured": self._credentials_configured(), "sent": 0, "failed": 0}
        async with self._lock:
            fids = sorted(self._read_devices())
        if not fids:
            return {"configured": self._credentials_configured(), "sent": 0, "failed": 0}
        if not self._credentials_configured():
            return {"configured": False, "sent": 0, "failed": len(fids) * len(clean_items)}

        try:
            result = await asyncio.to_thread(self._send_sync, clean_items, fids)
        except Exception as exc:
            logger.warning("Firebase proactive send failed: %s", exc)
            return {"configured": True, "sent": 0, "failed": len(fids) * len(clean_items)}

        invalid_fids = set(result.pop("invalidFids", []))
        if invalid_fids:
            async with self._lock:
                devices = self._read_devices()
                for fid in invalid_fids:
                    devices.pop(fid, None)
                self._write_devices(devices)
        return result

    def _send_sync(self, items: list[dict[str, Any]], fids: list[str]) -> dict[str, Any]:
        _, messaging = self._ensure_firebase()
        sent = 0
        failed = 0
        invalid: set[str] = set()
        for item in items:
            data = {
                "kind": "ombre_proactive",
                "id": self._clean_text(item.get("id"), 100),
                "title": self._clean_text(item.get("title"), 60) or "Entangle",
                "text": self._clean_text(item.get("text"), 240),
                "ts": self._clean_text(item.get("ts"), 80),
                "timezone": self._clean_text(item.get("timezone"), 80),
            }
            if not data["id"] or not data["text"]:
                continue
            message = messaging.MulticastMessage(
                tokens=fids,
                data=data,
                android=messaging.AndroidConfig(priority="high", ttl=timedelta(days=1)),
            )
            response = messaging.send_each_for_multicast(message)
            sent += int(response.success_count)
            failed += int(response.failure_count)
            for index, send_response in enumerate(response.responses):
                if send_response.success:
                    continue
                name = type(send_response.exception).__name__
                if name in {"UnregisteredError", "NotFoundError"}:
                    invalid.add(fids[index])
        return {
            "configured": True,
            "sent": sent,
            "failed": failed,
            "invalidFids": sorted(invalid),
        }

    def _ensure_firebase(self):
        import firebase_admin
        from firebase_admin import credentials, messaging

        if self._firebase_app is not None:
            return self._firebase_app, messaging
        try:
            self._firebase_app = firebase_admin.get_app()
            return self._firebase_app, messaging
        except ValueError:
            pass

        raw_json = os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        raw_base64 = os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_BASE64", "").strip()
        if raw_base64 and not raw_json:
            raw_json = base64.b64decode(raw_base64).decode("utf-8")
        if raw_json:
            value = json.loads(raw_json)
            if not isinstance(value, dict):
                raise ValueError("OMBRE_FIREBASE_SERVICE_ACCOUNT_JSON must contain a JSON object")
            self._firebase_app = firebase_admin.initialize_app(credentials.Certificate(value))
        else:
            self._firebase_app = firebase_admin.initialize_app()
        return self._firebase_app, messaging

    @staticmethod
    def _credentials_configured() -> bool:
        return bool(
            os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
            or os.environ.get("OMBRE_FIREBASE_SERVICE_ACCOUNT_BASE64", "").strip()
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        )

    def _read_devices(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.devices_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        entries = value.get("devices") if isinstance(value, dict) else None
        if not isinstance(entries, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            fid = self._clean_identifier(item.get("fid"), 512)
            if fid:
                result[fid] = deepcopy(item)
        return result

    def _write_devices(self, devices: dict[str, dict[str, Any]]) -> None:
        payload = {
            "version": 1,
            "devices": sorted(devices.values(), key=lambda item: str(item.get("updatedAt") or "")),
        }
        temporary = self.devices_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.devices_path)

    @staticmethod
    def _clean_identifier(value: Any, limit: int) -> str:
        return re.sub(r"[^A-Za-z0-9._~:-]+", "", str(value or ""))[:limit]

    @staticmethod
    def _clean_text(value: Any, limit: int) -> str:
        return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit]
