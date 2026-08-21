import assert from "node:assert/strict";
import test from "node:test";

import { createStreamUpdateCoordinator } from "../../companion_frontend/stream_updates.js";

function fakeTimers() {
  let nextId = 1;
  const callbacks = new Map();
  return {
    setTimer(callback) {
      const id = nextId++;
      callbacks.set(id, callback);
      return id;
    },
    clearTimer(id) {
      callbacks.delete(id);
    },
    runAll() {
      const pending = [...callbacks.values()];
      callbacks.clear();
      for (const callback of pending) callback();
    },
    count() {
      return callbacks.size;
    }
  };
}

test("many stream deltas collapse into one render and one checkpoint", () => {
  const timers = fakeTimers();
  const rendered = [];
  const persisted = [];
  const updates = createStreamUpdateCoordinator({
    onRender: (value) => rendered.push(value.content),
    onPersist: (value) => persisted.push(value.content),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer
  });

  const message = { content: "" };
  for (let index = 0; index < 10000; index += 1) {
    message.content += "字";
    updates.schedule(message);
  }

  assert.equal(timers.count(), 2);
  assert.deepEqual(rendered, []);
  timers.runAll();
  assert.deepEqual(rendered, ["字".repeat(10000)]);
  assert.deepEqual(persisted, ["字".repeat(10000)]);
});

test("flush cancels pending work and writes the final value once", () => {
  const timers = fakeTimers();
  const rendered = [];
  const persisted = [];
  const updates = createStreamUpdateCoordinator({
    onRender: (value) => rendered.push(value.content),
    onPersist: (value) => persisted.push(value.content),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer
  });

  const message = { content: "未完成" };
  updates.schedule(message);
  message.content = "完整回答";
  updates.flush(message);
  timers.runAll();

  assert.deepEqual(rendered, ["完整回答"]);
  assert.deepEqual(persisted, ["完整回答"]);
});
