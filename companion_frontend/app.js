import { platform } from "./platform.js";

const storageKeys = {
  messages: "companion.messages.v1",
  settings: "companion.settings.v1",
  sessionId: "companion.sessionId.v1",
  schedule: "companion.schedule.v1",
  scheduleSettings: "companion.scheduleSettings.v1",
  scheduleNudges: "companion.scheduleNudges.v1"
};

const defaultBackend = platform.getDefaultApiBaseUrl();
const defaultSettings = {
  backendUrl: defaultBackend,
  gatewayToken: "",
  model: "zeta-gateway",
  temperature: 0.7,
  backgroundUrl: "",
  backgroundTransparency: 0.35,
  backgroundFit: "cover",
  accentColor: "#17a897",
  duettoUrl: ""
};

const COURSE_PERIODS = [
  { index: 1, start: "08:00", end: "08:45" },
  { index: 2, start: "08:55", end: "09:40" },
  { index: 3, start: "10:00", end: "10:45" },
  { index: 4, start: "10:55", end: "11:40" },
  { index: 5, start: "13:30", end: "14:15" },
  { index: 6, start: "14:25", end: "15:10" },
  { index: 7, start: "15:30", end: "16:15" },
  { index: 8, start: "16:25", end: "17:10" },
  { index: 9, start: "18:00", end: "18:45" },
  { index: 10, start: "18:55", end: "19:40" },
  { index: 11, start: "19:50", end: "20:35" },
  { index: 12, start: "20:45", end: "21:30" }
];

const COURSE_COLORS = [
  "#73a8ef",
  "#d9bd86",
  "#8fd2c5",
  "#63c9da",
  "#d96d93",
  "#78a97f",
  "#b4c875",
  "#afa9dc",
  "#9fcfad",
  "#d39abf"
];

const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

const state = {
  messages: loadJson(storageKeys.messages, []),
  settings: normalizeSettings(loadJson(storageKeys.settings, {})),
  activeTab: "chat",
  memoryPanel: "memories",
  memoryItems: [],
  memoryQuery: "",
  memoryVisibleCount: 80,
  scheduleItems: normalizeScheduleItems(loadJson(storageKeys.schedule, [])),
  scheduleSettings: normalizeScheduleSettings(loadJson(storageKeys.scheduleSettings, {})),
  visibleWeekStart: dateKey(startOfWeek(new Date())),
  scheduleNudges: loadJson(storageKeys.scheduleNudges, {}),
  scheduleSyncStatus: "本地日程",
  busy: false,
  editingMessageId: "",
  systemPrompt: "",
  systemPromptError: "",
  sessionId: platform.storage.getString(storageKeys.sessionId) || crypto.randomUUID(),
  loadedMemoryPanels: new Set()
};

platform.storage.setString(storageKeys.sessionId, state.sessionId);

