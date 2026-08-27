package io.github.jdbjdncncmax.ombrebrain;

import android.app.AppOpsManager;
import android.app.usage.UsageEvents;
import android.app.usage.UsageStatsManager;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.location.Address;
import android.location.Geocoder;
import android.location.Location;
import android.location.LocationManager;
import android.os.Build;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;

import java.text.SimpleDateFormat;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

final class DeviceContextReader {
    private static final int MAX_USAGE_ENTRIES = 3;
    private static final int MAX_ATTENTION_ENTRIES = 8;
    private static final long ATTENTION_SESSION_BREAK_MS = 10 * 60 * 1000L;
    private static final long ATTENTION_ROLLING_WINDOW_MS = 90 * 60 * 1000L;
    private static final long ATTENTION_SESSION_THRESHOLD_MS = 30 * 60 * 1000L;
    private static final long ATTENTION_ROLLING_THRESHOLD_MS = 60 * 60 * 1000L;
    private static final Set<String> ENTERTAINMENT_PACKAGES = new HashSet<>(Arrays.asList(
        "com.twitter.android",
        "com.zhihu.android",
        "com.xingin.xhs",
        "tv.danmaku.bili",
        "com.bilibili.app.in",
        "com.android.chrome",
        "com.google.android.youtube",
        "com.ss.android.ugc.aweme",
        "com.zhiliaoapp.musically",
        "com.ss.android.ugc.trill",
        "com.smile.gifmaker",
        "com.instagram.android",
        "com.facebook.katana",
        "com.reddit.frontpage",
        "com.netflix.mediaclient",
        "com.tencent.qqlive",
        "com.youku.phone",
        "com.qiyi.video"
    ));
    private static final long LOCATION_FRESH_MS = 30 * 1000L;
    private static final long LOCATION_TIMEOUT_MS = 12 * 1000L;
    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();

    interface Callback {
        void onSuccess(JSObject snapshot);
        void onError(Throwable error);
    }

    private DeviceContextReader() {}

    static JSObject readUsageSnapshot(Context context) {
        boolean usageAccessGranted = hasUsageAccess(context);
        JSObject usage = usageAccessGranted
            ? readTodayUsage(context.getApplicationContext())
            : permissionRequired("usage_access");
        return buildSnapshot(
            unavailable("background_location_not_requested"),
            usage,
            false,
            usageAccessGranted
        );
    }

