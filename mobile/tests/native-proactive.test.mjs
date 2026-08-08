import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const mobileRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
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

test("Android uses a native background worker for proactive notifications", async () => {
  const worker = await readFile(path.join(javaRoot, "ProactiveNotificationWorker.java"), "utf8");
  const plugin = await readFile(path.join(javaRoot, "CompanionNativePlugin.java"), "utf8");
  const activity = await readFile(path.join(javaRoot, "MainActivity.java"), "utf8");
  const manifest = await readFile(path.join(appRoot, "src", "main", "AndroidManifest.xml"), "utf8");
  const gradle = await readFile(path.join(appRoot, "build.gradle"), "utf8");

  assert.match(worker, /PeriodicWorkRequest/);
  assert.match(worker, /PERIODIC_MINUTES = 15L/);
  assert.match(worker, /\/api\/solo\/outbox\?limit=10/);
  assert.match(worker, /\/api\/solo\/outbox\/ack/);
  assert.match(worker, /NotificationManagerCompat/);
  assert.match(plugin, /configureProactiveNotifications/);
  assert.match(activity, /registerPlugin\(CompanionNativePlugin\.class\)/);
  assert.ok(
    activity.indexOf("registerPlugin(CompanionNativePlugin.class)") < activity.indexOf("super.onCreate(savedInstanceState)"),
    "Capacitor local plugins must be registered before BridgeActivity starts"
  );
  assert.match(manifest, /android\.permission\.POST_NOTIFICATIONS/);
  assert.match(gradle, /androidx\.work:work-runtime:2\.11\.2/);
});
