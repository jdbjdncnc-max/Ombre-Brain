import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from firebase_push import FirebasePushService


class FirebasePushServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = FirebasePushService(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    async def test_registration_is_persisted_without_exposing_credentials(self):
        registered = await self.service.register(
            "fid-one",
            device_id="android-one",
            platform="android",
        )

        self.assertTrue(registered["registered"])
        self.assertEqual(registered["registeredDevices"], 1)
        reloaded = FirebasePushService(Path(self.temporary.name))
        self.assertEqual(reloaded.status()["registeredDevices"], 1)

        removed = await reloaded.unregister("fid-one")
        self.assertTrue(removed["removed"])
        self.assertEqual(removed["registeredDevices"], 0)

    async def test_push_removes_invalid_installation_ids(self):
        await self.service.register("fid-valid")
        await self.service.register("fid-expired")
        result = {
            "configured": True,
            "sent": 1,
            "failed": 1,
            "invalidFids": ["fid-expired"],
        }

        with patch.object(self.service, "_credentials_configured", return_value=True), patch.object(
            self.service,
            "_send_sync",
            return_value=result,
        ) as send:
            response = await self.service.send_proactive([{
                "id": "proactive-one",
                "text": "突然想找你。",
                "timezone": "Asia/Taipei",
            }])

        self.assertEqual(response["sent"], 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1], ["fid-expired", "fid-valid"])
        self.assertEqual(self.service.status()["registeredDevices"], 1)

    async def test_missing_server_credentials_keeps_messages_for_polling_fallback(self):
        await self.service.register("fid-one")
        with patch.object(self.service, "_credentials_configured", return_value=False):
            response = await self.service.send_proactive([{"id": "p1", "text": "找你"}])

        self.assertFalse(response["configured"])
        self.assertEqual(response["sent"], 0)
        self.assertEqual(response["failed"], 1)

    def test_legacy_b64_environment_name_is_recognized(self):
        with patch.dict("os.environ", {"OMBRE_FIREBASE_SERVICE_ACCOUNT_B64": "encoded"}, clear=True):
            self.assertTrue(self.service._credentials_configured())

    def test_proactive_push_contains_lock_screen_notification_and_dedup_tag(self):
        captured = []

        class Value:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class Messaging:
            Notification = Value
            AndroidNotification = Value
            AndroidConfig = Value
            MulticastMessage = Value

            @staticmethod
            def send_each_for_multicast(message):
                captured.append(message)
                return SimpleNamespace(success_count=1, failure_count=0, responses=[])

        with patch.object(self.service, "_ensure_firebase", return_value=(object(), Messaging)):
            result = self.service._send_sync(
                [{"id": "proactive-one", "title": "Zeta", "text": "锁屏也要看到我。"}],
                ["fid-one"],
            )

        self.assertEqual(result["sent"], 1)
        message = captured[0]
        self.assertEqual(message.notification.body, "锁屏也要看到我。")
        self.assertEqual(message.android.priority, "high")
        self.assertEqual(message.android.notification.channel_id, "ombre_proactive")
        self.assertEqual(message.android.notification.tag, "proactive-one")
        self.assertEqual(message.android.notification.visibility, "public")


if __name__ == "__main__":
    unittest.main()
