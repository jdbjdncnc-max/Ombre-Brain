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
  const registration = await readFile(path.join(javaRoot, "FirebaseRegistration.java"), "utf8");
  const firebaseService = await readFile(path.join(javaRoot, "EntangleFirebaseMessagingService.java"), "utf8");
  const backgroundService = await readFile(path.join(javaRoot, "ProactiveBackgroundService.java"), "utf8");
  const bootReceiver = await readFile(path.join(javaRoot, "ProactiveBootReceiver.java"), "utf8");
  const activity = await readFile(path.join(javaRoot, "MainActivity.java"), "utf8");
  const manifest = await readFile(path.join(appRoot, "src", "main", "AndroidManifest.xml"), "utf8");
  const strings = await readFile(path.join(appRoot, "src", "main", "res", "values", "strings.xml"), "utf8");
  const gradle = await readFile(path.join(appRoot, "build.gradle"), "utf8");
  const frontend = await readFile(path.join(mobileRoot, "..", "companion_frontend", "app.js"), "utf8");

  assert.match(worker, /PeriodicWorkRequest/);
  assert.match(worker, /PERIODIC_MINUTES = 15L/);
  assert.doesNotMatch(worker, /setRequiredNetworkType/);
  assert.doesNotMatch(worker, /NetworkType\.CONNECTED/);
  assert.match(worker, /\/api\/solo\/outbox\?limit=50/);
  assert.match(worker, /\/api\/solo\/outbox\/ack/);
  assert.match(worker, /JSONObject newestItem = null/);
  assert.doesNotMatch(worker, /delivered\.removeAll\(ackIds\)/);
  assert.match(worker, /NotificationManagerCompat/);
  assert.match(worker, /notify\(id, 0, builder\.build\(\)\)/);
  assert.match(worker, /schedule\(Context context, boolean runNow\) \{\s*createNotificationChannel\(context\)/);
  assert.match(plugin, /configureProactiveNotifications/);
  assert.match(plugin, /EntangleFirebaseMessagingService\.syncRegistration/);
  assert.match(plugin, /saveChatExport/);
  assert.match(plugin, /MediaStore\.Downloads\.EXTERNAL_CONTENT_URI/);
  assert.match(plugin, /Environment\.DIRECTORY_DOWNLOADS/);
  assert.match(registration, /\/api\/notifications\/register/);
  assert.match(registration, /"fid"/);
  assert.doesNotMatch(registration, /storeToken[\s\S]*ProactiveNotificationWorker\.schedule/);
  assert.match(firebaseService, /onNewToken/);
  assert.match(firebaseService, /onMessageReceived/);
  assert.match(firebaseService, /ombre_proactive/);
  assert.match(firebaseService, /TOKEN_REQUEST_IN_FLIGHT/);
  assert.match(firebaseService, /GATEWAY_REGISTRATION_IN_FLIGHT/);
  assert.match(firebaseService, /registerStoredTokenBlocking/);
  assert.match(firebaseService, /Settings\.Secure\.ANDROID_ID/);
  assert.match(firebaseService, /markDeliveredIfNew/);
  assert.match(firebaseService, /FirebaseRegistration\.sync/);
  assert.match(firebaseService, /SERVICE_NOT_AVAILABLE/);
  assert.match(backgroundService, /startForeground/);
  assert.match(backgroundService, /POLL_INTERVAL_MS = 60_000L/);
  assert.match(backgroundService, /ProactiveNotificationWorker\.runOnce/);
  assert.match(backgroundService, /START_STICKY/);
  assert.match(backgroundService, /PowerManager\.PARTIAL_WAKE_LOCK/);
  assert.match(backgroundService, /wakeLock\.acquire\(POLL_INTERVAL_MS \* 3\)/);
  assert.match(worker, /VISIBILITY_PUBLIC/);
  assert.match(bootReceiver, /ACTION_BOOT_COMPLETED/);
  assert.match(bootReceiver, /ACTION_MY_PACKAGE_REPLACED/);
  assert.match(bootReceiver, /ProactiveBackgroundService\.start/);
  assert.match(plugin, /ProactiveBackgroundService\.start/);
  assert.match(plugin, /ProactiveBackgroundService\.stop/);
  assert.match(plugin, /ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS/);
  assert.match(plugin, /isIgnoringBatteryOptimizations/);
  assert.match(activity, /registerPlugin\(CompanionNativePlugin\.class\)/);
  assert.ok(
    activity.indexOf("registerPlugin(CompanionNativePlugin.class)") < activity.indexOf("super.onCreate(savedInstanceState)"),
    "Capacitor local plugins must be registered before BridgeActivity starts"
  );
  assert.match(manifest, /android\.permission\.POST_NOTIFICATIONS/);
  assert.match(manifest, /EntangleFirebaseMessagingService/);
  assert.match(manifest, /ProactiveBackgroundService/);
  assert.match(manifest, /FOREGROUND_SERVICE_SPECIAL_USE/);
  assert.match(manifest, /PROPERTY_SPECIAL_USE_FGS_SUBTYPE/);
  assert.match(manifest, /RECEIVE_BOOT_COMPLETED/);
  assert.match(manifest, /ProactiveBootReceiver/);
  assert.match(manifest, /REQUEST_IGNORE_BATTERY_OPTIMIZATIONS/);
  assert.doesNotMatch(manifest, /firebase_messaging_installation_id_enabled/);
  assert.match(strings, /<string name="app_name">Entangle<\/string>/);
  assert.match(gradle, /androidx\.work:work-runtime:2\.11\.2/);
  assert.match(gradle, /com\.google\.firebase:firebase-messaging/);
  assert.match(frontend, /\/api\/solo\/messages/);
  assert.match(frontend, /proactiveId/);
  assert.match(frontend, /usage: normalizeTokenUsage\(item\.usage\)/);
  assert.match(frontend, /normalizePromptCacheContext\(item\?\.promptCacheContext\)/);
  assert.match(frontend, /recall: \{ available: true, promptCacheContext \}/);
  assert.match(frontend, /if \(!await saveMessages\(\)\)/);
  assert.ok(
    frontend.indexOf("if (!await saveMessages())") < frontend.indexOf("state.proactiveMessageCursor = latestId"),
    "the chat database must persist proactive replies before the cursor advances"
  );
});
