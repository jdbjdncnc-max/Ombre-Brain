import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from solo.actions import ACTION_SPECS, action_scores, perform_action
from solo.emotion_model import default_channels
from solo.service import SoloService


class SoloActionTests(unittest.TestCase):
    def test_sulk_and_grievance_make_unsent_writing_more_likely_than_idle(self):
        channels = default_channels()
        channels.update({"want_to_share": 90, "sulk": 85, "grievance": 90, "numb": 5})

        scores = action_scores(channels)

        self.assertGreater(scores["write_unsent"], scores["idle"])

    def test_local_game_keeps_real_self_evidence_and_complete_moves(self):
        result = perform_action(
            ACTION_SPECS["play_game"],
            default_channels(),
            rng=random.Random(8),
        )

        self.assertEqual(result["kind"], "self")
        self.assertEqual(result["evidence"], {"kind": "self"})
        self.assertEqual(result["game"]["name"], "tic-tac-toe")
        self.assertGreaterEqual(len(result["game"]["moves"]), 5)
        self.assertLessEqual(len(result["game"]["moves"]), 9)
        self.assertNotIn("http", json.dumps(result, ensure_ascii=False).lower())


class SoloTimelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = SoloService(
            Path(self.temporary.name),
            enabled=True,
            pulse_seconds=60,
            decision_seconds=180,
            activity_min_seconds=5400,
            jitter_ratio=0,
            timezone_name="Asia/Taipei",
            rng=random.Random(11),
        )
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    async def asyncTearDown(self):
        await self.service.stop()
        self.temporary.cleanup()

    async def test_activity_and_emotion_change_share_the_same_id(self):
        with patch("solo.service.choose_action", return_value=ACTION_SPECS["play_game"]):
            result = await self.service.pulse_once(now=self.now)

        activity = result["state"]["lastActivity"]
        self.assertEqual(activity["type"], "play_game")
        self.assertEqual(activity["evidence"], {"kind": "self"})
        events = self.service._read_jsonl(self.service.emotion_events_path, limit=20)
        series = self.service._read_jsonl(self.service.emotion_series_path, limit=20)
        self.assertTrue(any(item.get("activityId") == activity["id"] for item in events))
        self.assertTrue(any(item.get("activityId") == activity["id"] for item in series))

    async def test_short_wakes_do_not_flood_the_timeline(self):
        await self.service.pulse_once(now=self.now)
        await self.service.pulse_once(now=self.now + timedelta(minutes=60), force_decision=True)
        first_timeline = await self.service.get_timeline(hours=24)
        await self.service.pulse_once(now=self.now + timedelta(minutes=91), force_decision=True)
        second_timeline = await self.service.get_timeline(hours=24)

        self.assertEqual(len(first_timeline["activities"]), 1)
        self.assertEqual(len(second_timeline["activities"]), 2)
        self.assertTrue(any(
            item.get("activityId") == first_timeline["activities"][0]["id"]
            for item in first_timeline["series"]
        ))

    async def test_unsent_and_talking_points_are_returned_with_the_timeline(self):
        with patch("solo.service.choose_action", return_value=ACTION_SPECS["write_unsent"]):
            await self.service.pulse_once(now=self.now)
        with patch("solo.service.choose_action", return_value=ACTION_SPECS["add_talking_point"]):
            await self.service.pulse_once(
                now=self.now + timedelta(hours=2),
                force_decision=True,
            )

        timeline = await self.service.get_timeline(hours=24)

        self.assertEqual(len(timeline["unsent"]), 1)
        self.assertEqual(len(timeline["talkingPoints"]), 1)
        self.assertIn("这句话先留在这里", timeline["unsent"][0]["text"])
        self.assertFalse(timeline["talkingPoints"][0]["used"])


if __name__ == "__main__":
    unittest.main()
