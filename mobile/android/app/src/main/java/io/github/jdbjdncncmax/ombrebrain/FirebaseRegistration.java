package io.github.jdbjdncncmax.ombrebrain;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class FirebaseRegistration {
    public static final String KEY_FIREBASE_FID = "firebase_fid";

    private FirebaseRegistration() {}

    public static void storeToken(Context context, String fid) {
        String cleanFid = clean(fid);
        if (cleanFid.isEmpty()) {
            return;
        }
        context.getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0)
            .edit()
            .putString(KEY_FIREBASE_FID, cleanFid)
            .apply();
        // The configured worker already has a periodic schedule. Scheduling a new
        // immediate worker here would create a getToken -> worker -> getToken loop.
    }

    public static boolean sync(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(
            ProactiveNotificationWorker.PREFS_NAME,
            0
        );
        String baseUrl = clean(preferences.getString(ProactiveNotificationWorker.KEY_BASE_URL, ""))
            .replaceAll("/+$", "");
        String gatewayToken = clean(preferences.getString(ProactiveNotificationWorker.KEY_TOKEN, ""));
        String fid = clean(preferences.getString(KEY_FIREBASE_FID, ""));
        if (baseUrl.isEmpty() || fid.isEmpty()) {
            return false;
        }

        HttpURLConnection connection = null;
        try {
            JSONObject payload = new JSONObject();
            payload.put("fid", fid);
            payload.put("device_id", clean(Settings.Secure.getString(
                context.getContentResolver(),
                Settings.Secure.ANDROID_ID
            )));
            payload.put("platform", "android");

            connection = (HttpURLConnection) new URL(baseUrl + "/api/notifications/register").openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(20000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            if (!gatewayToken.isEmpty()) {
                connection.setRequestProperty("Authorization", "Bearer " + gatewayToken);
                connection.setRequestProperty("x-api-key", gatewayToken);
            }
            try (OutputStream output = connection.getOutputStream()) {
                output.write(payload.toString().getBytes(StandardCharsets.UTF_8));
            }
            int status = connection.getResponseCode();
            readBounded(status >= 200 && status < 400
                ? connection.getInputStream()
                : connection.getErrorStream());
            return status >= 200 && status < 300;
        } catch (Exception error) {
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static void readBounded(InputStream stream) throws Exception {
        if (stream == null) {
            return;
        }
        try (InputStream input = stream; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[2048];
            int total = 0;
            int count;
            while ((count = input.read(buffer)) >= 0 && total < 64 * 1024) {
                int allowed = Math.min(count, 64 * 1024 - total);
                output.write(buffer, 0, allowed);
                total += allowed;
            }
        }
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
