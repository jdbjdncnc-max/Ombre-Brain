import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL(
  "../android/app/src/main/java/io/github/jdbjdncncmax/ombrebrain/MainActivity.java",
  import.meta.url
), "utf8");

test("Android handles a dead WebView renderer and recreates the view", () => {
  assert.match(source, /onRenderProcessGone/);
  assert.match(source, /removeView\(webView\)/);
  assert.match(source, /webView\.destroy\(\)/);
  assert.match(source, /recreate\(\)/);
  assert.match(source, /return true;/);
});
