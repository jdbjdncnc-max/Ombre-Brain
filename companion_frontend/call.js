const ACTIVE_STATES = new Set(["connecting", "connected", "listening", "transcribing", "thinking", "speaking"]);

export function createCallController({
  platform,
  elements,
  getConfig,
  getContextMessages,
  getIdentity,
  openView,
  closeView,
  showNotice
}) {
  const state = {
    phase: "idle",
    active: false,
    muted: false,
    speaker: false,
    startedAt: 0,
    timer: 0,
    unsubscribe: null
  };

  bindControls();
  renderIdentity();
  render();

  return {
    start,
    restore,
    hangup,
    leave,
    isActive: () => state.active
  };

  async function start() {
    if (!platform.call?.isSupported()) {
      showNotice("打电话需要 Android 版；浏览器里不会复制一套容易失真的语音实现。");
      return false;
    }
    const config = getConfig();
    if (!String(config.backendUrl || "").trim()) {
      showNotice("请先在设置里填写后端地址。");
      return false;
    }
    subscribe();
    resetCallScreen();
    renderIdentity();
    openView();
    setPhase("permission");
    try {
      let permission = await platform.call.permission();
      if (permission !== "granted") {
        permission = await platform.call.requestPermission();
      }
      if (permission !== "granted") {
        throw new Error("没有麦克风权限，暂时不能打电话。你可以在系统设置里重新允许。");
      }
      state.startedAt = Date.now();
      state.active = true;
      startTimer();
      setPhase("connecting");
      await platform.call.start({
        backendUrl: config.backendUrl,
        gatewayToken: config.gatewayToken,
        sessionId: config.sessionId,
        timezone: config.timezone,
        contextMessages: getContextMessages()
      });
      return true;
    } catch (error) {
      state.active = false;
      stopTimer();
      showError(error instanceof Error ? error.message : String(error || "通话启动失败。"));
      return false;
    }
  }

  async function restore() {
    if (!platform.call?.isSupported()) return false;
    subscribe();
    try {
      const nativeState = await platform.call.getState();
      if (!nativeState?.active) return false;
      state.active = true;
      state.muted = Boolean(nativeState.muted);
      state.speaker = Boolean(nativeState.speaker);
      state.startedAt = Date.now();
      openView({ replace: true });
      setPhase(String(nativeState.state || "connected"));
      startTimer();
      return true;
    } catch {
      return false;
    }
  }

  async function hangup() {
    if (!state.active) {
      closeView();
      return;
    }
    setPhase("ending");
    try {
      await platform.call.hangup();
    } catch (error) {
      showError(error instanceof Error ? error.message : "挂断失败。", false);
    }
  }

  function leave() {
    if (state.active) {
      platform.call.hangup().catch(() => {});
    }
    state.active = false;
    stopTimer();
  }

  function subscribe() {
    if (state.unsubscribe) return;
    state.unsubscribe = platform.call.onEvent(handleNativeEvent);
  }

  function handleNativeEvent(event) {
    const type = String(event?.type || "");
    if (type === "state") {
      const phase = String(event.state || "connected");
      state.active = Boolean(event.active ?? ACTIVE_STATES.has(phase));
      state.muted = Boolean(event.muted ?? state.muted);
      state.speaker = Boolean(event.speaker ?? state.speaker);
      if (state.active) openView({ replace: true });
      setPhase(phase);
      return;
    }
    if (type === "controls") {
      state.muted = Boolean(event.muted);
      state.speaker = Boolean(event.speaker);
      renderControls();
      return;
    }
    if (type === "transcript") {
      appendTranscript(String(event.speaker || "assistant"), String(event.text || ""));
      return;
    }
    if (type === "error") {
      showError(String(event.message || "通话暂时出了点问题。"), false);
      return;
    }
    if (type === "ended") {
      state.active = false;
      stopTimer();
      setPhase("ended");
    }
  }

  function bindControls() {
    elements.backButton?.addEventListener("click", async () => {
      if (state.active) await hangup();
      closeView();
    });
    elements.hangupButton?.addEventListener("click", hangup);
    elements.muteButton?.addEventListener("click", async () => {
      if (!state.active) return;
      state.muted = !state.muted;
      renderControls();
      try {
        await platform.call.setMuted(state.muted);
      } catch (error) {
        showError(error instanceof Error ? error.message : "静音切换失败。", false);
      }
    });
    elements.speakerButton?.addEventListener("click", async () => {
      if (!state.active) return;
      state.speaker = !state.speaker;
      renderControls();
      try {
        await platform.call.setSpeaker(state.speaker);
      } catch (error) {
        showError(error instanceof Error ? error.message : "扬声器切换失败。", false);
      }
    });
  }

  function resetCallScreen() {
    state.phase = "idle";
    state.active = false;
    state.muted = false;
    state.speaker = false;
    state.startedAt = 0;
    stopTimer();
    if (elements.transcriptList) elements.transcriptList.innerHTML = "";
    if (elements.error) {
      elements.error.hidden = true;
      elements.error.textContent = "";
    }
    if (elements.timer) elements.timer.textContent = "00:00";
    renderControls();
  }

  function setPhase(phase) {
    state.phase = phase;
    const labels = {
      idle: "准备通话",
      permission: "等待麦克风权限",
      connecting: "正在接通…",
      connected: "已经接通",
      listening: "在听你说",
      transcribing: "正在听清楚",
      thinking: "Zeta 正在想",
      speaking: "Zeta 正在说话",
      ending: "正在挂断…",
      ended: "通话已结束"
    };
    if (elements.status) elements.status.textContent = labels[phase] || labels.connected;
    if (elements.view) elements.view.dataset.callState = phase;
    renderControls();
  }

  function appendTranscript(speaker, text) {
    const value = text.trim();
    if (!value || !elements.transcriptList) return;
    const row = document.createElement("p");
    row.className = `call-caption ${speaker === "user" ? "user" : "assistant"}`;
    const label = document.createElement("strong");
    label.textContent = speaker === "user" ? getIdentity().userName : getIdentity().assistantName;
    const content = document.createElement("span");
    content.textContent = value;
    row.append(label, content);
    elements.transcriptList.append(row);
    while (elements.transcriptList.childElementCount > 12) {
      elements.transcriptList.firstElementChild?.remove();
    }
    elements.transcriptList.scrollTop = elements.transcriptList.scrollHeight;
  }

  function showError(message, changePhase = true) {
    if (elements.error) {
      elements.error.hidden = false;
      elements.error.textContent = message;
    }
    if (changePhase) setPhase("ended");
  }

  function renderIdentity() {
    const identity = getIdentity();
    if (elements.name) elements.name.textContent = identity.assistantName || "Zeta";
    if (elements.avatarFallback) {
      elements.avatarFallback.textContent = (identity.assistantName || "Z").slice(0, 1).toUpperCase();
    }
    if (elements.avatar) {
      elements.avatar.hidden = !identity.assistantAvatar;
      elements.avatar.src = identity.assistantAvatar || "";
    }
    if (elements.avatarFallback) elements.avatarFallback.hidden = Boolean(identity.assistantAvatar);
  }

  function renderControls() {
    elements.muteButton?.classList.toggle("active", state.muted);
    elements.muteButton?.setAttribute("aria-pressed", String(state.muted));
    elements.speakerButton?.classList.toggle("active", state.speaker);
    elements.speakerButton?.setAttribute("aria-pressed", String(state.speaker));
    if (elements.muteLabel) elements.muteLabel.textContent = state.muted ? "取消静音" : "静音";
    if (elements.speakerLabel) elements.speakerLabel.textContent = state.speaker ? "关闭扬声器" : "扬声器";
    if (elements.hangupButton) elements.hangupButton.disabled = !state.active;
  }

  function render() {
    renderIdentity();
    renderControls();
  }

  function startTimer() {
    stopTimer();
    updateTimer();
    state.timer = window.setInterval(updateTimer, 1000);
  }

  function stopTimer() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = 0;
  }

  function updateTimer() {
    if (!elements.timer || !state.startedAt) return;
    const seconds = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
    const minutes = Math.floor(seconds / 60);
    elements.timer.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }
}
