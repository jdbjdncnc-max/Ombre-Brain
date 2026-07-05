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
  memoryPanel: "memories",
  busy: false,
  sessionId: localStorage.getItem(storageKeys.sessionId) || crypto.randomUUID(),
  loadedMemoryPanels: new Set()
};

localStorage.setItem(storageKeys.sessionId, state.sessionId);

const els = {
  clock: document.querySelector("#clock"),
  serverStatus: document.querySelector("#serverStatus"),
  chatView: document.querySelector("#chatView"),
  memoryView: document.querySelector("#memoryView"),
  settingsView: document.querySelector("#settingsView"),
  messageList: document.querySelector("#messageList"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  newChatButton: document.querySelector("#newChatButton"),
  refreshMemoryButton: document.querySelector("#refreshMemoryButton"),
  memorySearchForm: document.querySelector("#memorySearchForm"),
  memorySearchInput: document.querySelector("#memorySearchInput"),
  memorySummary: document.querySelector("#memorySummary"),
  memoryList: document.querySelector("#memoryList"),
  diaryStats: document.querySelector("#diaryStats"),
  publicDiaryList: document.querySelector("#publicDiaryList"),
  userProfileText: document.querySelector("#userProfileText"),
  aiProfileText: document.querySelector("#aiProfileText"),
  saveProfileButton: document.querySelector("#saveProfileButton"),
  profileStatus: document.querySelector("#profileStatus"),
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
  tabs: [...document.querySelectorAll(".tab-button")],
  memorySegments: [...document.querySelectorAll(".segment-button")],
  memoryPanels: [...document.querySelectorAll(".memory-panel")]
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

  els.memorySegments.forEach((button) => {
    button.addEventListener("click", () => setMemoryPanel(button.dataset.memoryPanel));
  });

  els.refreshMemoryButton.addEventListener("click", () => {
    state.loadedMemoryPanels.delete(state.memoryPanel);
    loadActiveMemoryPanel();
  });

  els.memorySearchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadMemories(els.memorySearchInput.value.trim(), true);
  });

  els.saveProfileButton.addEventListener("click", saveProfile);

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
    state.loadedMemoryPanels.clear();
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
  return {
    ...buildAuthHeaders(),
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "X-Ombre-Session-Id": state.sessionId
  };
}

