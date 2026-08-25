const MAX_HEALTH_SERIES_POINTS = 180;

export function normalizeHealthSnapshot(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (value.status && value.status !== "ready") {
    return null;
  }

  const rawHeartRate = value.continuous?.heartRate || {};
  const rawSteps = value.discrete?.steps || {};
  const rawSleep = value.discrete?.sleep || {};
  const series = normalizeSeries(rawHeartRate.series);
  const latestPoint = series.at(-1);

  return {
    schemaVersion: positiveInteger(value.schemaVersion) || 1,
    status: "ready",
    source: cleanText(value.source, 64) || "android_health_connect",
    capturedAt: isoTimestamp(value.capturedAt) || new Date().toISOString(),
    latestDataAt: isoTimestamp(value.latestDataAt)
      || isoTimestamp(rawHeartRate.lastUpdatedAt)
      || latestPoint?.at
      || "",
    wearableSync: normalizeWearableSync(value.wearableSync),
    continuous: {
      heartRate: {
        available: Boolean(rawHeartRate.available || series.length),
        unit: "bpm",
        windowHours: positiveNumber(rawHeartRate.windowHours) || 24,
        latestValue: healthNumber(rawHeartRate.latestValue) ?? latestPoint?.value ?? null,
        measurementType: cleanText(rawHeartRate.measurementType, 48) || "latest_sample",
        averageValue: healthNumber(rawHeartRate.averageValue),
        minValue: healthNumber(rawHeartRate.minValue),
        maxValue: healthNumber(rawHeartRate.maxValue),
        sampleCount: nonNegativeInteger(rawHeartRate.sampleCount) ?? series.length,
        lastUpdatedAt: isoTimestamp(rawHeartRate.lastUpdatedAt) || latestPoint?.at || "",
        series
      }
    },
    discrete: {
      steps: {
        available: Boolean(rawSteps.available || healthNumber(rawSteps.value) !== null),
        value: healthNumber(rawSteps.value),
        unit: "steps",
        windowHours: positiveNumber(rawSteps.windowHours) || 24,
        windowType: cleanText(rawSteps.windowType, 48) || "rolling_window",
        startAt: isoTimestamp(rawSteps.startAt) || "",
        endAt: isoTimestamp(rawSteps.endAt) || "",
        lastUpdatedAt: isoTimestamp(rawSteps.lastUpdatedAt) || ""
      },
      sleep: {
        available: Boolean(rawSleep.available || healthNumber(rawSleep.value) !== null),
        value: healthNumber(rawSleep.value),
        unit: "minutes",
        windowHours: positiveNumber(rawSleep.windowHours) || 48,
        startAt: isoTimestamp(rawSleep.startAt) || "",
        endAt: isoTimestamp(rawSleep.endAt) || "",
        lastUpdatedAt: isoTimestamp(rawSleep.lastUpdatedAt) || isoTimestamp(rawSleep.endAt) || "",
        durationBasis: cleanText(rawSleep.durationBasis, 64),
        sessionDurationMinutes: healthNumber(rawSleep.sessionDurationMinutes),
        stages: normalizeSleepStages(rawSleep.stages)
      }
    }
  };
}

export function buildHealthMessageContext(snapshot) {
  const normalized = normalizeHealthSnapshot(snapshot);
  if (!normalized) {
    return null;
  }

  const heartRate = normalized.continuous.heartRate;
  const steps = normalized.discrete.steps;
  const sleep = normalized.discrete.sleep;
  const hasHeartRate = heartRate.available && healthNumber(heartRate.latestValue) !== null;
  const hasSteps = steps.available && healthNumber(steps.value) !== null;
  const hasSleep = sleep.available && healthNumber(sleep.value) !== null;
  if (!hasHeartRate && !hasSteps && !hasSleep) {
    return null;
  }

  const latestDataAt = newestTimestamp([
    normalized.latestDataAt,
    heartRate.lastUpdatedAt,
    steps.lastUpdatedAt,
    sleep.lastUpdatedAt
  ]);
  const context = {
    schemaVersion: 1,
    source: normalized.source,
    capturedAt: normalized.capturedAt,
    ...(latestDataAt ? { latestDataAt, dataAgeMinutes: ageMinutes(latestDataAt) } : {}),
    continuous: {},
    discrete: {}
  };

  if (hasHeartRate) {
    const measuredAt = heartRate.lastUpdatedAt;
    context.continuous.heartRate = compactObject({
      latestValue: heartRate.latestValue,
      readingKind: "latest_exact_sample",
      unit: "bpm",
      measuredAt,
      dataAgeMinutes: measuredAt ? ageMinutes(measuredAt) : null,
      trend: heartRateTrend(heartRate.series)
    });
  }
  if (hasSteps) {
    context.discrete.steps = compactObject({
      value: Math.max(0, Math.round(steps.value)),
      unit: "steps",
      period: steps.windowType === "local_calendar_day" ? "today_local_time" : steps.windowType,
      startAt: steps.startAt,
      endAt: steps.endAt,
      lastUpdatedAt: steps.lastUpdatedAt
    });
  }
  if (hasSleep) {
    context.discrete.sleep = compactObject({
      value: Math.max(0, Math.round(sleep.value)),
      unit: "minutes",
      windowHours: sleep.windowHours,
      startAt: sleep.startAt,
      endAt: sleep.endAt,
      durationBasis: sleep.durationBasis,
      stages: sleep.stages
    });
  }

  return context;
}

