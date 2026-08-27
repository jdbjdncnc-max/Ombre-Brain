import assert from "node:assert/strict";
import test from "node:test";

import {
  createMessageActivityStore,
  messageActivityDate
} from "../../companion_frontend/message_activity.js";

test("message activity keeps historical counts after the visible chat is cleared", async () => {
  const store = await createMessageActivityStore({ indexedDb: createFakeIndexedDb() });
  await store.record([
    { id: "u1", role: "user", createdAt: "2026-08-27T01:00:00.000Z", timezone: "Asia/Taipei" },
    { id: "a1", role: "assistant", createdAt: "2026-08-27T01:00:01.000Z", timezone: "Asia/Taipei" },
    { id: "s1", role: "summary", createdAt: "2026-08-27T01:00:02.000Z" },
    { id: "a2", role: "assistant", createdAt: "2026-08-27T01:00:03.000Z", pending: true }
  ]);
  await store.record([]);

  const summary = await store.summary();
  assert.equal(summary.durable, true);
  assert.deepEqual(summary.days["2026-08-27"], { user: 1, assistant: 1, total: 2 });
  assert.equal(summary.total, 2);
});

test("message activity assigns a message to its own timezone day", () => {
  assert.equal(messageActivityDate({
    createdAt: "2026-08-26T16:30:00.000Z",
    timezone: "Asia/Taipei"
  }), "2026-08-27");
});

function createFakeIndexedDb() {
  const records = new Map();
  let created = false;
  const database = {
    objectStoreNames: { contains: () => created },
    createObjectStore() { created = true; },
    transaction() {
      const transaction = {
        objectStore() {
          return {
            getAllKeys() {
              const request = {};
              queueMicrotask(() => {
                request.result = [...records.keys()];
                request.onsuccess?.();
              });
              return request;
            },
            getAll() {
              const request = {};
              queueMicrotask(() => {
                request.result = [...records.values()].map((value) => structuredClone(value));
                request.onsuccess?.();
              });
              return request;
            },
            put(value) {
              records.set(value.id, structuredClone(value));
            }
          };
        }
      };
      setTimeout(() => transaction.oncomplete?.(), 0);
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
