package io.github.jdbjdncncmax.ombrebrain;

import org.json.JSONObject;

import java.util.concurrent.TimeUnit;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

final class GatewayHttp {
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private static final OkHttpClient CLIENT = new OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build();

    private GatewayHttp() {}

    static JSONObject get(String url, String token) throws Exception {
        return execute(new Request.Builder().url(url), token, null);
    }

    static JSONObject post(String url, String token, JSONObject body) throws Exception {
        return execute(new Request.Builder().url(url), token, body == null ? new JSONObject() : body);
    }

    private static JSONObject execute(Request.Builder builder, String token, JSONObject body) throws Exception {
        if (token != null && !token.trim().isEmpty()) {
            builder.header("Authorization", "Bearer " + token.trim());
            builder.header("x-api-key", token.trim());
        }
        if (body == null) {
            builder.get();
        } else {
            builder.post(RequestBody.create(body.toString(), JSON));
        }
        try (Response response = CLIENT.newCall(builder.build()).execute()) {
            String text = response.body() == null ? "" : response.body().string();
            if (!response.isSuccessful()) {
                throw new IllegalStateException("HTTP " + response.code());
            }
            return text.trim().isEmpty() ? new JSONObject() : new JSONObject(text);
        }
    }
}
