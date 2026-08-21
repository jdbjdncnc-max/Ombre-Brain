import assert from "node:assert/strict";
import test from "node:test";

import { createChatHistoryStore } from "../../companion_frontend/chat_storage.js";

test("chat history migrates from localStorage to IndexedDB", async () => {
  const legacyMessages = [{ id: "u1", role: "user", content: "旧记录" }];
  let removedKey = "";
  const store = await createChatHistoryStore({
    legacyStorage: {
      getJson: () => legacyMessages,
      remove: (key) => { removedKey = key; }
    },
    legacyKey: "companion.messages.v1",
    indexedDb: createFakeIndexedDb(),
    storageManager: createStorageManager()
  });

  const loaded = await store.load();
  assert.equal(loaded.source, "indexeddb");
  assert.equal(loaded.migrated, true);
  assert.deepEqual(loaded.messages, legacyMessages);
  assert.equal(removedKey, "companion.messages.v1");

  const nextMessages = [...legacyMessages, { id: "a1", role: "assistant", content: "新记录" }];
  await store.save(nextMessages);
  assert.equal(await store.verify(nextMessages), true);
});

test("chat history loads the legacy snapshot when IndexedDB is unavailable", async () => {
  const legacyMessages = [{ id: "u1", role: "user", content: "保底记录" }];
  const store = await createChatHistoryStore({
    legacyStorage: { getJson: () => legacyMessages },
    legacyKey: "companion.messages.v1",
    indexedDb: null,
    storageManager: createStorageManager()
  });

  const loaded = await store.load();
  assert.equal(loaded.source, "localstorage");
  assert.deepEqual(loaded.messages, legacyMessages);
  await assert.rejects(store.save(legacyMessages), /无法打开聊天数据库|无法打开/);
});

function createStorageManager() {
  return {
    estimate: async () => ({ usage: 6 * 1024 * 1024, quota: 10 * 1024 ** 3 }),
    persisted: async () => false
  };
}

function createFakeIndexedDb() {
  const records = new Map();
  const database = {
    objectStoreNames: { contains: () => false },
    createObjectStore() {},
    transaction() {
      const transaction = {
        objectStore() {
          return {
            get(key) {
              const request = {};
              queueMicrotask(() => {
                request.result = records.get(key);
                request.onsuccess?.();
              });
              return request;
            },
            put(value, key) {
              records.set(key, structuredClone(value));
              queueMicrotask(() => transaction.oncomplete?.());
            }
          };
        }
      };
      return transaction;
    }
  };
  return {
    open() {
      const request = { result: database };
      queueMicrotask(() => {
        request.onupgradeneeded?.();
        request.onsuccess?.();
      });
      return request;
    }
  };
}