const els = {
  clock: document.querySelector("#clock"),
  serverStatus: document.querySelector("#serverStatus"),
  chatView: document.querySelector("#chatView"),
  homeView: document.querySelector("#homeView"),
  toolsView: document.querySelector("#toolsView"),
  memoryView: document.querySelector("#memoryView"),
  scheduleView: document.querySelector("#scheduleView"),
  settingsView: document.querySelector("#settingsView"),
  messageList: document.querySelector("#messageList"),
  composer: document.querySelector("#composer"),
  editNotice: document.querySelector("#editNotice"),
  cancelEditButton: document.querySelector("#cancelEditButton"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
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
  scheduleNotifyButton: document.querySelector("#scheduleNotifyButton"),
  scheduleTodayCount: document.querySelector("#scheduleTodayCount"),
  scheduleNextTitle: document.querySelector("#scheduleNextTitle"),
  scheduleNextMeta: document.querySelector("#scheduleNextMeta"),
  scheduleSyncStatus: document.querySelector("#scheduleSyncStatus"),
  todoCaptureStatus: document.querySelector("#todoCaptureStatus"),
  todoForm: document.querySelector("#todoForm"),
  todoDate: document.querySelector("#todoDate"),
  todoTime: document.querySelector("#todoTime"),
  todoTitle: document.querySelector("#todoTitle"),
  termForm: document.querySelector("#termForm"),
  termStartDate: document.querySelector("#termStartDate"),
  termWeekCount: document.querySelector("#termWeekCount"),
  saveTermButton: document.querySelector("#saveTermButton"),
  courseImportText: document.querySelector("#courseImportText"),
  courseImportStatus: document.querySelector("#courseImportStatus"),
  importCoursesButton: document.querySelector("#importCoursesButton"),
  clearCourseImportButton: document.querySelector("#clearCourseImportButton"),
  previousWeekButton: document.querySelector("#previousWeekButton"),
  nextWeekButton: document.querySelector("#nextWeekButton"),
  currentWeekButton: document.querySelector("#currentWeekButton"),
  weekTitle: document.querySelector("#weekTitle"),
  weekRange: document.querySelector("#weekRange"),
  weekGrid: document.querySelector("#weekGrid"),
  clearDoneButton: document.querySelector("#clearDoneButton"),
  scheduleTimeline: document.querySelector("#scheduleTimeline"),
  upcomingSummary: document.querySelector("#upcomingSummary"),
  upcomingScheduleList: document.querySelector("#upcomingScheduleList"),
  homeFeatureCards: [...document.querySelectorAll("[data-home-feature]")],
  duettoCard: document.querySelector("#duettoCard"),
  duettoCardSubtitle: document.querySelector("#duettoCardSubtitle"),
  homeNotice: document.querySelector("#homeNotice"),
  toolPageButtons: [...document.querySelectorAll("[data-tool-page]")],
  toolPlaceholderButtons: [...document.querySelectorAll("[data-tool-placeholder]")],
  toolsBackButton: document.querySelector("#toolsBackButton"),
  toolsIntro: document.querySelector("#toolsIntro"),
  backendUrl: document.querySelector("#backendUrl"),
  duettoUrl: document.querySelector("#duettoUrl"),
  gatewayToken: document.querySelector("#gatewayToken"),
  modelName: document.querySelector("#modelName"),
  temperature: document.querySelector("#temperature"),
  temperatureValue: document.querySelector("#temperatureValue"),
  systemPromptStatus: document.querySelector("#systemPromptStatus"),
  backgroundUrl: document.querySelector("#backgroundUrl"),
  chooseBackgroundButton: document.querySelector("#chooseBackgroundButton"),
  resetBackgroundButton: document.querySelector("#resetBackgroundButton"),
  backgroundFile: document.querySelector("#backgroundFile"),
  backgroundTransparency: document.querySelector("#backgroundTransparency"),
  backgroundTransparencyValue: document.querySelector("#backgroundTransparencyValue"),
  backgroundFit: document.querySelector("#backgroundFit"),
  accentColor: document.querySelector("#accentColor"),
  saveSettingsButton: document.querySelector("#saveSettingsButton"),
  clearChatButton: document.querySelector("#clearChatButton"),
  tabs: [...document.querySelectorAll(".tab-button")],
  memorySegments: [...document.querySelectorAll(".segment-button")],
  memoryPanels: [...document.querySelectorAll(".memory-panel")]
};

const systemPromptReady = loadSystemPrompt();

bindEvents();
hydrateSettingsForm();
saveSettings();
applySettings();
renderMessages();
initSchedule();
renderSchedule();
updateClock();
refreshHealth();
loadSchedule().catch(() => {
  setScheduleSyncStatus("本地日程", "offline");
});
setInterval(checkScheduleNudges, 30000);
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

  els.cancelEditButton.addEventListener("click", cancelMessageEdit);

  els.tabs.forEach((tab) => {
    tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
  });

  els.homeFeatureCards.forEach((card) => {
    card.addEventListener("click", () => handleHomeFeature(card.dataset.homeFeature));
  });

  els.toolPageButtons.forEach((button) => {
    button.addEventListener("click", () => openToolsPage(button.dataset.toolPage));
  });

  els.toolPlaceholderButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const labels = { packages: "包管理", workflows: "工作流", tools: "工具管理" };
      els.toolsIntro.textContent = `${labels[button.dataset.toolPlaceholder] || "这项功能"}将在后续对话中接入。`;
    });
  });

  els.toolsBackButton.addEventListener("click", () => setActiveTab("home"));

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

  els.todoForm.addEventListener("submit", (event) => {
    event.preventDefault();
    addTodoFromForm();
  });

  els.termForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveTermSettings();
  });

  els.importCoursesButton.addEventListener("click", importCourses);

  els.clearCourseImportButton.addEventListener("click", () => {
    els.courseImportText.value = "";
    els.courseImportStatus.textContent = "";
  });

  els.previousWeekButton.addEventListener("click", () => shiftVisibleWeek(-1));
  els.nextWeekButton.addEventListener("click", () => shiftVisibleWeek(1));
  els.currentWeekButton.addEventListener("click", () => focusCurrentScheduleWeek());

  els.clearDoneButton.addEventListener("click", clearDoneTodos);

  els.scheduleNotifyButton.addEventListener("click", requestScheduleNotifications);

  els.saveSettingsButton.addEventListener("click", () => {
    readSettingsForm();
    saveSettings();
    applySettings();
    refreshHealth();
    state.loadedMemoryPanels.clear();
    loadSchedule().catch(() => {
      setScheduleSyncStatus("本地日程", "offline");
    });
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

    platform.readFileAsDataUrl(file).then((dataUrl) => {
      els.backgroundUrl.value = dataUrl;
      readSettingsForm();
      saveSettings();
      applySettings();
    }).catch((error) => {
      els.backgroundUrl.value = "";
      els.serverStatus.textContent = error instanceof Error ? error.message : String(error);
      els.serverStatus.classList.add("offline");
      els.serverStatus.classList.remove("online");
    });
  });

  els.resetBackgroundButton.addEventListener("click", () => {
    els.backgroundUrl.value = "";
    readSettingsForm();
    saveSettings();
    applySettings();
  });

  for (const input of [
    els.backendUrl,
    els.duettoUrl,
    els.gatewayToken,
    els.modelName,
    els.temperature,
    els.backgroundUrl,
    els.backgroundTransparency,
    els.backgroundFit,
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

  els.backgroundTransparency.addEventListener("input", () => {
    const transparency = Number(els.backgroundTransparency.value);
    els.backgroundTransparencyValue.value = `${Math.round(transparency * 100)}%`;
    document.documentElement.style.setProperty("--background-image-opacity", String(1 - transparency));
  });
}

async function sendMessage() {
  const text = els.messageInput.value.trim();
  if (!text || state.busy) {
    return;
  }

  await systemPromptReady;
  if (!state.systemPrompt) {
    els.systemPromptStatus.textContent = state.systemPromptError || "提示词为空";
    setActiveTab("settings");
    return;
  }

  readSettingsForm();
  saveSettings();
  applySettings();

  if (state.editingMessageId) {
    if (!applyUserMessageEdit(state.editingMessageId, text)) {
      cancelMessageEdit();
      return;
    }
    cancelMessageEdit({ clearInput: false });
  } else {
    state.messages.push({
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString()
    });
  }

  captureTodoFromMessage(text);
  els.messageInput.value = "";
  autosizeTextarea(els.messageInput);
  await generateAssistantReply();
}

async function generateAssistantReply() {
  const assistantMessage = {
    id: crypto.randomUUID(),
    role: "assistant",
    content: "",
    reasoning: "",
    pending: true,
    createdAt: new Date().toISOString()
  };

  state.messages.push(assistantMessage);
  setBusy(true);
  renderMessages();

  try {
    const response = await platform.request(apiUrl("/v1/chat/completions"), {
      method: "POST",
      headers: buildGatewayHeaders(),
      body: JSON.stringify({
        model: state.settings.model,
        messages: buildRequestMessages(),
        temperature: state.settings.temperature,
        include_reasoning: true,
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

    await readOpenAiStream(response.body, ({ content, reasoning }) => {
      assistantMessage.content += content;
      assistantMessage.reasoning += reasoning;
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

async function regenerateAssistantMessage(messageId) {
  if (state.busy) {
    return;
  }
  await systemPromptReady;
  if (!state.systemPrompt) {
    els.systemPromptStatus.textContent = state.systemPromptError || "提示词为空";
    setActiveTab("settings");
    return;
  }
  const previousMessages = conversationBeforeAssistant(messageId);
  if (!previousMessages) {
    return;
  }

  readSettingsForm();
  saveSettings();
  applySettings();
  if (state.editingMessageId) {
    cancelMessageEdit();
  }
  state.messages = previousMessages;
  saveMessages();
  await generateAssistantReply();
}

function applyUserMessageEdit(messageId, content) {
  const editIndex = state.messages.findIndex((message) => message.id === messageId && message.role === "user");
  if (editIndex < 0) {
    return false;
  }
  state.messages[editIndex] = {
    ...state.messages[editIndex],
    content,
    editedAt: new Date().toISOString()
  };
  state.messages = state.messages.slice(0, editIndex + 1);
  return true;
}

function conversationBeforeAssistant(messageId) {
  const messageIndex = state.messages.findIndex((message) => message.id === messageId && message.role === "assistant");
  if (messageIndex < 0) {
    return null;
  }
  const previousMessages = state.messages.slice(0, messageIndex);
  return previousMessages.at(-1)?.role === "user" ? previousMessages : null;
}

function beginMessageEdit(messageId) {
  if (state.busy) {
    return;
  }
  const message = state.messages.find((item) => item.id === messageId && item.role === "user");
  if (!message) {
    return;
  }
  state.editingMessageId = message.id;
  els.messageInput.value = message.content;
  els.editNotice.hidden = false;
  els.sendButton.title = "保存并重新发送";
  els.sendButton.setAttribute("aria-label", "保存并重新发送");
  autosizeTextarea(els.messageInput);
  els.messageInput.focus();
  els.messageInput.setSelectionRange(els.messageInput.value.length, els.messageInput.value.length);
}

function cancelMessageEdit({ clearInput = true } = {}) {
  state.editingMessageId = "";
  els.editNotice.hidden = true;
  els.sendButton.title = "发送";
  els.sendButton.setAttribute("aria-label", "发送");
  if (clearInput) {
    els.messageInput.value = "";
    autosizeTextarea(els.messageInput);
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
  const scheduleContext = buildScheduleInjection();
  const systemMessages = [];

  if (state.systemPrompt) {
    systemMessages.push({ role: "system", content: state.systemPrompt });
  }

  if (scheduleContext) {
    systemMessages.push({ role: "system", content: scheduleContext });
  }

  return [...systemMessages, ...messages];
}

async function loadSystemPrompt() {
  try {
    const response = await fetch("/frontend/system_prompt.md", {
      headers: { "Accept": "text/markdown, text/plain" },
      cache: "no-cache"
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const prompt = (await response.text()).trim();
    if (!prompt) {
      throw new Error("文件内容为空");
    }
    state.systemPrompt = prompt;
    state.systemPromptError = "";
    els.systemPromptStatus.textContent = "已注入";
    els.systemPromptStatus.classList.add("ready");
    return prompt;
  } catch (error) {
    state.systemPrompt = "";
    state.systemPromptError = error instanceof Error ? error.message : String(error);
    els.systemPromptStatus.textContent = `加载失败：${state.systemPromptError}`;
    els.systemPromptStatus.classList.remove("ready");
    return "";
  }
}

async function gatewayFetch(path, options = {}) {
  const headers = {
    "Accept": "application/json",
    ...buildAuthHeaders(),
    ...(options.headers || {})
  };
  const response = await platform.request(apiUrl(path), {
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
  state.memoryQuery = query;
  state.memoryVisibleCount = 80;
  els.memorySummary.textContent = "载入中";
  els.memoryList.replaceChildren();
  const items = await gatewayFetch("/api/buckets");
  const allItems = Array.isArray(items) ? items : [];
  state.memoryItems = query ? allItems.filter((item) => memoryMatches(item, query)) : allItems;
  renderMemoryList();
}

function renderMemoryList() {
  els.memoryList.replaceChildren();
  const items = state.memoryItems;
  if (!items.length) {
    els.memoryList.append(emptyNode("还没有可显示的记忆。"));
    els.memorySummary.textContent = state.memoryQuery ? "找到 0 条" : "共 0 条";
    return;
  }

  const visibleItems = items.slice(0, state.memoryVisibleCount);
  els.memorySummary.textContent = state.memoryQuery
    ? `找到 ${items.length} 条，已显示 ${visibleItems.length} 条`
    : `共 ${items.length} 条，已显示 ${visibleItems.length} 条`;

  for (const item of visibleItems) {
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

  if (visibleItems.length < items.length) {
    const row = document.createElement("div");
    row.className = "load-more-row";
    const button = document.createElement("button");
    button.className = "text-button";
    button.type = "button";
    button.textContent = `加载更多 ${Math.min(80, items.length - visibleItems.length)} 条`;
    button.addEventListener("click", () => {
      state.memoryVisibleCount += 80;
      renderMemoryList();
    });
    row.append(button);
    els.memoryList.append(row);
  }
}

function memoryMatches(item, query) {
  const terms = String(query || "")
    .toLowerCase()
    .split(/\s+/)
    .map((term) => term.trim())
    .filter(Boolean);
  if (!terms.length) {
    return true;
  }
  const text = [
    item.id,
    item.name,
    item.type,
    item.content_preview,
    item.summary_text,
    item.summary,
    arrayLabel(item.tags),
    arrayLabel(item.domain)
  ].join(" ").toLowerCase();
  return terms.every((term) => text.includes(term));
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

function initSchedule() {
  els.todoDate.value = todayDateKey();
  els.todoTime.value = nextQuarterHour();
  hydrateTermForm();
  focusCurrentScheduleWeek(false);
  updateNotificationButton();
}

async function loadSchedule() {
  try {
    const data = await gatewayFetch("/api/schedule");
    const items = Array.isArray(data) ? data : data.items;
    state.scheduleItems = normalizeScheduleItems(items);
    state.scheduleSettings = normalizeScheduleSettings(data?.settings || state.scheduleSettings);
    hydrateTermForm();
    saveScheduleLocal();
    saveScheduleSettingsLocal();
    setScheduleSyncStatus("已同步", "online");
    renderSchedule();
  } catch (error) {
    setScheduleSyncStatus("本地日程", "offline");
    throw error;
  }
}

function addTodoFromForm() {
  const date = normalizeDateValue(els.todoDate.value) || todayDateKey();
  const start = normalizeTimeValue(els.todoTime.value);
  const title = els.todoTitle.value.trim();
  if (!date || !start || !title) {
    els.todoCaptureStatus.textContent = "缺少时间或任务";
    return;
  }

  addScheduleItems([{
    id: crypto.randomUUID(),
    type: "todo",
    date,
    start,
    end: "",
    title,
    location: "",
    note: "",
    done: false,
    source: "manual",
    createdAt: new Date().toISOString()
  }]);
  els.todoTitle.value = "";
  els.todoCaptureStatus.textContent = "已加入";
  clearInlineStatusLater(els.todoCaptureStatus);
}

function importCourses() {
  const lines = els.courseImportText.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const parsed = lines.map(parseCourseLine).filter(Boolean);
  if (!parsed.length) {
    els.courseImportStatus.textContent = "没有识别到课程";
    return;
  }
  const result = addScheduleItems(parsed);
  els.courseImportStatus.textContent = `导入 ${result.added} 条规则`;
  clearInlineStatusLater(els.courseImportStatus);
}

function addScheduleItems(items) {
  const existing = new Set(state.scheduleItems.map(scheduleKey));
  let added = 0;
  for (const rawItem of normalizeScheduleItems(items)) {
    const key = scheduleKey(rawItem);
    if (existing.has(key)) {
      continue;
    }
    existing.add(key);
    state.scheduleItems.push(rawItem);
    added += 1;
  }
  state.scheduleItems = normalizeScheduleItems(state.scheduleItems);
  saveSchedule();
  return { added };
}

function clearDoneTodos() {
  const before = state.scheduleItems.length;
  state.scheduleItems = state.scheduleItems.filter((item) => !(item.type === "todo" && item.done));
  if (state.scheduleItems.length !== before) {
    saveSchedule();
  }
}

function renderSchedule() {
  const today = todayDateKey();
  const now = new Date();
  const visibleWeekStart = parseDateKey(state.visibleWeekStart) || startOfWeek(now);
  const visibleWeekEnd = addDays(visibleWeekStart, 6);
  const todayItems = sortScheduleItems(expandedScheduleItemsForRange(startOfDay(now), endOfDay(now)));
  const activeItems = todayItems.filter((item) => !item.done);
  const upcoming = nextScheduleItems(now, 8);
  const nextItem = upcoming[0] || activeItems[0];

  els.scheduleTodayCount.textContent = `今日 ${activeItems.length} 项`;
  els.scheduleNextTitle.textContent = nextItem ? nextItem.title : "今天清爽";
  els.scheduleNextMeta.textContent = nextItem ? scheduleMetaLine(nextItem, now) : "没有临近日程";
  els.upcomingSummary.textContent = upcoming.length ? `${upcoming.length} 项` : "";
  els.scheduleSyncStatus.textContent = state.scheduleSyncStatus;
  renderWeekHeader(visibleWeekStart, visibleWeekEnd);
  renderWeekGrid(visibleWeekStart, visibleWeekEnd);

  els.scheduleTimeline.replaceChildren();
  if (!todayItems.length) {
    els.scheduleTimeline.append(emptyNode("今天没有日程。"));
  } else {
    for (const item of todayItems) {
      els.scheduleTimeline.append(renderScheduleItem(item, today));
    }
  }

  const futureItems = upcoming.filter((item) => item.date !== today);
  els.upcomingScheduleList.replaceChildren();
  if (!futureItems.length) {
    els.upcomingScheduleList.append(emptyNode("未来七天没有课程或待办。"));
  } else {
    for (const item of futureItems) {
      els.upcomingScheduleList.append(renderScheduleItem(item, today));
    }
  }
}

function renderWeekHeader(weekStart, weekEnd) {
  const weekIndex = weekIndexForDate(weekStart);
  const termStart = parseDateKey(state.scheduleSettings.termStartDate);
  if (!termStart) {
    els.weekTitle.textContent = "周课表";
  } else if (weekIndex < 1) {
    els.weekTitle.textContent = "未开学";
  } else if (weekIndex > state.scheduleSettings.termWeekCount) {
    els.weekTitle.textContent = "已结课";
  } else {
    els.weekTitle.textContent = `第 ${weekIndex} 周`;
  }
  els.weekRange.textContent = `${formatScheduleDate(dateKey(weekStart))} - ${formatScheduleDate(dateKey(weekEnd))}`;
}

function renderWeekGrid(weekStart) {
  const weekEndExclusive = addDays(weekStart, 7);
  const items = expandedScheduleItemsForRange(weekStart, weekEndExclusive)
    .filter((item) => item.type === "course");
  els.weekGrid.replaceChildren();
  els.weekGrid.style.setProperty("--period-count", COURSE_PERIODS.length);

  const corner = document.createElement("div");
  corner.className = "week-corner";
  corner.textContent = "节";
  els.weekGrid.append(corner);

  for (let index = 0; index < 7; index += 1) {
    const date = addDays(weekStart, index);
    const header = document.createElement("div");
    header.className = "week-day";
    if (dateKey(date) === todayDateKey()) {
      header.classList.add("is-today");
    }
    header.style.gridColumn = String(index + 2);
    header.style.gridRow = "1";
    header.innerHTML = `<strong>${WEEKDAY_LABELS[index]}</strong><span>${formatShortDate(date)}</span>`;
    els.weekGrid.append(header);
  }

  for (const period of COURSE_PERIODS) {
    const label = document.createElement("div");
    label.className = "period-label";
    label.style.gridColumn = "1";
    label.style.gridRow = String(period.index + 1);
    label.innerHTML = `<strong>${period.index}</strong><span>${period.start}</span><span>${period.end}</span>`;
    els.weekGrid.append(label);

    for (let day = 1; day <= 7; day += 1) {
      const cell = document.createElement("div");
      cell.className = "period-cell";
      cell.style.gridColumn = String(day + 1);
      cell.style.gridRow = String(period.index + 1);
      els.weekGrid.append(cell);
    }
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "week-course";
    if (isScheduleItemNow(item)) {
      card.classList.add("is-now");
    }
    card.style.gridColumn = String(item.weekday + 1);
    card.style.gridRow = `${item.startPeriod + 1} / ${item.endPeriod + 2}`;
    card.style.setProperty("--course-bg", courseColor(item));
    card.title = compactParts([item.title, timeRangeLabel(item), item.location]).join(" · ");

    const title = document.createElement("strong");
    title.textContent = item.title;
    const meta = document.createElement("span");
    meta.textContent = compactParts([
      item.location ? `@${item.location}` : "",
      weekRuleLabel(item)
    ]).join(" ");
    card.append(title, meta);
    els.weekGrid.append(card);
  }

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "week-empty";
    empty.textContent = "这一周没有课程。";
    els.weekGrid.append(empty);
  }
}

function expandedScheduleItemsForRange(startDate, endDate) {
  const start = startOfDay(startDate);
  const end = new Date(endDate);
  const result = [];
  for (const item of state.scheduleItems) {
    if (item.recurrence === "weekly") {
      for (let date = new Date(start); date < end; date = addDays(date, 1)) {
        if (weekdayFromDate(date) !== item.weekday || !courseOccursOnDate(item, date)) {
          continue;
        }
        result.push({
          ...item,
          date: dateKey(date),
          done: false,
          occurrenceId: `${item.id}:${dateKey(date)}`
        });
      }
      continue;
    }
    const itemDate = parseDateKey(item.date);
    if (itemDate && itemDate >= start && itemDate < end) {
      result.push(item);
    }
  }
  return sortScheduleItems(result);
}

function courseOccursOnDate(item, date) {
  const weekIndex = weekIndexForDate(date);
  const effectiveWeek = weekIndex || 1;
  if (effectiveWeek < item.weekStart || effectiveWeek > item.weekEnd) {
    return false;
  }
  if (item.weekParity === "odd" && effectiveWeek % 2 === 0) {
    return false;
  }
  if (item.weekParity === "even" && effectiveWeek % 2 !== 0) {
    return false;
  }
  return true;
}

function renderScheduleItem(item, today) {
  const card = document.createElement("article");
  card.className = "schedule-item";
  if (isScheduleItemNow(item)) {
    card.classList.add("is-now");
  }
  if (item.done) {
    card.classList.add("is-done");
  }

  const time = document.createElement("div");
  time.className = "schedule-time";
  const timeText = document.createElement("span");
  timeText.textContent = timeRangeLabel(item);
  const kind = document.createElement("span");
  kind.className = "schedule-kind";
  kind.textContent = item.type === "course" ? "课程" : "待办";
  time.append(timeText, kind);

  const main = document.createElement("div");
  main.className = "schedule-main";
  const title = document.createElement("div");
  title.className = "schedule-title";
  title.textContent = item.title;
  const meta = document.createElement("div");
  meta.className = "item-meta";
  meta.textContent = compactParts([
    item.date === today ? "" : formatScheduleDate(item.date),
    item.location,
    item.done ? "已完成" : isScheduleItemNow(item) ? "进行中" : "",
    item.note
  ]).join(" · ");

  const actions = document.createElement("div");
  actions.className = "schedule-actions";
  if (item.type === "todo") {
    const doneButton = document.createElement("button");
    doneButton.className = "text-button";
    doneButton.type = "button";
    doneButton.textContent = item.done ? "撤销" : "完成";
    doneButton.addEventListener("click", () => toggleTodoDone(item.id));
    actions.append(doneButton);
  }
  const deleteButton = document.createElement("button");
  deleteButton.className = "text-button";
  deleteButton.type = "button";
  deleteButton.textContent = "删除";
  deleteButton.addEventListener("click", () => deleteScheduleItem(item.id));
  actions.append(deleteButton);

  main.append(title);
  if (meta.textContent) {
    main.append(meta);
  }
  main.append(actions);
  card.append(time, main);
  return card;
}

function toggleTodoDone(id) {
  state.scheduleItems = state.scheduleItems.map((item) => (
    item.id === id ? { ...item, done: !item.done } : item
  ));
  saveSchedule();
}

function deleteScheduleItem(id) {
  state.scheduleItems = state.scheduleItems.filter((item) => item.id !== id);
  saveSchedule();
}

function saveSchedule() {
  state.scheduleItems = normalizeScheduleItems(state.scheduleItems);
  saveScheduleLocal();
  renderSchedule();
  syncSchedule().catch(() => {
    setScheduleSyncStatus("本地日程", "offline");
  });
}

function saveScheduleLocal() {
  platform.storage.setJson(storageKeys.schedule, state.scheduleItems);
}

function saveScheduleSettingsLocal() {
  platform.storage.setJson(storageKeys.scheduleSettings, state.scheduleSettings);
}

function hydrateTermForm() {
  els.termStartDate.value = state.scheduleSettings.termStartDate || "";
  els.termWeekCount.value = state.scheduleSettings.termWeekCount;
}

function saveTermSettings() {
  const termStartDate = normalizeDateValue(els.termStartDate.value);
  const termWeekCount = clampInteger(els.termWeekCount.value, 1, 30, 18);
  state.scheduleSettings = normalizeScheduleSettings({
    termStartDate,
    termWeekCount
  });
  hydrateTermForm();
  saveScheduleSettingsLocal();
  if (termStartDate) {
    focusCurrentScheduleWeek(false);
  }
  renderSchedule();
  syncSchedule().catch(() => {
    setScheduleSyncStatus("本地日程", "offline");
  });
  els.courseImportStatus.textContent = "学期设置已保存";
  clearInlineStatusLater(els.courseImportStatus);
}

async function syncSchedule() {
  await gatewayFetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      version: 1,
      updatedAt: new Date().toISOString(),
      settings: state.scheduleSettings,
      items: state.scheduleItems
    })
  });
  setScheduleSyncStatus("已同步", "online");
}

function setScheduleSyncStatus(text, tone = "") {
  state.scheduleSyncStatus = text;
  els.scheduleSyncStatus.textContent = text;
  els.scheduleSyncStatus.classList.toggle("online", tone === "online");
  els.scheduleSyncStatus.classList.toggle("offline", tone === "offline");
}

function captureTodoFromMessage(text) {
  const todo = extractTodoFromMessage(text);
  if (!todo) {
    return;
  }
  const result = addScheduleItems([todo]);
  if (result.added) {
    els.todoCaptureStatus.textContent = "已从对话加入";
    clearInlineStatusLater(els.todoCaptureStatus);
  }
}

function extractTodoFromMessage(text) {
  const source = String(text || "").trim();
  if (!/(今天|今日|明天|后天|\d{4}[/-]\d{1,2}[/-]\d{1,2})/.test(source)) {
    return null;
  }
  if (!/(待办|任务|完成|做完|写完|提交|复习|整理|背|看|读|练|要|需要|得)/.test(source)) {
    return null;
  }

  const timeMatch = source.match(/(?:^|[^\d])(\d{1,2})(?:[:：](\d{2})|点半|点)(?:\s*(?:前|之前|左右|以后|后))?/);
  if (!timeMatch) {
    return null;
  }

  const date = dateFromText(source) || todayDateKey();
  const hour = Number(timeMatch[1]);
  const minute = timeMatch[0].includes("点半") ? 30 : Number(timeMatch[2] || 0);
  const start = normalizeTimeParts(hour, minute);
  if (!start) {
    return null;
  }

  const timeEnd = timeMatch.index + timeMatch[0].length;
  let title = source.slice(timeEnd)
    .replace(/^[\s，,。.!！；;：:]*(前|之前|左右|以后|后)?/, "")
    .replace(/^(要|需要|得|把|去)?\s*(完成|做完|写完|提交|复习|背完|背|看完|看|读完|读|整理|练习|做)?/, "")
    .replace(/[。.!！]+$/, "")
    .trim();
  if (title.length < 2) {
    title = source.slice(0, timeMatch.index)
      .replace(/(今天|今日|明天|后天|要|需要|得|在|之前|前|待办|任务)/g, "")
      .trim();
  }
  if (title.length < 2 || title.length > 80) {
    return null;
  }

  return {
    id: crypto.randomUUID(),
    type: "todo",
    date,
    start,
    end: "",
    title,
    location: "",
    note: "来自对话",
    done: false,
    source: "chat",
    createdAt: new Date().toISOString()
  };
}

function buildScheduleInjection() {
  const now = new Date();
  const today = todayDateKey(now);
  const futureWindow = expandedScheduleItemsForRange(startOfDay(now), addDays(startOfDay(now), 7));
  const current = futureWindow.filter((item) => !item.done && isScheduleItemNow(item, now));
  const todayRemaining = sortScheduleItems(futureWindow)
    .filter((item) => item.date === today && !item.done && (itemDateTime(item)?.getTime() || 0) >= now.getTime() - 10 * 60000)
    .slice(0, 8);
  const soon = nextScheduleItems(now, 5).filter((item) => item.date !== today).slice(0, 4);
  const items = [...current, ...todayRemaining, ...soon];
  const uniqueItems = [];
  const seen = new Set();
  for (const item of items) {
    if (seen.has(item.id)) {
      continue;
    }
    seen.add(item.id);
    uniqueItems.push(item);
  }
  if (!uniqueItems.length) {
    return "";
  }
  return [
    "以下是由日程 tab 注入的当前日程附件。请只在相关时使用；如果课程或待办临近，可以主动监督用户按时去上课或完成任务。",
    `当前时间：${formatPromptDateTime(now)}`,
    ...uniqueItems.map((item, index) => `${index + 1}. ${schedulePromptLine(item, today)}`)
  ].join("\n");
}

function checkScheduleNudges() {
  const now = Date.now();
  const today = startOfDay(new Date());
  for (const item of expandedScheduleItemsForRange(today, addDays(today, 1))) {
    if (item.done) {
      continue;
    }
    const start = itemDateTime(item);
    if (!start) {
      continue;
    }
    const reminders = [
      ["before", start.getTime() - 10 * 60000],
      ["start", start.getTime()]
    ];
    for (const [kind, at] of reminders) {
      if (now < at || now - at > 5 * 60000) {
        continue;
      }
      const key = `${item.id}:${item.date}:${item.start}:${kind}`;
      if (state.scheduleNudges[key]) {
        continue;
      }
      state.scheduleNudges[key] = new Date().toISOString();
      saveScheduleNudges();
      fireScheduleNudge(item, kind);
    }
  }
}

function fireScheduleNudge(item, kind) {
  const isBefore = kind === "before";
  const prefix = item.type === "course"
    ? (isBefore ? "10 分钟后上课" : "上课时间到了")
    : (isBefore ? "10 分钟后待办" : "待办时间到了");
  const detail = compactParts([
    `${prefix}：${item.title}`,
    timeRangeLabel(item),
    item.location
  ]).join(" · ");
  pushLocalReminderMessage(detail);
  showBrowserNotification(prefix, compactParts([item.title, item.location]).join(" · "));
}

function pushLocalReminderMessage(text) {
  state.messages.push({
    id: crypto.randomUUID(),
    role: "assistant",
    content: `提醒：${text}`,
    createdAt: new Date().toISOString()
  });
  saveMessages();
  renderMessages(state.activeTab === "chat");
}

async function requestScheduleNotifications() {
  if (!platform.notifications.isSupported()) {
    els.todoCaptureStatus.textContent = "当前浏览器不支持提醒";
    clearInlineStatusLater(els.todoCaptureStatus);
    return;
  }
  const permission = await platform.notifications.requestPermission();
  updateNotificationButton();
  els.todoCaptureStatus.textContent = permission === "granted" ? "提醒已开启" : "提醒未开启";
  clearInlineStatusLater(els.todoCaptureStatus);
}

function updateNotificationButton() {
  if (!platform.notifications.isSupported()) {
    els.scheduleNotifyButton.disabled = true;
    return;
  }
  els.scheduleNotifyButton.classList.toggle("tab-active", platform.notifications.permission() === "granted");
}

function showBrowserNotification(title, body) {
  platform.notifications.show(title, body, {
    tag: `companion-schedule-${title}-${body}`,
    silent: false
  });
}

function saveScheduleNudges() {
  platform.storage.setJson(storageKeys.scheduleNudges, state.scheduleNudges);
}

function normalizeScheduleSettings(value) {
  const settings = value && typeof value === "object" ? value : {};
  return {
    termStartDate: normalizeDateValue(settings.termStartDate || settings.term_start_date || ""),
    termWeekCount: clampInteger(settings.termWeekCount || settings.term_week_count, 1, 30, 18)
  };
}

function shiftVisibleWeek(delta) {
  const current = parseDateKey(state.visibleWeekStart) || startOfWeek(new Date());
  state.visibleWeekStart = dateKey(addDays(current, delta * 7));
  renderSchedule();
}

function focusCurrentScheduleWeek(shouldRender = true) {
  const today = startOfWeek(new Date());
  const termStart = parseDateKey(state.scheduleSettings.termStartDate);
  if (termStart && today < termStart) {
    state.visibleWeekStart = dateKey(termStart);
  } else {
    state.visibleWeekStart = dateKey(today);
  }
  if (shouldRender) {
    renderSchedule();
  }
}

function parseWeekday(value) {
  const text = String(value || "").trim().replace(/^周/, "");
  const map = {
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    日: 7,
    天: 7
  };
  if (map[text]) {
    return map[text];
  }
  const number = Number(text);
  return number >= 1 && number <= 7 ? number : 0;
}

function parsePeriodRange(value) {
  const match = String(value || "").trim().match(/^(\d{1,2})(?:\s*(?:-|~|—|–|至|到)\s*(\d{1,2}))?$/);
  if (!match) {
    return null;
  }
  const startPeriod = clampInteger(match[1], 1, COURSE_PERIODS.length, 1);
  const endPeriod = clampInteger(match[2] || match[1], startPeriod, COURSE_PERIODS.length, startPeriod);
  return { startPeriod, endPeriod };
}

function parseWeekRange(value) {
  const text = String(value || "");
  const range = text.match(/第?\s*(\d{1,2})\s*(?:-|~|—|–|至|到)\s*(\d{1,2})\s*周/);
  const single = text.match(/第?\s*(\d{1,2})\s*周/);
  let weekStart = 1;
  let weekEnd = state?.scheduleSettings?.termWeekCount || 18;
  if (range) {
    weekStart = clampInteger(range[1], 1, 30, 1);
    weekEnd = clampInteger(range[2], weekStart, 30, weekEnd);
  } else if (single) {
    weekStart = clampInteger(single[1], 1, 30, 1);
    weekEnd = weekStart;
  }
  let weekParity = "all";
  if (/单周/.test(text)) {
    weekParity = "odd";
  } else if (/双周/.test(text)) {
    weekParity = "even";
  }
  return { weekStart, weekEnd, weekParity };
}

function periodForStart(value) {
  const index = COURSE_PERIODS.find((period) => period.start === value)?.index;
  return index || 1;
}

function periodForEnd(value, startPeriod) {
  const index = COURSE_PERIODS.find((period) => period.end === value)?.index;
  return index || startPeriod;
}

function weekdayFromDate(date) {
  const day = date.getDay();
  return day === 0 ? 7 : day;
}

function weekdayFromDateKey(value) {
  const date = parseDateKey(value);
  return date ? weekdayFromDate(date) : 1;
}

function weekIndexForDate(dateValue) {
  const termStart = parseDateKey(state.scheduleSettings.termStartDate);
  if (!termStart) {
    return 0;
  }
  const weekStart = startOfWeek(dateValue);
  const diff = weekStart.getTime() - termStart.getTime();
  return Math.floor(diff / (7 * 24 * 60 * 60000)) + 1;
}

function startOfWeek(value) {
  const date = startOfDay(value);
  const offset = weekdayFromDate(date) - 1;
  date.setDate(date.getDate() - offset);
  return date;
}

function startOfDay(value) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  return date;
}

function endOfDay(value) {
  const date = startOfDay(value);
  date.setHours(23, 59, 59, 999);
  return date;
}

function addDays(value, days) {
  const date = new Date(value);
  date.setDate(date.getDate() + days);
  return date;
}

function parseDateKey(value) {
  const key = normalizeDateValue(value);
  if (!key) {
    return null;
  }
  const date = new Date(`${key}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatShortDate(date) {
  return `${pad2(date.getMonth() + 1)}/${pad2(date.getDate())}`;
}

function courseColor(item) {
  const key = Number.parseInt(String(item.colorKey || "").replace(/\D/g, ""), 10);
  if (Number.isFinite(key)) {
    return COURSE_COLORS[key % COURSE_COLORS.length];
  }
  return COURSE_COLORS[hashText(item.title) % COURSE_COLORS.length];
}

function colorKeyForCourse(title) {
  return `course-${hashText(title) % COURSE_COLORS.length}`;
}

function hashText(value) {
  let hash = 0;
  for (const char of String(value || "")) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return hash;
}

function weekRuleLabel(item) {
  if (item.recurrence !== "weekly") {
    return "";
  }
  const range = item.weekStart === item.weekEnd
    ? `第${item.weekStart}周`
    : `${item.weekStart}-${item.weekEnd}周`;
  const parity = item.weekParity === "odd" ? "单周" : item.weekParity === "even" ? "双周" : "";
  return compactParts([range, parity]).join(" ");
}

function clampInteger(value, low, high, fallback) {
  const number = Number.parseInt(value, 10);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(low, Math.min(high, number));
}

function parseCourseLine(line) {
  const text = String(line || "").trim();
  if (!text) {
    return null;
  }

  const cells = text.split(/[,，\t|]/).map((cell) => cell.trim()).filter(Boolean);
  if (cells.length >= 3) {
    const weekday = parseWeekday(cells[0]);
    const periods = parsePeriodRange(cells[1]);
    if (weekday && periods) {
      return makeWeeklyCourseItem({
        weekday,
        startPeriod: periods.startPeriod,
        endPeriod: periods.endPeriod,
        title: cells[2],
        location: cells[3] || "",
        weekText: cells.slice(4).join(" ")
      });
    }

    const firstDate = normalizeDateValue(cells[0]);
    const secondDate = normalizeDateValue(cells[1]);
    if (firstDate && cells.length >= 4) {
      return makeCourseItem({
        date: firstDate,
        start: cells[1],
        end: cells[2],
        title: cells[3],
        location: cells.slice(4).join(" ")
      });
    }
    if (secondDate && cells.length >= 5) {
      return makeCourseItem({
        title: cells[0],
        date: secondDate,
        start: cells[2],
        end: cells[3],
        location: cells.slice(4).join(" ")
      });
    }
  }

  const weeklyMatch = text.match(/^(?:周)?([一二三四五六日天1-7])\s+(\d{1,2})(?:\s*(?:-|~|—|–|至|到)\s*(\d{1,2}))?\s+(.+)$/);
  if (weeklyMatch) {
    const weekday = parseWeekday(weeklyMatch[1]);
    const startPeriod = Number(weeklyMatch[2]);
    const endPeriod = Number(weeklyMatch[3] || weeklyMatch[2]);
    const weekText = text.match(/第?\s*\d{1,2}\s*(?:-|~|—|–|至|到)\s*\d{1,2}\s*周|第?\s*\d{1,2}\s*周|单周|双周|单双周/)?.[0] || "";
    const withoutWeekText = weekText ? weeklyMatch[4].replace(weekText, "").trim() : weeklyMatch[4];
    const titleAndLocation = splitTitleLocation(withoutWeekText);
    return makeWeeklyCourseItem({
      weekday,
      startPeriod,
      endPeriod,
      title: titleAndLocation.title,
      location: titleAndLocation.location,
      weekText
    });
  }

  const match = text.match(/^(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s+(\d{1,2}[:：]\d{2})\s*(?:(?:-|~|—|–|至|到)\s*(\d{1,2}[:：]\d{2}))?\s+(.+)$/);
  if (!match) {
    return null;
  }
  const titleAndLocation = splitTitleLocation(match[4]);
  return makeCourseItem({
    date: match[1],
    start: match[2],
    end: match[3] || "",
    title: titleAndLocation.title,
    location: titleAndLocation.location
  });
}

function makeWeeklyCourseItem(raw) {
  const weekday = clampInteger(raw.weekday, 1, 7, 1);
  const startPeriod = clampInteger(raw.startPeriod, 1, COURSE_PERIODS.length, 1);
  const endPeriod = clampInteger(raw.endPeriod, startPeriod, COURSE_PERIODS.length, startPeriod);
  const title = String(raw.title || "").trim();
  if (!title) {
    return null;
  }
  const weeks = parseWeekRange(raw.weekText);
  const start = COURSE_PERIODS[startPeriod - 1]?.start || "08:00";
  const end = COURSE_PERIODS[endPeriod - 1]?.end || "";
  return {
    id: crypto.randomUUID(),
    type: "course",
    recurrence: "weekly",
    weekday,
    weekStart: weeks.weekStart,
    weekEnd: weeks.weekEnd,
    weekParity: weeks.weekParity,
    startPeriod,
    endPeriod,
    date: "",
    start,
    end,
    title,
    location: String(raw.location || "").trim(),
    note: "",
    done: false,
    source: "weekly",
    colorKey: colorKeyForCourse(title),
    createdAt: new Date().toISOString()
  };
}

function makeCourseItem(raw) {
  const date = normalizeDateValue(raw.date);
  const start = normalizeTimeValue(raw.start);
  const end = normalizeTimeValue(raw.end);
  const title = String(raw.title || "").trim();
  if (!date || !start || !title) {
    return null;
  }
  return {
    id: crypto.randomUUID(),
    type: "course",
    date,
    start,
    end,
    title,
    location: String(raw.location || "").trim(),
    note: "",
    done: false,
    source: "import",
    colorKey: colorKeyForCourse(title),
    createdAt: new Date().toISOString()
  };
}

function splitTitleLocation(value) {
  const text = String(value || "").trim();
  const match = text.match(/^(.+?)\s*[@＠]\s*(.+)$/);
  if (!match) {
    return { title: text, location: "" };
  }
  return {
    title: match[1].trim(),
    location: match[2].trim()
  };
}

function normalizeScheduleItems(items) {
  const list = Array.isArray(items) ? items : Array.isArray(items?.items) ? items.items : [];
  return sortScheduleItems(list.map((item) => {
    const start = normalizeTimeValue(item?.start);
    const title = String(item?.title || "").trim();
    if (!start || !title) {
      return null;
    }
    const recurrence = item?.recurrence === "weekly" ? "weekly" : "";
    const date = recurrence ? "" : normalizeDateValue(item?.date);
    if (!recurrence && !date) {
      return null;
    }
    const startPeriod = item?.startPeriod ? clampInteger(item.startPeriod, 1, COURSE_PERIODS.length, 1) : periodForStart(start);
    const endPeriod = item?.endPeriod ? clampInteger(item.endPeriod, startPeriod, COURSE_PERIODS.length, startPeriod) : periodForEnd(normalizeTimeValue(item.end), startPeriod);
    const weekday = recurrence
      ? clampInteger(item.weekday, 1, 7, 1)
      : weekdayFromDateKey(date);
    const weekStart = clampInteger(item.weekStart, 1, 30, 1);
    const weekEnd = clampInteger(item.weekEnd, weekStart, 30, 18);
    const weekParity = ["odd", "even"].includes(item.weekParity) ? item.weekParity : "all";
    return {
      id: String(item.id || crypto.randomUUID()),
      type: item.type === "course" ? "course" : "todo",
      recurrence,
      weekday,
      weekStart,
      weekEnd,
      weekParity,
      startPeriod,
      endPeriod,
      date,
      start,
      end: normalizeTimeValue(item.end),
      title: title.slice(0, 120),
      location: String(item.location || "").trim().slice(0, 120),
      note: String(item.note || "").trim().slice(0, 200),
      done: Boolean(item.done),
      source: String(item.source || "manual").slice(0, 40),
      colorKey: String(item.colorKey || colorKeyForCourse(title)).slice(0, 24),
      createdAt: item.createdAt || item.created_at || new Date().toISOString()
    };
  }).filter(Boolean));
}

function sortScheduleItems(items) {
  return [...items].sort((a, b) => {
    const left = a.recurrence === "weekly"
      ? `W${a.weekday}-${a.startPeriod}-${a.start}`
      : `${a.date}T${a.start}`;
    const right = b.recurrence === "weekly"
      ? `W${b.weekday}-${b.startPeriod}-${b.start}`
      : `${b.date}T${b.start}`;
    return left.localeCompare(right) || a.title.localeCompare(b.title);
  });
}

function nextScheduleItems(now = new Date(), limit = 8) {
  const maxDate = addDays(startOfDay(now), 7);
  return sortScheduleItems(expandedScheduleItemsForRange(startOfDay(now), maxDate))
    .filter((item) => {
      if (item.done) {
        return false;
      }
      const start = itemDateTime(item);
      return start && start.getTime() >= now.getTime() - 10 * 60000;
    })
    .slice(0, limit);
}

function scheduleKey(item) {
  if (item.recurrence === "weekly") {
    return [
      item.type,
      item.recurrence,
      item.weekday,
      item.startPeriod,
      item.endPeriod,
      item.weekStart,
      item.weekEnd,
      item.weekParity,
      item.title
    ].join("|").toLowerCase();
  }
  return [item.type, item.date, item.start, item.end, item.title].join("|").toLowerCase();
}

function itemDateTime(item) {
  if (!item?.date || !item?.start) {
    return null;
  }
  const date = new Date(`${item.date}T${item.start}:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function itemEndDateTime(item) {
  const start = itemDateTime(item);
  if (!start) {
    return null;
  }
  if (item.end) {
    const end = new Date(`${item.date}T${item.end}:00`);
    if (!Number.isNaN(end.getTime()) && end > start) {
      return end;
    }
  }
  return new Date(start.getTime() + (item.type === "course" ? 90 : 30) * 60000);
}

function isScheduleItemNow(item, now = new Date()) {
  const start = itemDateTime(item);
  const end = itemEndDateTime(item);
  return Boolean(start && end && now >= start && now <= end);
}

function scheduleMetaLine(item, now = new Date()) {
  const start = itemDateTime(item);
  if (!start) {
    return timeRangeLabel(item);
  }
  const diff = start.getTime() - now.getTime();
  const when = item.date === todayDateKey(now) ? "今天" : formatScheduleDate(item.date);
  if (isScheduleItemNow(item, now)) {
    return compactParts(["正在进行", timeRangeLabel(item), item.location]).join(" · ");
  }
  if (diff > 0 && diff < 60 * 60000) {
    return compactParts([`${Math.ceil(diff / 60000)} 分钟后`, timeRangeLabel(item), item.location]).join(" · ");
  }
  return compactParts([when, timeRangeLabel(item), item.location]).join(" · ");
}

function schedulePromptLine(item, today) {
  const kind = item.type === "course" ? "课程" : "待办";
  const status = item.done ? "已完成" : isScheduleItemNow(item) ? "正在进行" : "未完成";
  return compactParts([
    `[${kind}]`,
    item.date === today ? "今天" : item.date,
    timeRangeLabel(item),
    item.title,
    item.location ? `地点：${item.location}` : "",
    item.note,
    status
  ]).join(" ");
}

function timeRangeLabel(item) {
  return item.end ? `${item.start}-${item.end}` : item.start;
}

function normalizeDateValue(value) {
  const text = String(value || "").trim().replace(/\//g, "-");
  const match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!match) {
    return "";
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
    return "";
  }
  return dateKey(date);
}

function normalizeTimeValue(value) {
  const text = String(value || "").trim().replace("：", ":");
  const match = text.match(/^(\d{1,2}):(\d{2})$/);
  if (!match) {
    return "";
  }
  return normalizeTimeParts(Number(match[1]), Number(match[2]));
}

function normalizeTimeParts(hour, minute) {
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return "";
  }
  return `${pad2(hour)}:${pad2(minute)}`;
}

function dateFromText(text) {
  const explicit = String(text || "").match(/(\d{4}[/-]\d{1,2}[/-]\d{1,2})/);
  if (explicit) {
    return normalizeDateValue(explicit[1]);
  }
  const base = new Date();
  if (/后天/.test(text)) {
    base.setDate(base.getDate() + 2);
  } else if (/明天/.test(text)) {
    base.setDate(base.getDate() + 1);
  }
  return dateKey(base);
}

function todayDateKey(date = new Date()) {
  return dateKey(date);
}

function dateKey(date) {
  return [
    date.getFullYear(),
    pad2(date.getMonth() + 1),
    pad2(date.getDate())
  ].join("-");
}

function nextQuarterHour() {
  const date = new Date();
  date.setMinutes(Math.ceil((date.getMinutes() + 1) / 15) * 15, 0, 0);
  if (date.getMinutes() === 60) {
    date.setHours(date.getHours() + 1, 0, 0, 0);
  }
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function formatScheduleDate(value) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    weekday: "short"
  }).format(date);
}

function formatPromptDateTime(date) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date);
}

function clearInlineStatusLater(target) {
  window.setTimeout(() => {
    target.textContent = "";
  }, 2800);
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

async function readOpenAiStream(stream, onDelta) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch (error) {
      throw new Error(streamReadErrorMessage(error));
    }
    const { value, done } = chunk;
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      dispatchOpenAiBlock(block, onDelta);
      boundary = buffer.indexOf("\n\n");
    }
  }

  if (buffer.trim()) {
    dispatchOpenAiBlock(buffer, onDelta);
  }
}

function dispatchOpenAiBlock(block, onDelta) {
  const lines = block.split(/\r?\n/);
  const dataLines = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  for (const rawData of dataLines) {
    if (!rawData || rawData === "[DONE]") {
      continue;
    }

    let parsed;
    try {
      parsed = JSON.parse(rawData);
    } catch {
      throw new Error(`流式响应格式异常：${rawData.slice(0, 160)}`);
    }
    if (parsed.error) {
      throw new Error(parsed.error.message || "模型请求失败。");
    }

    const choice = parsed.choices?.[0] || {};
    const delta = choice.delta || choice.message || {};
    const content = typeof delta.content === "string" ? delta.content : "";
    const reasoning = firstReasoningText(
      delta.reasoning,
      delta.reasoning_content,
      delta.reasoning_details,
      choice.reasoning,
      choice.reasoning_content,
      choice.reasoning_details
    );
    if (content || reasoning) {
      onDelta({ content, reasoning });
    }
  }
}

function firstReasoningText(...values) {
  for (const value of values) {
    const text = reasoningText(value);
    if (text) {
      return text;
    }
  }
  return "";
}

function reasoningText(value) {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(reasoningText).join("");
  }
  if (!value || typeof value !== "object") {
    return "";
  }
  for (const key of ["text", "content", "reasoning", "summary"]) {
    const text = reasoningText(value[key]);
    if (text) {
      return text;
    }
  }
  return "";
}

function streamReadErrorMessage(error) {
  const detail = error instanceof Error ? error.message : String(error || "");
  return [
    "流式响应中断。",
    "上游模型可能已经完成请求，但浏览器/WebView 读取网关返回流时失败。",
    "如果 OpenRouter 有请求日志，优先检查网关返回头和 SSE 转发。",
    detail ? `底层错误：${detail}` : ""
  ].filter(Boolean).join("\n");
}

function renderMarkdown(element, source) {
  const markdown = String(source || "");
  if (!markdown) {
    element.textContent = " ";
    return;
  }
  if (!window.marked?.parse || !window.DOMPurify?.sanitize) {
    element.textContent = markdown;
    return;
  }
  try {
    const html = window.marked.parse(markdown, {
      gfm: true,
      breaks: true
    });
    element.innerHTML = window.DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form"],
      FORBID_ATTR: ["style"]
    });
    if (typeof window.renderMathInElement === "function") {
      window.renderMathInElement(element, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "\\[", right: "\\]", display: true },
          { left: "\\(", right: "\\)", display: false },
          { left: "$", right: "$", display: false }
        ],
        throwOnError: false,
        strict: "ignore"
      });
    }
    for (const link of element.querySelectorAll("a[href]")) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  } catch {
    element.textContent = markdown;
  }
}

