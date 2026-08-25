import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const mobileRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(mobileRoot, "..");
const appRoot = path.join(mobileRoot, "android", "app");
const javaRoot = path.join(
  appRoot,
  "src",
  "main",
  "java",
  "io",
  "github",
  "jdbjdncncmax",
  "ombrebrain"
);

test("Android reads Health Connect and the frontend attaches compact data", async () => {
  const reader = await readFile(path.join(javaRoot, "HealthSnapshotReader.java"), "utf8");
  const plugin = await readFile(path.join(javaRoot, "CompanionNativePlugin.java"), "utf8");
  const gadgetbridge = await readFile(path.join(javaRoot, "GadgetbridgeSyncRequester.java"), "utf8");
  const manifest = await readFile(path.join(appRoot, "src", "main", "AndroidManifest.xml"), "utf8");
  const frontend = await readFile(path.join(repositoryRoot, "companion_frontend", "app.js"), "utf8");

  assert.match(reader, /HealthConnectManager/);
  assert.match(reader, /AggregateRecordsRequest/);
  assert.match(reader, /atStartOfDay\(ZoneId\.systemDefault\(\)\)/);
  assert.match(reader, /latest_exact_sample/);
  assert.match(reader, /sleep_stages_excluding_awake/);
  assert.match(reader, /setAscending\(false\)/);
  assert.match(reader, /HeartRateRecord/);
  assert.match(reader, /SleepSessionRecord/);
  assert.match(plugin, /readHealthSnapshot/);
  assert.match(plugin, /requestHealthPermissions/);
  assert.match(plugin, /GadgetbridgeSyncRequester\.request/);
  assert.match(gadgetbridge, /command\.ACTIVITY_SYNC/);
  assert.match(gadgetbridge, /0x00000081/);
  assert.match(manifest, /android\.permission\.health\.READ_STEPS/);
  assert.match(manifest, /android\.permission\.health\.READ_HEART_RATE/);
  assert.match(manifest, /android\.permission\.health\.READ_SLEEP/);
  assert.match(manifest, /android\.intent\.action\.VIEW_PERMISSION_USAGE/);
  assert.match(frontend, /context:\s*\{/);
  assert.match(frontend, /health \? \{ health \}/);
});
