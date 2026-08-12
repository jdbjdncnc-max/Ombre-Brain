package io.github.jdbjdncncmax.ombrebrain;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Build;
import android.os.IBinder;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONObject;

import java.util.List;

public class CallForegroundService extends Service implements CallSocketClient.Listener, CallAudioEngine.Listener {
    static final String ACTION_START = "io.github.jdbjdncncmax.ombrebrain.call.START";
    static final String ACTION_HANGUP = "io.github.jdbjdncncmax.ombrebrain.call.HANGUP";
    static final String ACTION_MUTE = "io.github.jdbjdncncmax.ombrebrain.call.MUTE";
    static final String ACTION_SPEAKER = "io.github.jdbjdncncmax.ombrebrain.call.SPEAKER";
    static final String EXTRA_BACKEND_URL = "backendUrl";
    static final String EXTRA_GATEWAY_TOKEN = "gatewayToken";
    static final String EXTRA_SESSION_ID = "sessionId";
    static final String EXTRA_TIMEZONE = "timezone";
    static final String EXTRA_CONTEXT_MESSAGES = "contextMessages";
    static final String EXTRA_INVITE_ID = "inviteId";
    static final String EXTRA_ENABLED = "enabled";

    private static final String CHANNEL_ID = "ombre_voice_call";
    private static final int NOTIFICATION_ID = 7802;
    private static volatile CallForegroundService active;
    private static volatile String currentState = "idle";
    private static volatile boolean currentMuted;
    private static volatile boolean currentSpeaker;

    private CallSocketClient socket;
    private CallAudioEngine audio;
    private AudioManager audioManager;
    private boolean ending;

    static void start(Context context, Intent intent) {
        ContextCompat.startForegroundService(context, intent);
    }