    static boolean hasUsageAccess(Context context) {
        AppOpsManager manager = (AppOpsManager) context.getSystemService(Context.APP_OPS_SERVICE);
        if (manager == null) {
            return false;
        }
        int mode;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            mode = manager.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.getPackageName()
            );
        } else {
            mode = manager.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.getPackageName()
            );
        }
        return mode == AppOpsManager.MODE_ALLOWED;
    }

    static void read(
        Context context,
        boolean preciseLocationGranted,
        boolean usageAccessGranted,
        Callback callback
    ) {
        Context appContext = context.getApplicationContext();
        EXECUTOR.execute(() -> {
            try {
                JSObject usage = usageAccessGranted
                    ? readTodayUsage(appContext)
                    : permissionRequired("usage_access");
                if (!preciseLocationGranted) {
                    callback.onSuccess(buildSnapshot(
                        permissionRequired("precise_location"),
                        usage,
                        false,
                        usageAccessGranted
                    ));
                    return;
                }
                readLocation(appContext, usage, usageAccessGranted, callback);
            } catch (Throwable error) {
                callback.onError(error);
            }
        });
    }

    private static void readLocation(
        Context context,
        JSObject usage,
        boolean usageAccessGranted,
        Callback callback
    ) {
        LocationManager manager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
        if (manager == null) {
            callback.onSuccess(buildSnapshot(
                unavailable("location_service_unavailable"),
                usage,
                true,
                usageAccessGranted
            ));
            return;
        }

        Location fallback = bestLastKnown(manager);
        if (isFresh(fallback)) {
            finishLocation(context, fallback, usage, usageAccessGranted, callback);
            return;
        }

        String provider = currentProvider(manager);
        if (provider.isEmpty() || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            finishLocation(context, fallback, usage, usageAccessGranted, callback);
            return;
        }

        AtomicBoolean finished = new AtomicBoolean(false);
        CancellationSignal cancellation = new CancellationSignal();
        Handler handler = new Handler(Looper.getMainLooper());
        Runnable timeout = () -> {
            cancellation.cancel();
            if (finished.compareAndSet(false, true)) {
                finishLocation(context, fallback, usage, usageAccessGranted, callback);
            }
        };
        handler.postDelayed(timeout, LOCATION_TIMEOUT_MS);
        try {
            manager.getCurrentLocation(provider, cancellation, EXECUTOR, location -> {
                if (!finished.compareAndSet(false, true)) {
                    return;
                }
                handler.removeCallbacks(timeout);
                finishLocation(
                    context,
                    betterLocation(location, fallback),
                    usage,
                    usageAccessGranted,
                    callback
                );
            });
        } catch (SecurityException | IllegalArgumentException error) {
            handler.removeCallbacks(timeout);
            if (finished.compareAndSet(false, true)) {
                finishLocation(context, fallback, usage, usageAccessGranted, callback);
            }
        }
    }

    private static void finishLocation(
        Context context,
        Location location,
        JSObject usage,
        boolean usageAccessGranted,
        Callback callback
    ) {
        EXECUTOR.execute(() -> {
            try {
                JSObject locationResult = location == null
                    ? unavailable("location_unavailable")
                    : buildLocation(context, location);
                callback.onSuccess(buildSnapshot(locationResult, usage, true, usageAccessGranted));
            } catch (Throwable error) {
                callback.onError(error);
            }
        });
    }

    private static JSObject buildSnapshot(
        JSObject location,
        JSObject usage,
        boolean preciseLocationGranted,
        boolean usageAccessGranted
    ) {
        JSObject permissions = new JSObject();
        permissions.put("preciseLocation", preciseLocationGranted ? "granted" : "required");
        permissions.put("usageAccess", usageAccessGranted ? "granted" : "required");

        JSObject snapshot = new JSObject();
        snapshot.put("schemaVersion", 1);
        snapshot.put("status", preciseLocationGranted && usageAccessGranted ? "ready" : "partial");
        snapshot.put("source", "android_system_sensors");
        snapshot.put("capturedAt", Instant.now().toString());
        snapshot.put("permissions", permissions);
        snapshot.put("location", location);
        snapshot.put("appUsage", usage);
        return snapshot;
    }

    private static JSObject buildLocation(Context context, Location location) {
        JSObject result = new JSObject();
        result.put("status", "ready");
        result.put("source", "android_location_manager");
        result.put("latitude", location.getLatitude());
        result.put("longitude", location.getLongitude());
        result.put("accuracyMeters", Math.max(0.0f, location.getAccuracy()));
        result.put("provider", clean(location.getProvider(), 32));
        result.put("observedAt", Instant.ofEpochMilli(location.getTime()).toString());
        if (location.hasAltitude()) {
            result.put("altitudeMeters", location.getAltitude());
        }

        JSObject address = reverseGeocode(context, location);
        if (address.length() > 0) {
            result.put("address", address);
        }
        return result;
    }

    @SuppressWarnings("deprecation")
    private static JSObject reverseGeocode(Context context, Location location) {
        JSObject result = new JSObject();
        if (!Geocoder.isPresent()) {
            return result;
        }
        try {
            Geocoder geocoder = new Geocoder(context, Locale.SIMPLIFIED_CHINESE);
            List<Address> addresses = geocoder.getFromLocation(
                location.getLatitude(),
                location.getLongitude(),
                1
            );
            if (addresses == null || addresses.isEmpty()) {
                return result;
            }
            Address address = addresses.get(0);
            putText(result, "formatted", address.getAddressLine(0), 220);
            putText(result, "country", address.getCountryName(), 80);
            putText(result, "adminArea", address.getAdminArea(), 80);
            putText(result, "subAdminArea", address.getSubAdminArea(), 80);
            putText(result, "locality", address.getLocality(), 80);
            putText(result, "subLocality", address.getSubLocality(), 80);
            putText(result, "thoroughfare", address.getThoroughfare(), 100);
            putText(result, "subThoroughfare", address.getSubThoroughfare(), 40);
            putText(result, "featureName", address.getFeatureName(), 100);
        } catch (Exception ignored) {
            // Coordinates are still valid even when the system geocoder is temporarily unavailable.
        }
        return result;
    }

    private static JSObject readTodayUsage(Context context) {
        UsageStatsManager manager = (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);
        if (manager == null) {
            return unavailable("usage_service_unavailable");
        }

        Calendar start = Calendar.getInstance();
        start.set(Calendar.HOUR_OF_DAY, 0);
        start.set(Calendar.MINUTE, 0);
        start.set(Calendar.SECOND, 0);
        start.set(Calendar.MILLISECOND, 0);
        long startMillis = start.getTimeInMillis();
        long endMillis = System.currentTimeMillis();
        Map<String, Long> foregroundDurations = new HashMap<>();
        Map<String, Long> lastUsedAt = new HashMap<>();
        List<UsageInterval> usageIntervals = new ArrayList<>();
        String activePackage = "";
        long activeSince = startMillis;
        String currentForegroundPackage = "";
        long currentForegroundAt = 0L;
        // A short lookback establishes which app was already in front at midnight.
        UsageEvents events = manager.queryEvents(startMillis - 12 * 60 * 60 * 1000L, endMillis);
        UsageEvents.Event event = new UsageEvents.Event();
        while (events != null && events.hasNextEvent()) {
            events.getNextEvent(event);
            String packageName = clean(event.getPackageName(), 180);
            if (packageName.isEmpty()) {
                continue;
            }
            long rawTimestamp = event.getTimeStamp();
            long timestamp = Math.max(startMillis, Math.min(endMillis, rawTimestamp));
            if (event.getEventType() == UsageEvents.Event.ACTIVITY_RESUMED) {
                if (!activePackage.isEmpty() && !activePackage.equals(packageName) && timestamp > activeSince) {
                    foregroundDurations.merge(activePackage, timestamp - activeSince, Long::sum);
                    addUsageInterval(usageIntervals, activePackage, activeSince, timestamp);
                }
                activePackage = packageName;
                activeSince = timestamp;
                lastUsedAt.put(packageName, timestamp);
                if (rawTimestamp >= startMillis
                    && !isInfrastructurePackage(packageName)
                    && timestamp >= currentForegroundAt) {
                    currentForegroundPackage = packageName;
                    currentForegroundAt = timestamp;
                }
            } else if (event.getEventType() == UsageEvents.Event.ACTIVITY_PAUSED
                || event.getEventType() == UsageEvents.Event.ACTIVITY_STOPPED) {
                if (packageName.equals(activePackage)) {
                    if (timestamp > activeSince) {
                        foregroundDurations.merge(packageName, timestamp - activeSince, Long::sum);
                        addUsageInterval(usageIntervals, packageName, activeSince, timestamp);
                    }
                    activePackage = "";
                    activeSince = timestamp;
                }
                lastUsedAt.put(packageName, timestamp);
            }
        }
        if (!activePackage.isEmpty() && endMillis > activeSince) {
            foregroundDurations.merge(activePackage, endMillis - activeSince, Long::sum);
            addUsageInterval(usageIntervals, activePackage, activeSince, endMillis);
        }

        List<AppUsageEntry> entries = new ArrayList<>();
        long totalMillis = 0L;
        for (Map.Entry<String, Long> item : foregroundDurations.entrySet()) {
            String packageName = clean(item.getKey(), 180);
            long foregroundMillis = Math.max(0L, item.getValue());
            if (!packageName.isEmpty() && !isInfrastructurePackage(packageName)) {
                totalMillis += foregroundMillis;
            }
            if (packageName.isEmpty()
                || packageName.equals(context.getPackageName())
                || isInfrastructurePackage(packageName)
                || foregroundMillis < 60_000L) {
                continue;
            }
            entries.add(new AppUsageEntry(
                appLabel(context, packageName),
                packageName,
                foregroundMillis,
                Math.max(0L, lastUsedAt.getOrDefault(packageName, 0L))
            ));
        }
        entries.sort(Comparator.comparingLong((AppUsageEntry item) -> item.foregroundMillis).reversed());

        JSArray items = new JSArray();
        for (int index = 0; index < Math.min(MAX_USAGE_ENTRIES, entries.size()); index += 1) {
            AppUsageEntry entry = entries.get(index);
            JSObject item = new JSObject();
            item.put("appName", entry.appName);
            item.put("packageName", entry.packageName);
            item.put("foregroundMinutes", Math.max(1L, Math.round(entry.foregroundMillis / 60_000.0)));
            if (entry.lastUsedAt > 0L) {
                item.put("lastUsedAt", Instant.ofEpochMilli(entry.lastUsedAt).toString());
            }
            items.put(item);
        }

        SimpleDateFormat dateFormat = new SimpleDateFormat("yyyy-MM-dd", Locale.ROOT);
        JSObject result = new JSObject();
        result.put("status", "ready");
        result.put("source", "android_usage_stats");
        result.put("date", dateFormat.format(new Date(startMillis)));
        result.put("startAt", Instant.ofEpochMilli(startMillis).toString());
        result.put("endAt", Instant.ofEpochMilli(endMillis).toString());
        result.put("totalForegroundMinutes", Math.max(0L, Math.round(totalMillis / 60_000.0)));
        if (!currentForegroundPackage.isEmpty()) {
            JSObject currentScreenApp = new JSObject();
            currentScreenApp.put("status", "ready");
            currentScreenApp.put("mode", "current_foreground_app");
            currentScreenApp.put("appName", appLabel(context, currentForegroundPackage));
            currentScreenApp.put("packageName", currentForegroundPackage);
            currentScreenApp.put("observedAt", Instant.ofEpochMilli(currentForegroundAt).toString());
            result.put("currentScreenApp", currentScreenApp);
        }
        result.put("entries", items);
        result.put("attention", buildAttentionSnapshot(
            context,
            usageIntervals,
            activePackage,
            endMillis
        ));
        return result;
    }

    private static void addUsageInterval(
        List<UsageInterval> intervals,
        String packageName,
        long startMillis,
        long endMillis
    ) {
        if (!isEntertainmentPackage(packageName) || endMillis <= startMillis) {
            return;
        }
        intervals.add(new UsageInterval(packageName, startMillis, endMillis));
    }

    private static JSObject buildAttentionSnapshot(
        Context context,
        List<UsageInterval> intervals,
        String activePackage,
        long nowMillis
    ) {
        JSObject result = new JSObject();
        result.put("status", "ready");
        result.put("source", "android_usage_events");
        result.put("sessionBreakMinutes", ATTENTION_SESSION_BREAK_MS / 60_000L);
        result.put("sessionThresholdMinutes", ATTENTION_SESSION_THRESHOLD_MS / 60_000L);
        result.put("rollingWindowMinutes", ATTENTION_ROLLING_WINDOW_MS / 60_000L);
        result.put("rollingThresholdMinutes", ATTENTION_ROLLING_THRESHOLD_MS / 60_000L);

        intervals.sort(Comparator.comparingLong(item -> item.startMillis));
        long rollingStart = nowMillis - ATTENTION_ROLLING_WINDOW_MS;
        long rollingMillis = 0L;
        for (UsageInterval interval : intervals) {
            rollingMillis += overlapMillis(interval, rollingStart, nowMillis);
        }

        int sessionStartIndex = intervals.size();
        if (!intervals.isEmpty()) {
            sessionStartIndex = intervals.size() - 1;
            while (sessionStartIndex > 0) {
                UsageInterval current = intervals.get(sessionStartIndex);
                UsageInterval previous = intervals.get(sessionStartIndex - 1);
                if (current.startMillis - previous.endMillis > ATTENTION_SESSION_BREAK_MS) {
                    break;
                }
                sessionStartIndex -= 1;
            }
        }

        long sessionMillis = 0L;
        long sessionStartAt = 0L;
        Map<String, Long> sessionByPackage = new HashMap<>();
        for (int index = sessionStartIndex; index < intervals.size(); index += 1) {
            UsageInterval interval = intervals.get(index);
            if (sessionStartAt == 0L) sessionStartAt = interval.startMillis;
            long duration = Math.max(0L, interval.endMillis - interval.startMillis);
            sessionMillis += duration;
            sessionByPackage.merge(interval.packageName, duration, Long::sum);
        }

        boolean activeEntertainment = isEntertainmentPackage(activePackage);
        String thresholdLevel = "";
        if (activeEntertainment && rollingMillis >= ATTENTION_ROLLING_THRESHOLD_MS) {
            thresholdLevel = "rolling_60";
        } else if (activeEntertainment && sessionMillis >= ATTENTION_SESSION_THRESHOLD_MS) {
            thresholdLevel = "session_30";
        }

        result.put("active", activeEntertainment);
        result.put("sessionMinutes", Math.max(0L, Math.round(sessionMillis / 60_000.0)));
        result.put("rollingMinutes", Math.max(0L, Math.round(rollingMillis / 60_000.0)));
        if (sessionStartAt > 0L) {
            result.put("sessionStartedAt", Instant.ofEpochMilli(sessionStartAt).toString());
        }
        if (activeEntertainment) {
            result.put("currentApp", appLabel(context, activePackage));
            result.put("currentPackage", activePackage);
        }
        if (!thresholdLevel.isEmpty() && sessionStartAt > 0L) {
            result.put("thresholdLevel", thresholdLevel);
            result.put("shouldNotify", true);
            result.put("eventId", "attention_" + sessionStartAt + "_" + thresholdLevel);
        } else {
            result.put("shouldNotify", false);
        }

        List<AppUsageEntry> entries = new ArrayList<>();
        for (Map.Entry<String, Long> item : sessionByPackage.entrySet()) {
            entries.add(new AppUsageEntry(
                appLabel(context, item.getKey()),
                item.getKey(),
                item.getValue(),
                0L
            ));
        }
        entries.sort(Comparator.comparingLong((AppUsageEntry item) -> item.foregroundMillis).reversed());
        JSArray apps = new JSArray();
        for (int index = 0; index < Math.min(MAX_ATTENTION_ENTRIES, entries.size()); index += 1) {
            AppUsageEntry entry = entries.get(index);
            JSObject item = new JSObject();
            item.put("appName", entry.appName);
            item.put("packageName", entry.packageName);
            item.put("minutes", Math.max(1L, Math.round(entry.foregroundMillis / 60_000.0)));
            apps.put(item);
        }
        result.put("apps", apps);
        return result;
    }

    private static long overlapMillis(UsageInterval interval, long startMillis, long endMillis) {
        return Math.max(0L, Math.min(interval.endMillis, endMillis) - Math.max(interval.startMillis, startMillis));
    }

    private static boolean isEntertainmentPackage(String packageName) {
        return ENTERTAINMENT_PACKAGES.contains(clean(packageName, 180));
    }

    private static Location bestLastKnown(LocationManager manager) {
        Location best = null;
        for (String provider : new String[] {
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER,
            LocationManager.PASSIVE_PROVIDER
        }) {
            try {
                best = betterLocation(manager.getLastKnownLocation(provider), best);
            } catch (SecurityException | IllegalArgumentException ignored) {}
        }
        return best;
    }

    private static Location betterLocation(Location candidate, Location current) {
        if (candidate == null) {
            return current;
        }
        if (current == null) {
            return candidate;
        }
        long timeDelta = candidate.getTime() - current.getTime();
        if (timeDelta > 120_000L) {
            return candidate;
        }
        if (timeDelta < -120_000L) {
            return current;
        }
        return candidate.getAccuracy() <= current.getAccuracy() ? candidate : current;
    }

    private static boolean isFresh(Location location) {
        return location != null
            && System.currentTimeMillis() - location.getTime() <= LOCATION_FRESH_MS
            && location.getAccuracy() <= 120.0f;
    }

    private static String currentProvider(LocationManager manager) {
        try {
            if (manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                return LocationManager.GPS_PROVIDER;
            }
            if (manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                return LocationManager.NETWORK_PROVIDER;
            }
        } catch (Exception ignored) {}
        return "";
    }

    private static String appLabel(Context context, String packageName) {
        try {
            PackageManager packageManager = context.getPackageManager();
            ApplicationInfo info = packageManager.getApplicationInfo(packageName, 0);
            return clean(String.valueOf(packageManager.getApplicationLabel(info)), 80);
        } catch (PackageManager.NameNotFoundException error) {
            return packageName;
        }
    }

    private static boolean isInfrastructurePackage(String packageName) {
        String value = packageName.toLowerCase(Locale.ROOT);
        return value.equals("android")
            || value.contains("systemui")
            || value.contains("launcher")
            || value.equals("com.miui.home")
            || value.contains("inputmethod")
            || value.contains("permissioncontroller")
            || value.contains("packageinstaller")
            || value.contains("securitycenter");
    }

    private static JSObject permissionRequired(String reason) {
        JSObject result = new JSObject();
        result.put("status", "permission_required");
        result.put("reason", reason);
        return result;
    }

    private static JSObject unavailable(String reason) {
        JSObject result = new JSObject();
        result.put("status", "unavailable");
        result.put("reason", reason);
        return result;
    }

    private static void putText(JSObject target, String key, String value, int limit) {
        String cleaned = clean(value, limit);
        if (!cleaned.isEmpty()) {
            target.put(key, cleaned);
        }
    }

    private static String clean(String value, int limit) {
        String cleaned = value == null ? "" : value.replaceAll("[\\r\\n\\t]+", " ").trim();
        return cleaned.length() <= limit ? cleaned : cleaned.substring(0, limit);
    }

    private static final class AppUsageEntry {
        final String appName;
        final String packageName;
        final long foregroundMillis;
        final long lastUsedAt;

        AppUsageEntry(String appName, String packageName, long foregroundMillis, long lastUsedAt) {
            this.appName = appName;
            this.packageName = packageName;
            this.foregroundMillis = foregroundMillis;
            this.lastUsedAt = lastUsedAt;
        }
    }

    private static final class UsageInterval {
        final String packageName;
        final long startMillis;
        final long endMillis;

        UsageInterval(String packageName, long startMillis, long endMillis) {
            this.packageName = packageName;
            this.startMillis = startMillis;
            this.endMillis = endMillis;
        }
    }
}
