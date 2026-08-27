import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../../companion_frontend/", import.meta.url);

test("settings categories include calls, both prompts, and message font controls", async () => {
  const [html, app, theme, grouping] = await Promise.all([
    readFile(new URL("index.html", frontendRoot), "utf8"),
    readFile(new URL("app.js", frontendRoot), "utf8"),
    readFile(new URL("claude-theme.css", frontendRoot), "utf8"),
    readFile(new URL("assets/claude-ui/claude-ui.js", frontendRoot), "utf8")
  ]);

  assert.match(html, /id="assistantMessageFontSize"[^>]+min="12"[^>]+max="24"/);
  assert.match(html, /id="userMessageFontSize"[^>]+min="12"[^>]+max="24"/);
  assert.match(app, /assistantMessageFontSize:\s*normalizeMessageFontSize/);
  assert.match(app, /userMessageFontSize:\s*normalizeMessageFontSize/);
  assert.match(theme, /\.message\.assistant \.message-content\s*\{[\s\S]*?--assistant-message-font-size/);
  assert.match(theme, /\.message\.user \.message-content\s*\{[\s\S]*?--user-message-font-size/);
  assert.match(grouping, /"#incomingCallSettingsTitle"/);
  assert.match(grouping, /"#systemPromptFileName"/);
  assert.match(html, /id="proactivePrompt"[^>]+maxlength="500"/);
  assert.match(app, /\/api\/proactive-prompt/);
  assert.match(grouping, /"#proactivePrompt"/);
  assert.match(grouping, /"#emotionPromptFileName"/);
  assert.match(grouping, /"#assistantMessageFontSize"/);
  assert.match(grouping, /"#userMessageFontSize"/);
  assert.match(html, /id="messageHeatmapTitle"/);
  assert.match(html, /id="messageHeatmap"/);
  assert.match(app, /createMessageActivityStore/);
  assert.match(grouping, /"#messageHeatmapTitle"[\s\S]*?"#importChatMode"/);
  assert.match(theme, /\.composer-menu\s*\{[\s\S]*?background:\s*#f4f9f7/);
});
