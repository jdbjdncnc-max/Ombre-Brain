package io.github.jdbjdncncmax.ombrebrain;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

public class ProactiveBootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
            && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            return;
        }
        SharedPreferences preferences = context.getSharedPreferences(
            ProactiveNotificationWorker.PREFS_NAME,
            0
        );
        String baseUrl = preferences.getString(ProactiveNotificationWorker.KEY_BASE_URL, "");
        if (baseUrl != null && !baseUrl.trim().isEmpty()) {
            ProactiveNotificationWorker.schedule(context, true);
            try {
                ProactiveBackgroundService.start(context);
            } catch (RuntimeException ignored) {
                // WorkManager remains as the reboot-safe fallback when this Android
                // version does not allow a foreground service to start at boot.
            }
        }
    }
}
