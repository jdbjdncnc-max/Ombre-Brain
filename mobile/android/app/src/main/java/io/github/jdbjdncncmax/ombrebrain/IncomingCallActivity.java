package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.core.content.ContextCompat;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class IncomingCallActivity extends Activity {
    static final String ACTION_OPEN = "io.github.jdbjdncncmax.ombrebrain.call.OPEN_INCOMING";
    static final String ACTION_ANSWER = "io.github.jdbjdncncmax.ombrebrain.call.ANSWER_INCOMING";
    static final String ACTION_DECLINE = "io.github.jdbjdncncmax.ombrebrain.call.DECLINE_INCOMING";
    static final String EXTRA_INVITE_ID = "inviteId";
    static final String EXTRA_CALLER = "caller";
    static final String EXTRA_REASON = "reason";
    static final String EXTRA_RING_UNTIL = "ringUntil";
    static final String EXTRA_EXPIRES_AT = "expiresAt";
    private static final int REQUEST_MICROPHONE = 718;

    private String inviteId = "";
    private String reason = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                    | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                    | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            );
        }
        getWindow().setStatusBarColor(Color.rgb(0, 19, 31));
        render(getIntent());
        handleAction(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        render(intent);
        handleAction(intent);
    }

    private void render(Intent intent) {
        inviteId = clean(intent == null ? "" : intent.getStringExtra(EXTRA_INVITE_ID));
        String caller = clean(intent == null ? "" : intent.getStringExtra(EXTRA_CALLER));
        reason = clean(intent == null ? "" : intent.getStringExtra(EXTRA_REASON));
        if (caller.isEmpty()) caller = "Zeta";
        if (reason.isEmpty()) reason = "突然有点想听听你的声音";

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(dp(28), dp(48), dp(28), dp(44));
        root.setBackgroundColor(Color.rgb(0, 19, 31));

        ImageView icon = new ImageView(this);
        icon.setImageResource(R.mipmap.ic_launcher_round);
        root.addView(icon, new LinearLayout.LayoutParams(dp(132), dp(132)));

        TextView name = label(caller, 34, Color.WHITE);
        name.setPadding(0, dp(24), 0, 0);
        root.addView(name);
        TextView state = label("Entangle 来电", 16, Color.rgb(92, 230, 218));
        root.addView(state);
        TextView message = label(reason, 18, Color.rgb(207, 226, 232));
        message.setPadding(0, dp(32), 0, dp(44));
        root.addView(message);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.setGravity(Gravity.CENTER);
        Button decline = button("挂断", Color.rgb(174, 48, 64));
        Button answer = button("接听", Color.rgb(28, 171, 125));
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(0, dp(58), 1f);
        buttonParams.setMargins(dp(8), 0, dp(8), 0);
        buttons.addView(decline, buttonParams);
        buttons.addView(answer, buttonParams);
        root.addView(buttons, new LinearLayout.LayoutParams(-1, -2));

        decline.setOnClickListener(view -> decline());
        answer.setOnClickListener(view -> answer());
        setContentView(root);
    }

    private void handleAction(Intent intent) {
        String action = intent == null ? "" : clean(intent.getAction());
        if (ACTION_ANSWER.equals(action)) answer();
        if (ACTION_DECLINE.equals(action)) decline();
    }

    private void answer() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_MICROPHONE);
            return;
        }
        IncomingCallNotifier.cancel(this);
        respond("answer");
        SharedPreferences prefs = getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0);
        String baseUrl = clean(prefs.getString(ProactiveNotificationWorker.KEY_BASE_URL, ""));
        if (baseUrl.isEmpty()) {
            startActivity(new Intent(this, MainActivity.class));
            finish();
            return;
        }
        Intent call = new Intent(this, CallForegroundService.class)
            .setAction(CallForegroundService.ACTION_START)
            .putExtra(CallForegroundService.EXTRA_BACKEND_URL, baseUrl)
            .putExtra(CallForegroundService.EXTRA_GATEWAY_TOKEN, clean(prefs.getString(ProactiveNotificationWorker.KEY_TOKEN, "")))
            .putExtra(CallForegroundService.EXTRA_SESSION_ID, clean(prefs.getString(ProactiveNotificationWorker.KEY_SESSION_ID, "zeta-main")))
            .putExtra(CallForegroundService.EXTRA_TIMEZONE, clean(prefs.getString(ProactiveNotificationWorker.KEY_TIMEZONE, "Asia/Taipei")))
            .putExtra(CallForegroundService.EXTRA_CONTEXT_MESSAGES, "[]")
            .putExtra(CallForegroundService.EXTRA_INVITE_ID, inviteId);
        CallForegroundService.start(this, call);
        startActivity(new Intent(this, MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP));
        finish();
    }

    private void decline() {
        IncomingCallNotifier.cancel(this);
        respond("decline");
        finishAndRemoveTask();
    }

    private void respond(String action) {
        if (inviteId.isEmpty()) return;
        SharedPreferences prefs = getSharedPreferences(ProactiveNotificationWorker.PREFS_NAME, 0);
        String baseUrl = clean(prefs.getString(ProactiveNotificationWorker.KEY_BASE_URL, "")).replaceAll("/+$", "");
        String token = clean(prefs.getString(ProactiveNotificationWorker.KEY_TOKEN, ""));
        if (baseUrl.isEmpty()) return;
        ExecutorService executor = Executors.newSingleThreadExecutor();
        executor.execute(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("action", action);
                GatewayHttp.post(baseUrl + "/api/call/invite/" + inviteId + "/answer", token, body);
            } catch (Exception ignored) {
            } finally {
                executor.shutdown();
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_MICROPHONE && grantResults.length > 0
            && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            answer();
        }
    }

    private TextView label(String text, float size, int color) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER);
        return view;
    }

    private Button button(String text, int color) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextSize(17);
        button.setTextColor(Color.WHITE);
        button.setBackgroundColor(color);
        return button;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
