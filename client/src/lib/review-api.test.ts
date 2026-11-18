import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveReview,
  listReviewQueue,
  rejectReview,
  retryReview,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function _jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("review API", () => {
  it("loads queue with status filter", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        _jsonResponse({ items: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await listReviewQueue("approved");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/review/queue?status=approved",
    );
  });

  it("approves with answer JSON", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        _jsonResponse({ id: 12, status: "written_back", faq_id: 3 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveReview(12, "核准答案");

    expect(result.status).toBe("written_back");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(init.body).toBe(JSON.stringify({ answer: "核准答案" }));
  });

  it("rejects with optional reason", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        _jsonResponse({ id: 12, status: "rejected" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await rejectReview(12, "");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(JSON.stringify({ reason: null }));
  });

  it("retries sync with POST", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        _jsonResponse({ id: 12, status: "written_back" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await retryReview(12);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/review/12/retry");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
  });
});
