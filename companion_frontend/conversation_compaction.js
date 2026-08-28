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
  if (source.length < minimumMessages + 2) return null;

  let keptTokens = 0;
  let targetIndex = source.length;
  for (let index = source.length - 1; index >= 0; index -= 1) {
    keptTokens += estimateMessageTokens(source[index]);
    targetIndex = index;
    if (keptTokens >= keepTokens) break;
  }
  if (targetIndex < minimumMessages) return null;

  let cutoff = nearestNaturalBoundary(source, targetIndex, minimumMessages);
  cutoff = Math.max(minimumMessages, Math.min(source.length - 2, cutoff));
  const compressed = source.slice(0, cutoff);
  const kept = source.slice(cutoff);
  if (!compressed.some((message) => message.role === "user") || !kept.length) return null;
  return {
    compressed,
    kept,
    cutoff,
    compressedTokens: estimateConversationTokens(compressed),
    keptTokens: estimateConversationTokens(kept)
  };
}

function nearestNaturalBoundary(messages, targetIndex, minimumMessages) {
  const candidates = [];
  for (let index = minimumMessages; index <= messages.length - 2; index += 1) {
    const previous = messages[index - 1];
    const next = messages[index];
    if (previous?.role !== "assistant") continue;
    const previousTime = new Date(previous.createdAt || "").getTime();
    const nextTime = new Date(next.createdAt || "").getTime();
    const gapMinutes = Number.isFinite(previousTime) && Number.isFinite(nextTime)
      ? Math.max(0, (nextTime - previousTime) / 60_000)
      : 0;
    candidates.push({
      index,
      distance: Math.abs(index - targetIndex),
      gapMinutes
    });
  }
  if (!candidates.length) return targetIndex;
  const nearbyPause = candidates
    .filter((item) => item.gapMinutes >= 45 && item.distance <= 6)
    .sort((left, right) => right.gapMinutes - left.gapMinutes || left.distance - right.distance)[0];
  if (nearbyPause) return nearbyPause.index;
  return candidates.sort((left, right) => left.distance - right.distance)[0].index;
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
