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
