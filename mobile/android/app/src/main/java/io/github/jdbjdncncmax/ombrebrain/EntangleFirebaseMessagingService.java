package io.github.jdbjdncncmax.ombrebrain;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.annotation.NonNull;

import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import org.json.JSONObject;

import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class EntangleFirebaseMessagingService extends FirebaseMessagingService {
    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        registerToken(getApplicationContext(), token);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        Map<String, String> data = message.getData();
        String type = clean(data.get("type"));
        if ("call_invite".equals(type)) {
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
        if ("proactive_message".equals(type)) {
            String id = clean(data.get("messageId"));
            String text = clean(data.get("text"));
            if (id.isEmpty() || text.isEmpty()) return;
            String title = clean(data.get("title"));
            ProactiveNotificationWorker.showNotification(
                getApplicationContext(),
                title.isEmpty() ? "Zeta" : title,
                text,
                id
            );
            ProactiveNotificationWorker.rememberDelivered(getApplicationContext(), id);
        }
    }

    static void syncRegistration(Context context) {
        Context appContext = context.getApplicationContext();
        try {
            FirebaseApp app = FirebaseApp.initializeApp(appContext);
            if (app == null) {
                saveStatus(appContext, "等待 google-services.json");
                return;
            }
            FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
                if (!task.isSuccessful() || task.getResult() == null) {
                    saveStatus(appContext, "令牌获取失败");
                    return;
                }
                registerToken(appContext, task.getResult());
            });
        } catch (Exception error) {
            saveStatus(appContext, "等待 Firebase 配置");
        }
    }

    private static void registerToken(Context context, String token) {
        String cleanToken = clean(token);
        if (cleanToken.isEmpty()) return;
        SharedPreferences prefs = context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0);
        prefs.edit().putString(ProactiveNotificationWorker.KEY_FCM_TOKEN, cleanToken).apply();
        String baseUrl = clean(prefs.getString(ProactiveNotificationWorker.KEY_BASE_URL, "")).replaceAll("/+$", "");
        String gatewayToken = clean(prefs.getString(ProactiveNotificationWorker.KEY_TOKEN, ""));
        if (baseUrl.isEmpty()) {
            saveStatus(context, "请先填写后端地址");
            return;
        }
        ExecutorService executor = Executors.newSingleThreadExecutor();
        executor.execute(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("token", cleanToken);
                body.put("platform", "android");
                String appVersion = "";
                try {
                    appVersion = context.getPackageManager()
                        .getPackageInfo(context.getPackageName(), 0).versionName;
                } catch (Exception ignored) {}
                body.put("appVersion", clean(appVersion));
                GatewayHttp.post(baseUrl + "/api/call/devices", gatewayToken, body);
                saveStatus(context, "ready");
            } catch (Exception error) {
                saveStatus(context, "网关登记失败");
            } finally {
                executor.shutdown();
            }
        });
    }

    private static void saveStatus(Context context, String value) {
        context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0)
            .edit().putString(ProactiveNotificationWorker.KEY_FCM_STATUS, clean(value)).apply();
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