    static JSONObject stateSnapshot() {
        JSONObject value = new JSONObject();
        try {
            value.put("type", "state");
            value.put("state", currentState);
            value.put("muted", currentMuted);
            value.put("speaker", currentSpeaker);
            value.put("active", active != null && !"idle".equals(currentState) && !"ended".equals(currentState));
        } catch (Exception ignored) {}
        return value;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        active = this;
        audioManager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
        createNotificationChannel();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : intent.getAction();
        if (ACTION_HANGUP.equals(action)) {
            hangup("user");
            return START_NOT_STICKY;
        }
        if (ACTION_MUTE.equals(action)) {
            setMuted(intent.getBooleanExtra(EXTRA_ENABLED, false));
            return START_NOT_STICKY;
        }
        if (ACTION_SPEAKER.equals(action)) {
            setSpeaker(intent.getBooleanExtra(EXTRA_ENABLED, false));
            return START_NOT_STICKY;
        }
        if (!ACTION_START.equals(action) || socket != null) {
            return START_NOT_STICKY;
        }

        startForegroundCompat("正在连接 Zeta…");
        setState("connecting");
        String backendUrl = clean(intent.getStringExtra(EXTRA_BACKEND_URL));
        String gatewayToken = clean(intent.getStringExtra(EXTRA_GATEWAY_TOKEN));
        String sessionId = clean(intent.getStringExtra(EXTRA_SESSION_ID));
        String timezone = clean(intent.getStringExtra(EXTRA_TIMEZONE));
        String contextMessages = clean(intent.getStringExtra(EXTRA_CONTEXT_MESSAGES));
        String inviteId = clean(intent.getStringExtra(EXTRA_INVITE_ID));
        audio = new CallAudioEngine(this, this);
        setSpeaker(false);
        try {
            socket = new CallSocketClient(
                backendUrl,
                gatewayToken,
                sessionId,
                timezone,
                contextMessages.isEmpty() ? "[]" : contextMessages,
                inviteId,
                this
            );
            socket.connect();
        } catch (Exception error) {
            onFailure(error.getMessage() == null ? "通话地址无效。" : error.getMessage());
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onOpen() {
        setState("connecting");
    }

    @Override
    public void onJson(JSONObject message) {
        String type = message.optString("type", "");
        if ("ready".equals(type)) {
            if (audio != null) audio.start();
            setState("listening");
            return;
        }
        if ("status".equals(type)) {
            setState(message.optString("state", "connected"));
            return;
        }
        if ("audio_stop".equals(type)) {
            if (audio != null) audio.stopPlayback();
            return;
        }
        if ("audio_end".equals(type)) {
            if (audio != null) audio.afterPlayback(null);
            return;
        }
        if ("marker".equals(type) && "hangup".equals(message.optString("name", ""))) {
            if (audio != null) {
                audio.afterPlayback(() -> hangup("assistant"));
            } else {
                hangup("assistant");
            }
            return;
        }
        if ("ended".equals(type)) {
            hangup(message.optString("reason", "remote"));
            return;
        }
        if ("error".equals(type)) {
            emit(message);
            updateNotification("通话暂时不可用");
            if ("call_not_configured".equals(message.optString("code", ""))
                || "unauthorized".equals(message.optString("code", ""))) {
                hangup("error");
            }
            return;
        }
        emit(message);
    }

    @Override
    public void onAudio(byte[] pcm) {
        if (audio != null) audio.play(pcm);
    }

    @Override
    public void onFailure(String message) {
        JSONObject event = new JSONObject();
        try {
            event.put("type", "error");
            event.put("code", "connection_error");
            event.put("message", clean(message).isEmpty() ? "通话连接失败。" : clean(message));
        } catch (Exception ignored) {}
        emit(event);
        hangup("error");
    }

    @Override
    public void onClosed() {
        if (!ending) hangup("remote");
    }

    @Override
    public void onSpeechStart() {
        CallSocketClient activeSocket = socket;
        if (activeSocket != null) activeSocket.sendControl("speech_start");
    }

    @Override
    public void onAudioFrame(byte[] frame) {
        CallSocketClient activeSocket = socket;
        if (activeSocket != null) activeSocket.sendAudio(frame);
    }

    @Override
    public void onSpeechEnd() {
        CallSocketClient activeSocket = socket;
        if (activeSocket != null) activeSocket.sendControl("speech_end");
    }

    @Override
    public void onBargeIn() {
        CallSocketClient activeSocket = socket;
        if (activeSocket != null) activeSocket.sendControl("barge_in");
    }

    @Override
    public void onError(String message) {
        JSONObject event = new JSONObject();
        try {
            event.put("type", "error");
            event.put("code", "audio_error");
            event.put("message", message);
        } catch (Exception ignored) {}
        emit(event);
    }

    private void setMuted(boolean value) {
        currentMuted = value;
        if (audio != null) audio.setMuted(value);
        emitControlState();
    }

    private void setSpeaker(boolean value) {
        currentSpeaker = value;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            int desiredType = value
                ? AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
                : AudioDeviceInfo.TYPE_BUILTIN_EARPIECE;
            List<AudioDeviceInfo> devices = audioManager.getAvailableCommunicationDevices();
            for (AudioDeviceInfo device : devices) {
                if (device.getType() == desiredType) {
                    audioManager.setCommunicationDevice(device);
                    break;
                }
            }
        } else {
            audioManager.setSpeakerphoneOn(value);
        }
        emitControlState();
    }

    private void emitControlState() {
        JSONObject event = stateSnapshot();
        try {
            event.put("type", "controls");
        } catch (Exception ignored) {}
        emit(event);
    }

    private void setState(String value) {
        currentState = clean(value).isEmpty() ? "connected" : clean(value);
        JSONObject event = stateSnapshot();
        emit(event);
        updateNotification(notificationText(currentState));
    }

    private void hangup(String reason) {
        if (ending) return;
        ending = true;
        CallSocketClient activeSocket = socket;
        socket = null;
        if (activeSocket != null) {
            if ("user".equals(reason)) activeSocket.sendControl("hangup");
            activeSocket.close();
        }
        CallAudioEngine activeAudio = audio;
        audio = null;
        if (activeAudio != null) activeAudio.stop();
        currentState = "ended";
        JSONObject event = stateSnapshot();
        try {
            event.put("type", "ended");
            event.put("reason", reason);
        } catch (Exception ignored) {}
        emit(event);
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        if (!ending) hangup("system");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.clearCommunicationDevice();
        } else {
            audioManager.setSpeakerphoneOn(false);
        }
        audioManager.setMode(AudioManager.MODE_NORMAL);
        active = null;
        currentState = "idle";
        currentMuted = false;
        currentSpeaker = false;
        super.onDestroy();
    }

    private void startForegroundCompat(String text) {
        Notification notification = buildNotification(text);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void updateNotification(String text) {
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        manager.notify(NOTIFICATION_ID, buildNotification(text));
    }

    private Notification buildNotification(String text) {
        Intent launch = new Intent(this, MainActivity.class);
        launch.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentIntent = PendingIntent.getActivity(
            this,
            7803,
            launch,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        Intent hangup = new Intent(this, CallForegroundService.class).setAction(ACTION_HANGUP);
        PendingIntent hangupIntent = PendingIntent.getService(
            this,
            7804,
            hangup,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("正在和 Zeta 通话")
            .setContentText(text)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "挂断", hangupIntent)
            .build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "语音通话",
            NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("Entangle 与 Zeta 的语音通话状态");
        manager.createNotificationChannel(channel);
    }

    private static void emit(JSONObject event) {
        CallEventBus.emit(event);
    }

    private static String notificationText(String state) {
        switch (state) {
            case "listening": return "正在听你说";
            case "transcribing": return "正在听清楚";
            case "thinking": return "Zeta 正在想";
            case "speaking": return "Zeta 正在说话";
            default: return "正在连接 Zeta…";
        }
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
