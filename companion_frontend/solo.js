const SOURCE_LABELS = {
  you: "因为你",
  world: "因为世界",
  peer: "因为同类",
  self: "因为自己"
};

export function createSoloPanel({ requestState, onClose }) {
  const els = {
    back: document.querySelector("#soloBackButton"),
    refresh: document.querySelector("#soloRefreshButton"),
    status: document.querySelector("#soloPanelStatus"),
    mode: document.querySelector("#soloModeChip"),
    drive: document.querySelector("#soloDriveChip"),
    mood: document.querySelector("#soloMoodLine"),
    updated: document.querySelector("#soloUpdatedAt"),
    emotions: document.querySelector("#soloEmotionList"),
    homeMood: document.querySelector("#zetaMoodSummary"),
    homeMode: document.querySelector("#zetaModeSummary")
  };

  let expandedKey = "";
  let loading = false;
  let timer = 0;
  const previousValues = new Map();

  els.back?.addEventListener("click", () => onClose?.());
  els.refresh?.addEventListener("click", () => refresh());

  async function refresh({ silent = false } = {}) {
    if (loading) {
      return;
    }
    loading = true;
    els.refresh?.classList.add("is-loading");
    if (!silent) {
      setStatus("正在读取此刻的心情…");
    }
    try {
      const data = normalizeState(await requestState());
      render(data);
      setStatus(data.enabled ? "情绪会随时间继续变化" : "独处系统目前未开启，正在显示基线状态", !data.enabled);
    } catch (error) {
      setStatus(error?.message || "暂时读不到情绪状态", true);
      if (!els.emotions?.children.length) {
        renderEmpty("暂时读不到情绪状态，稍后再试试。", "连接失败");
      }
    } finally {
      loading = false;
      els.refresh?.classList.remove("is-loading");
    }
  }

  function render(data) {
    const mode = data.mode?.label || "安静待着";
    const drive = data.drive?.label || "好奇";
    const mood = data.moodLine || "安静待着";
    els.mode.textContent = mode;
    els.drive.textContent = drive;
    els.mood.textContent = mood;
    els.updated.textContent = formatUpdatedAt(data.updatedAt);
    els.homeMood.textContent = `现在的心情：${mood}`;
    els.homeMode.textContent = `当前模式：${mode}`;
    renderBuckets(data.buckets);
  }

  function renderBuckets(buckets) {
    const items = Array.isArray(buckets) ? buckets.slice(0, 6) : [];
    if (!items.length) {
      renderEmpty("还没有可展示的情绪数据。", "暂无数据");
      return;
    }

    const fragment = document.createDocumentFragment();
    items.forEach((bucket) => {
      const key = safeKey(bucket.key);
      const value = clampNumber(bucket.value);
      const article = document.createElement("article");
      article.className = `solo-emotion${bucket.primary ? " is-primary" : ""}`;
      article.dataset.bucket = key;

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "solo-emotion-toggle";
      toggle.setAttribute("aria-expanded", String(expandedKey === key));
      toggle.addEventListener("click", () => {
        expandedKey = expandedKey === key ? "" : key;
        renderBuckets(items);
      });

      const accent = document.createElement("span");
      accent.className = "solo-emotion-accent";
      accent.setAttribute("aria-hidden", "true");
      const label = document.createElement("strong");
      label.textContent = String(bucket.label || key || "情绪");
      const track = document.createElement("span");
      track.className = "solo-emotion-track";
      const fill = document.createElement("span");
      fill.className = "solo-emotion-fill";
      fill.style.width = `${previousValues.get(key) ?? 0}%`;
      track.append(fill);
      const output = document.createElement("output");
      output.textContent = String(Math.round(value));
      const chevron = document.createElement("span");
      chevron.className = "solo-emotion-chevron";
      chevron.textContent = expandedKey === key ? "⌃" : "⌄";
      toggle.append(accent, label, track, output, chevron);
      article.append(toggle);

      if (expandedKey === key) {
        article.append(renderDetails(bucket));
      }
      fragment.append(article);
      previousValues.set(key, value);
      requestAnimationFrame(() => {
        fill.style.width = `${value}%`;
      });
    });
    els.emotions.replaceChildren(fragment);
  }

  function renderDetails(bucket) {
    const details = document.createElement("div");
    details.className = "solo-emotion-details";
    const channels = document.createElement("div");
    channels.className = "solo-channel-list";
    (Array.isArray(bucket.channels) ? bucket.channels : []).forEach((channel) => {
      const row = document.createElement("div");
      row.className = "solo-channel";
      const label = document.createElement("span");
      label.textContent = String(channel.label || channel.key || "细情绪");
      const track = document.createElement("span");
      track.className = "solo-channel-track";
      const fill = document.createElement("span");
      fill.style.width = `${clampNumber(channel.value)}%`;
      track.append(fill);
      const value = document.createElement("b");
      value.textContent = String(Math.round(clampNumber(channel.value)));
      row.append(label, track, value);
      channels.append(row);
    });

    const causeSection = document.createElement("div");
    causeSection.className = "solo-cause-section";
    const heading = document.createElement("strong");
    heading.textContent = "最近是因为什么";
    causeSection.append(heading);
    const causes = Array.isArray(bucket.causes) ? bucket.causes.slice(0, 3) : [];
    if (!causes.length) {
      const empty = document.createElement("p");
      empty.className = "solo-cause-empty";
      empty.textContent = "暂时还没有新的归因记录。";
      causeSection.append(empty);
    } else {
      causes.forEach((cause) => causeSection.append(renderCause(cause)));
    }
    details.append(channels, causeSection);
    return details;
  }

  function renderCause(cause) {
    const item = document.createElement("div");
    item.className = "solo-cause";
    const source = document.createElement("span");
    source.className = `solo-cause-source source-${safeKey(cause.source)}`;
    source.textContent = SOURCE_LABELS[cause.source] || SOURCE_LABELS.self;
    const copy = document.createElement("span");
    copy.className = "solo-cause-copy";
    copy.textContent = String(cause.text || "情绪发生了变化");
    const meta = document.createElement("small");
    const delta = Number(cause.delta) || 0;
    meta.textContent = `${formatCompactTime(cause.ts)} · ${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`;
    item.append(source, copy, meta);
    return item;
  }

  function renderEmpty(message, status) {
    const empty = document.createElement("div");
    empty.className = "solo-panel-empty";
    const icon = document.createElement("span");
    icon.textContent = "☾";
    const title = document.createElement("strong");
    title.textContent = status;
    const copy = document.createElement("p");
    copy.textContent = message;
    empty.append(icon, title, copy);
    els.emotions.replaceChildren(empty);
  }

  function setStatus(message, muted = false) {
    els.status.textContent = message;
    els.status.classList.toggle("is-muted", muted);
  }

  function start() {
    refresh({ silent: true });
    if (!timer) {
      timer = window.setInterval(() => refresh({ silent: true }), 60000);
    }
  }

  function open() {
    refresh({ silent: true });
  }

  return { open, refresh, start };
}

function normalizeState(payload) {
  const source = payload?.buckets ? payload : payload?.state || {};
  return {
    enabled: Boolean(source.enabled),
    updatedAt: String(source.updatedAt || ""),
    mode: source.mode && typeof source.mode === "object" ? source.mode : { label: String(source.mode || "安静待着") },
    drive: source.drive && typeof source.drive === "object" ? source.drive : { label: String(source.drive || "好奇") },
    moodLine: String(source.moodLine || "安静待着"),
    buckets: Array.isArray(source.buckets) ? source.buckets : []
  };
}

function clampNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : 0;
}

function safeKey(value) {
  return String(value || "unknown").replace(/[^a-z0-9_-]/gi, "").slice(0, 32) || "unknown";
}

function formatUpdatedAt(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "等待首次更新";
  }
  return `${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date)} 更新`;
}

function formatCompactTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "刚刚";
  }
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}
