// Operit ToolPkg bridge for the Zeta memory gateway.
// Fill these values after deploying Ombre Brain on Zeabur.
const ZETA_GATEWAY_BASE_URL = "";
const ZETA_GATEWAY_TOKEN = "";

function gatewayBaseUrl() {
  const value = (globalThis.ZETA_GATEWAY_BASE_URL || ZETA_GATEWAY_BASE_URL || "").trim();
  if (!value) throw new Error("ZETA_GATEWAY_BASE_URL is not configured");
  return value.replace(/\/+$/, "");
}

function gatewayToken() {
  return (globalThis.ZETA_GATEWAY_TOKEN || ZETA_GATEWAY_TOKEN || "").trim();
}

async function gatewayRequest(path, body, method) {
  const headers = { "Content-Type": "application/json" };
  const token = gatewayToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const request = {
    method: method || "POST",
    headers,
  };
  if (body !== undefined && request.method !== "GET") {
    request.body = JSON.stringify(body);
  }

  const url = `${gatewayBaseUrl()}${path}`;
  const response = await netFetch(url, request);
  const text = await readResponseText(response);
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    data = { raw: text };
  }
  const status = response && typeof response.status === "number" ? response.status : 200;
  if (status >= 400 || data.ok === false) {
    throw new Error(data.error || `Gateway request failed: ${status}`);
  }
  return data;
}

async function netFetch(url, request) {
  if (globalThis.Tools && Tools.Net && typeof Tools.Net.fetch === "function") {
    return Tools.Net.fetch(url, request);
  }
  if (globalThis.Tools && Tools.Net && typeof Tools.Net.request === "function") {
    return Tools.Net.request({ url, ...request });
  }
  if (typeof fetch === "function") {
    return fetch(url, request);
  }
  throw new Error("No network fetch API is available in this Operit environment");
}

async function readResponseText(response) {
  if (!response) return "";
  if (typeof response.text === "function") return response.text();
  if (typeof response.body === "string") return response.body;
  if (typeof response.data === "string") return response.data;
  if (response.data !== undefined) return JSON.stringify(response.data);
  return JSON.stringify(response);
}

export async function zeta_gateway_status() {
  return gatewayRequest("/gateway/status", undefined, "GET");
}

export async function zeta_save_raw_conversation(session_id, messages, source) {
  return gatewayRequest("/gateway/raw", {
    session_id,
    source: source || "operit",
    messages,
  });
}

export async function zeta_write_memory(entry) {
  return gatewayRequest("/gateway/memory", entry);
}

export async function zeta_recall_memories(current_text, recent_context, options) {
  const result = await gatewayRequest("/gateway/recall", {
    current_text,
    recent_context: recent_context || "",
    max_results: options && options.max_results ? options.max_results : 5,
    keyword_limit: options && options.keyword_limit !== undefined ? options.keyword_limit : 4,
    semantic_limit: options && options.semantic_limit !== undefined ? options.semantic_limit : 1,
    track_usage: !options || options.track_usage === undefined ? true : Boolean(options.track_usage),
  });
  return result.injection_text || "";
}

export async function zeta_lookup_raw(raw_ref) {
  return gatewayRequest("/gateway/raw/lookup", { raw_ref });
}

export async function zeta_before_user_message(session_id, user_message, recent_context) {
  const raw = await zeta_save_raw_conversation(session_id, [{
    speaker: "user",
    content: user_message,
  }]);
  const injection_text = await zeta_recall_memories(user_message, recent_context || "");
  return {
    raw_refs: raw.raw_refs || [],
    injection_text,
  };
}

export async function zeta_after_assistant_message(session_id, zeta_reply, memory_entries) {
  const raw = await zeta_save_raw_conversation(session_id, [{
    speaker: "zeta",
    content: zeta_reply,
  }]);
  const saved_memories = [];
  for (const entry of memory_entries || []) {
    saved_memories.push(await zeta_write_memory(entry));
  }
  return {
    raw_refs: raw.raw_refs || [],
    saved_memories,
  };
}
