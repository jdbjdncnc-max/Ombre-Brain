const DATABASE_NAME = "entangle-chat-history";
const DATABASE_VERSION = 1;
const STORE_NAME = "snapshots";
const ACTIVE_SNAPSHOT_KEY = "active";

export async function createChatHistoryStore({
  legacyStorage,
  legacyKey,
  indexedDb = globalThis.indexedDB,
  storageManager = globalThis.navigator?.storage
} = {}) {
  let database = null;
  let openError = null;
  try {
    database = indexedDb ? await openDatabase(indexedDb) : null;
  } catch (error) {
    openError = error;
  }

  let pendingMessages = null;
  let drainPromise = null;
  let lastSavedAt = "";

  async function load() {
    if (database) {
      const record = await readSnapshot(database).catch(() => null);
      if (Array.isArray(record?.messages)) {
        lastSavedAt = String(record.updatedAt || "");
        return { messages: record.messages, source: "indexeddb", migrated: false };
      }
    }

    const legacyMessages = legacyStorage?.getJson?.(legacyKey, []);
    const messages = Array.isArray(legacyMessages) ? legacyMessages : [];
    if (!database) {
      return { messages, source: "localstorage", migrated: false, error: openError };
    }

    try {
      await writeSnapshot(database, messages);
      legacyStorage?.remove?.(legacyKey);
      lastSavedAt = new Date().toISOString();
      return { messages, source: "indexeddb", migrated: true };
    } catch (error) {
      openError = error;
      return { messages, source: "localstorage", migrated: false, error };
    }
  }

  function save(messages) {
    if (!Array.isArray(messages)) {
      return Promise.reject(new TypeError("聊天记录必须是数组。"));
    }
    if (!database) {
      return Promise.reject(openError || new Error("当前环境无法打开聊天数据库。"));
    }

    pendingMessages = messages;
    if (!drainPromise) {
      drainPromise = drainSaves().finally(() => {
        drainPromise = null;
      });
    }
    return drainPromise;
  }

  async function drainSaves() {
    while (pendingMessages) {
      const messages = pendingMessages;
      pendingMessages = null;
      await writeSnapshot(database, messages);
      lastSavedAt = new Date().toISOString();
    }
    return { saved: true, savedAt: lastSavedAt };
  }

  async function verify(expectedMessages) {
    if (!database || !Array.isArray(expectedMessages)) return false;
    await flush();
    const record = await readSnapshot(database);
    const actual = Array.isArray(record?.messages) ? record.messages : null;
    return Boolean(actual)
      && actual.length === expectedMessages.length
      && messageBoundarySignature(actual) === messageBoundarySignature(expectedMessages);
  }

  async function flush() {
    if (drainPromise) await drainPromise;
  }

  async function estimate() {
    const value = await storageManager?.estimate?.().catch(() => ({})) || {};
    const persisted = await storageManager?.persisted?.().catch(() => false) || false;
    return {
      durable: Boolean(database),
      kind: database ? "indexeddb" : "localstorage",
      usage: Number(value.usage || 0),
      quota: Number(value.quota || 0),
      persisted,
      lastSavedAt,
      error: openError
    };
  }

  return { load, save, verify, flush, estimate };
}

function openDatabase(indexedDb) {
  return new Promise((resolve, reject) => {
    const request = indexedDb.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) database.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("无法打开聊天数据库。"));
    request.onblocked = () => reject(new Error("聊天数据库正在被另一个页面占用。"));
  });
}

function readSnapshot(database) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(ACTIVE_SNAPSHOT_KEY);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error || new Error("无法读取聊天记录。"));
    transaction.onabort = () => reject(transaction.error || new Error("读取聊天记录时数据库中止。"));
  });
}

function writeSnapshot(database, messages) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite", { durability: "strict" });
    transaction.objectStore(STORE_NAME).put({
      schema: "entangle.chat.snapshot.v1",
      updatedAt: new Date().toISOString(),
      messages
    }, ACTIVE_SNAPSHOT_KEY);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("无法保存聊天记录。"));
    transaction.onabort = () => reject(transaction.error || new Error("保存聊天记录时数据库中止。"));
  });
}

function messageBoundarySignature(messages) {
  const first = messages[0] || {};
  const last = messages.at(-1) || {};
  return [
    String(first.id || ""),
    String(first.createdAt || ""),
    String(last.id || ""),
    String(last.createdAt || ""),
    String(last.role || ""),
    String(last.content || "").length,
    String(last.reasoning || "").length,
    Boolean(last.pending)
  ].join("\u0000");
}
