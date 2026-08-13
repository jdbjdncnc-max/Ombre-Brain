import { createBrowserPlatform } from "./platform.browser.js";

export function createAndroidPlatform(
  bridge,
  { capacitorPlugin = false, callBridge = null, callCapacitorPlugin = false } = {}
) {
  const fallback = createBrowserPlatform();

  return {
    kind: "android",
    storage: createBridgeStorageAdapter(bridge, fallback.storage),
    files: createBridgeFileAdapter(bridge, fallback.files, capacitorPlugin),
    notifications: createBridgeNotificationAdapter(bridge, fallback.notifications, capacitorPlugin),
    health: createBridgeHealthAdapter(bridge, fallback.health, capacitorPlugin),
    deviceContext: createBridgeDeviceContextAdapter(bridge, fallback.deviceContext, capacitorPlugin),
    call: createBridgeCallAdapter(callBridge, fallback.call, callCapacitorPlugin),
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

function createBridgeFileAdapter(bridge, fallback, capacitorPlugin) {
  return {
    isSupported() {
      return hasBridgeMethod(bridge, "saveChatExport") || fallback.isSupported();
    },
    async saveChatExport(options = {}) {
      if (!hasBridgeMethod(bridge, "saveChatExport")) {
        return fallback.saveChatExport(options);
      }
      if (capacitorPlugin) {
        return (await bridge.saveChatExport({
          filename: String(options.filename || "entangle-chat.json"),
          content: String(options.content || "")
        })) || {};
      }
      const value = callBridgeString(
        bridge,
        "saveChatExport",
        String(options.filename || "entangle-chat.json"),
        String(options.content || "")
      );
      try {
        return JSON.parse(value || "{}") || {};
      } catch {
        return {};
      }
    }
  };
}

function createBridgeCallAdapter(bridge, fallback, capacitorPlugin) {
  if (!bridge) {
    return fallback;
  }
  const invoke = async (method, payload = {}) => {
    if (!hasBridgeMethod(bridge, method)) {
      return fallback[method]?.(payload) || {};
    }
    if (capacitorPlugin) {
      return (await bridge[method](payload)) || {};
    }
    const value = callBridgeString(bridge, method, JSON.stringify(payload));
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
      return hasBridgeMethod(bridge, "startCall");
    },
    async permission() {
      const value = await invoke("microphonePermission");
      return String(value?.value || "prompt");
    },
    async requestPermission() {
      const value = await invoke("requestMicrophonePermission");
      return String(value?.value || "denied");
    },
    start(options) {
      return invoke("startCall", {
        ...options,
        contextMessages: JSON.stringify(options?.contextMessages || [])
      });
    },
    hangup() {
      return invoke("hangup");
    },
    setMuted(enabled) {
      return invoke("setMuted", { enabled: Boolean(enabled) });
    },
    setSpeaker(enabled) {
      return invoke("setSpeaker", { enabled: Boolean(enabled) });
    },
    getState() {
      return invoke("getState");
    },
    onEvent(handler) {
      if (capacitorPlugin && hasBridgeMethod(bridge, "addListener")) {
        const listener = bridge.addListener("callEvent", handler);
        return () => Promise.resolve(listener).then((handle) => handle?.remove?.()).catch(() => {});
      }
      globalThis.CompanionOnCallEvent = (value) => {
        try {
          handler(typeof value === "string" ? JSON.parse(value) : value);
        } catch {}
      };
      return () => {
        if (globalThis.CompanionOnCallEvent) {
          delete globalThis.CompanionOnCallEvent;
        }
      };
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
    },
    remove(key) {
      if (!callBridgeVoid(bridge, "removeStorage", key)) {
        fallback.remove(key);
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
    async configureProactive({ backendUrl = "", gatewayToken = "", title = "", sessionId = "", timezone = "" } = {}) {
      if (!hasBridgeMethod(bridge, "configureProactiveNotifications")) {
        return { configured: false, native: false };
      }
      if (capacitorPlugin) {
        const result = await bridge.configureProactiveNotifications({
          backendUrl: String(backendUrl || ""),
          gatewayToken: String(gatewayToken || ""),
          title: String(title || ""),
          sessionId: String(sessionId || ""),
          timezone: String(timezone || "")
        });
        return { ...(result || {}), native: true };
      }
      callBridgeVoid(
        bridge,
        "configureProactiveNotifications",
        String(backendUrl || ""),
        String(gatewayToken || ""),
        String(title || ""),
        String(sessionId || ""),
        String(timezone || "")
      );
      return { configured: Boolean(backendUrl), native: true };
    },
    async incomingCallStatus() {
      if (capacitorPlugin && hasBridgeMethod(bridge, "incomingCallStatus")) {
        return bridge.incomingCallStatus({});
      }
      return { supported: false };
    },
    async openIncomingCallSettings() {
      if (capacitorPlugin && hasBridgeMethod(bridge, "openIncomingCallSettings")) {
        return bridge.openIncomingCallSettings({});
      }
      return { supported: false };
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
