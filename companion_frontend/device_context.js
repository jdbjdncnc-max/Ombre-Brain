const MAX_USAGE_ENTRIES = 3;

export function normalizeDeviceContextSnapshot(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const permissions = normalizePermissions(value.permissions);
  const location = normalizeLocation(value.location);
  const appUsage = normalizeAppUsage(value.appUsage);
  if (!location && !appUsage && !permissions) {
    return null;
  }
  return {
    schemaVersion: 1,
    status: cleanText(value.status, 24) || "partial",
    source: cleanText(value.source, 64) || "android_system_sensors",
    capturedAt: isoTimestamp(value.capturedAt) || new Date().toISOString(),
    ...(permissions ? { permissions } : {}),
    ...(location ? { location } : {}),
    ...(appUsage ? { appUsage } : {})
  };
}

export function buildDeviceMessageContext(value) {
  const snapshot = normalizeDeviceContextSnapshot(value);
  if (!snapshot) {
    return null;
  }
  const location = snapshot.location?.status === "ready" ? snapshot.location : null;
  const appUsage = snapshot.appUsage?.status === "ready" ? snapshot.appUsage : null;
  if (!location && !appUsage) {
    return null;
  }
  return {
    schemaVersion: 1,
    source: snapshot.source,
    capturedAt: snapshot.capturedAt,
    ...(location ? { location } : {}),
    ...(appUsage ? { appUsage } : {})
  };
}

export function deviceLocationLabel(value) {
  const location = normalizeLocation(value);
  if (!location || location.status !== "ready") {
    return "等待精确定位";
  }
  const address = location.address || {};
  return address.thoroughfare
    || address.formatted
    || [address.locality, address.subLocality].filter(Boolean).join(" ")
    || `${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`;
}

export function deviceUsageLabel(value) {
  const usage = normalizeAppUsage(value);
  if (!usage || usage.status !== "ready") {
    return "等待今日应用时长";
  }
  return usage.entries.length
    ? usage.entries.map((entry) => `${entry.appName} ${formatDeviceUsageMinutes(entry.foregroundMinutes)}`).join(" · ")
    : "今天暂无应用记录";
}

export function deviceCurrentScreenLabel(value) {
  const app = normalizeScreenApp(value);
  return app ? app.appName : "暂无记录";
}

export function formatDeviceUsageMinutes(value) {
  const minutes = Math.max(0, Math.round(Number(value) || 0));
  if (minutes < 60) {
    return `${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

function normalizePermissions(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  return {
    preciseLocation: permissionValue(value.preciseLocation),
    usageAccess: permissionValue(value.usageAccess)
  };
}

function normalizeLocation(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const status = cleanText(value.status, 32) || "unavailable";
  if (status !== "ready") {
    return { status, reason: cleanText(value.reason, 64) };
  }
  const latitude = boundedNumber(value.latitude, -90, 90);
  const longitude = boundedNumber(value.longitude, -180, 180);
  if (latitude === null || longitude === null) {
    return null;
  }
  const accuracyMeters = boundedNumber(value.accuracyMeters, 0, 100000);
  const altitudeMeters = boundedNumber(value.altitudeMeters, -12000, 100000);
  const address = normalizeAddress(value.address);
  return {
    status: "ready",
    source: cleanText(value.source, 64) || "android_location_manager",
    latitude,
    longitude,
    ...(accuracyMeters !== null ? { accuracyMeters: Math.round(accuracyMeters * 10) / 10 } : {}),
    ...(altitudeMeters !== null ? { altitudeMeters: Math.round(altitudeMeters * 10) / 10 } : {}),
    provider: cleanText(value.provider, 32),
    observedAt: isoTimestamp(value.observedAt),
    ...(address ? { address } : {})
  };
}

function normalizeAddress(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const result = {};
  for (const [key, limit] of Object.entries({
    formatted: 220,
    country: 80,
    adminArea: 80,
    subAdminArea: 80,
    locality: 80,
    subLocality: 80,
    thoroughfare: 100,
    subThoroughfare: 40,
    featureName: 100
  })) {
    const text = cleanText(value[key], limit);
    if (text) {
      result[key] = text;
    }
  }
  return Object.keys(result).length ? result : null;
}

function normalizeAppUsage(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const status = cleanText(value.status, 32) || "unavailable";
  if (status !== "ready") {
    return { status, reason: cleanText(value.reason, 64) };
  }
  const entries = Array.isArray(value.entries)
    ? value.entries.map(normalizeUsageEntry).filter(Boolean).slice(0, MAX_USAGE_ENTRIES)
    : [];
  const currentScreenApp = normalizeScreenApp(value.currentScreenApp);
  const totalForegroundMinutes = boundedNumber(value.totalForegroundMinutes, 0, 1440);
  return {
    status: "ready",
    source: cleanText(value.source, 64) || "android_usage_stats",
    date: /^\d{4}-\d{2}-\d{2}$/.test(String(value.date || "")) ? String(value.date) : "",
    startAt: isoTimestamp(value.startAt),
    endAt: isoTimestamp(value.endAt),
    totalForegroundMinutes: totalForegroundMinutes === null ? 0 : Math.round(totalForegroundMinutes),
    ...(currentScreenApp ? { currentScreenApp } : {}),
    entries
  };
}

function normalizeScreenApp(value) {
  if (!value || typeof value !== "object" || cleanText(value.status, 24) !== "ready") {
    return null;
  }
  const appName = cleanText(value.appName, 80);
  const packageName = cleanText(value.packageName, 180).replace(/[^A-Za-z0-9._-]/g, "");
  if (!appName && !packageName) {
    return null;
  }
  return {
    status: "ready",
    mode: "latest_external_before_ombre",
    appName: appName || packageName,
    packageName,
    observedAt: isoTimestamp(value.observedAt)
  };
}

function normalizeUsageEntry(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const appName = cleanText(value.appName, 80);
  const packageName = cleanText(value.packageName, 180).replace(/[^A-Za-z0-9._-]/g, "");
  const foregroundMinutes = boundedNumber(value.foregroundMinutes, 0, 1440);
  if ((!appName && !packageName) || foregroundMinutes === null) {
    return null;
  }
  return {
    appName: appName || packageName,
    packageName,
    foregroundMinutes: Math.round(foregroundMinutes),
    lastUsedAt: isoTimestamp(value.lastUsedAt)
  };
}

function permissionValue(value) {
  return String(value || "").trim().toLowerCase() === "granted" ? "granted" : "required";
}

function boundedNumber(value, minimum, maximum) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum ? number : null;
}

function isoTimestamp(value) {
  const date = new Date(String(value || ""));
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function cleanText(value, limit) {
  return String(value || "").replace(/[\r\n\t]+/g, " ").trim().slice(0, limit);
}
