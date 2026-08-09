import { createBrowserPlatform } from "./platform.browser.js";

export function createAndroidPlatform(bridge, { capacitorPlugin = false } = {}) {
  const fallback = createBrowserPlatform();

  return {
    kind: "android",
    storage: createBridgeStorageAdapter(bridge, fallback.storage),
    notifications: createBridgeNotificationAdapter(bridge, fallback.notifications, capacitorPlugin),
    health: createBridgeHealthAdapter(bridge, fallback.health, capacitorPlugin),
    deviceContext: createBridgeDeviceContextAdapter(bridge, fallback.deviceContext, capacitorPlugin),
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
    openExternalUrl(url) {
      const target = String(url || "").trim();
      if (!target) {
        return false;
      }
      if (callBridgeVoid(bridge, "openExternalUrl", target)) {
        return true;
      }
      return fallback.openExternalUrl(target);
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

function createBridgeDeviceContextAdapter(bridge, fallback, capacitorPlugin) {
  const methodMap = {
    deviceContextStatus: "status",
    requestLocationPermission: "requestLocationPermission",
    openUsageAccessSettings: "openUsageAccessSettings",
    readDeviceContext: "readSnapshot"
  };
  const invoke = async (method) => {
    if (!hasBridgeMethod(bridge, method)) {
      return fallback[methodMap[method]]();
    }
    if (capacitorPlugin) {
      return await bridge[method]({});
    }
    const value = callBridgeString(bridge, method);
    if (!value) {
      return {};
    }
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  };

  return {
    isSupported() {
      return hasBridgeMethod(bridge, "readDeviceContext");
    },
    status() {
      return invoke("deviceContextStatus");
    },
    requestLocationPermission() {
      return invoke("requestLocationPermission");
    },
    openUsageAccessSettings() {
      return invoke("openUsageAccessSettings");
    },
    readSnapshot() {
      return invoke("readDeviceContext");
    }
  };
}

function createBridgeHealthAdapter(bridge, fallback, capacitorPlugin) {
  const invoke = async (method) => {
    if (!hasBridgeMethod(bridge, method)) {
      return fallback[method === "healthStatus" ? "status" : method === "requestHealthPermissions" ? "requestPermissions" : "readSnapshot"]();
    }
    if (capacitorPlugin) {
      return await bridge[method]({});
    }
    const value = callBridgeString(bridge, method);
    if (!value) {
      return {};
    }
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  };

  return {
    isSupported() {
      return hasBridgeMethod(bridge, "readHealthSnapshot");
    },
    status() {
      return invoke("healthStatus");
    },
    requestPermissions() {
      return invoke("requestHealthPermissions");
    },
    readSnapshot() {
      return invoke("readHealthSnapshot");
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

function createBridgeNotificationAdapter(bridge, fallback, capacitorPlugin) {
  let cachedPermission = "prompt";

  return {
    isSupported() {
      return hasBridgeMethod(bridge, "showNotification") || fallback.isSupported();
    },
    permission() {
      if (capacitorPlugin && hasBridgeMethod(bridge, "notificationPermission")) {
        Promise.resolve(bridge.notificationPermission({}))
          .then((value) => {
            cachedPermission = permissionValue(value, cachedPermission);
          })
          .catch(() => {});
        return cachedPermission;
      }
      const value = callBridgeString(bridge, "notificationPermission");
      return value || fallback.permission();
    },
    async requestPermission() {
      if (capacitorPlugin && hasBridgeMethod(bridge, "requestNotificationPermission")) {
        try {
          const value = await bridge.requestNotificationPermission({});
          cachedPermission = permissionValue(value, cachedPermission);
          return cachedPermission;
        } catch {
          return cachedPermission;
        }
      }
      const value = callBridgeString(bridge, "requestNotificationPermission");
      return value || fallback.requestPermission();
    },
    show(title, body, options = {}) {
      if (capacitorPlugin && hasBridgeMethod(bridge, "showNotification")) {
        bridge.showNotification({
          title: String(title || ""),
          body: String(body || ""),
          options
        }).catch(() => {});
        return true;
      }
      if (callBridgeVoid(bridge, "showNotification", String(title || ""), String(body || ""), JSON.stringify(options))) {
        return true;
      }
      return fallback.show(title, body, options);
    },
    async configureProactive({ backendUrl = "", gatewayToken = "", title = "" } = {}) {
      if (!hasBridgeMethod(bridge, "configureProactiveNotifications")) {
        return { configured: false, native: false };
      }
      if (capacitorPlugin) {
        const result = await bridge.configureProactiveNotifications({
          backendUrl: String(backendUrl || ""),
          gatewayToken: String(gatewayToken || ""),
          title: String(title || "")
        });
        return { ...(result || {}), native: true };
      }
      callBridgeVoid(
        bridge,
        "configureProactiveNotifications",
        String(backendUrl || ""),
        String(gatewayToken || ""),
        String(title || "")
      );
      return { configured: Boolean(backendUrl), native: true };
    }
  };
}

function permissionValue(value, fallback = "prompt") {
  const raw = typeof value === "string" ? value : value?.value;
  const normalized = String(raw || "").trim().toLowerCase();
  return ["granted", "denied", "prompt"].includes(normalized) ? normalized : fallback;
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
