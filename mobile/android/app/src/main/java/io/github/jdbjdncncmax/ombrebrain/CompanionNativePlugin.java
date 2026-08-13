package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.Settings;
import android.provider.MediaStore;

import androidx.core.content.ContextCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.net.URI;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

@CapacitorPlugin(
    name = "CompanionNative",
    permissions = {
        @Permission(
            alias = "notifications",
            strings = { Manifest.permission.POST_NOTIFICATIONS }
        ),
        @Permission(
            alias = "healthSteps",
            strings = { "android.permission.health.READ_STEPS" }
        ),
        @Permission(
            alias = "healthHeartRate",
            strings = { "android.permission.health.READ_HEART_RATE" }
        ),
        @Permission(
            alias = "healthSleep",
            strings = { "android.permission.health.READ_SLEEP" }
        ),
        @Permission(
            alias = "location",
            strings = {
                Manifest.permission.ACCESS_COARSE_LOCATION,
                Manifest.permission.ACCESS_FINE_LOCATION
            }
        )
    }
)
public class CompanionNativePlugin extends Plugin {
    private static final int MAX_CHAT_EXPORT_BYTES = 64 * 1024 * 1024;
    private static final String[] HEALTH_PERMISSION_ALIASES = {
        "healthSteps",
        "healthHeartRate",
        "healthSleep"
    };

    @PluginMethod
    public void notificationPermission(PluginCall call) {
        JSObject result = new JSObject();
        result.put("value", currentPermission());
        call.resolve(result);
    }

