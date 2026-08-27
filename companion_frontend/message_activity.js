const DATABASE_NAME = "entangle-message-activity";
const DATABASE_VERSION = 1;
const STORE_NAME = "messages";

export async function createMessageActivityStore({
  indexedDb = globalThis.indexedDB
} = {}) {
  let database = null;
  let openError = null;
  const knownIds = new Set();

  try {
    database = indexedDb ? await openDatabase(indexedDb) : null;
    if (database) {
      for (const id of await readAllKeys(database)) {
        knownIds.add(String(id));
      }
    }
  } catch (error) {
    openError = error;
  }

  async function record(messages) {
    if (!database || !Array.isArray(messages)) {
      return { added: 0, durable: Boolean(database), error: openError };
    }
    const entries = messages
      .map(activityEntry)
      .filter((entry) => entry && !knownIds.has(entry.id));
    if (!entries.length) {
      return { added: 0, durable: true };
    }
    await writeEntries(database, entries);
    entries.forEach((entry) => knownIds.add(entry.id));
    return { added: entries.length, durable: true };
  }

  async function summary() {
    if (!database) {
      return { durable: false, days: {}, total: 0, error: openError };
    }
    const days = {};
    const entries = await readAll(database);
    for (const entry of entries) {
      const date = String(entry?.date || "");
      const role = String(entry?.role || "");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !["user", "assistant"].includes(role)) {
        continue;
      }
      const item = days[date] || { user: 0, assistant: 0, total: 0 };
      item[role] += 1;
      item.total += 1;
      days[date] = item;
    }
    return {
      durable: true,
      days,
      total: Object.values(days).reduce((sum, item) => sum + item.total, 0)
    };
  }

  return { record, summary };
}

export function messageActivityDate(message) {
  const createdAt = String(message?.createdAt || "").trim();
  const timestamp = new Date(createdAt);
  if (!createdAt || Number.isNaN(timestamp.getTime())) {
    return "";
  }
  const timezone = String(message?.timezone || "").trim();
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(timestamp);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  } catch {
    const year = timestamp.getFullYear();
    const month = String(timestamp.getMonth() + 1).padStart(2, "0");
    const day = String(timestamp.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }
}

function activityEntry(message) {
  const id = String(message?.id || "").trim();
  const role = String(message?.role || "");
  const date = messageActivityDate(message);
  if (!id || !date || !["user", "assistant"].includes(role) || message?.pending) {
    return null;
  }
  return {
    id,
    role,
    date,
    createdAt: String(message.createdAt),
    timezone: String(message.timezone || "")
  };
}

function openDatabase(indexedDb) {
  return new Promise((resolve, reject) => {
    const request = indexedDb.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("无法打开消息统计数据库。"));
    request.onblocked = () => reject(new Error("消息统计数据库正在被另一个页面占用。"));
  });
}

function readAllKeys(database) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).getAllKeys();
    request.onsuccess = () => resolve(Array.isArray(request.result) ? request.result : []);
    request.onerror = () => reject(request.error || new Error("无法读取消息统计索引。"));
  });
}

function readAll(database) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).getAll();
    request.onsuccess = () => resolve(Array.isArray(request.result) ? request.result : []);
    request.onerror = () => reject(request.error || new Error("无法读取消息统计。"));
  });
}

function writeEntries(database, entries) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    entries.forEach((entry) => store.put(entry));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("无法保存消息统计。"));
    transaction.onabort = () => reject(transaction.error || new Error("保存消息统计时数据库中止。"));
  });
}
