import { afterEach, describe, expect, it, vi } from "vitest";

import { sendFeedback } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sendFeedback", () => {
  it("posts rating and question", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({ ok: true, recorded: true, id: 9 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendFeedback({
      conversation_id: 1,
      message_id: 12,
      rating: "down",
      question: "价保规则是什么",
    });

    expect(result).toEqual({ ok: true, recorded: true, id: 9 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/feedback");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(
      JSON.stringify({
        conversation_id: 1,
        message_id: 12,
        rating: "down",
        question: "价保规则是什么",
      }),
    );
  });
});
