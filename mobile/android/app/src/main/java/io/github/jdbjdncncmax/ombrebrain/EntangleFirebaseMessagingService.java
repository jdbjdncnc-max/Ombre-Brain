package io.github.jdbjdncncmax.ombrebrain;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;
import android.util.Log;

import androidx.annotation.NonNull;

import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import org.json.JSONObject;

import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

public class EntangleFirebaseMessagingService extends FirebaseMessagingService {
    private static final String TAG = "EntangleFCM";
    private static final AtomicBoolean TOKEN_REQUEST_IN_FLIGHT = new AtomicBoolean(false);
    private static final AtomicBoolean GATEWAY_REGISTRATION_IN_FLIGHT = new AtomicBoolean(false);
    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        FirebaseRegistration.storeToken(getApplicationContext(), token);
        registerToken(getApplicationContext(), token);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        Map<String, String> data = message.getData();
        if ("call_invite".equals(data.get("type"))) {
            IncomingCallNotifier.show(
                getApplicationContext(),
                clean(data.get("inviteId")),
                clean(data.get("caller")),
                clean(data.get("reason")),
                clean(data.get("ringUntil")),
                clean(data.get("expiresAt"))
            );
            return;
        }
        if (!"ombre_proactive".equals(clean(data.get("kind")))) return;
        String id = clean(data.get("id"));
        String text = clean(data.get("text"));
        String title = clean(data.get("title"));
        if (id.isEmpty() || text.isEmpty()) return;
        if (!ProactiveNotificationWorker.markDeliveredIfNew(this, id)) return;
        ProactiveNotificationWorker.showNotification(
            this,
            title.isEmpty() ? "Entangle" : title,
            text,
            id
        );
        ProactiveNotificationWorker.schedule(this, true);
    }

    static void syncRegistration(Context context) {
        Context appContext = context.getApplicationContext();
        if (!TOKEN_REQUEST_IN_FLIGHT.compareAndSet(false, true)) {
            return;
        }
        try {
            FirebaseApp app = FirebaseApp.initializeApp(appContext);
            if (app == null) {
                saveStatus(appContext, "等待 google-services.json");
                TOKEN_REQUEST_IN_FLIGHT.set(false);
                return;
            }
            saveStatus(appContext, "正在获取令牌");
            FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
                try {
                    if (!task.isSuccessful() || task.getResult() == null) {
                        Throwable error = task.getException();
                        saveStatus(appContext, tokenFailureStatus(error));
                        Log.w(TAG, "FCM token request failed: " + safeErrorDetail(error), error);
                        return;
                    }
                    FirebaseRegistration.storeToken(appContext, task.getResult());
                    registerToken(appContext, task.getResult());
                } finally {
                    TOKEN_REQUEST_IN_FLIGHT.set(false);
                }
            });
        } catch (Exception error) {
            TOKEN_REQUEST_IN_FLIGHT.set(false);
            saveStatus(appContext, tokenFailureStatus(error));
            Log.w(TAG, "Unable to start FCM token request: " + safeErrorDetail(error), error);
        }
    }

    private static void registerToken(Context context, String token) {
        String cleanToken = clean(token);
        if (cleanToken.isEmpty()) return;
        SharedPreferences prefs = context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0);
        prefs.edit().putString(ProactiveNotificationWorker.KEY_FCM_TOKEN, cleanToken).apply();
        if (!GATEWAY_REGISTRATION_IN_FLIGHT.compareAndSet(false, true)) {
            return;
        }
        saveStatus(context, "令牌已生成，正在登记");
        ExecutorService executor = Executors.newSingleThreadExecutor();
        executor.execute(() -> {
            try {
                registerTokenBlocking(context, cleanToken);
            } finally {
                GATEWAY_REGISTRATION_IN_FLIGHT.set(false);
                executor.shutdown();
            }
        });
    }

    static boolean registerStoredTokenBlocking(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0);
        String token = clean(prefs.getString(ProactiveNotificationWorker.KEY_FCM_TOKEN, ""));
        return !token.isEmpty() && registerTokenBlocking(context, token);
    }

    private static synchronized boolean registerTokenBlocking(Context context, String token) {
        SharedPreferences prefs = context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0);
        String baseUrl = clean(prefs.getString(ProactiveNotificationWorker.KEY_BASE_URL, "")).replaceAll("/+$", "");
        String gatewayToken = clean(prefs.getString(ProactiveNotificationWorker.KEY_TOKEN, ""));
        if (baseUrl.isEmpty()) {
            saveStatus(context, "请先填写后端地址");
            return false;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("token", clean(token));
            body.put("platform", "android");
            body.put("deviceId", clean(Settings.Secure.getString(
                context.getContentResolver(),
                Settings.Secure.ANDROID_ID
            )));
            String appVersion = "";
            try {
                appVersion = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionName;
            } catch (Exception ignored) {}
            body.put("appVersion", clean(appVersion));
            GatewayHttp.post(baseUrl + "/api/call/devices", gatewayToken, body);
            FirebaseRegistration.sync(context);
            saveStatus(context, "ready");
            Log.i(TAG, "FCM token registered with call gateway");
            return true;
        } catch (Exception error) {
            saveStatus(context, "网关登记失败");
            Log.w(TAG, "FCM gateway registration failed: " + safeErrorDetail(error), error);
            return false;
        }
    }

    private static void saveStatus(Context context, String value) {
        context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0)
            .edit().putString(ProactiveNotificationWorker.KEY_FCM_STATUS, clean(value)).apply();
    }

    private static String tokenFailureStatus(Throwable error) {
        String detail = safeErrorDetail(error).toUpperCase();
        if (detail.contains("SERVICE_NOT_AVAILABLE") || detail.contains("TIMEOUT")) {
            return "Google 服务暂不可用，稍后自动重试";
        }
        if (detail.contains("AUTHENTICATION_FAILED") || detail.contains("FIS_AUTH_ERROR")) {
            return "Firebase 配置认证失败";
        }
        if (detail.contains("TOO_MANY_REGISTRATIONS")) {
            return "Firebase 注册次数过多，请稍后重试";
        }
        return "令牌获取失败，稍后自动重试";
    }

    private static String safeErrorDetail(Throwable error) {
        if (error == null) return "unknown";
        String message = clean(error.getMessage());
        return error.getClass().getSimpleName() + (message.isEmpty() ? "" : ": " + message);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
