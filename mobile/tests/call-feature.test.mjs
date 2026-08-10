import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const mobileRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(mobileRoot, "..");
const frontendRoot = path.join(repositoryRoot, "companion_frontend");
const javaRoot = path.join(
  mobileRoot,
  "android",
  "app",
  "src",
  "main",
  "java",
  "io",
  "github",
  "jdbjdncncmax",
  "ombrebrain"
);

test("call card opens a native Android call view with captions and controls", async () => {
  const html = await readFile(path.join(frontendRoot, "index.html"), "utf8");
  const app = await readFile(path.join(frontendRoot, "app.js"), "utf8");
  const controller = await readFile(path.join(frontendRoot, "call.js"), "utf8");

  assert.match(html, /id="callView"/);
  assert.match(html, /id="callTranscriptList"/);
  assert.match(html, /id="callMuteButton"/);
  assert.match(html, /id="callHangupButton"/);
  assert.match(html, /id="callSpeakerButton"/);
  assert.match(app, /callController\.start\(\)/);
  assert.match(app, /buildCallContextMessages/);
  assert.match(controller, /requestPermission/);
  assert.match(controller, /type === "transcript"/);
});

test("Android call service owns microphone, VAD, websocket, audio routing and immediate marker handling", async () => {
  const service = await readFile(path.join(javaRoot, "CallForegroundService.java"), "utf8");
  const engine = await readFile(path.join(javaRoot, "CallAudioEngine.java"), "utf8");
  const socket = await readFile(path.join(javaRoot, "CallSocketClient.java"), "utf8");
  const plugin = await readFile(path.join(javaRoot, "CallNativePlugin.java"), "utf8");
  const manifest = await readFile(
    path.join(mobileRoot, "android", "app", "src", "main", "AndroidManifest.xml"),
    "utf8"
  );

  assert.match(service, /FOREGROUND_SERVICE_TYPE_MICROPHONE/);
  assert.match(service, /"hangup"\.equals\(message\.optString\("name"/);
  assert.match(service, /audio\.afterPlayback\(\(\) -> hangup\("assistant"\)\)/);
  assert.match(engine, /VOICE_COMMUNICATION/);
  assert.match(engine, /AcousticEchoCanceler/);
  assert.match(engine, /onBargeIn/);
  assert.match(socket, /\/api\/call\/ws/);
  assert.match(plugin, /name = "CallNative"/);
  assert.match(manifest, /android\.permission\.RECORD_AUDIO/);
  assert.match(manifest, /android:foregroundServiceType="microphone"/);
});
