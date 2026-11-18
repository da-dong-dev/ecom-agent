/**
 * SSE（Server-Sent Events，服务器单向推送事件流）请求与解析。
 *
 * 只做两件事：
 * 1. POST /api/chat，带上 client_message_id；
 * 2. 把 `data: {...}\n\n` 解析成帧，交给上层 reducer。
 */
import { BASE, errorForStatus, newTraceId } from "./base";

export type ChatFrame = {
  event?: string;
  delta?: string;
  [key: string]: unknown;
};

export type SendChatOptions = {
  message: string;
  clientMessageId: string;
  conversationId?: number | null;
  selectedAction?: Record<string, unknown> | null;
  signal?: AbortSignal;
  onFrame: (frame: ChatFrame) => void;
};

/** 发一轮对话；重试时必须复用同一个 clientMessageId。 */
export async function sendChat(options: SendChatOptions): Promise<void> {
  const response = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Trace-Id": newTraceId(),
    },
    body: JSON.stringify({
      message: options.message,
      conversation_id: options.conversationId ?? null,
      client_message_id: options.clientMessageId,
      selected_action: options.selectedAction ?? null,
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw errorForStatus(response.status);
  }
  if (!response.body) {
    throw errorForStatus(502);
  }
  await readSse(response.body, options.onFrame);
}

/** 读完整条 SSE 流；分块到达由 drainFrames 负责拼装。 */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onFrame: (frame: ChatFrame) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      buffer = drainFrames(buffer, onFrame);
    }
    buffer += decoder.decode();
    drainFrames(buffer, onFrame);
  } finally {
    reader.releaseLock();
  }
}

/**
 * 从 buffer 里取出完整帧，返回剩下的半帧。
 *
 * 处理：分块到达、CRLF、多行 data、keepalive/注释、坏 JSON。
 * 坏帧只跳过这一帧，不能让整条流崩掉。
 */
export function drainFrames(
  buffer: string,
  onFrame: (frame: ChatFrame) => void,
): string {
  let rest = buffer.replace(/\r\n/g, "\n");
  let index = rest.indexOf("\n\n");

  while (index >= 0) {
    const block = rest.slice(0, index);
    rest = rest.slice(index + 2);
    parseBlock(block, onFrame);
    index = rest.indexOf("\n\n");
  }

  return rest;
}

function parseBlock(
  block: string,
  onFrame: (frame: ChatFrame) => void,
): void {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");

  if (!data) {
    return;
  }

  try {
    const frame: unknown = JSON.parse(data);
    if (frame && typeof frame === "object") {
      onFrame(frame as ChatFrame);
    }
  } catch {
    // 坏帧不阻断后续；服务端偶发错误不应该让整条流崩掉
  }
}
