package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;

public class ProactiveNotificationWorker extends Worker {
    public static final String PREFS_NAME = "ombre_native_notifications_v1";
    public static final String KEY_BASE_URL = "backend_url";
    public static final String KEY_TOKEN = "gateway_token";
    public static final String KEY_TITLE = "notification_title";
    public static final String KEY_SESSION_ID = "session_id";
    public static final String KEY_TIMEZONE = "timezone";
    public static final String KEY_FCM_TOKEN = "fcm_token";
    public static final String KEY_FCM_STATUS = "fcm_status";
    public static final long PERIODIC_MINUTES = 15L;

    private static final String KEY_DELIVERED = "delivered_ids";
    private static final String PERIODIC_WORK_NAME = "ombre-proactive-notifications";
    private static final String IMMEDIATE_WORK_NAME = "ombre-proactive-notifications-now";
    private static final String CHANNEL_ID = "ombre_proactive_messages";

    public ProactiveNotificationWorker(
        @NonNull Context appContext,
        @NonNull WorkerParameters workerParams
    ) {
        super(appContext, workerParams);
    }

    @NonNull
    @Override
    public Result doWork() {
        return runOnce(getApplicationContext());
    }

    static Result runOnce(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS_NAME, 0);
        String baseUrl = clean(preferences.getString(KEY_BASE_URL, "")).replaceAll("/+$", "");
        String token = clean(preferences.getString(KEY_TOKEN, ""));
        String defaultTitle = clean(preferences.getString(KEY_TITLE, ""));

        if (baseUrl.isEmpty()) {
            return Result.success();
        }
        if (!EntangleFirebaseMessagingService.registerStoredTokenBlocking(context)) {
            EntangleFirebaseMessagingService.syncRegistration(context);
        }
        FirebaseRegistration.sync(context);
        uploadDeviceContext(context, baseUrl, token);
        if (!notificationGranted(context)) {
            return Result.success();
        }

        pollIncomingCall(context, baseUrl, token);

        HttpResult outbox = request("GET", baseUrl + "/api/solo/outbox?limit=50", token, null);
        if (!outbox.isSuccess()) {
            return outbox.shouldRetry() ? Result.retry() : Result.success();
        }

