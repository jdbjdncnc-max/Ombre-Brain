from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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


def test_device_registration_replaces_rotated_token_for_same_phone(tmp_path):
    store = CallDeliveryStore(tmp_path)
    first = "fcm_first_" + "a" * 70
    second = "fcm_second_" + "b" * 70

    store.register_device(first, device_key="android-phone-1")
    store.register_device(second, device_key="android-phone-1")

    assert store.device_tokens() == [second]


def test_proactive_push_includes_system_notification_for_restricted_android_apps(monkeypatch):
    sent_messages = []

    class FakeMessaging:
        @staticmethod
        def Notification(**kwargs):
            return SimpleNamespace(**kwargs)

        @staticmethod
        def AndroidNotification(**kwargs):
            return SimpleNamespace(**kwargs)

        @staticmethod
        def AndroidConfig(**kwargs):
            return SimpleNamespace(**kwargs)

        @staticmethod
        def Message(**kwargs):
            return SimpleNamespace(**kwargs)

        @staticmethod
        def send(message, *, app):
            sent_messages.append((message, app))

    push = FirebaseCallPush()
    push._app = object()
    push._messaging = FakeMessaging

    result = push.send_proactive(
        ["fcm_" + "z" * 80],
        [{"id": "proactive_1", "title": "Zeta", "text": "后台消息"}],
    )

    assert result["sent"] == 1
    message, app = sent_messages[0]
    assert app is push._app
    assert message.notification.title == "Zeta"
    assert message.notification.body == "后台消息"
    assert message.android.notification.channel_id == "ombre_proactive_messages"
    assert message.android.notification.tag == "proactive_1"
    assert message.data["kind"] == "ombre_proactive"