    @PluginMethod
    public void requestNotificationPermission(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
            || getPermissionState("notifications") == PermissionState.GRANTED) {
            resolvePermission(call);
            return;
        }
        requestPermissionForAlias("notifications", call, "notificationPermissionCallback");
    }

    @PermissionCallback
    private void notificationPermissionCallback(PluginCall call) {
        resolvePermission(call);
    }

    @PluginMethod
    public void configureProactiveNotifications(PluginCall call) {
        String rawBaseUrl = clean(call.getString("backendUrl"));
        String token = clean(call.getString("gatewayToken"));
        String title = clean(call.getString("title"));
        String sessionId = clean(call.getString("sessionId"));
        String timezone = clean(call.getString("timezone"));

        if (!rawBaseUrl.isEmpty() && !isAllowedBaseUrl(rawBaseUrl)) {
            call.reject("后台通知地址必须是 https 地址（本机调试可用 http://localhost）。");
            return;
        }

        String baseUrl = rawBaseUrl.replaceAll("/+$", "");
        SharedPreferences preferences = getContext().getSharedPreferences(
            ProactiveNotificationWorker.PREFS_NAME,
            0
        );
        preferences.edit()
            .putString(ProactiveNotificationWorker.KEY_BASE_URL, baseUrl)
            .putString(ProactiveNotificationWorker.KEY_TOKEN, token)
            .putString(ProactiveNotificationWorker.KEY_TITLE, title)
            .putString(ProactiveNotificationWorker.KEY_SESSION_ID, sessionId)
            .putString(ProactiveNotificationWorker.KEY_TIMEZONE, timezone)
            .apply();

        if (baseUrl.isEmpty()) {
            ProactiveNotificationWorker.cancel(getContext());
        } else {
            ProactiveNotificationWorker.schedule(getContext(), true);
            EntangleFirebaseMessagingService.syncRegistration(getContext());
        }

        JSObject result = new JSObject();
        result.put("configured", !baseUrl.isEmpty());
        result.put("intervalMinutes", ProactiveNotificationWorker.PERIODIC_MINUTES);
        result.put("delivery", "fcm_with_worker_fallback");
        call.resolve(result);
    }

    @PluginMethod
    public void incomingCallStatus(PluginCall call) {
        SharedPreferences preferences = getContext().getSharedPreferences(
            ProactiveNotificationWorker.PREFS_NAME,
            0
        );
        JSObject result = new JSObject();
        result.put("supported", true);
        result.put("notificationPermission", currentPermission());
        result.put("fullScreenAllowed", IncomingCallNotifier.fullScreenAllowed(getContext()));
        result.put("firebaseStatus", clean(preferences.getString(ProactiveNotificationWorker.KEY_FCM_STATUS, "")));
        boolean tokenRegistered = !clean(preferences.getString(ProactiveNotificationWorker.KEY_FCM_TOKEN, "")).isEmpty();
        result.put("tokenRegistered", tokenRegistered);
        if (!tokenRegistered && !clean(preferences.getString(ProactiveNotificationWorker.KEY_BASE_URL, "")).isEmpty()) {
            EntangleFirebaseMessagingService.syncRegistration(getContext());
        }
        call.resolve(result);
    }

    @PluginMethod
    public void openIncomingCallSettings(PluginCall call) {
        if (Build.VERSION.SDK_INT < 34 || IncomingCallNotifier.fullScreenAllowed(getContext())) {
            EntangleFirebaseMessagingService.syncRegistration(getContext());
            incomingCallStatus(call);
            return;
        }
        try {
            Intent intent = new Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT)
                .setData(Uri.parse("package:" + getContext().getPackageName()))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            JSObject result = new JSObject();
            result.put("opened", true);
            result.put("fullScreenAllowed", false);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("无法打开锁屏来电授权页面。", error);
        }
    }

    @PluginMethod
    public void saveChatExport(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            call.reject("当前 Android 版本暂不支持直接保存到 Entangle 文件夹。请使用浏览器版导出。");
            return;
        }
        String filename = safeExportFilename(call.getString("filename"));
        String content = call.getString("content");
        if (content == null || content.isEmpty()) {
            call.reject("聊天记录文件是空的。");
            return;
        }
        byte[] contentBytes = content.getBytes(StandardCharsets.UTF_8);
        if (contentBytes.length > MAX_CHAT_EXPORT_BYTES) {
            call.reject("聊天记录超过 64 MB，请先分段导出。");
            return;
        }

        ContentResolver resolver = getContext().getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, filename);
        values.put(MediaStore.MediaColumns.MIME_TYPE, "application/json");
        values.put(
            MediaStore.MediaColumns.RELATIVE_PATH,
            Environment.DIRECTORY_DOWNLOADS + "/Entangle/"
        );
        values.put(MediaStore.MediaColumns.IS_PENDING, 1);
        Uri uri = null;
        try {
            uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (uri == null) {
                throw new IllegalStateException("系统没有创建导出文件。");
            }
            try (OutputStream output = resolver.openOutputStream(uri, "w")) {
                if (output == null) {
                    throw new IllegalStateException("系统无法写入导出文件。");
                }
                output.write(contentBytes);
                output.flush();
            }
            ContentValues completed = new ContentValues();
            completed.put(MediaStore.MediaColumns.IS_PENDING, 0);
            resolver.update(uri, completed, null, null);
            JSObject result = new JSObject();
            result.put("saved", true);
            result.put("filename", filename);
            result.put("path", "内部存储/Download/Entangle/" + filename);
            call.resolve(result);
        } catch (Exception error) {
            if (uri != null) {
                resolver.delete(uri, null, null);
            }
            call.reject("无法把聊天记录保存到 Entangle 文件夹。", error);
        }
    }
    @PluginMethod
    public void healthStatus(PluginCall call) {
        call.resolve(healthStatusResult());
    }

    @PluginMethod
    public void requestHealthPermissions(PluginCall call) {
        if (!HealthSnapshotReader.isSupported(getContext())) {
            call.resolve(healthStatusResult());
            return;
        }
        if (healthGranted()) {
            call.resolve(healthStatusResult());
            return;
        }
        requestPermissionForAliases(
            HEALTH_PERMISSION_ALIASES,
            call,
            "healthPermissionCallback"
        );
    }

    @PermissionCallback
    private void healthPermissionCallback(PluginCall call) {
        call.resolve(healthStatusResult());
    }

    @PluginMethod
    public void readHealthSnapshot(PluginCall call) {
        if (!HealthSnapshotReader.isSupported(getContext())) {
            call.resolve(healthStatusResult());
            return;
        }
        if (!healthGranted()) {
            call.resolve(healthStatusResult());
            return;
        }
        HealthSnapshotReader.read(getContext(), new HealthSnapshotReader.Callback() {
            @Override
            public void onSuccess(JSObject snapshot) {
                snapshot.put("permission", "granted");
                call.resolve(snapshot);
            }

            @Override
            public void onError(Throwable error) {
                String message = error == null ? "暂时无法读取 Health Connect。" : clean(error.getMessage());
                call.reject(message.isEmpty() ? "暂时无法读取 Health Connect。" : message);
            }
        });
    }

    @PluginMethod
    public void deviceContextStatus(PluginCall call) {
        call.resolve(deviceContextStatusResult());
    }

    @PluginMethod
    public void requestLocationPermission(PluginCall call) {
        if (preciseLocationGranted()) {
            call.resolve(deviceContextStatusResult());
            return;
        }
        requestPermissionForAlias("location", call, "locationPermissionCallback");
    }

    @PermissionCallback
    private void locationPermissionCallback(PluginCall call) {
        call.resolve(deviceContextStatusResult());
    }

    @PluginMethod
    public void openUsageAccessSettings(PluginCall call) {
        try {
            Intent intent = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS);
            intent.setData(Uri.parse("package:" + getContext().getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            JSObject result = deviceContextStatusResult();
            result.put("opened", true);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("无法打开应用使用情况授权页面。", error);
        }
    }

    @PluginMethod
    public void readDeviceContext(PluginCall call) {
        boolean locationGranted = preciseLocationGranted();
        boolean usageGranted = DeviceContextReader.hasUsageAccess(getContext());
        DeviceContextReader.read(
            getContext(),
            locationGranted,
            usageGranted,
            new DeviceContextReader.Callback() {
                @Override
                public void onSuccess(JSObject snapshot) {
                    call.resolve(snapshot);
                }

                @Override
                public void onError(Throwable error) {
                    String message = error == null ? "暂时无法读取设备环境。" : clean(error.getMessage());
                    call.reject(message.isEmpty() ? "暂时无法读取设备环境。" : message);
                }
            }
        );
    }

    @PluginMethod
    public void showNotification(PluginCall call) {
        if (!notificationGranted()) {
            call.reject("通知权限尚未开启。");
            return;
        }
        String title = clean(call.getString("title"));
        String body = clean(call.getString("body"));
        if (body.isEmpty()) {
            call.reject("通知正文不能为空。");
            return;
        }
        ProactiveNotificationWorker.showNotification(
            getContext(),
            title.isEmpty() ? "Entangle" : title,
            body,
            "manual_" + System.currentTimeMillis()
        );
        call.resolve();
    }

    private void resolvePermission(PluginCall call) {
        if (notificationGranted()) {
            ProactiveNotificationWorker.schedule(getContext(), true);
        }
        JSObject result = new JSObject();
        result.put("value", currentPermission());
        call.resolve(result);
    }

    private boolean notificationGranted() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
            || getPermissionState("notifications") == PermissionState.GRANTED;
    }

    private boolean healthGranted() {
        if (!HealthSnapshotReader.isSupported(getContext())) {
            return false;
        }
        for (String alias : HEALTH_PERMISSION_ALIASES) {
            if (getPermissionState(alias) != PermissionState.GRANTED) {
                return false;
            }
        }
        return true;
    }

    private String currentHealthPermission() {
        if (!HealthSnapshotReader.isSupported(getContext())) {
            return "unsupported";
        }
        if (healthGranted()) {
            return "granted";
        }
        for (String alias : HEALTH_PERMISSION_ALIASES) {
            if (getPermissionState(alias) == PermissionState.DENIED) {
                return "denied";
            }
        }
        return "prompt";
    }

    private boolean preciseLocationGranted() {
        return ContextCompat.checkSelfPermission(
            getContext(),
            Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED;
    }

    private JSObject deviceContextStatusResult() {
        boolean locationGranted = preciseLocationGranted();
        boolean usageGranted = DeviceContextReader.hasUsageAccess(getContext());
        JSObject result = new JSObject();
        result.put("supported", true);
        result.put("status", locationGranted && usageGranted ? "ready" : "permission_required");
        result.put("locationPermission", locationGranted ? "granted" : "required");
        result.put("usageAccess", usageGranted ? "granted" : "required");
        return result;
    }

    private JSObject healthStatusResult() {
        boolean supported = HealthSnapshotReader.isSupported(getContext());
        String permission = currentHealthPermission();
        JSObject result = new JSObject();
        result.put("supported", supported);
        result.put("permission", permission);
        result.put("status", !supported
            ? "unsupported"
            : "granted".equals(permission) ? "ready" : "permission_required");
        return result;
    }

    private String currentPermission() {
        if (notificationGranted()) {
            return "granted";
        }
        PermissionState state = getPermissionState("notifications");
        return state == PermissionState.DENIED ? "denied" : "prompt";
    }

    private static boolean isAllowedBaseUrl(String value) {
        try {
            URI uri = URI.create(value);
            String scheme = clean(uri.getScheme()).toLowerCase();
            String host = clean(uri.getHost()).toLowerCase();
            if ("https".equals(scheme) && !host.isEmpty()) {
                return true;
            }
            return "http".equals(scheme)
                && ("localhost".equals(host) || "127.0.0.1".equals(host) || "10.0.2.2".equals(host));
        } catch (IllegalArgumentException error) {
            return false;
        }
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static String safeExportFilename(String value) {
        String filename = clean(value).replaceAll("[^A-Za-z0-9._-]", "_");
        if (filename.isEmpty()) {
            filename = "entangle-chat.json";
        }
        return filename.endsWith(".json") ? filename : filename + ".json";
    }
}
