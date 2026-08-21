package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.app.Person;
import androidx.core.content.ContextCompat;

final class IncomingCallNotifier {
    private static final String CHANNEL_ID = "entangle_incoming_calls";
    private static final int NOTIFICATION_ID = 7801;

    private IncomingCallNotifier() {}

    static boolean show(
        Context context,
        String inviteId,
        String caller,
        String reason,
        String ringUntil,
        String expiresAt
    ) {
        if (!notificationGranted(context) || inviteId == null || inviteId.trim().isEmpty()) {
            return false;
        }
        createChannel(context);
        Intent answer = activityIntent(context, inviteId, caller, reason, ringUntil, expiresAt, IncomingCallActivity.ACTION_ANSWER);
        Intent decline = activityIntent(context, inviteId, caller, reason, ringUntil, expiresAt, IncomingCallActivity.ACTION_DECLINE);
        Intent open = activityIntent(context, inviteId, caller, reason, ringUntil, expiresAt, IncomingCallActivity.ACTION_OPEN);
        PendingIntent answerIntent = PendingIntent.getActivity(
            context, requestCode(inviteId, 1), answer,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        PendingIntent declineIntent = PendingIntent.getActivity(
            context, requestCode(inviteId, 2), decline,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        PendingIntent fullScreenIntent = PendingIntent.getActivity(
            context, requestCode(inviteId, 3), open,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        Person person = new Person.Builder()
            .setName(caller == null || caller.trim().isEmpty() ? "Zeta" : caller.trim())
            .setImportant(true)
            .build();
        String cleanReason = reason == null || reason.trim().isEmpty()
            ? "突然有点想听听你的声音"
            : reason.trim();
        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(person.getName() + " 正在呼叫你")
            .setContentText(cleanReason)
            .setStyle(NotificationCompat.CallStyle.forIncomingCall(person, declineIntent, answerIntent))
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setAutoCancel(false)
            .setContentIntent(fullScreenIntent)
            .setFullScreenIntent(fullScreenIntent, true);
        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, builder.build());
        return true;
    }

    static void cancel(Context context) {
        NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID);
    }

    static boolean fullScreenAllowed(Context context) {
        if (Build.VERSION.SDK_INT < 34) return true;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        return manager != null && manager.canUseFullScreenIntent();
    }

    private static Intent activityIntent(
        Context context,
        String inviteId,
        String caller,
        String reason,
        String ringUntil,
        String expiresAt,
        String action
    ) {
        return new Intent(context, IncomingCallActivity.class)
            .setAction(action)
            .putExtra(IncomingCallActivity.EXTRA_INVITE_ID, inviteId)
            .putExtra(IncomingCallActivity.EXTRA_CALLER, caller)
            .putExtra(IncomingCallActivity.EXTRA_REASON, reason)
            .putExtra(IncomingCallActivity.EXTRA_RING_UNTIL, ringUntil)
            .putExtra(IncomingCallActivity.EXTRA_EXPIRES_AT, expiresAt)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
    }

    private static void createChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        Uri ringtone = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
        AudioAttributes audio = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build();
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "Entangle 即时来电",
            NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("Zeta 主动打电话时显示在锁屏上的来电提醒");
        channel.setSound(ringtone, audio);
        channel.enableVibration(true);
        channel.setLockscreenVisibility(NotificationCompat.VISIBILITY_PUBLIC);
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(channel);
    }

    private static int requestCode(String inviteId, int suffix) {
        return Math.abs((inviteId + ":" + suffix).hashCode());
    }

    private static boolean notificationGranted(Context context) {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
            || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
    }
}
