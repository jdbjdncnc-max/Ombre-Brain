package io.github.jdbjdncncmax.ombrebrain;

import androidx.annotation.NonNull;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

final class CallSocketClient {
    interface Listener {
        void onOpen();
        void onJson(JSONObject message);
        void onAudio(byte[] pcm);
        void onFailure(String message);
        void onClosed();
    }

    private final OkHttpClient client;
    private final String backendUrl;
    private final String gatewayToken;
    private final String sessionId;
    private final String timezone;
    private final String contextMessages;
    private final String inviteId;
    private final Listener listener;
    private WebSocket socket;

    CallSocketClient(
        String backendUrl,
        String gatewayToken,
        String sessionId,
        String timezone,
        String contextMessages,
        String inviteId,
        Listener listener
    ) {
        this.backendUrl = backendUrl;
        this.gatewayToken = gatewayToken;
        this.sessionId = sessionId;
        this.timezone = timezone;
        this.contextMessages = contextMessages;
        this.inviteId = inviteId;
        this.listener = listener;
        this.client = new OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build();
    }

    void connect() {
        Request.Builder request = new Request.Builder().url(webSocketUrl(backendUrl));
        if (!gatewayToken.isEmpty()) {
            request.header("Authorization", "Bearer " + gatewayToken);
            request.header("x-api-key", gatewayToken);
        }
        socket = client.newWebSocket(request.build(), new WebSocketListener() {
            @Override
            public void onOpen(@NonNull WebSocket webSocket, @NonNull Response response) {
                JSONObject start = new JSONObject();
                try {
                    start.put("type", "start");
                    start.put("sessionId", sessionId);
                    start.put("timezone", timezone);
                    start.put("contextMessages", new JSONArray(contextMessages));
                    start.put("inviteId", inviteId);
                } catch (Exception ignored) {}
                webSocket.send(start.toString());
                listener.onOpen();
            }

            @Override
            public void onMessage(@NonNull WebSocket webSocket, @NonNull String text) {
                try {
                    listener.onJson(new JSONObject(text));
                } catch (Exception error) {
                    listener.onFailure("服务器返回了无法识别的通话消息。");
                }
            }

            @Override
            public void onMessage(@NonNull WebSocket webSocket, @NonNull ByteString bytes) {
                listener.onAudio(bytes.toByteArray());
            }

            @Override
            public void onFailure(@NonNull WebSocket webSocket, @NonNull Throwable error, Response response) {
                listener.onFailure(error.getMessage() == null ? "通话连接失败。" : error.getMessage());
            }

            @Override
            public void onClosed(@NonNull WebSocket webSocket, int code, @NonNull String reason) {
                listener.onClosed();
            }
        });
    }

    void sendControl(String type) {
        WebSocket active = socket;
        if (active == null) return;
        JSONObject message = new JSONObject();
        try {
            message.put("type", type);
        } catch (Exception ignored) {}
        active.send(message.toString());
    }

    void sendAudio(byte[] frame) {
        WebSocket active = socket;
        if (active != null && frame.length > 0) {
            active.send(ByteString.of(frame));
        }
    }

    void close() {
        WebSocket active = socket;
        socket = null;
        if (active != null) {
            active.close(1000, "hangup");
        }
        client.dispatcher().executorService().shutdown();
    }

    private static String webSocketUrl(String value) {
        URI uri = URI.create(value.replaceAll("/+$", ""));
        String scheme = "https".equalsIgnoreCase(uri.getScheme()) ? "wss" : "ws";
        String path = uri.getPath() == null ? "" : uri.getPath().replaceAll("/+$", "");
        try {
            return new URI(
                scheme,
                uri.getUserInfo(),
                uri.getHost(),
                uri.getPort(),
                path + "/api/call/ws",
                null,
                null
            ).toString();
        } catch (Exception error) {
            throw new IllegalArgumentException("后端通话地址无效。", error);
        }
    }
}
