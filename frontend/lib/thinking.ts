export interface ThinkingSegment {
  type: "answer" | "thinking";
  content: string;
  complete?: boolean;
}

const OPEN_THINKING_RE = /<think\b[^>]*>/i;
const CLOSE_THINKING_RE = /<\/think>/i;

export function splitThinkingBlocks(content: string): ThinkingSegment[] {
  const segments: ThinkingSegment[] = [];
  let cursor = 0;

  while (cursor < content.length) {
    const openMatch = content.slice(cursor).match(OPEN_THINKING_RE);
    if (!openMatch || openMatch.index === undefined) {
      segments.push({ type: "answer", content: content.slice(cursor) });
      break;
    }

    const openStart = cursor + openMatch.index;
    if (openStart > cursor) {
      segments.push({ type: "answer", content: content.slice(cursor, openStart) });
    }

    const thinkingStart = openStart + openMatch[0].length;
    const closeMatch = content.slice(thinkingStart).match(CLOSE_THINKING_RE);
    if (!closeMatch || closeMatch.index === undefined) {
      segments.push({
        type: "thinking",
        content: content.slice(thinkingStart),
        complete: false,
      });
      return segments;
    }

    const closeStart = thinkingStart + closeMatch.index;
    segments.push({
      type: "thinking",
      content: content.slice(thinkingStart, closeStart),
      complete: true,
    });
    cursor = closeStart + closeMatch[0].length;
  }

  return segments.filter((segment) => segment.type === "thinking" || segment.content.length > 0);
}

export function stripThinkingBlocks(content: string): string {
  return splitThinkingBlocks(content)
    .filter((segment) => segment.type === "answer")
    .map((segment) => segment.content)
    .join("")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function formatThinkingDuration(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) {
    return "Thought";
  }

  const seconds = durationMs / 1000;
  return `Thought for ${seconds.toFixed(1)}s`;
}
