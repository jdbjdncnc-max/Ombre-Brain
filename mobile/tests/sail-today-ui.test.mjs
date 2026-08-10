import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../../companion_frontend/", import.meta.url);

test("home keeps four simple feature cards and moves Sail details off the home page", async () => {
  const html = await readFile(new URL("index.html", frontendRoot), "utf8");
  const homeStart = html.indexOf('<section id="homeView"');
  const sailStart = html.indexOf('<section id="sailView"');

  assert.ok(homeStart >= 0);
  assert.ok(sailStart > homeStart);

  const home = html.slice(homeStart, sailStart);
  assert.match(home, /data-home-feature="call"/);
  assert.match(home, /data-home-feature="music"/);
  assert.match(home, /data-home-feature="sail"/);
  assert.match(home, /data-home-feature="zeta"/);
  assert.match(home, /Sail 的今天/);
  assert.doesNotMatch(home, /id="healthHeartRate"/);
  assert.doesNotMatch(home, /id="healthSteps"/);
  assert.doesNotMatch(home, /id="sailUsageList"/);

  const sail = html.slice(sailStart);
  assert.match(sail, /id="sailLocationName"/);
  assert.match(sail, /id="sailCurrentApp"/);
  assert.match(sail, /id="healthHeartRate"/);
  assert.match(sail, /id="healthSteps"/);
  assert.match(sail, /id="sailUsageList"/);
});

test("Sail card opens a dedicated detail view", async () => {
  const app = await readFile(new URL("app.js", frontendRoot), "utf8");

  assert.match(app, /function openSailView\(\)/);
  assert.match(app, /feature === "sail"/);
  assert.match(app, /setActiveTab\("sail"\)/);
  assert.match(app, /els\.sailView\.classList\.toggle\("view-active", tab === "sail"\)/);
  assert.match(app, /history\.pushState\([^;]+companionView: "sail"/s);
});
