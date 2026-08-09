package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.content.SharedPreferences;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.net.URI;

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
        )
    }
)
public class CompanionNativePlugin extends Plugin {
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
            .apply();

        if (baseUrl.isEmpty()) {
            ProactiveNotificationWorker.cancel(getContext());
        } else {
            ProactiveNotificationWorker.schedule(getContext(), true);
        }

        JSObject result = new JSObject();
        result.put("configured", !baseUrl.isEmpty());
        result.put("intervalMinutes", ProactiveNotificationWorker.PERIODIC_MINUTES);
        call.resolve(result);
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
            title.isEmpty() ? "Ombre" : title,
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
}
