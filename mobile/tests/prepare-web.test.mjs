import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const mobileRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = path.join(mobileRoot, "www");

test("prepared frontend contains the web app and platform adapters", async () => {
  const index = await readFile(path.join(outputDir, "index.html"), "utf8");
  const app = await readFile(path.join(outputDir, "app.js"), "utf8");
  const platform = await readFile(path.join(outputDir, "platform.js"), "utf8");
  const androidPlatform = await readFile(path.join(outputDir, "platform.android.js"), "utf8");
  const chatTransfer = await readFile(path.join(outputDir, "chat_transfer.js"), "utf8");
  const chatStorage = await readFile(path.join(outputDir, "chat_storage.js"), "utf8");
  const solo = await readFile(path.join(outputDir, "solo.js"), "utf8");
  assert.match(index, /<head(?:\s|>)/i);
  assert.match(index, /id="soloEmotionChart"/);
  assert.match(index, /id="soloActivityTrack"/);
  assert.match(index, /id="mcpManager"/);
  assert.match(index, /id="importChatButton"/);
  assert.match(index, /id="importChatContinueSession"/);
  assert.match(index, /href="styles\.css\?v=/);
  assert.match(index, /src="app\.js\?v=/);
  assert.doesNotMatch(index, /(?:href|src)="\/frontend\//);
  assert.match(solo, /requestTimeline/);
  assert.match(solo, /conversation: "因为我和她"/);
  assert.match(app, /ombre_context_kind: "conversation_summary"/);
  assert.match(app, /网关已保存 · 本轮已注入/);
  assert.match(app, /event\.key === "Enter" && event\.ctrlKey/);
  assert.match(app, /\/api\/emotion-appraisal/);
  assert.match(app, /skip_emotion_appraisal: true/);
  assert.match(app, /syncNativeProactiveNotifications/);
  assert.match(app, /persistImportedChat/);
  assert.match(app, /createChatHistoryStore/);
  assert.match(chatStorage, /entangle-chat-history/);
  assert.match(app, /url\("background\.svg"\)/);
  assert.doesNotMatch(app, /url\("\/frontend\//);
  assert.match(platform, /Capacitor\?\.Plugins\?\.CompanionNative/);
  assert.match(androidPlatform, /configureProactiveNotifications/);
  assert.match(chatTransfer, /ombre-companion-chat-export/);
  assert.doesNotMatch(app, /\/api\/solo\/outbox\?limit=/);
  assert.match(app, /\/api\/solo\/messages/);
  assert.match(app, /\/api\/solo\/outbox\/ack/);
  assert.doesNotMatch(app, /\[Ombre 消息信息\]/);
  assert.equal((await stat(path.join(outputDir, "app.js"))).isFile(), true);
  assert.equal((await stat(path.join(outputDir, "solo.js"))).isFile(), true);
  assert.equal((await stat(path.join(outputDir, "platform.js"))).isFile(), true);
  assert.equal((await stat(path.join(outputDir, "platform.browser.js"))).isFile(), true);
  assert.equal((await stat(path.join(outputDir, "platform.android.js"))).isFile(), true);
  assert.equal((await stat(path.join(outputDir, "chat_transfer.js"))).isFile(), true);
  assert.equal((await stat(path.join(outputDir, "chat_storage.js"))).isFile(), true);
});

test("fallback bundle publishes a versioned native bridge contract", async () => {
  const metadata = JSON.parse(
    await readFile(path.join(outputDir, "mobile-build.json"), "utf8")
  );

  assert.deepEqual(metadata, {
    schema: "ombre.frontend-bundle.v1",
    source: "companion_frontend",
    purpose: "bundled-offline-fallback",
    minimumNativeBridgeVersion: 1
  });
});

test("production config uses bundled assets instead of a remote server URL", async () => {
  const config = JSON.parse(
    await readFile(path.join(mobileRoot, "capacitor.config.json"), "utf8")
  );

  assert.equal(config.webDir, "www");
  assert.equal(config.appId, "io.github.jdbjdncncmax.ombrebrain");
  assert.equal(config.appName, "Entangle");
  assert.equal(config.loggingBehavior, "none");
  assert.equal(config.server?.url, undefined);
});
