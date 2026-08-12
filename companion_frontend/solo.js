const SOURCE_LABELS = {
  you: "因为你",
  conversation: "因为我和她",
  world: "因为世界",
  peer: "因为同类",
  self: "因为自己"
};

const BUCKET_META = {
  joy: { label: "开心", color: "#d6ad43" },
  miss: { label: "想你", color: "#bf809d" },
  ache: { label: "心疼", color: "#d88b52" },
  cross: { label: "恼火", color: "#b95750" },
  low: { label: "低落", color: "#718ba1" },
  spark: { label: "心动", color: "#c94f80" }
};

const ACTIVITY_ICONS = {
  real: "↗",
  mcp: "⇄",
  self: "✎",
  memory: "♡",
  idle: "○"
};

export function createSoloPanel({ requestState, requestTimeline, onClose }) {
  const els = {
    back: document.querySelector("#soloBackButton"),
    refresh: document.querySelector("#soloRefreshButton"),
    status: document.querySelector("#soloPanelStatus"),
    mode: document.querySelector("#soloModeChip"),
    drive: document.querySelector("#soloDriveChip"),
    mood: document.querySelector("#soloMoodLine"),
    updated: document.querySelector("#soloUpdatedAt"),
    emotions: document.querySelector("#soloEmotionList"),
    timelineSummary: document.querySelector("#soloTimelineSummary"),
    timelineLegend: document.querySelector("#soloTimelineLegend"),
    emotionChart: document.querySelector("#soloEmotionChart"),
    activityTrack: document.querySelector("#soloActivityTrack"),
    activityList: document.querySelector("#soloActivityList"),
    unsentList: document.querySelector("#soloUnsentList"),
    talkingList: document.querySelector("#soloTalkingList"),
    homeMood: document.querySelector("#zetaMoodSummary"),
    homeMode: document.querySelector("#zetaModeSummary")
  };

  let expandedKey = "";
  let loading = false;
  let timer = 0;
  let selectedActivityId = "";
  let currentTimeline = normalizeTimeline({});
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
      const timelineTask = typeof requestTimeline === "function"
        ? requestTimeline().catch((error) => ({ timelineError: error?.message || "轨迹暂时不可用" }))
        : Promise.resolve({});
      const [statePayload, timelinePayload] = await Promise.all([requestState(), timelineTask]);
      const data = normalizeState(statePayload);
      currentTimeline = normalizeTimeline(timelinePayload);
      render(data, currentTimeline);
      if (timelinePayload?.timelineError) {
        setStatus("情绪已更新，轨迹暂时读不到", true);
      } else {
        setStatus(data.enabled ? "情绪和轨迹会随时间继续变化" : "独处系统目前未开启，正在显示基线状态", !data.enabled);
      }
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

  function render(data, timeline) {
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
    renderTimeline(timeline);
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

  function renderTimeline(timeline) {
    const activities = Array.isArray(timeline.activities) ? timeline.activities : [];
    els.timelineSummary.textContent = activities.length
      ? `记录了 ${activities.length} 件真实发生的事`
      : "还没有形成第一条轨迹";
    renderEmotionChart(timeline.series, activities);
    renderActivityTrack(activities);
    renderActivityCards(activities);
    renderNoteList(els.unsentList, timeline.unsent, "还没有留下未发送的话。", "text");
    renderNoteList(els.talkingList, timeline.talkingPoints, "暂时没有攒下想说的事。", "text");
  }

  function renderEmotionChart(series, activities) {
    const points = (Array.isArray(series) ? series : [])
      .map((item) => ({ ...item, time: new Date(item.ts).getTime() }))
      .filter((item) => Number.isFinite(item.time) && item.buckets && typeof item.buckets === "object")
      .sort((left, right) => left.time - right.time);
    els.timelineLegend.replaceChildren();
    els.emotionChart.replaceChildren();
    if (!points.length) {
      els.emotionChart.append(makeTimelineEmpty("等待第一份情绪快照。"));
      return;
    }

    const latest = points[points.length - 1].buckets;
    const keys = Object.keys(BUCKET_META)
      .sort((left, right) => (Number(latest[right]) || 0) - (Number(latest[left]) || 0))
      .slice(0, 3);
    keys.forEach((key) => {
      const chip = document.createElement("span");
      chip.style.setProperty("--line-color", BUCKET_META[key].color);
      chip.textContent = BUCKET_META[key].label;
      els.timelineLegend.append(chip);
    });

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 320 108");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `近二十四小时${keys.map((key) => BUCKET_META[key].label).join("、")}变化`);
    [18, 54, 90].forEach((y) => {
      const grid = document.createElementNS(svg.namespaceURI, "line");
      setSvgAttributes(grid, { x1: 10, x2: 310, y1: y, y2: y, class: "solo-chart-grid" });
      svg.append(grid);
    });

    const end = Math.max(Date.now(), points[points.length - 1].time);
    const start = end - 24 * 60 * 60 * 1000;
    const activity = activities.find((item) => item.id === selectedActivityId);
    const activityTime = activity ? new Date(activity.ts).getTime() : NaN;
    if (Number.isFinite(activityTime)) {
      const x = timelineX(activityTime, start, end);
      const focus = document.createElementNS(svg.namespaceURI, "line");
      setSvgAttributes(focus, { x1: x, x2: x, y1: 7, y2: 101, class: "solo-chart-focus" });
      svg.append(focus);
    }

    keys.forEach((key) => {
      const polyline = document.createElementNS(svg.namespaceURI, "polyline");
      const coordinates = points.map((item) => {
        const x = timelineX(item.time, start, end);
        const value = clampNumber(item.buckets[key]);
        const y = 98 - value * 0.84;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      setSvgAttributes(polyline, {
        points: coordinates,
        fill: "none",
        stroke: BUCKET_META[key].color,
        "stroke-width": key === keys[0] ? 3 : 2,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        class: "solo-chart-line"
      });
      svg.append(polyline);
      if (points.length === 1) {
        const circle = document.createElementNS(svg.namespaceURI, "circle");
        setSvgAttributes(circle, {
          cx: timelineX(points[0].time, start, end),
          cy: 98 - clampNumber(points[0].buckets[key]) * 0.84,
          r: 3,
          fill: BUCKET_META[key].color
        });
        svg.append(circle);
      }
    });
    els.emotionChart.append(svg);
  }

  function renderActivityTrack(activities) {
    els.activityTrack.replaceChildren();
    const line = document.createElement("span");
    line.className = "solo-activity-line";
    els.activityTrack.append(line);
    if (!activities.length) {
      return;
    }
    const end = Date.now();
    const start = end - 24 * 60 * 60 * 1000;
    activities.slice().reverse().forEach((activity) => {
      const time = new Date(activity.ts).getTime();
      if (!Number.isFinite(time)) {
        return;
      }
      const point = document.createElement("button");
      point.type = "button";
      point.className = `solo-activity-point${activity.id === selectedActivityId ? " is-selected" : ""}`;
      point.style.left = `${Math.max(1, Math.min(99, ((time - start) / (end - start)) * 100))}%`;
      point.title = `${formatCompactTime(activity.ts)} ${activity.title || "独处活动"}`;
      point.setAttribute("aria-label", point.title);
      point.textContent = ACTIVITY_ICONS[activity.kind] || "•";
      point.addEventListener("click", () => selectActivity(activity.id));
      els.activityTrack.append(point);
    });
  }

  function renderActivityCards(activities) {
    els.activityList.replaceChildren();
    if (!activities.length) {
      els.activityList.append(makeTimelineEmpty("它真正做过的事会出现在这里，不会为了填满时间轴而编造。"));
      return;
    }
    activities.forEach((activity) => {
      const article = document.createElement("article");
      article.className = `solo-activity-card${activity.id === selectedActivityId ? " is-selected" : ""}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "solo-activity-summary";
      button.addEventListener("click", () => selectActivity(activity.id));
      const icon = document.createElement("span");
      icon.className = `solo-activity-icon kind-${safeKey(activity.kind)}`;
      icon.textContent = ACTIVITY_ICONS[activity.kind] || "•";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = String(activity.title || "独处活动");
      const summary = document.createElement("small");
      summary.textContent = String(activity.summary || activity.felt || "留下了一条真实轨迹");
      copy.append(title, summary);
      const time = document.createElement("time");
      time.textContent = formatCompactTime(activity.ts);
      button.append(icon, copy, time);
      article.append(button);

      if (activity.id === selectedActivityId) {
        const detail = document.createElement("div");
        detail.className = "solo-activity-detail";
        const felt = document.createElement("p");
        felt.textContent = activity.felt ? `感受：${activity.felt}` : "这次没有额外的情绪评价。";
        const drive = document.createElement("p");
        drive.textContent = `当时因为：${activity.drive || "没有单独记录驱动力"}`;
        const body = document.createElement("pre");
        body.textContent = String(activity.detail || activity.summary || "没有更多内容。");
        detail.append(felt, drive, body);
        if (activity.evidence?.kind === "http" && safeHttpUrl(activity.evidence.url)) {
          const link = document.createElement("a");
          link.href = activity.evidence.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "打开原始来源 ↗";
          detail.append(link);
        } else {
          const evidence = document.createElement("small");
          evidence.textContent = activity.kind === "self" || activity.kind === "idle"
            ? "证据类型：自身生成，未声称存在外部来源"
            : `证据类型：${activity.evidence?.kind || "未记录"}`;
          detail.append(evidence);
        }
        article.append(detail);
      }
      els.activityList.append(article);
    });
  }

  function renderNoteList(container, items, emptyText, field) {
    container.replaceChildren();
    const values = Array.isArray(items) ? items.slice(0, 4) : [];
    if (!values.length) {
      const empty = document.createElement("p");
      empty.className = "solo-note-empty";
      empty.textContent = emptyText;
      container.append(empty);
      return;
    }
    values.forEach((item) => {
      const card = document.createElement("div");
      const text = document.createElement("p");
      text.textContent = String(item[field] || "");
      const time = document.createElement("time");
      time.textContent = formatCompactTime(item.ts);
      card.append(text, time);
      container.append(card);
    });
  }

  function selectActivity(id) {
    selectedActivityId = selectedActivityId === id ? "" : id;
    renderTimeline(currentTimeline);
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

function normalizeTimeline(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  return {
    hours: Number(source.hours) || 24,
    series: Array.isArray(source.series) ? source.series : [],
    activities: Array.isArray(source.activities) ? source.activities : [],
    unsent: Array.isArray(source.unsent) ? source.unsent : [],
    talkingPoints: Array.isArray(source.talkingPoints) ? source.talkingPoints : []
  };
}

function makeTimelineEmpty(message) {
  const empty = document.createElement("p");
  empty.className = "solo-timeline-empty";
  empty.textContent = message;
  return empty;
}

function setSvgAttributes(element, attributes) {
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
}

function timelineX(time, start, end) {
  const ratio = Math.max(0, Math.min(1, (time - start) / Math.max(1, end - start)));
  return 10 + ratio * 300;
}

function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
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