function buildAuthHeaders() {
  const headers = {};
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

async function gatewayFetch(path, options = {}) {
  const headers = {
    "Accept": "application/json",
    ...buildAuthHeaders(),
    ...(options.headers || {})
  };
  const response = await fetch(`${cleanBaseUrl(state.settings.backendUrl)}${path}`, {
    ...options,
    headers
  });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json().catch(() => ({})) : await response.text();
  if (!response.ok) {
    const message = typeof payload === "string" ? payload : payload.error || payload.message || "请求失败";
    throw new Error(message);
  }
  return payload;
}

async function loadActiveMemoryPanel() {
  if (state.loadedMemoryPanels.has(state.memoryPanel)) {
    return;
  }
  if (state.memoryPanel === "memories") {
    await loadMemories(els.memorySearchInput.value.trim()).catch(renderMemoryError);
  } else if (state.memoryPanel === "diary") {
    await loadDiaries().catch(renderDiaryError);
  } else {
    await loadProfile().catch(renderProfileError);
  }
  state.loadedMemoryPanels.add(state.memoryPanel);
}

async function loadMemories(query = "", force = false) {
  if (force) {
    state.loadedMemoryPanels.delete("memories");
  }
  els.memorySummary.textContent = "载入中";
  els.memoryList.replaceChildren();
  const path = query ? `/api/search?q=${encodeURIComponent(query)}` : "/api/buckets";
  const items = await gatewayFetch(path);
  const visible = Array.isArray(items) ? items.slice(0, 60) : [];
  els.memorySummary.textContent = query
    ? `找到 ${visible.length} 条`
    : `共 ${visible.length} 条`;
  renderMemoryList(visible);
}

function renderMemoryList(items) {
  els.memoryList.replaceChildren();
  if (!items.length) {
    els.memoryList.append(emptyNode("还没有可显示的记忆。"));
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "memory-item";

    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = item.name || item.id || "未命名记忆";

    const preview = document.createElement("p");
    preview.className = "item-body";
    preview.textContent = item.content_preview || item.summary_text || item.summary || "";

    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = compactParts([
      item.type,
      arrayLabel(item.domain),
      arrayLabel(item.tags),
      item.importance ? `重要度 ${item.importance}` : "",
      item.score ? `分数 ${Number(item.score).toFixed(1)}` : ""
    ]).join(" · ");

    const detailButton = document.createElement("button");
    detailButton.className = "text-button";
    detailButton.type = "button";
    detailButton.textContent = "详情";
    detailButton.addEventListener("click", () => loadMemoryDetail(item.id, preview, detailButton));

    card.append(title, preview, meta, detailButton);
    els.memoryList.append(card);
  }
}

async function loadMemoryDetail(id, target, button) {
  if (!id) {
    return;
  }
  button.disabled = true;
  button.textContent = "载入中";
  try {
    const detail = await gatewayFetch(`/api/bucket/${encodeURIComponent(id)}`);
    target.textContent = detail.content || target.textContent || "没有详情内容。";
    button.remove();
  } catch (error) {
    button.disabled = false;
    button.textContent = "重试";
    target.textContent = error instanceof Error ? error.message : String(error);
  }
}

async function loadDiaries() {
  els.publicDiaryList.replaceChildren();
  const data = await gatewayFetch("/gateway/diaries?visibility=all&limit=30&include_content=true");
  const counts = data.counts || {};
  renderDiaryStats(counts);
  renderPublicDiaries(Array.isArray(data.public) ? data.public : []);
}

function renderDiaryStats(counts) {
  const values = [
    ["公开", counts.public || 0],
    ["私密", counts.private || 0],
    ["总计", counts.total || 0]
  ];
  els.diaryStats.replaceChildren(...values.map(([label, value]) => {
    const node = document.createElement("div");
    node.className = "metric";
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    node.append(span, strong);
    return node;
  }));
}

function renderPublicDiaries(items) {
  els.publicDiaryList.replaceChildren();
  if (!items.length) {
    els.publicDiaryList.append(emptyNode("还没有公开日记。"));
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "memory-item";
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = item.title || "公开日记";
    const body = document.createElement("p");
    body.className = "item-body";
    body.textContent = item.content || item.summary_text || "";
    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = compactParts([
      formatDate(item.created),
      item.mood,
      arrayLabel(item.tags)
    ]).join(" · ");
    card.append(title, body, meta);
    els.publicDiaryList.append(card);
  }
}

async function loadProfile() {
  els.profileStatus.textContent = "载入中";
  const data = await gatewayFetch("/api/profile");
  els.userProfileText.value = data.user || "";
  els.aiProfileText.value = data.ai || "";
  els.profileStatus.textContent = data.updated_at ? `更新于 ${formatDate(data.updated_at)}` : "";
}

async function saveProfile() {
  els.saveProfileButton.disabled = true;
  els.profileStatus.textContent = "保存中";
  try {
    const data = await gatewayFetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user: els.userProfileText.value,
        ai: els.aiProfileText.value,
        source: "frontend"
      })
    });
    els.profileStatus.textContent = data.updated_at ? `已保存 ${formatDate(data.updated_at)}` : "已保存";
  } catch (error) {
    els.profileStatus.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    els.saveProfileButton.disabled = false;
  }
}

function renderMemoryError(error) {
  els.memorySummary.textContent = "无法载入";
  els.memoryList.replaceChildren(emptyNode(error instanceof Error ? error.message : String(error)));
}

function renderDiaryError(error) {
  els.publicDiaryList.replaceChildren(emptyNode(error instanceof Error ? error.message : String(error)));
}

function renderProfileError(error) {
  els.profileStatus.textContent = error instanceof Error ? error.message : String(error);
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
    els.messageList.append(emptyNode("今天想从哪里开始？", "empty-state"));
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
  els.memoryView.classList.toggle("view-active", tab === "memory");
  els.settingsView.classList.toggle("view-active", tab === "settings");
  els.tabs.forEach((button) => {
    button.classList.toggle("tab-active", button.dataset.tab === tab);
  });
  if (tab === "memory") {
    loadActiveMemoryPanel();
  }
}

function setMemoryPanel(panel) {
  state.memoryPanel = panel;
  els.memorySegments.forEach((button) => {
    button.classList.toggle("segment-active", button.dataset.memoryPanel === panel);
  });
  els.memoryPanels.forEach((panelEl) => {
    panelEl.classList.toggle("panel-active", panelEl.id === `${panel}Panel`);
  });
  loadActiveMemoryPanel();
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

function emptyNode(text, className = "empty-inline") {
  const node = document.createElement("div");
  node.className = className;
  node.textContent = text;
  return node;
}

function arrayLabel(value) {
  return Array.isArray(value) ? value.filter(Boolean).slice(0, 4).join(", ") : "";
}

function compactParts(parts) {
  return parts.map((part) => String(part || "").trim()).filter(Boolean);
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value).slice(0, 16);
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}
