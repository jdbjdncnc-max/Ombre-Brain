package io.github.jdbjdncncmax.ombrebrain;

import org.json.JSONObject;

import java.util.Set;
import java.util.concurrent.CopyOnWriteArraySet;

final class CallEventBus {
    interface Listener {
        void onCallEvent(JSONObject event);
    }

    private static final Set<Listener> LISTENERS = new CopyOnWriteArraySet<>();
    private static volatile JSONObject lastEvent = stateEvent("idle");

    private CallEventBus() {}

    static void addListener(Listener listener) {
        LISTENERS.add(listener);
        listener.onCallEvent(lastEvent);
    }

    static void removeListener(Listener listener) {
        LISTENERS.remove(listener);
    }

    static void emit(JSONObject event) {
        lastEvent = event;
        for (Listener listener : LISTENERS) {
            listener.onCallEvent(event);
        }
    }

    static JSONObject lastEvent() {
        return lastEvent;
    }

    static JSONObject stateEvent(String state) {
        JSONObject event = new JSONObject();
        try {
            event.put("type", "state");
            event.put("state", state);
        } catch (Exception ignored) {}
        return event;
    }
}
