const storageKeys = {
  messages: "companion.messages.v1",
  settings: "companion.settings.v1",
  sessionId: "companion.sessionId.v1"
};

const defaultBackend = location.protocol === "file:" ? "http://localhost:8000" : location.origin;
const defaultSettings = {
  backendUrl: defaultBackend,
  gatewayToken: "",
  model: "zeta-gateway",
  temperature: 0.7,
  systemPrompt: "你是一个温柔、清醒、可靠的个人 AI 伙伴。回答自然具体，优先帮助用户把事情推进。",
  backgroundUrl: "",
  glassOpacity: 0.68,
  accentColor: "#17a897"
};

const state = {
  messages: loadJson(storageKeys.messages, []),
  settings: { ...defaultSettings, ...loadJson(storageKeys.settings, {}) },
  activeTab: "chat",
  busy: false,
  sessionId: localStorage.getItem(storageKeys.sessionId) || crypto.randomUUID()
};

localStorage.setItem(storageKeys.sessionId, state.sessionId);

const els = {
  clock: document.querySelector("#clock"),
  serverStatus: document.querySelector("#serverStatus"),
  chatView: document.querySelector("#chatView"),
  settingsView: document.querySelector("#settingsView"),
  messageList: document.querySelector("#messageList"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  newChatButton: document.querySelector("#newChatButton"),
  backendUrl: document.querySelector("#backendUrl"),
  gatewayToken: document.querySelector("#gatewayToken"),
  modelName: document.querySelector("#modelName"),
  temperature: document.querySelector("#temperature"),
  temperatureValue: document.querySelector("#temperatureValue"),
  systemPrompt: document.querySelector("#systemPrompt"),
  backgroundUrl: document.querySelector("#backgroundUrl"),
  chooseBackgroundButton: document.querySelector("#chooseBackgroundButton"),
  resetBackgroundButton: document.querySelector("#resetBackgroundButton"),
  backgroundFile: document.querySelector("#backgroundFile"),
  glassOpacity: document.querySelector("#glassOpacity"),
  glassOpacityValue: document.querySelector("#glassOpacityValue"),
  accentColor: document.querySelector("#accentColor"),
  saveSettingsButton: document.querySelector("#saveSettingsButton"),
  clearChatButton: document.querySelector("#clearChatButton"),
  tabs: [...document.querySelectorAll(".tab-button")]
};

bindEvents();
hydrateSettingsForm();
applySettings();
renderMessages();
updateClock();
refreshHealth();
setInterval(updateClock, 30000);

function bindEvents() {
  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  els.messageInput.addEventListener("input", () => {
    autosizeTextarea(els.messageInput);
  });

  els.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  els.tabs.forEach((tab) => {
    tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
  });

  els.newChatButton.addEventListener("click", () => {
    state.messages = [];
    state.sessionId = crypto.randomUUID();
    localStorage.setItem(storageKeys.sessionId, state.sessionId);
    saveMessages();
    renderMessages();
  });

  els.saveSettingsButton.addEventListener("click", () => {
    readSettingsForm();
    saveSettings();
    applySettings();
    refreshHealth();
  });

  els.clearChatButton.addEventListener("click", () => {
    state.messages = [];
    saveMessages();
    renderMessages();
  });

  els.chooseBackgroundButton.addEventListener("click", () => {
    els.backgroundFile.click();
  });

  els.backgroundFile.addEventListener("change", () => {
    const file = els.backgroundFile.files?.[0];
    if (!file) {
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      els.backgroundUrl.value = String(reader.result || "");
      readSettingsForm();
      saveSettings();
      applySettings();
    };
    reader.readAsDataURL(file);
  });

  els.resetBackgroundButton.addEventListener("click", () => {
    els.backgroundUrl.value = "";
    readSettingsForm();
    saveSettings();
    applySettings();
  });

  for (const input of [
    els.backendUrl,
    els.gatewayToken,
    els.modelName,
    els.temperature,
    els.systemPrompt,
    els.backgroundUrl,
    els.glassOpacity,
    els.accentColor
  ]) {
    input.addEventListener("change", () => {
      readSettingsForm();
      saveSettings();
      applySettings();
    });
  }

  els.temperature.addEventListener("input", () => {
    els.temperatureValue.value = Number(els.temperature.value).toFixed(1);
  });

  els.glassOpacity.addEventListener("input", () => {
    els.glassOpacityValue.value = `${Math.round(Number(els.glassOpacity.value) * 100)}%`;
    document.documentElement.style.setProperty("--panel-alpha", els.glassOpacity.value);
  });
}

async function sendMessage() {
  const text = els.messageInput.value.trim();
  if (!text || state.busy) {
    return;
  }

  readSettingsForm();
  saveSettings();
  applySettings();

  const userMessage = {
    id: crypto.randomUUID(),
    role: "user",
    content: text,
    createdAt: new Date().toISOString()
  };
  const assistantMessage = {
    id: crypto.randomUUID(),
    role: "assistant",
    content: "",
    pending: true,
    createdAt: new Date().toISOString()
  };

  state.messages.push(userMessage, assistantMessage);
  els.messageInput.value = "";
  autosizeTextarea(els.messageInput);
  setBusy(true);
  renderMessages();

  try {
    const response = await fetch(`${cleanBaseUrl(state.settings.backendUrl)}/v1/chat/completions`, {
      method: "POST",
      headers: buildGatewayHeaders(),
      body: JSON.stringify({
        model: state.settings.model,
        messages: buildRequestMessages(),
        temperature: state.settings.temperature,
        stream: true
      })
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error?.message || error.message || `请求失败：${response.status}`);
    }

    if (!response.body) {
      throw new Error("后端没有返回流式内容。");
    }

    await readOpenAiStream(response.body, (token) => {
      assistantMessage.content += token;
      renderMessages(false);
    });

    assistantMessage.pending = false;
    if (!assistantMessage.content.trim()) {
      assistantMessage.content = "我这边没有收到有效回复。";
    }
  } catch (error) {
    assistantMessage.pending = false;
    assistantMessage.content = error instanceof Error ? error.message : String(error);
  } finally {
    setBusy(false);
    saveMessages();
    renderMessages();
  }
}

function buildGatewayHeaders() {
  const headers = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "X-Ombre-Session-Id": state.sessionId
  };

  if (state.settings.gatewayToken) {
    headers.Authorization = `Bearer ${state.settings.gatewayToken}`;
    headers["x-api-key"] = state.settings.gatewayToken;
  }

  return headers;
}

