import { describe, expect, it } from "vitest";

import { parseServerTime } from "./time";

describe("parseServerTime", () => {
  it("treats naive backend timestamps as UTC", () => {
    expect(parseServerTime("2026-11-16T13:06:33")?.toISOString()).toBe(
      "2026-11-16T13:06:33.000Z",
    );
  });

  it("keeps an explicit timezone", () => {
    expect(parseServerTime("2026-11-16T13:06:33Z")?.toISOString()).toBe(
      "2026-11-16T13:06:33.000Z",
    );
    expect(parseServerTime("2026-11-16T21:06:33+08:00")?.toISOString()).toBe(
      "2026-11-16T13:06:33.000Z",
    );
  });

  it("returns null for empty or invalid values", () => {
    expect(parseServerTime(null)).toBeNull();
    expect(parseServerTime("")).toBeNull();
    expect(parseServerTime("not-a-date")).toBeNull();
  });
});
