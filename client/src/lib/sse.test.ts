import { afterEach, describe, expect, it, vi } from "vitest";

import { drainFrames, readSse, sendChat, type ChatFrame } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("drainFrames", () => {
  it("parses a complete SSE frame", () => {
    const frames: ChatFrame[] = [];
    const rest = drainFrames(
      'data: {"event":"ack","conversation_id":1}\n\n',
      (frame) => frames.push(frame),
    );

    expect(rest).toBe("");
    expect(frames).toEqual([{ event: "ack", conversation_id: 1 }]);
  });

  it("keeps a partial frame until the next chunk", () => {
    const frames: ChatFrame[] = [];
    const rest = drainFrames('data: {"delta":"你', (frame) =>
      frames.push(frame),
    );
    expect(rest).toBe('data: {"delta":"你');
    expect(frames).toEqual([]);

    const rest2 = drainFrames(`${rest}好"}\n\n`, (frame) =>
      frames.push(frame),
    );
    expect(rest2).toBe("");
    expect(frames).toEqual([{ delta: "你好" }]);
  });

  it("handles CRLF and multi-line data", () => {
    const frames: ChatFrame[] = [];
    drainFrames(
      'data: {"a":1,\r\ndata: "b":2}\r\n\r\n',
      (frame) => frames.push(frame),
    );

    expect(frames).toEqual([{ a: 1, b: 2 }]);
  });

  it("ignores keepalive, comments and bad JSON", () => {
    const frames: ChatFrame[] = [];
    drainFrames(
      ': keepalive\n\ndata: not-json\n\ndata: {"event":"done"}\n\n',
      (frame) => frames.push(frame),
    );

    expect(frames).toEqual([{ event: "done" }]);
  });
});

describe("readSse", () => {
  it("parses frames split across byte chunks", async () => {
    const frames: ChatFrame[] = [];
    const stream = streamOf([
      'data: {"event":"route",',
      '"route":"rules"}\n\n',
      'data: {"delta":"好"}\n\n',
    ]);

    await readSse(stream, (frame) => frames.push(frame));

    expect(frames).toEqual([
      { event: "route", route: "rules" },
      { delta: "好" },
    ]);
  });
});

describe("sendChat", () => {
  it("posts client_message_id and parses frames", async () => {
    const frames: ChatFrame[] = [];
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(streamOf(['data: {"event":"ack"}\n\n']), {
          status: 200,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await sendChat({
      message: "你好",
      clientMessageId: "cm-1",
      onFrame: (frame) => frames.push(frame),
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0][1];
    expect(init?.headers).toMatchObject({
      "Content-Type": "application/json",
    });
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body.client_message_id).toBe("cm-1");
    expect(body.message).toBe("你好");
    expect(frames).toEqual([{ event: "ack" }]);
  });

  it("maps non-2xx to ApiError", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response("too many", { status: 429 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      sendChat({
        message: "你好",
        clientMessageId: "cm-2",
        onFrame: () => undefined,
      }),
    ).rejects.toMatchObject({ status: 429, code: "busy" });
  });
});
