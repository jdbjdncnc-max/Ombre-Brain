package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.content.Intent;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONObject;

import java.net.URI;
import java.util.Iterator;

@CapacitorPlugin(
    name = "CallNative",
    permissions = {
        @Permission(alias = "microphone", strings = { Manifest.permission.RECORD_AUDIO })
    }
)
public class CallNativePlugin extends Plugin implements CallEventBus.Listener {
    @Override
    public void load() {
        CallEventBus.addListener(this);
    }

    @Override
    protected void handleOnDestroy() {
        CallEventBus.removeListener(this);
        super.handleOnDestroy();
    }

    @PluginMethod
    public void microphonePermission(PluginCall call) {
        JSObject result = new JSObject();
        result.put("value", microphoneGranted() ? "granted" : "prompt");
        call.resolve(result);
    }

    @PluginMethod
    public void requestMicrophonePermission(PluginCall call) {
        if (microphoneGranted()) {
            microphonePermission(call);
            return;
        }
        requestPermissionForAlias("microphone", call, "microphonePermissionCallback");
    }

    @PermissionCallback
    private void microphonePermissionCallback(PluginCall call) {
        JSObject result = new JSObject();
        result.put("value", microphoneGranted() ? "granted" : "denied");
        call.resolve(result);
    }

    @PluginMethod
    public void startCall(PluginCall call) {
        if (!microphoneGranted()) {
            call.reject("需要先允许麦克风权限。");
            return;
        }
        String backendUrl = clean(call.getString("backendUrl"));
        String gatewayToken = clean(call.getString("gatewayToken"));
        String sessionId = clean(call.getString("sessionId"));
        String timezone = clean(call.getString("timezone"));
        String contextMessages = clean(call.getString("contextMessages"));
        if (!isAllowedBaseUrl(backendUrl)) {
            call.reject("通话后端必须是 https 地址（本机调试可用 localhost）。");
            return;
        }
        if (contextMessages.length() > 300_000) {
            call.reject("通话上下文过大，请先完成一次对话总结。");
            return;
        }
        Intent intent = new Intent(getContext(), CallForegroundService.class)
            .setAction(CallForegroundService.ACTION_START)
            .putExtra(CallForegroundService.EXTRA_BACKEND_URL, backendUrl)
            .putExtra(CallForegroundService.EXTRA_GATEWAY_TOKEN, gatewayToken)
            .putExtra(CallForegroundService.EXTRA_SESSION_ID, sessionId)
            .putExtra(CallForegroundService.EXTRA_TIMEZONE, timezone)
            .putExtra(CallForegroundService.EXTRA_CONTEXT_MESSAGES, contextMessages)
            .putExtra(CallForegroundService.EXTRA_INVITE_ID, "");
        CallForegroundService.start(getContext(), intent);
        call.resolve(toJsObject(CallForegroundService.stateSnapshot()));
    }

    @PluginMethod
    public void hangup(PluginCall call) {
        sendAction(CallForegroundService.ACTION_HANGUP, false);
        call.resolve();
    }

    @PluginMethod
    public void setMuted(PluginCall call) {
        sendAction(CallForegroundService.ACTION_MUTE, Boolean.TRUE.equals(call.getBoolean("enabled")));
        call.resolve();
    }

    @PluginMethod
    public void setSpeaker(PluginCall call) {
        sendAction(CallForegroundService.ACTION_SPEAKER, Boolean.TRUE.equals(call.getBoolean("enabled")));
        call.resolve();
    }

    @PluginMethod
    public void getState(PluginCall call) {
        call.resolve(toJsObject(CallForegroundService.stateSnapshot()));
    }

    private void sendAction(String action, boolean enabled) {
        Intent intent = new Intent(getContext(), CallForegroundService.class)
            .setAction(action)
            .putExtra(CallForegroundService.EXTRA_ENABLED, enabled);
        getContext().startService(intent);
    }

    private boolean microphoneGranted() {
        return getPermissionState("microphone") == PermissionState.GRANTED;
    }

    @Override
    public void onCallEvent(JSONObject event) {
        if (getActivity() != null) {
            getActivity().runOnUiThread(() -> notifyListeners("callEvent", toJsObject(event), true));
        }
    }

    private static JSObject toJsObject(JSONObject source) {
        JSObject target = new JSObject();
        Iterator<String> keys = source.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            try {
                target.put(key, source.get(key));
            } catch (Exception ignored) {}
        }
        return target;
    }

    private static boolean isAllowedBaseUrl(String value) {
        try {
            URI uri = URI.create(value);
            String scheme = clean(uri.getScheme()).toLowerCase();
            String host = clean(uri.getHost()).toLowerCase();
            if ("https".equals(scheme) && !host.isEmpty()) return true;
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
