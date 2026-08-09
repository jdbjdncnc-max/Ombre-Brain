import assert from "node:assert/strict";
import test from "node:test";

import {
  dispatchOpenAiBlock,
  readOpenAiStream
} from "../../companion_frontend/openai_stream.js";

const encoder = new TextEncoder();

function chunk(content, finishReason = null) {
  return `data: ${JSON.stringify({
    id: "chatcmpl-test",
    model: "test-model",
    choices: [{ index: 0, delta: content ? { content } : {}, finish_reason: finishReason }]
  })}\n\n`;
}

test("[DONE] completes the reply without waiting for a broken HTTP close", async () => {
  let cancelled = false;
  let sent = false;
  const stream = new ReadableStream({
    pull(controller) {
      if (!sent) {
        sent = true;
        controller.enqueue(encoder.encode(`${chunk("完整回答", "stop")}data: [DONE]\n\n`));
        return;
      }
      controller.error(new Error("broken framing after DONE"));
    },
    cancel() {
      cancelled = true;
    }
  }, { highWaterMark: 0 });
  const deltas = [];

  await readOpenAiStream(stream, (delta) => deltas.push(delta));

  assert.equal(deltas.map((delta) => delta.content).join(""), "完整回答");
  assert.equal(cancelled, true);
});

test("a gateway interruption warning is delivered separately from assistant text", () => {
  const deltas = [];
  const warning = {
    id: "ombre-test123",
    model: "test-model",
    ombre_stream_warning: {
      message: "连接中断，这条回复可能没有写完。",
      stream_id: "ombre-test123"
    }
  };

  const done = dispatchOpenAiBlock(`data: ${JSON.stringify(warning)}`, (delta) => deltas.push(delta));

  assert.equal(done, false);
  assert.deepEqual(deltas[0].streamWarning, {
    message: "连接中断，这条回复可能没有写完。",
    streamId: "ombre-test123"
  });
  assert.equal(deltas[0].content, "");
});

test("protocol reset and tool status arrive as private UI events", () => {
  const deltas = [];
  const reset = {
    id: "ombre-test123",
    model: "test-model",
    ombre_stream_control: { reset_content: false, reset_reasoning: true }
  };
  const status = {
    id: "ombre-test123",
    model: "test-model",
    ombre_tool_status: {
      calls: [{
        id: "0:0:galatea-garden:get_self",
        server: "galatea-garden",
        tool: "get_self",
        phase: "completed",
        ok: true
      }]
    }
  };

  dispatchOpenAiBlock(`data: ${JSON.stringify(reset)}`, (delta) => deltas.push(delta));
  dispatchOpenAiBlock(`data: ${JSON.stringify(status)}`, (delta) => deltas.push(delta));

  assert.deepEqual(deltas[0].streamControl, {
    resetContent: false,
    resetReasoning: true
  });
  assert.deepEqual(deltas[1].toolStatus.calls[0], {
    id: "0:0:galatea-garden:get_self",
    server: "galatea-garden",
    tool: "get_self",
    phase: "completed",
    ok: true
  });
  assert.equal("arguments" in deltas[1].toolStatus.calls[0], false);
});
