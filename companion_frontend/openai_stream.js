export async function readOpenAiStream(stream, onDelta) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch (error) {
      throw new Error(streamReadErrorMessage(error));
    }
    const { value, done } = chunk;
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (dispatchOpenAiBlock(block, onDelta)) {
        try {
          await reader.cancel();
        } catch {
          // [DONE] 已明确表示回复结束，底层连接的关闭错误不应覆盖完整回复。
        }
        return;
      }
      boundary = buffer.indexOf("\n\n");
    }
  }

  if (buffer.trim()) {
    dispatchOpenAiBlock(buffer, onDelta);
  }
}

export function dispatchOpenAiBlock(block, onDelta) {
  const lines = block.split(/\r?\n/);
  const dataLines = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  for (const rawData of dataLines) {
    if (!rawData) {
      continue;
    }
    if (rawData === "[DONE]") {
      return true;
    }

    let parsed;
    try {
      parsed = JSON.parse(rawData);
    } catch {
      throw new Error(`流式响应格式异常：${rawData.slice(0, 160)}`);
    }
    if (parsed.error) {
      throw new Error(parsed.error.message || "模型请求失败。");
    }

    const warning = parsed.ombre_stream_warning;
    if (warning && typeof warning === "object") {
      onDelta({
        content: "",
        reasoning: "",
        model: typeof parsed.model === "string" ? parsed.model.trim() : "",
        usage: null,
        streamWarning: {
          message: String(warning.message || "连接中断，这条回复可能没有写完。"),
          streamId: String(warning.stream_id || parsed.id || "")
        }
      });
      continue;
    }

    const streamControl = parsed.ombre_stream_control;
    if (streamControl && typeof streamControl === "object") {
      onDelta({
        content: "",
        reasoning: "",
        model: typeof parsed.model === "string" ? parsed.model.trim() : "",
        usage: null,
        streamControl: {
          resetContent: Boolean(streamControl.reset_content),
          resetReasoning: Boolean(streamControl.reset_reasoning)
        }
      });
      continue;
    }

    const toolStatus = parsed.ombre_tool_status;
    if (toolStatus && Array.isArray(toolStatus.calls)) {
      onDelta({
        content: "",
        reasoning: "",
        model: typeof parsed.model === "string" ? parsed.model.trim() : "",
        usage: null,
        toolStatus: {
          calls: toolStatus.calls
            .filter((call) => call && typeof call === "object")
            .map((call) => ({
              id: String(call.id || ""),
              server: String(call.server || ""),
              tool: String(call.tool || ""),
              phase: call.phase === "completed" ? "completed" : "running",
              ...(call.phase === "completed" ? { ok: Boolean(call.ok) } : {})
            }))
        }
      });
      continue;
    }

    const choice = parsed.choices?.[0] || {};
    const delta = choice.delta || choice.message || {};
    const content = typeof delta.content === "string" ? delta.content : "";
    const reasoning = firstReasoningText(
      delta.reasoning,
      delta.reasoning_content,
      delta.reasoning_details,
      choice.reasoning,
      choice.reasoning_content,
      choice.reasoning_details
    );
    const model = typeof parsed.model === "string" ? parsed.model.trim() : "";
    const usage = normalizeTokenUsage(parsed.usage);
    if (content || reasoning || model || usage) {
      onDelta({ content, reasoning, model, usage });
    }
  }
  return false;
}

export function normalizeTokenUsage(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const inputTokens = tokenCount(value.inputTokens, value.prompt_tokens, value.input_tokens);
  const outputTokens = tokenCount(value.outputTokens, value.completion_tokens, value.output_tokens);
  const totalTokens = tokenCount(
    value.totalTokens,
    value.total_tokens,
    inputTokens !== null || outputTokens !== null
      ? (inputTokens || 0) + (outputTokens || 0)
      : null
  );
  const cachedTokens = tokenCount(
    value.cachedTokens,
    value.prompt_tokens_details?.cached_tokens,
    value.input_tokens_details?.cached_tokens,
    value.cache_read_input_tokens,
    value.cached_tokens
  );
  if (inputTokens === null && outputTokens === null && totalTokens === null && cachedTokens === null) {
    return null;
  }
  return { inputTokens, outputTokens, totalTokens, cachedTokens };
}

function tokenCount(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number) && number >= 0) {
      return Math.round(number);
    }
  }
  return null;
}

function firstReasoningText(...values) {
  for (const value of values) {
    const text = reasoningText(value);
    if (text) {
      return text;
    }
  }
  return "";
}

function reasoningText(value) {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(reasoningText).join("");
  }
  if (!value || typeof value !== "object") {
    return "";
  }
  for (const key of ["text", "content", "reasoning", "summary"]) {
    const text = reasoningText(value[key]);
    if (text) {
      return text;
    }
  }
  return "";
}

function streamReadErrorMessage(error) {
  const detail = error instanceof Error ? error.message : String(error || "");
  return [
    "流式响应中断。",
    "上游模型可能已经完成请求，但浏览器/WebView 读取网关返回流时失败。",
    "如果 OpenRouter 有请求日志，优先检查网关返回头和 SSE 转发。",
    detail ? `底层错误：${detail}` : ""
  ].filter(Boolean).join("\n");
}