function createMessageActions(message) {
  const actions = document.createElement("div");
  actions.className = "message-actions";
  const button = document.createElement("button");
  button.type = "button";

  if (message.role === "user") {
    button.textContent = "编辑";
    button.title = "编辑并从这条消息重新发送";
    button.addEventListener("click", () => beginMessageEdit(message.id));
  } else {
    button.textContent = "重新生成";
    button.title = "从这条回复之前重新生成";
    button.addEventListener("click", () => regenerateAssistantMessage(message.id));
  }
  button.disabled = state.busy;
  actions.append(button);
  return actions;
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

    const body = document.createElement("div");
    body.className = `message-body message-${message.role}-body`;

    const content = document.createElement("div");
    content.className = "message-content";
    if (message.role === "assistant") {
      renderMarkdown(content, message.content);
    } else {
      content.textContent = message.content || " ";
    }
    if (message.role === "assistant" && message.reasoning) {
      const reasoning = document.createElement("details");
      reasoning.className = "message-reasoning";
      reasoning.open = Boolean(message.pending);

      const summary = document.createElement("summary");
      summary.textContent = message.pending ? "思考中…" : "思考过程";

      const reasoningContent = document.createElement("div");
      reasoningContent.className = "message-reasoning-content";
      renderMarkdown(reasoningContent, message.reasoning);

      reasoning.append(summary, reasoningContent);
      body.append(reasoning);
    }
    body.append(content);
    if (!message.pending) {
      body.append(createMessageActions(message));
    }
    item.append(body);
    els.messageList.append(item);
  }

  if (shouldScroll) {
    els.messageList.scrollTop = els.messageList.scrollHeight;
  }
}

