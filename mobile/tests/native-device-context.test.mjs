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

test("Android reads sensor location and today's UsageStats without IP geolocation", async () => {
  const reader = await readFile(path.join(javaRoot, "DeviceContextReader.java"), "utf8");
  const plugin = await readFile(path.join(javaRoot, "CompanionNativePlugin.java"), "utf8");
  const manifest = await readFile(path.join(appRoot, "src", "main", "AndroidManifest.xml"), "utf8");
  const frontend = await readFile(path.join(repositoryRoot, "companion_frontend", "app.js"), "utf8");

  assert.match(reader, /LocationManager/);
  assert.match(reader, /getCurrentLocation/);
  assert.match(reader, /Geocoder/);
  assert.match(reader, /queryEvents/);
  assert.match(reader, /MAX_USAGE_ENTRIES = 3/);
  assert.match(reader, /latest_external_before_ombre/);
  assert.match(reader, /currentScreenApp/);
  assert.match(reader, /packageinstaller/);
  assert.match(reader, /securitycenter/);
  assert.doesNotMatch(reader, /https?:\/\//);
  assert.match(plugin, /readDeviceContext/);
  assert.match(plugin, /openUsageAccessSettings/);
  assert.match(manifest, /android\.permission\.ACCESS_FINE_LOCATION/);
  assert.match(manifest, /android\.permission\.PACKAGE_USAGE_STATS/);
  assert.doesNotMatch(manifest, /ACCESS_BACKGROUND_LOCATION/);
  assert.match(frontend, /device \? \{ device \}/);
});