        try {
            JSONArray items = new JSONObject(outbox.body).optJSONArray("items");
            if (items == null || items.length() == 0) {
                return Result.success();
            }

            Set<String> delivered = new HashSet<>(preferences.getStringSet(KEY_DELIVERED, new HashSet<>()));
            List<String> ackIds = new ArrayList<>();
            JSONObject newestItem = null;
            for (int index = 0; index < items.length(); index += 1) {
                JSONObject item = items.optJSONObject(index);
                if (item == null) {
                    continue;
                }
                String id = clean(item.optString("id"));
                String body = clean(item.optString("text"));
                if (id.isEmpty() || body.isEmpty()) {
                    continue;
                }
                ackIds.add(id);
                newestItem = item;
            }

            if (newestItem != null) {
                String id = clean(newestItem.optString("id"));
                String body = clean(newestItem.optString("text"));
                if (!id.isEmpty() && !body.isEmpty() && !delivered.contains(id)) {
                    String title = clean(newestItem.optString("title"));
                    showNotification(
                        context,
                        title.isEmpty() ? (defaultTitle.isEmpty() ? "Entangle" : defaultTitle) : title,
                        body,
                        id
                    );
                    delivered.add(id);
                }
            }

            preferences.edit().putStringSet(KEY_DELIVERED, boundedIds(delivered)).apply();
            if (ackIds.isEmpty()) {
                return Result.success();
            }

            JSONObject ackBody = new JSONObject();
            ackBody.put("ids", new JSONArray(ackIds));
            HttpResult ack = request(
                "POST",
                baseUrl + "/api/solo/outbox/ack",
                token,
                ackBody.toString()
            );
            if (ack.isSuccess()) {
                preferences.edit().putStringSet(KEY_DELIVERED, boundedIds(delivered)).apply();
                return Result.success();
            }
            return ack.shouldRetry() ? Result.retry() : Result.success();
        } catch (Exception error) {
            return Result.retry();
        }
    }

    public static void schedule(Context context, boolean runNow) {
        createNotificationChannel(context);
        PeriodicWorkRequest periodic = new PeriodicWorkRequest.Builder(
            ProactiveNotificationWorker.class,
            PERIODIC_MINUTES,
            TimeUnit.MINUTES
        ).build();
        WorkManager manager = WorkManager.getInstance(context.getApplicationContext());
        manager.enqueueUniquePeriodicWork(
            PERIODIC_WORK_NAME,
            ExistingPeriodicWorkPolicy.UPDATE,
            periodic
        );
        if (runNow) {
            OneTimeWorkRequest immediate = new OneTimeWorkRequest.Builder(ProactiveNotificationWorker.class)
                .build();
            manager.enqueueUniqueWork(IMMEDIATE_WORK_NAME, ExistingWorkPolicy.REPLACE, immediate);
        }
    }

    public static void cancel(Context context) {
        WorkManager manager = WorkManager.getInstance(context.getApplicationContext());
        manager.cancelUniqueWork(PERIODIC_WORK_NAME);
        manager.cancelUniqueWork(IMMEDIATE_WORK_NAME);
    }

    public static synchronized boolean markDeliveredIfNew(Context context, String id) {
        String cleanId = clean(id);
        if (cleanId.isEmpty()) {
            return false;
        }
        SharedPreferences preferences = context.getSharedPreferences(PREFS_NAME, 0);
        Set<String> delivered = new HashSet<>(preferences.getStringSet(KEY_DELIVERED, new HashSet<>()));
        if (delivered.contains(cleanId)) {
            return false;
        }
        delivered.add(cleanId);
        preferences.edit().putStringSet(KEY_DELIVERED, boundedIds(delivered)).apply();
        return true;
    }

    public static void showNotification(Context context, String title, String body, String id) {
        createNotificationChannel(context);
        Intent intent = new Intent(context, MainActivity.class)
            .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            context,
            Math.abs(id.hashCode()),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT);
        if (notificationGranted(context)) {
            NotificationManagerCompat.from(context).notify(id, 0, builder.build());
        }
    }

    private static void pollIncomingCall(Context context, String baseUrl, String token) {
        HttpResult result = request("GET", baseUrl + "/api/call/invite", token, null);
        if (!result.isSuccess()) return;
        try {
            JSONObject invite = new JSONObject(result.body).optJSONObject("invite");
            if (invite == null || !"pending".equals(invite.optString("state"))) return;
            if (invite.optBoolean("ringable", false)) {
                IncomingCallNotifier.show(
                    context,
                    invite.optString("id"),
                    "Zeta",
                    invite.optString("reason"),
                    invite.optString("ringUntil"),
                    invite.optString("expiresAt")
                );
            } else {
                String inviteId = clean(invite.optString("id"));
                String reason = clean(invite.optString("reason"));
                showNotification(
                    context,
                    "Zeta 刚才想找你",
                    reason.isEmpty() ? "刚才有一通没接到的来电" : reason,
                    "missed_" + inviteId
                );
                JSONObject response = new JSONObject();
                response.put("action", "missed");
                request(
                    "POST",
                    baseUrl + "/api/call/invite/" + inviteId + "/answer",
                    token,
                    response.toString()
                );
            }
        } catch (Exception ignored) {}
    }

    private static void uploadDeviceContext(Context context, String baseUrl, String token) {
        try {
            JSONObject snapshot = new JSONObject(DeviceContextReader.readUsageSnapshot(context).toString());
            GatewayHttp.post(baseUrl + "/api/solo/device-context", token, snapshot);
        } catch (Exception ignored) {}
    }

    private static void createNotificationChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "Entangle 主动消息",
            NotificationManager.IMPORTANCE_DEFAULT
        );
        channel.setDescription("独处系统主动想联系你时发出的消息");
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.createNotificationChannel(channel);
        }
    }

    private static boolean notificationGranted(Context context) {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
            || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
    }

    private static HttpResult request(String method, String url, String token, String jsonBody) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setRequestMethod(method);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(20000);
            connection.setRequestProperty("Accept", "application/json");
            if (!token.isEmpty()) {
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setRequestProperty("x-api-key", token);
            }
            if (jsonBody != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(jsonBody.getBytes(StandardCharsets.UTF_8));
                }
            }
            int status = connection.getResponseCode();
            InputStream stream = status >= 200 && status < 400
                ? connection.getInputStream()
                : connection.getErrorStream();
            return new HttpResult(status, readBounded(stream));
        } catch (Exception error) {
            return new HttpResult(0, "");
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static String readBounded(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        try (InputStream input = stream; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int total = 0;
            int count;
            while ((count = input.read(buffer)) >= 0 && total < 1024 * 1024) {
                int allowed = Math.min(count, 1024 * 1024 - total);
                output.write(buffer, 0, allowed);
                total += allowed;
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static Set<String> boundedIds(Set<String> values) {
        List<String> list = new ArrayList<>(values);
        int start = Math.max(0, list.size() - 200);
        return new HashSet<>(list.subList(start, list.size()));
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static class HttpResult {
        final int status;
        final String body;

        HttpResult(int status, String body) {
            this.status = status;
            this.body = body == null ? "" : body;
        }

        boolean isSuccess() {
            return status >= 200 && status < 300;
        }

        boolean shouldRetry() {
            return status == 0 || status == 408 || status == 429 || status >= 500;
        }
    }
}
