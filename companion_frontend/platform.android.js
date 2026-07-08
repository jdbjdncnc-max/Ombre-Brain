import { createBrowserPlatform } from "./platform.browser.js";

export function createAndroidPlatform(bridge) {
  const fallback = createBrowserPlatform();

  return {
    kind: "android",
    storage: createBridgeStorageAdapter(bridge, fallback.storage),
    notifications: createBridgeNotificationAdapter(bridge, fallback.notifications),
    getDefaultApiBaseUrl() {
      const bridgeUrl = callBridgeString(bridge, "getApiBaseUrl");
      return bridgeUrl || fallback.getDefaultApiBaseUrl();
    },
    request(url, options = {}) {
      return fallback.request(url, options);
    },
    readFileAsDataUrl(file) {
      return fallback.readFileAsDataUrl(file);
    },
    lifecycle: {
      onResume(handler) {
        window.CompanionOnResume = handler;
      },
      onPause(handler) {
        window.CompanionOnPause = handler;
      }
    }
  };
}

function createBridgeStorageAdapter(bridge, fallback) {
  return {
    getString(key, fallbackValue = "") {
      const value = callBridgeString(bridge, "getStorage", key);
      return value || fallback.getString(key, fallbackValue);
    },
    setString(key, value) {
      if (!callBridgeVoid(bridge, "setStorage", key, String(value))) {
        fallback.setString(key, value);
      }
    },
    getJson(key, fallbackValue) {
      const value = callBridgeString(bridge, "getStorage", key);
      if (value) {
        try {
          return JSON.parse(value) || fallbackValue;
        } catch {}
      }
      return fallback.getJson(key, fallbackValue);
    },
    setJson(key, value) {
      const text = JSON.stringify(value);
      if (!callBridgeVoid(bridge, "setStorage", key, text)) {
        fallback.setJson(key, value);
      }
    }
  };
}

function createBridgeNotificationAdapter(bridge, fallback) {
  return {
    isSupported() {
      return hasBridgeMethod(bridge, "showNotification") || fallback.isSupported();
    },
    permission() {
      const value = callBridgeString(bridge, "notificationPermission");
      return value || fallback.permission();
    },
    async requestPermission() {
      const value = callBridgeString(bridge, "requestNotificationPermission");
      return value || fallback.requestPermission();
    },
    show(title, body, options = {}) {
      if (callBridgeVoid(bridge, "showNotification", String(title || ""), String(body || ""), JSON.stringify(options))) {
        return true;
      }
      return fallback.show(title, body, options);
    }
  };
}

function hasBridgeMethod(bridge, method) {
  return Boolean(bridge && typeof bridge[method] === "function");
}

function callBridgeString(bridge, method, ...args) {
  if (!hasBridgeMethod(bridge, method)) {
    return "";
  }
  try {
    const value = bridge[method](...args);
    return typeof value === "string" ? value.trim() : "";
  } catch {
    return "";
  }
}

function callBridgeVoid(bridge, method, ...args) {
  if (!hasBridgeMethod(bridge, method)) {
    return false;
  }
  try {
    bridge[method](...args);
    return true;
  } catch {
    return false;
  }
}
