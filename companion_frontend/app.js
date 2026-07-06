const storageKeys = {
  messages: "companion.messages.v1",
  settings: "companion.settings.v1",
  sessionId: "companion.sessionId.v1",
  schedule: "companion.schedule.v1",
  scheduleNudges: "companion.scheduleNudges.v1"
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
  memoryItems: [],
  memoryQuery: "",
  memoryVisibleCount: 80,
  scheduleItems: normalizeScheduleItems(loadJson(storageKeys.schedule, [])),
  scheduleNudges: loadJson(storageKeys.scheduleNudges, {}),
  scheduleSyncStatus: "本地日程",
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
  scheduleView: document.querySelector("#scheduleView"),
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
  courseImportText: document.querySelector("#courseImportText"),
  courseImportStatus: document.querySelector("#courseImportStatus"),
  importCoursesButton: document.querySelector("#importCoursesButton"),
  clearCourseImportButton: document.querySelector("#clearCourseImportButton"),
  clearDoneButton: document.querySelector("#clearDoneButton"),
  scheduleTimeline: document.querySelector("#scheduleTimeline"),
  upcomingSummary: document.querySelector("#upcomingSummary"),
  upcomingScheduleList: document.querySelector("#upcomingScheduleList"),
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

  els.todoForm.addEventListener("submit", (event) => {
    event.preventDefault();
    addTodoFromForm();
  });

  els.importCoursesButton.addEventListener("click", importCourses);

  els.clearCourseImportButton.addEventListener("click", () => {
    els.courseImportText.value = "";
    els.courseImportStatus.textContent = "";
  });

  els.clearDoneButton.addEventListener("click", clearDoneTodos);

  els.scheduleNotifyButton.addEventListener("click", requestScheduleNotifications);

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

  captureTodoFromMessage(text);
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
  const scheduleContext = buildScheduleInjection();
  const systemMessages = [];

  if (state.settings.systemPrompt) {
    systemMessages.push({ role: "system", content: state.settings.systemPrompt });
  }

  if (scheduleContext) {
    systemMessages.push({ role: "system", content: scheduleContext });
  }

  return [...systemMessages, ...messages];
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
  updateNotificationButton();
}

async function loadSchedule() {
  try {
    const data = await gatewayFetch("/api/schedule");
    const items = Array.isArray(data) ? data : data.items;
    state.scheduleItems = normalizeScheduleItems(items);
    saveScheduleLocal();
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
  els.courseImportStatus.textContent = `导入 ${result.added} 项`;
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
  const todayItems = sortScheduleItems(state.scheduleItems.filter((item) => item.date === today));
  const activeItems = todayItems.filter((item) => !item.done);
  const upcoming = nextScheduleItems(now, 8);
  const nextItem = upcoming[0] || activeItems[0];

  els.scheduleTodayCount.textContent = `今日 ${activeItems.length} 项`;
  els.scheduleNextTitle.textContent = nextItem ? nextItem.title : "今天清爽";
  els.scheduleNextMeta.textContent = nextItem ? scheduleMetaLine(nextItem, now) : "没有临近日程";
  els.upcomingSummary.textContent = upcoming.length ? `${upcoming.length} 项` : "";
  els.scheduleSyncStatus.textContent = state.scheduleSyncStatus;

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
  localStorage.setItem(storageKeys.schedule, JSON.stringify(state.scheduleItems));
}

async function syncSchedule() {
  await gatewayFetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      version: 1,
      updatedAt: new Date().toISOString(),
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
  const current = state.scheduleItems.filter((item) => !item.done && isScheduleItemNow(item, now));
  const todayRemaining = sortScheduleItems(state.scheduleItems)
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
  for (const item of state.scheduleItems) {
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
  if (!("Notification" in window)) {
    els.todoCaptureStatus.textContent = "当前浏览器不支持提醒";
    clearInlineStatusLater(els.todoCaptureStatus);
    return;
  }
  const permission = await Notification.requestPermission();
  updateNotificationButton();
  els.todoCaptureStatus.textContent = permission === "granted" ? "提醒已开启" : "提醒未开启";
  clearInlineStatusLater(els.todoCaptureStatus);
}

function updateNotificationButton() {
  if (!("Notification" in window)) {
    els.scheduleNotifyButton.disabled = true;
    return;
  }
  els.scheduleNotifyButton.classList.toggle("tab-active", Notification.permission === "granted");
}

function showBrowserNotification(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") {
    return;
  }
  new Notification(title, {
    body,
    tag: `companion-schedule-${title}-${body}`,
    silent: false
  });
}

function saveScheduleNudges() {
  localStorage.setItem(storageKeys.scheduleNudges, JSON.stringify(state.scheduleNudges));
}

function parseCourseLine(line) {
  const text = String(line || "").trim();
  if (!text) {
    return null;
  }

  const cells = text.split(/[,，\t|]/).map((cell) => cell.trim()).filter(Boolean);
  if (cells.length >= 4) {
    const firstDate = normalizeDateValue(cells[0]);
    const secondDate = normalizeDateValue(cells[1]);
    if (firstDate) {
      return makeCourseItem({
        date: firstDate,
        start: cells[1],
        end: cells[2],
        title: cells[3],
        location: cells.slice(4).join(" ")
      });
    }
    if (secondDate) {
      return makeCourseItem({
        title: cells[0],
        date: secondDate,
        start: cells[2],
        end: cells[3],
        location: cells.slice(4).join(" ")
      });
    }
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
    const date = normalizeDateValue(item?.date);
    const start = normalizeTimeValue(item?.start);
    const title = String(item?.title || "").trim();
    if (!date || !start || !title) {
      return null;
    }
    return {
      id: String(item.id || crypto.randomUUID()),
      type: item.type === "course" ? "course" : "todo",
      date,
      start,
      end: normalizeTimeValue(item.end),
      title: title.slice(0, 120),
      location: String(item.location || "").trim().slice(0, 120),
      note: String(item.note || "").trim().slice(0, 200),
      done: Boolean(item.done),
      source: String(item.source || "manual").slice(0, 40),
      createdAt: item.createdAt || item.created_at || new Date().toISOString()
    };
  }).filter(Boolean));
}

function sortScheduleItems(items) {
  return [...items].sort((a, b) => {
    const left = `${a.date}T${a.start}`;
    const right = `${b.date}T${b.start}`;
    return left.localeCompare(right) || a.title.localeCompare(b.title);
  });
}

function nextScheduleItems(now = new Date(), limit = 8) {
  const maxTime = now.getTime() + 7 * 24 * 60 * 60000;
  return sortScheduleItems(state.scheduleItems)
    .filter((item) => {
      if (item.done) {
        return false;
      }
      const start = itemDateTime(item);
      return start && start.getTime() >= now.getTime() - 10 * 60000 && start.getTime() <= maxTime;
    })
    .slice(0, limit);
}

function scheduleKey(item) {
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
  els.scheduleView.classList.toggle("view-active", tab === "schedule");
  els.settingsView.classList.toggle("view-active", tab === "settings");
  els.tabs.forEach((button) => {
    button.classList.toggle("tab-active", button.dataset.tab === tab);
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