function handleHomeFeature(feature) {
  if (feature === "music") {
    const url = normalizeDuettoUrl(state.settings.duettoUrl);
    if (!url) {
      setActiveTab("settings");
      els.duettoUrl.focus();
      return;
    }
    showHomeNotice("正在打开 Duetto…");
    platform.openExternalUrl(url);
    return;
  }

  const messages = {
    reading: "共读会在后续对话中接入。",
    health: "身体数据会在后续对话中接入。",
    zeta: "Zeta 的今日状态会在后续对话中接入。"
  };
  showHomeNotice(messages[feature] || "这项功能会在后续对话中接入。");
}

function openToolsPage(section) {
  const descriptions = {
    packages: "包管理：安装、启停与更新扩展包。",
    workflows: "工作流：编排自动流程和定时动作。",
    tools: "工具管理：查看可用能力与调用状态。"
  };
  els.toolsIntro.textContent = descriptions[section] || "集中管理扩展包、工作流和可调用工具。";
  setActiveTab("tools");
}

function showHomeNotice(message) {
  els.homeNotice.textContent = message;
}

function renderHomeState() {
  const configured = Boolean(normalizeDuettoUrl(state.settings.duettoUrl));
  els.duettoCard.classList.toggle("feature-ready", configured);
  els.duettoCardSubtitle.textContent = configured
    ? "继续播放 · 打开 Duetto"
    : "继续播放 · 尚未连接 Duetto";
}

