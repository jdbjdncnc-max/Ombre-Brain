import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
