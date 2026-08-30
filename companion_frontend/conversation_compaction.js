export const DEFAULT_SUMMARY_SOFT_TOKENS = 64_000;
export const DEFAULT_SUMMARY_HARD_TOKENS = 80_000;
export const DEFAULT_SUMMARY_KEEP_TOKENS = 20_000;
export const CONVERSATION_DAY_START_HOUR = 4;

export function conversationDayKey(value, timezone = "") {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "";
  const shifted = new Date(timestamp.getTime() - CONVERSATION_DAY_START_HOUR * 60 * 60 * 1000);
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(shifted);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  } catch {
    return [
      shifted.getFullYear(),
      String(shifted.getMonth() + 1).padStart(2, "0"),
      String(shifted.getDate()).padStart(2, "0")
    ].join("-");
  }
}

export function estimateConversationTokens(messages) {
  return (Array.isArray(messages) ? messages : [])
    .reduce((sum, message) => sum + estimateMessageTokens(message), 0);
}

export function chooseEpisodeCompression(messages, {
  keepTokens = DEFAULT_SUMMARY_KEEP_TOKENS,
  minimumMessages = 4
} = {}) {
  const source = (Array.isArray(messages) ? messages : [])
    .filter((message) => message?.role === "user" || message?.role === "assistant");
  if (source.length < 2) return null;

  let keptTokens = 0;
  let targetIndex = source.length;
  for (let index = source.length - 1; index >= 0; index -= 1) {
    keptTokens += estimateMessageTokens(source[index]);
    targetIndex = index;
    if (keptTokens >= keepTokens) break;
  }
  const boundary = nearestNaturalBoundary(source, targetIndex, Math.min(minimumMessages, source.length));
  if (
    !boundary
    || (source.length < minimumMessages && boundary.kind !== "topic_closure")
    || (targetIndex < minimumMessages && boundary.kind !== "topic_closure")
  ) return null;

  let cutoff = boundary.kind === "topic_closure"
    ? boundary.index
    : Math.max(minimumMessages, Math.min(source.length, boundary.index));
  if (boundary.kind !== "topic_closure") {
    cutoff = Math.min(source.length - 2, cutoff);
  }
  const compressed = source.slice(0, cutoff);
  const kept = source.slice(cutoff);
  if (
    !compressed.some((message) => message.role === "user")
    || (!kept.length && boundary.kind !== "topic_closure")
  ) return null;
  return {
    compressed,
    kept,
    cutoff,
    compressedTokens: estimateConversationTokens(compressed),
    keptTokens: estimateConversationTokens(kept),
    boundaryKind: boundary.kind,
    boundaryReason: boundary.reason
  };
}

function nearestNaturalBoundary(messages, targetIndex, minimumMessages) {
  const candidates = [];
  for (let index = minimumMessages; index <= messages.length; index += 1) {
    const previous = messages[index - 1];
    const next = messages[index];
    if (previous?.role !== "assistant") continue;
    const previousTime = new Date(previous.createdAt || "").getTime();
    const nextTime = new Date(next?.createdAt || "").getTime();
    const gapMinutes = Number.isFinite(previousTime) && Number.isFinite(nextTime)
      ? Math.max(0, (nextTime - previousTime) / 60_000)
      : 0;
    const closure = topicClosureReason(messages.slice(Math.max(0, index - 2), index));
    candidates.push({
      index,
      distance: Math.abs(index - targetIndex),
      gapMinutes,
      kind: closure ? "topic_closure" : gapMinutes >= 30 ? "time_pause" : "assistant_turn",
      reason: closure || (gapMinutes >= 30 ? `停顿约 ${Math.round(gapMinutes)} 分钟` : "完整回复结束")
    });
  }
  if (!candidates.length) return null;
  const nearbyClosure = candidates
    .filter((item) => item.kind === "topic_closure" && (item.distance <= 12 || targetIndex < minimumMessages))
    .sort((left, right) => left.distance - right.distance || right.index - left.index)[0];
  if (nearbyClosure) return nearbyClosure;
  if (targetIndex < minimumMessages) return null;
  const nearbyPause = candidates
    .filter((item) => item.kind === "time_pause" && item.distance <= 8)
    .sort((left, right) => right.gapMinutes - left.gapMinutes || left.distance - right.distance)[0];
  if (nearbyPause) return nearbyPause;
  return candidates
    .filter((item) => item.index <= messages.length - 2)
    .sort((left, right) => left.distance - right.distance)[0] || null;
}

function topicClosureReason(messages) {
  const text = messages.map((message) => String(message?.content || "")).join("\n");
  const patterns = [
    [/晚安|好梦|睡啦|睡觉了|去睡|早点睡|休息啦|休息了/u, "晚安或休息"],
    [/明天见|明天再聊|下次再聊|回头聊|改天聊/u, "约定之后再聊"],
    [/先聊到这|今天先到这|就到这里|告一段落|收工|拜拜|再见/u, "明确结束当前话题"],
    [/good\s*night|see\s+you|talk\s+later|bye(?:bye)?/iu, "明确结束当前话题"]
  ];
  return patterns.find(([pattern]) => pattern.test(text))?.[1] || "";
}

function estimateMessageTokens(message) {
  const text = messageText(message);
  let cjk = 0;
  let other = 0;
  for (const character of text) {
    if (/\p{Script=Han}|\p{Script=Hiragana}|\p{Script=Katakana}|\p{Script=Hangul}/u.test(character)) {
      cjk += 1;
    } else {
      other += 1;
    }
  }
  const imageCost = Array.isArray(message?.images) ? message.images.length * 800 : 0;
  return 8 + cjk + Math.ceil(other / 4) + imageCost;
}

function messageText(message) {
  const content = typeof message?.content === "string"
    ? message.content
    : Array.isArray(message?.content)
      ? message.content.map((part) => typeof part === "string" ? part : String(part?.text || "")).join("\n")
      : "";
  return `${content}\n${String(message?.userThinking || "")}`;
}
