import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../../companion_frontend/", import.meta.url);

test("todos support permanent validity, subtitles, and detailed content", async () => {
  const [html, app] = await Promise.all([
    readFile(new URL("index.html", frontendRoot), "utf8"),
    readFile(new URL("app.js", frontendRoot), "utf8")
  ]);

  assert.match(html, /id="todoValidity"/);
  assert.match(html, /value="permanent">永久/);
  assert.match(html, /id="todoTitle"[^>]+maxlength="120"/);
  assert.match(html, /id="todoDetails"[^>]+maxlength="500"/);
  assert.match(app, /validity === "permanent"/);
  assert.match(app, /持续有效，直到完成或删除/);
  assert.match(app, /details:\s*String\(item\.details \|\| item\.note/);
});