function buildRequestMessages() {
  const messages = state.messages
    .filter((message) => !message.pending)
    .map(({ role, content }) => ({ role, content }));

  if (state.settings.systemPrompt) {
    return [
      { role: "system", content: state.settings.systemPrompt },
      ...messages
    ];
  }

  return messages;
}

async function readOpenAiStream(stream, onToken) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      dispatchOpenAiBlock(block, onToken);
      boundary = buffer.indexOf("\n\n");
    }
  }

  if (buffer.trim()) {
    dispatchOpenAiBlock(buffer, onToken);
  }
}

function dispatchOpenAiBlock(block, onToken) {
  const lines = block.split(/\r?\n/);
  const dataLines = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  for (const rawData of dataLines) {
    if (!rawData || rawData === "[DONE]") {
      continue;
    }

    const parsed = JSON.parse(rawData);
    if (parsed.error) {
      throw new Error(parsed.error.message || "模型请求失败。");
    }

    const token =
      parsed.choices?.[0]?.delta?.content ||
      parsed.choices?.[0]?.message?.content ||
      "";
    if (token) {
      onToken(token);
    }
  }
}

function renderMessages(shouldScroll = true) {
  els.messageList.innerHTML = "";

  if (!state.messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "今天想从哪里开始？";
    els.messageList.append(empty);
    return;
  }

  for (const message of state.messages) {
    const item = document.createElement("article");
    item.className = `message ${message.role}${message.pending ? " pending" : ""}`;

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content || " ";
    item.append(content);
    els.messageList.append(item);
  }

  if (shouldScroll) {
    els.messageList.scrollTop = els.messageList.scrollHeight;
  }
}

