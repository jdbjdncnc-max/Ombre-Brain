export const CHAT_EXPORT_FORMAT = "ombre-companion-chat-export";
export const CHAT_EXPORT_VERSION = 1;
export const MAX_CHAT_IMPORT_BYTES = 32 * 1024 * 1024;
export const MAX_CHAT_IMPORT_MESSAGES = 50000;

const ALLOWED_MESSAGE_ROLES = new Set(["user", "assistant", "summary"]);
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export function parseChatExport(text) {
  const source = String(text || "").replace(/^\uFEFF/, "").trim();
  if (!source) {
    throw new Error("这个文件是空的。");
  }

  let payload;
  try {
    payload = JSON.parse(source);
  } catch {
    throw new Error("无法读取这个 JSON 文件，请选择 Entangle 导出的聊天记录。");
  }

  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("这个文件不是有效的 Entangle 聊天记录。");
  }
  if (payload.format !== CHAT_EXPORT_FORMAT) {
    throw new Error("文件格式不匹配，请选择由“导出聊天记录”生成的 JSON 文件。");
  }
  if (Number(payload.version) !== CHAT_EXPORT_VERSION) {
    throw new Error(`暂不支持这个聊天记录版本（版本 ${String(payload.version || "未知")}）。`);
  }
  if (!Array.isArray(payload.messages)) {
    throw new Error("文件里没有可恢复的聊天记录。");
  }
  if (payload.messages.length > MAX_CHAT_IMPORT_MESSAGES) {
    throw new Error(`聊天记录超过 ${MAX_CHAT_IMPORT_MESSAGES.toLocaleString("zh-CN")} 条，暂时无法一次导入。`);
  }

  const messages = payload.messages.filter((message, index) => {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      throw new Error(`第 ${index + 1} 条聊天记录损坏，已停止导入。`);
    }
    if (!ALLOWED_MESSAGE_ROLES.has(String(message.role || ""))) {
      throw new Error(`第 ${index + 1} 条聊天记录类型异常，已停止导入。`);
    }
    return !(message.role === "summary" && message.pending);
  });

  return {
    exportedAt: validDateString(payload.exportedAt),
    sessionId: normalizeSessionId(payload.sessionId),
    participants: normalizeParticipants(payload.participants),
    messages
  };
}

export function prepareImportedMessages(messages, {
  createId = () => crypto.randomUUID(),
  timezone = "UTC"
} = {}) {
  const usedIds = new Set();
  return messages.map((message) => {
    const originalId = String(message.id || "").trim();
    const id = originalId && !usedIds.has(originalId) ? originalId : String(createId());
    usedIds.add(id);

    const pendingAssistant = message.role === "assistant" && Boolean(message.pending);
    const normalized = {
      ...message,
      id,
      role: String(message.role),
      content: typeof message.content === "string" ? message.content : "",
      pending: false
    };
    if (pendingAssistant) {
      normalized.streamInterrupted = true;
      normalized.streamError = String(
        normalized.streamError || "导出时这条回复仍在生成，恢复后已标记为可能不完整。"
      );
    }
    if (
      (normalized.role === "user" || normalized.role === "assistant")
      && normalized.createdAt
      && !normalized.timezone
    ) {
      normalized.timezone = String(timezone || "UTC");
    }
    return normalized;
  });
}

export function mergeChatMessages(currentMessages, importedMessages) {
  const combined = [...importedMessages, ...currentMessages];
  const deduplicated = new Map();
  combined.forEach((message, index) => {
    const key = messageKey(message, index);
    if (deduplicated.has(key)) {
      const previous = deduplicated.get(key);
      deduplicated.set(key, { ...previous, message });
    } else {
      deduplicated.set(key, { message, order: index });
    }
  });

  return [...deduplicated.values()]
    .sort((left, right) => {
      const leftTime = messageTime(left.message);
      const rightTime = messageTime(right.message);
      if (leftTime !== null && rightTime !== null && leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      return left.order - right.order;
    })
    .map((entry) => entry.message);
}

export function isUsableSessionId(value) {
  return SESSION_ID_PATTERN.test(String(value || "").trim());
}

function normalizeSessionId(value) {
  const sessionId = String(value || "").trim();
  return isUsableSessionId(sessionId) ? sessionId : "";
}

function normalizeParticipants(value) {
  const participants = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    user: String(participants.user || "").trim().slice(0, 80),
    assistant: String(participants.assistant || "").trim().slice(0, 80),
    model: String(participants.model || "").trim().slice(0, 160)
  };
}

function validDateString(value) {
  const candidate = String(value || "").trim();
  return candidate && !Number.isNaN(new Date(candidate).getTime()) ? candidate : "";
}

function messageKey(message, index) {
  const id = String(message?.id || "").trim();
  if (id) {
    return `id:${id}`;
  }
  return [
    "legacy",
    String(message?.role || ""),
    String(message?.createdAt || ""),
    String(message?.content || ""),
    String(message?.userThinking || ""),
    String(index)
  ].join("\u0000");
}

function messageTime(message) {
  const time = new Date(message?.createdAt || "").getTime();
  return Number.isNaN(time) ? null : time;
}
