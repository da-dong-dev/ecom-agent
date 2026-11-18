import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, errorForStatus, newTraceId, request } from "./base";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("errorForStatus", () => {
  it("maps 404 to not_found", () => {
    const error = errorForStatus(404);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.code).toBe("not_found");
  });

  it("maps 429 to busy", () => {
    const error = errorForStatus(429);
    expect(error.status).toBe(429);
    expect(error.code).toBe("busy");
  });

  it("maps 5xx to internal_error", () => {
    const error = errorForStatus(503);
    expect(error.status).toBe(503);
    expect(error.code).toBe("internal_error");
  });

  it("maps review statuses to actionable codes", () => {
    expect(errorForStatus(409).code).toBe("conflict");
    expect(errorForStatus(422).code).toBe("invalid_arg");
    expect(errorForStatus(502).code).toBe("sync_failed");
  });
});

describe("request", () => {
  it("adds X-Trace-Id and maps non-2xx", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response("no", { status: 500 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(request("/api/meta")).rejects.toMatchObject({
      status: 500,
      code: "internal_error",
    });

    const init = fetchMock.mock.calls[0][1];
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Trace-Id")).toBeTruthy();
  });

  it("uses structured detail message when present", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            detail: { code: "sync_failed", message: "知识库同步失败" },
          }),
          { status: 502, headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(request("/api/review/1/approve")).rejects.toMatchObject({
      status: 502,
      code: "sync_failed",
      message: "知识库同步失败",
    });
  });
});

describe("newTraceId", () => {
  it("returns a non-empty unique trace id", () => {
    const first = newTraceId();
    const second = newTraceId();
    expect(first).toBeTruthy();
    expect(second).toBeTruthy();
    expect(first).not.toBe(second);
  });
});
