import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from solo.dynamics import absence_pressure, advance_emotions, apply_user_return, decay
from solo.emotion_model import (
    aggregate_bucket,
    apply_delta,
    default_channels,
    dimensions,
)
from solo.service import SoloService


class EmotionModelTests(unittest.TestCase):
    def test_decay_reaches_midpoint_after_one_half_life(self):
        self.assertAlmostEqual(decay(90, 10, 60, 60), 50)
        self.assertEqual(decay(90, 10, 60, 0), 90)
        self.assertEqual(decay(90, 10, 0, 60), 90)

    def test_decay_is_symmetric_around_the_same_baseline(self):
        high = decay(95, 50, 120, 30) - 50
        low = 50 - decay(5, 50, 120, 30)
        self.assertAlmostEqual(high, low)

    def test_absence_pressure_matches_curve_and_stays_bounded(self):
        self.assertAlmostEqual(absence_pressure(0, 4), 0)
        self.assertAlmostEqual(absence_pressure(4, 4), 1 - 2.718281828459045**-1, places=5)
        self.assertGreater(absence_pressure(16, 4), 0.99)
        self.assertLessEqual(absence_pressure(1000, 4), 1)

    def test_long_catch_up_matches_repeated_ten_minute_ticks(self):
        start = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)
        last_user = start - timedelta(hours=4)
        one_pass, _ = advance_emotions(
            default_channels(),
            start=start,
            end=start + timedelta(hours=6),
            last_user_message_at=last_user,
        )
        repeated = default_channels()
        cursor = start
        for _ in range(36):
            repeated, _ = advance_emotions(
                repeated,
                start=cursor,
                end=cursor + timedelta(minutes=10),
                last_user_message_at=last_user,
            )
            cursor += timedelta(minutes=10)
        for key in repeated:
            self.assertAlmostEqual(one_pass[key], repeated[key], places=6)

    def test_busy_time_reduces_grievance_but_not_longing(self):
        start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
        last_user = start - timedelta(hours=8)
        normal, _ = advance_emotions(
            default_channels(),
            start=start,
            end=start + timedelta(minutes=10),
            last_user_message_at=last_user,
            busy_factor=0,
        )
        busy, _ = advance_emotions(
            default_channels(),
            start=start,
            end=start + timedelta(minutes=10),
            last_user_message_at=last_user,
            busy_factor=0.8,
        )
        grievance_baseline = default_channels()["grievance"]
        self.assertLessEqual(busy["grievance"] - grievance_baseline, (normal["grievance"] - grievance_baseline) * 0.25)
        self.assertAlmostEqual(busy["longing"], normal["longing"])

    def test_absence_respects_per_bucket_hourly_change_limit(self):
        start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
        channels = default_channels()
        updated, _ = advance_emotions(
            channels,
            start=start,
            end=start + timedelta(hours=1),
            last_user_message_at=start - timedelta(days=2),
        )
        miss_change = sum(updated[key] - channels[key] for key in ("longing", "emptiness", "want_to_share"))
        cross_change = sum(updated[key] - channels[key] for key in ("grievance", "irritation"))
        self.assertLessEqual(miss_change, 35.0)
        self.assertLessEqual(cross_change, 35.0)

    def test_absence_rates_are_hourly_not_reapplied_every_ten_minutes(self):
        start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
        channels = default_channels()
        updated, _ = advance_emotions(
            channels,
            start=start,
            end=start + timedelta(minutes=10),
            last_user_message_at=start - timedelta(days=2),
        )
        self.assertLess(updated["longing"] - channels["longing"], 5.0)

    def test_quiet_hours_slow_absence_growth_without_freezing_decay(self):
        local_timezone = timezone(timedelta(hours=8))
        quiet_start = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)  # 02:00 local
        day_start = datetime(2026, 8, 6, 6, 0, tzinfo=timezone.utc)  # 14:00 local
        channels = default_channels()
        quiet, _ = advance_emotions(
            channels,
            start=quiet_start,
            end=quiet_start + timedelta(hours=1),
            last_user_message_at=quiet_start - timedelta(hours=8),
            localize=lambda value: value.astimezone(local_timezone),
        )
        day, _ = advance_emotions(
            channels,
            start=day_start,
            end=day_start + timedelta(hours=1),
            last_user_message_at=day_start - timedelta(hours=8),
            localize=lambda value: value.astimezone(local_timezone),
        )
        quiet_longing = quiet["longing"] - channels["longing"]
        day_longing = day["longing"] - channels["longing"]
        self.assertGreater(quiet_longing, 0)
        self.assertLess(quiet_longing, day_longing * 0.25)

    def test_user_return_keeps_grievance_but_drops_short_term_irritation(self):
        channels = default_channels()
        channels["irritation"] = 80
        channels["grievance"] = 80
        updated, _ = apply_user_return(channels)
        self.assertEqual(updated["irritation"], 28)
        self.assertEqual(updated["grievance"], 76)

    def test_every_channel_can_reach_one_hundred(self):
        channels = default_channels()
        for _ in range(10):
            channels = apply_delta(channels, "grievance", 15)
        self.assertEqual(channels["grievance"], 100)
        self.assertEqual(apply_delta(channels, "grievance", "not-a-number")["grievance"], 100)

    def test_bucket_formula_preserves_one_strong_channel(self):
        channels = {key: 10 for key in default_channels()}
        channels["delight"] = 90
        self.assertGreater(aggregate_bucket(channels, "joy"), 55)

    def test_connection_can_be_negative(self):
        channels = default_channels()
        channels.update({"grievance": 100, "sulk": 90, "loneliness": 90, "numb": 80})
        self.assertLess(dimensions(channels)["connection"], 0)


class EmotionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = SoloService(
            Path(self.temporary.name),
            enabled=True,
            pulse_seconds=60,
            decision_seconds=180,
            jitter_ratio=0,
            timezone_name="Asia/Taipei",
            rng=random.Random(2),
        )
        self.now = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        await self.service.stop()
        self.temporary.cleanup()

    async def test_state_endpoint_shape_contains_sorted_emotion_panel(self):
        result = await self.service.pulse_once(now=self.now)
        state = result["state"]
        self.assertEqual(len(state["buckets"]), 6)
        self.assertTrue(state["buckets"][0]["primary"])
        self.assertEqual(
            [item["value"] for item in state["buckets"]],
            sorted((item["value"] for item in state["buckets"]), reverse=True),
        )
        self.assertIn("connection", state["dimensions"])
        self.assertIn("label", state["drive"])

    async def test_absence_changes_persist_and_create_a_real_cause(self):
        await self.service.pulse_once(now=self.now)
        result = await self.service.pulse_once(now=self.now + timedelta(hours=12))
        state = result["state"]
        miss = next(item for item in state["buckets"] if item["key"] == "miss")
        self.assertGreater(next(item for item in miss["channels"] if item["key"] == "longing")["value"], 25)
        self.assertTrue(miss["causes"])
        self.assertEqual(miss["causes"][0]["source"], "you")
        self.assertTrue(self.service.emotion_path.exists())
        self.assertTrue(self.service.emotion_events_path.exists())
        self.assertTrue(self.service.emotion_series_path.exists())


if __name__ == "__main__":
    unittest.main()
