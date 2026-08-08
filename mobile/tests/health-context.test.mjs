import assert from "node:assert/strict";
import test from "node:test";

import {
  buildHealthMessageContext,
  buildHealthSparklinePath,
  formatHealthDurationHours,
  normalizeHealthSnapshot
} from "../../companion_frontend/health.js";

const snapshot = {
  schemaVersion: 1,
  status: "ready",
  source: "android_health_connect",
  capturedAt: "2026-08-08T02:00:00Z",
  latestDataAt: "2026-08-08T01:58:00Z",
  continuous: {
    heartRate: {
      available: true,
      unit: "bpm",
      windowHours: 24,
      latestValue: 78,
      averageValue: 72,
      minValue: 61,
      maxValue: 103,
      sampleCount: 3,
      lastUpdatedAt: "2026-08-08T01:58:00Z",
      series: [
        { at: "2026-08-08T01:00:00Z", value: 70 },
        { at: "2026-08-08T01:30:00Z", value: 73 },
        { at: "2026-08-08T01:58:00Z", value: 78 },
        { at: "bad-time", value: 999 }
      ]
    }
  },
  discrete: {
    steps: {
      available: true,
      value: 5432,
      unit: "steps",
      windowHours: 24,
      lastUpdatedAt: "2026-08-08T01:45:00Z"
    },
    sleep: {
      available: true,
      value: 435,
      unit: "minutes",
      windowHours: 48,
      startAt: "2026-08-07T15:00:00Z",
      endAt: "2026-08-07T22:15:00Z",
      stages: { deep: 90, light: 210, rem: 105, awake: 30 }
    }
  }
};

test("normalizes health data and builds a local sparkline", () => {
  const normalized = normalizeHealthSnapshot(snapshot);

  assert.equal(normalized.continuous.heartRate.series.length, 3);
  assert.equal(normalized.discrete.steps.value, 5432);
  assert.match(buildHealthSparklinePath(normalized.continuous.heartRate.series), /^M/);
  assert.match(buildHealthSparklinePath(normalized.continuous.heartRate.series), / L/);
  assert.equal(formatHealthDurationHours(435), "7.3");
});

test("message context keeps summaries but excludes the raw curve", () => {
  const context = buildHealthMessageContext(snapshot);

  assert.equal(context.continuous.heartRate.latestValue, 78);
  assert.equal(context.continuous.heartRate.windowHours, 24);
  assert.equal(context.continuous.heartRate.trend.direction, "rising");
  assert.equal(context.discrete.steps.value, 5432);
  assert.equal(context.discrete.sleep.value, 435);
  assert.equal("series" in context.continuous.heartRate, false);
});

test("empty health snapshots are not attached to messages", () => {
  assert.equal(buildHealthMessageContext({ status: "ready" }), null);
  assert.equal(normalizeHealthSnapshot({ status: "permission_required" }), null);
});