function setActiveTab(tab) {
  state.activeTab = tab;
  els.chatView.classList.toggle("view-active", tab === "chat");
  els.settingsView.classList.toggle("view-active", tab === "settings");
  els.tabs.forEach((button) => {
    button.classList.toggle("tab-active", button.dataset.tab === tab);
  });
}

function hydrateSettingsForm() {
  els.backendUrl.value = state.settings.backendUrl;
  els.gatewayToken.value = state.settings.gatewayToken;
  els.modelName.value = state.settings.model;
  els.temperature.value = state.settings.temperature;
  els.temperatureValue.value = Number(state.settings.temperature).toFixed(1);
  els.systemPrompt.value = state.settings.systemPrompt;
  els.backgroundUrl.value = state.settings.backgroundUrl;
  els.glassOpacity.value = state.settings.glassOpacity;
  els.glassOpacityValue.value = `${Math.round(Number(state.settings.glassOpacity) * 100)}%`;
  els.accentColor.value = state.settings.accentColor;
}

function readSettingsForm() {
  state.settings = {
    backendUrl: els.backendUrl.value.trim() || defaultBackend,
    gatewayToken: els.gatewayToken.value.trim(),
    model: els.modelName.value.trim() || defaultSettings.model,
    temperature: Number(els.temperature.value),
    systemPrompt: els.systemPrompt.value.trim(),
    backgroundUrl: els.backgroundUrl.value.trim(),
    glassOpacity: Number(els.glassOpacity.value),
    accentColor: els.accentColor.value || defaultSettings.accentColor
  };
}

function applySettings() {
  const root = document.documentElement;
  root.style.setProperty("--accent", state.settings.accentColor);
  root.style.setProperty("--panel-alpha", String(state.settings.glassOpacity));
  root.style.setProperty("--accent-ink", readableInkFor(state.settings.accentColor));

  if (state.settings.backgroundUrl) {
    root.style.setProperty("--bg-image", `url("${cssUrlEscape(state.settings.backgroundUrl)}")`);
  } else {
    root.style.setProperty("--bg-image", 'url("/frontend/background.svg")');
  }
}

async function refreshHealth() {
  const url = cleanBaseUrl(state.settings.backendUrl);
  try {
    const response = await fetch(`${url}/health`);
    const health = await response.json();
    if (!response.ok || health.status === "startup_error") {
      throw new Error(health.error || "启动异常");
    }

    const ready = Boolean(health.upstream_ready || health.hasApiKey);
    els.serverStatus.textContent = ready ? "网关在线" : "待配置";
    els.serverStatus.classList.add(ready ? "online" : "offline");
    els.serverStatus.classList.remove(ready ? "offline" : "online");

    if (health.model && state.settings.model === defaultSettings.model) {
      state.settings.model = health.model;
      els.modelName.value = health.model;
      saveSettings();
    }
  } catch {
    els.serverStatus.textContent = "未连接";
    els.serverStatus.classList.add("offline");
    els.serverStatus.classList.remove("online");
  }
}

function updateClock() {
  els.clock.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date());
}

function setBusy(value) {
  state.busy = value;
  els.sendButton.disabled = value;
  els.messageInput.disabled = value;
}

function autosizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
}

function saveMessages() {
  localStorage.setItem(storageKeys.messages, JSON.stringify(state.messages.slice(-80)));
}

function saveSettings() {
  localStorage.setItem(storageKeys.settings, JSON.stringify(state.settings));
}

function loadJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || "") || fallback;
  } catch {
    return fallback;
  }
}

function cleanBaseUrl(url) {
  return String(url || defaultBackend).replace(/\/+$/, "");
}

function cssUrlEscape(value) {
  return String(value).replace(/["\\\n\r]/g, "");
}

function readableInkFor(color) {
  const hex = color.replace("#", "");
  if (hex.length !== 6) {
    return "#073b35";
  }

  const red = parseInt(hex.slice(0, 2), 16);
  const green = parseInt(hex.slice(2, 4), 16);
  const blue = parseInt(hex.slice(4, 6), 16);
  const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
  return luminance > 0.62 ? "#17211f" : color;
}
