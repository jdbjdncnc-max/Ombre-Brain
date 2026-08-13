export function createBrowserPlatform() {
  return {
    kind: "browser",
    storage: createLocalStorageAdapter(),
    files: createBrowserFileAdapter(),
    notifications: createBrowserNotificationAdapter(),
    health: createBrowserHealthAdapter(),
    deviceContext: createBrowserDeviceContextAdapter(),
    call: createBrowserCallAdapter(),
    getDefaultApiBaseUrl,
    request,
    readFileAsDataUrl,
    openExternalUrl,
    lifecycle: {
      onResume() {},
      onPause() {}
    }
  };
}

function createBrowserCallAdapter() {
  const unsupported = async () => ({ status: "unsupported", active: false });
  return {
    isSupported() {
      return false;
    },
    permission: async () => "unsupported",
    requestPermission: async () => "unsupported",
    start: unsupported,
    hangup: unsupported,
    setMuted: unsupported,
    setSpeaker: unsupported,
    getState: unsupported,
    onEvent() {
      return () => {};
    }
  };
}

function createBrowserDeviceContextAdapter() {
  const unsupported = async () => ({
    status: "unsupported",
    supported: false,
    locationPermission: "unsupported",
    usageAccess: "unsupported"
  });
  return {
    isSupported() {
      return false;
    },
    status: unsupported,
    requestLocationPermission: unsupported,
    openUsageAccessSettings: unsupported,
    readSnapshot: unsupported
  };
}

function createBrowserHealthAdapter() {
  return {
    isSupported() {
      return false;
    },
    async status() {
      return { status: "unsupported", supported: false, permission: "unsupported" };
    },
    async requestPermissions() {
      return { status: "unsupported", supported: false, permission: "unsupported" };
    },
    async readSnapshot() {
      return { status: "unsupported", supported: false, permission: "unsupported" };
    }
  };
}

function createLocalStorageAdapter() {
  return {
    getString(key, fallback = "") {
      try {
        return localStorage.getItem(key) || fallback;
      } catch {
        return fallback;
      }
    },
    setString(key, value) {
      try {
        localStorage.setItem(key, String(value));
      } catch {}
    },
    getJson(key, fallback) {
      try {
        return JSON.parse(localStorage.getItem(key) || "") || fallback;
      } catch {
        return fallback;
      }
    },
    setJson(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {}
    },
    remove(key) {
      try {
        localStorage.removeItem(key);
      } catch {}
    }
  };
}

function createBrowserFileAdapter() {
  return {
    isSupported() {
      return true;
    },
    async saveChatExport({ filename = "entangle-chat.json", content = "" } = {}) {
      const blob = new Blob([String(content)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = String(filename || "entangle-chat.json");
      link.rel = "noopener";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      return { saved: true, path: "浏览器下载目录" };
    }
  };
}

function createBrowserNotificationAdapter() {
  return {
    isSupported() {
      return "Notification" in window;
    },
    permission() {
      return "Notification" in window ? Notification.permission : "unsupported";
    },
    async requestPermission() {
      if (!("Notification" in window)) {
        return "unsupported";
      }
      return Notification.requestPermission();
    },
    show(title, body, options = {}) {
      if (!("Notification" in window) || Notification.permission !== "granted") {
        return false;
      }
      new Notification(title, {
        body,
        silent: false,
        ...options
      });
      return true;
    },
    async configureProactive() {
      return { configured: false, native: false };
    },
    async incomingCallStatus() {
      return { supported: false };
    },
    async openIncomingCallSettings() {
      return { supported: false };
    }
  };
}

function getDefaultApiBaseUrl() {
  if (location.protocol === "http:" || location.protocol === "https:") {
    return location.origin;
  }
  return "";
}

async function request(url, options = {}) {
  try {
    return await fetch(url, options);
  } catch (error) {
    throw new Error(networkErrorMessage(url, error));
  }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("无法读取文件。"));
    reader.readAsDataURL(file);
  });
}

function openExternalUrl(url) {
  const target = String(url || "").trim();
  if (!target) {
    return false;
  }
  const opened = window.open(target, "_blank", "noopener,noreferrer");
  if (!opened) {
    window.location.assign(target);
  }
  return true;
}

function networkErrorMessage(url, error) {
  const detail = error instanceof Error ? error.message : String(error || "");
  return [
    "网络连接失败。",
    "请检查设置里的后端地址、网关令牌、部署状态，以及当前网络是否能访问该地址。",
    `请求地址：${String(url || "").slice(0, 180)}`,
    detail ? `底层错误：${detail}` : ""
  ].filter(Boolean).join("\n");
}