function normalizeDuettoUrl(value) {
  try {
    const url = new URL(String(value || "").trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") {
      return "";
    }
    if (url.pathname === "/") {
      url.pathname = "/pkg/index.html";
    }
    return url.toString();
  } catch {
    return "";
  }
}

function setActiveTab(tab) {
  state.activeTab = tab;
  els.chatView.classList.toggle("view-active", tab === "chat");
  els.homeView.classList.toggle("view-active", tab === "home");
  els.toolsView.classList.toggle("view-active", tab === "tools");
  els.memoryView.classList.toggle("view-active", tab === "memory");
  els.scheduleView.classList.toggle("view-active", tab === "schedule");
  els.settingsView.classList.toggle("view-active", tab === "settings");
  const navigationTab = tab === "tools" ? "home" : tab;
  els.tabs.forEach((button) => {
    button.classList.toggle("tab-active", button.dataset.tab === navigationTab);
  });
  if (tab === "memory") {
    loadActiveMemoryPanel();
  }
  if (tab === "schedule") {
    renderSchedule();
    checkScheduleNudges();
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
  els.duettoUrl.value = state.settings.duettoUrl;
  els.gatewayToken.value = state.settings.gatewayToken;
  els.modelName.value = state.settings.model;
  els.temperature.value = state.settings.temperature;
  els.temperatureValue.value = Number(state.settings.temperature).toFixed(1);
  els.backgroundUrl.value = state.settings.backgroundUrl;
  els.backgroundTransparency.value = state.settings.backgroundTransparency;
  els.backgroundTransparencyValue.value = `${Math.round(Number(state.settings.backgroundTransparency) * 100)}%`;
  els.backgroundFit.value = state.settings.backgroundFit;
  els.accentColor.value = state.settings.accentColor;
}

function readSettingsForm() {
  state.settings = {
    backendUrl: els.backendUrl.value.trim() || defaultBackend,
    duettoUrl: els.duettoUrl.value.trim(),
    gatewayToken: els.gatewayToken.value.trim(),
    model: els.modelName.value.trim() || defaultSettings.model,
    temperature: Number(els.temperature.value),
    backgroundUrl: els.backgroundUrl.value.trim(),
    backgroundTransparency: Number(els.backgroundTransparency.value),
    backgroundFit: els.backgroundFit.value === "contain" ? "contain" : "cover",
    accentColor: els.accentColor.value || defaultSettings.accentColor
  };
}

function applySettings() {
  const root = document.documentElement;
  root.style.setProperty("--accent", state.settings.accentColor);
  root.style.setProperty("--background-image-opacity", String(1 - state.settings.backgroundTransparency));
  root.style.setProperty("--bg-size", state.settings.backgroundFit);
  root.style.setProperty("--accent-ink", readableInkFor(state.settings.accentColor));

  if (state.settings.backgroundUrl) {
    root.style.setProperty("--bg-image", `url("${cssUrlEscape(state.settings.backgroundUrl)}")`);
  } else {
    root.style.setProperty("--bg-image", 'url("/frontend/background.svg")');
  }
  renderHomeState();
}

function normalizeSettings(value) {
  const settings = value && typeof value === "object" ? value : {};
  const { systemPrompt: _legacySystemPrompt, ...persistedSettings } = settings;
  const backgroundTransparency = Number(settings.backgroundTransparency);
  const previousBackgroundOpacity = Number(settings.backgroundOpacity);
  const normalizedTransparency = Number.isFinite(backgroundTransparency)
    ? backgroundTransparency
    : (Number.isFinite(previousBackgroundOpacity)
      ? 1 - previousBackgroundOpacity
      : defaultSettings.backgroundTransparency);
  return {
    ...defaultSettings,
    ...persistedSettings,
    backgroundTransparency: Number.isFinite(normalizedTransparency)
      ? Math.min(1, Math.max(0, normalizedTransparency))
      : defaultSettings.backgroundTransparency,
    backgroundFit: settings.backgroundFit === "contain" ? "contain" : "cover"
  };
}

async function refreshHealth() {
  try {
    const response = await platform.request(apiUrl("/health"));
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
  platform.storage.setJson(storageKeys.messages, state.messages.slice(-80));
}

function saveSettings() {
  platform.storage.setJson(storageKeys.settings, state.settings);
}

function loadJson(key, fallback) {
  return platform.storage.getJson(key, fallback);
}

function cleanBaseUrl(url) {
  return String(url || defaultBackend).replace(/\/+$/, "");
}

function apiUrl(path) {
  const baseUrl = cleanBaseUrl(state.settings.backendUrl);
  if (!baseUrl) {
    throw new Error("后端地址未配置。请先在设置里填写网关地址；APK 版需要由 Android 原生层注入 getApiBaseUrl。");
  }
  return `${baseUrl}${path}`;
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
