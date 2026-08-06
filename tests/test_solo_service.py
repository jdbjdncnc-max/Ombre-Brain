import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from solo.service import SoloService


class SoloServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temporary.name)
        self.service = SoloService(
            self.base_dir,
            enabled=True,
            pulse_seconds=60,
            decision_seconds=180,
            jitter_ratio=0,
            timezone_name="Asia/Taipei",
            rng=random.Random(1),
        )
        self.now = datetime(2026, 8, 6, 13, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        await self.service.stop()
        self.temporary.cleanup()

    async def test_short_pulses_do_not_force_a_decision_every_time(self):
        first = await self.service.pulse_once(now=self.now)
        second = await self.service.pulse_once(now=self.now + timedelta(seconds=60))

        self.assertTrue(first["decisionDue"])
        self.assertFalse(second["decisionDue"])
        state = second["state"]
        self.assertEqual(state["pulseCount"], 2)
        self.assertEqual(state["decisionCount"], 1)
        self.assertFalse(state["lastDecision"]["modelCalled"])
        self.assertEqual(state["clock"]["local"], "2026-08-06T21:01:00+08:00")

    async def test_manual_wake_forces_a_new_unique_decision(self):
        first = await self.service.pulse_once(now=self.now)
        forced = await self.service.pulse_once(
            now=self.now + timedelta(seconds=30),
            force_decision=True,
            trigger="manual-test",
        )

        self.assertTrue(forced["decisionDue"])
        self.assertNotEqual(first["state"]["lastWakeId"], forced["state"]["lastWakeId"])
        self.assertEqual(forced["state"]["decisionCount"], 2)
        self.assertEqual(forced["state"]["lastDecision"]["trigger"], "manual-test")

    async def test_second_instance_cannot_write_while_lease_is_fresh(self):
        await self.service.pulse_once(now=self.now)
        other = SoloService(
            self.base_dir,
            enabled=True,
            pulse_seconds=60,
            decision_seconds=180,
            jitter_ratio=0,
            timezone_name="Asia/Taipei",
        )

        result = await other.pulse_once(now=self.now + timedelta(seconds=10))

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "lock_not_owned")

    async def test_user_message_updates_runtime_state(self):
        await self.service.pulse_once(now=self.now)
        await self.service.note_user_message(
            sent_at="2026-08-06T13:02:03Z",
            timezone_name="Asia/Taipei",
        )

        state = json.loads(self.service.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["lastUserMessageAt"], "2026-08-06T13:02:03Z")
        self.assertEqual(state["userTimezone"], "Asia/Taipei")


if __name__ == "__main__":
    unittest.main()
