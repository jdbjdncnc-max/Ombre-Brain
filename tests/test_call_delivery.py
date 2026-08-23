from __future__ import annotations

from datetime import datetime, timezone

from call_delivery import CallDeliveryStore, FirebaseCallPush


def test_device_registration_is_private_and_invite_lifecycle_is_persisted(tmp_path):
    store = CallDeliveryStore(tmp_path)
    token = "fcm_" + "x" * 80

    device = store.register_device(token, app_version="1.0")
    assert device["registered"] is True
    assert token not in str(store.status())
    assert store.device_tokens() == [token]

    invite = store.create_invite(
        "想听听你的声音",
        source="solitude",
        session_id="zeta-main",
        now=datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc),
    )
    assert invite["state"] == "pending"
    assert invite["ringable"] is False

    answered = store.respond(invite["id"], "answer")
    assert answered is not None
    assert answered["state"] == "answered"
    assert store.pending_invite(invite["id"]) is None


def test_firebase_wrapper_fails_closed_without_credentials(monkeypatch):
    for key in (
        "OMBRE_FIREBASE_SERVICE_ACCOUNT_B64",
        "OMBRE_FIREBASE_SERVICE_ACCOUNT_JSON",
        "OMBRE_FIREBASE_PROJECT_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(key, raising=False)

    push = FirebaseCallPush()
    result = push.send_invite(["fcm_" + "y" * 80], {"id": "call_1"})
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert result["error"] == "Firebase service account is not configured"

    proactive = push.send_proactive(
        ["fcm_" + "y" * 80],
        [{"id": "proactive_1", "title": "Zeta", "text": "后台消息"}],
    )
    assert proactive["sent"] == 0
    assert proactive["failed"] == 1
    assert proactive["error"] == "Firebase service account is not configured"
