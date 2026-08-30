import assert from "node:assert/strict";
import test from "node:test";

import {
  chooseEpisodeCompression,
  conversationDayKey,
  estimateConversationTokens
} from "../../companion_frontend/conversation_compaction.js";

test("conversation day changes at 04:00 local time", () => {
  assert.equal(conversationDayKey("2026-08-29T03:59:00+08:00", "Asia/Taipei"), "2026-08-28");
  assert.equal(conversationDayKey("2026-08-29T04:00:00+08:00", "Asia/Taipei"), "2026-08-29");
});

test("episode compression keeps a recent raw tail and cuts after an assistant reply", () => {
  const messages = Array.from({ length: 20 }, (_, index) => ({
    id: `m${index}`,
    role: index % 2 ? "assistant" : "user",
    content: "今天的谈话内容".repeat(180),
    createdAt: new Date(Date.UTC(2026, 7, 28, 1, index * 5)).toISOString()
  }));
  const result = chooseEpisodeCompression(messages, { keepTokens: 7000 });
  assert.ok(result);
  assert.ok(result.compressed.length >= 4);
  assert.equal(result.compressed.at(-1).role, "assistant");
  assert.ok(estimateConversationTokens(result.kept) >= 6000);
});

test("a goodnight exchange can close the chapter at the final assistant reply", () => {
  const messages = [
    { id: "m1", role: "user", content: "今天聊了很多", createdAt: "2026-08-29T22:00:00+08:00" },
    { id: "m2", role: "assistant", content: "嗯，我都记着呢。", createdAt: "2026-08-29T22:01:00+08:00" },
    { id: "m3", role: "user", content: "那我去睡啦，晚安", createdAt: "2026-08-29T23:00:00+08:00" },
    { id: "m4", role: "assistant", content: "晚安，做个好梦。", createdAt: "2026-08-29T23:01:00+08:00" }
  ];

  const result = chooseEpisodeCompression(messages, { keepTokens: 7000, minimumMessages: 4 });

  assert.equal(result.boundaryKind, "topic_closure");
  assert.equal(result.boundaryReason, "晚安或休息");
  assert.equal(result.compressed.length, 4);
  assert.equal(result.kept.length, 0);
});

test("a two-message goodnight exchange is still a meaningful ending", () => {
  const result = chooseEpisodeCompression([
    { role: "user", content: "我先睡啦，晚安", createdAt: "2026-08-29T23:00:00+08:00" },
    { role: "assistant", content: "晚安，明天见。", createdAt: "2026-08-29T23:01:00+08:00" }
  ], { keepTokens: 7000, minimumMessages: 4 });

  assert.equal(result.boundaryKind, "topic_closure");
  assert.equal(result.compressed.length, 2);
});
