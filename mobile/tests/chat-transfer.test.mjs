import assert from "node:assert/strict";
import test from "node:test";

import {
  CHAT_EXPORT_FORMAT,
  CHAT_EXPORT_VERSION,
  mergeChatMessages,
  parseChatExport,
  prepareImportedMessages
} from "../../companion_frontend/chat_transfer.js";

function exportedPayload(overrides = {}) {
  return JSON.stringify({
    format: CHAT_EXPORT_FORMAT,
    version: CHAT_EXPORT_VERSION,
    exportedAt: "2026-08-09T12:00:00.000Z",
    sessionId: "49ed9ff5-c27e-4147-81bf-e29d2db71b75",
    participants: { user: "Sail", assistant: "Zeta", model: "test-model" },
    messages: [
      { id: "u1", role: "user", content: "你好", createdAt: "2026-08-09T12:00:00.000Z" },
      { id: "a1", role: "assistant", content: "你好呀", createdAt: "2026-08-09T12:00:01.000Z" }
    ],
    ...overrides
  });
}

test("chat export parser accepts the app export format", () => {
  const parsed = parseChatExport(exportedPayload());

  assert.equal(parsed.messages.length, 2);
  assert.equal(parsed.sessionId, "49ed9ff5-c27e-4147-81bf-e29d2db71b75");
  assert.deepEqual(parsed.participants, {
    user: "Sail",
    assistant: "Zeta",
    model: "test-model"
  });
});

test("chat export parser rejects unrelated and damaged files", () => {
  assert.throws(() => parseChatExport("not json"), /无法读取这个 JSON 文件/);
  assert.throws(
    () => parseChatExport(JSON.stringify({ format: "something-else", version: 1, messages: [] })),
    /文件格式不匹配/
  );
  assert.throws(
    () => parseChatExport(exportedPayload({ messages: [{ role: "system", content: "no" }] })),
    /类型异常/
  );
});

test("import preparation repairs duplicate ids and unfinished replies", () => {
  let generated = 0;
  const prepared = prepareImportedMessages([
    { id: "same", role: "user", content: "一", createdAt: "2026-08-09T12:00:00.000Z" },
    { id: "same", role: "assistant", content: "二", pending: true, createdAt: "2026-08-09T12:00:01.000Z" }
  ], {
    createId: () => `generated-${++generated}`,
    timezone: "Asia/Taipei"
  });

  assert.equal(prepared[0].id, "same");
  assert.equal(prepared[1].id, "generated-1");
  assert.equal(prepared[1].pending, false);
  assert.equal(prepared[1].streamInterrupted, true);
  assert.equal(prepared[0].timezone, "Asia/Taipei");
});

test("merge keeps current copies of duplicate messages and sorts the result", () => {
  const imported = [
    { id: "a", role: "user", content: "较早", createdAt: "2026-08-09T12:00:00.000Z" },
    { id: "b", role: "assistant", content: "旧内容", createdAt: "2026-08-09T12:00:01.000Z" }
  ];
  const current = [
    { id: "b", role: "assistant", content: "当前内容", createdAt: "2026-08-09T12:00:01.000Z" },
    { id: "c", role: "user", content: "较晚", createdAt: "2026-08-09T12:00:02.000Z" }
  ];

  const merged = mergeChatMessages(current, imported);
  assert.deepEqual(merged.map((message) => message.id), ["a", "b", "c"]);
  assert.equal(merged[1].content, "当前内容");
});