export function buildHealthSparklinePath(series, width = 180, height = 46, padding = 3) {
  const points = normalizeSeries(series);
  if (!points.length) {
    return "";
  }
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const usableWidth = Math.max(1, width - padding * 2);
  const usableHeight = Math.max(1, height - padding * 2);
  return points.map((point, index) => {
    const x = padding + (points.length === 1 ? usableWidth / 2 : index * usableWidth / (points.length - 1));
    const y = padding + (max - point.value) * usableHeight / range;
    return `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ");
}

export function formatHealthDurationHours(minutes) {
  const value = healthNumber(minutes);
  if (value === null) {
    return "—";
  }
  const hours = Math.max(0, value) / 60;
  return hours >= 10 ? hours.toFixed(0) : hours.toFixed(1).replace(/\.0$/, "");
}

function normalizeSeries(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  const byTime = new Map();
  for (const item of value) {
    const at = isoTimestamp(item?.at || item?.time || item?.timestamp);
    const pointValue = healthNumber(item?.value ?? item?.bpm);
    if (at && pointValue !== null && pointValue > 0 && pointValue < 320) {
      byTime.set(at, { at, value: pointValue });
    }
  }
  const points = [...byTime.values()].sort((left, right) => left.at.localeCompare(right.at));
  if (points.length <= MAX_HEALTH_SERIES_POINTS) {
    return points;
  }
  return Array.from({ length: MAX_HEALTH_SERIES_POINTS }, (_, index) => {
    const sourceIndex = Math.round(index * (points.length - 1) / (MAX_HEALTH_SERIES_POINTS - 1));
    return points[sourceIndex];
  });
}

function normalizeSleepStages(value) {
  if (!value || typeof value !== "object") {
    return {};
  }
  const result = {};
  for (const key of ["light", "deep", "rem", "awake", "sleeping", "unknown"]) {
    const minutes = healthNumber(value[key]);
    if (minutes !== null && minutes >= 0) {
      result[key] = Math.round(minutes);
    }
  }
  return result;
}

function normalizeWearableSync(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  return compactObject({
    available: Boolean(value.available),
    requested: Boolean(value.requested),
    packageName: cleanText(value.packageName, 120),
    note: cleanText(value.note, 180)
  });
}

function heartRateTrend(series) {
  const points = normalizeSeries(series);
  if (points.length < 2) {
    return null;
  }
  const latestTime = new Date(points.at(-1).at).getTime();
  const recent = points.filter((point) => latestTime - new Date(point.at).getTime() <= 60 * 60000);
  const windowPoints = recent.length >= 2 ? recent : points;
  const delta = Math.round(windowPoints.at(-1).value - windowPoints[0].value);
  return {
    direction: delta >= 3 ? "rising" : delta <= -3 ? "falling" : "stable",
    delta,
    windowMinutes: Math.max(1, Math.round(
      (new Date(windowPoints.at(-1).at).getTime() - new Date(windowPoints[0].at).getTime()) / 60000
    ))
  };
}

function newestTimestamp(values) {
  return values
    .map(isoTimestamp)
    .filter(Boolean)
    .sort()
    .at(-1) || "";
}

function ageMinutes(value) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? Math.max(0, Math.round((Date.now() - time) / 60000)) : null;
}

function compactObject(value) {
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => (
      item !== null
      && item !== undefined
      && item !== ""
      && !(typeof item === "object" && !Array.isArray(item) && !Object.keys(item).length)
    ))
  );
}

function isoTimestamp(value) {
  const date = new Date(value || "");
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function cleanText(value, limit) {
  return String(value || "").replace(/[\r\n\t]+/g, " ").trim().slice(0, limit);
}

function healthNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function positiveNumber(value) {
  const number = healthNumber(value);
  return number !== null && number > 0 ? number : null;
}

function positiveInteger(value) {
  const number = positiveNumber(value);
  return number === null ? null : Math.round(number);
}

function nonNegativeInteger(value) {
  const number = healthNumber(value);
  return number !== null && number >= 0 ? Math.round(number) : null;
}
