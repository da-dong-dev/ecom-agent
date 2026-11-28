import { describe, expect, it } from "vitest";

import type { ConversationSummary } from "../lib/api";
import { chooseBootstrapTarget } from "./useConversations";

const items: ConversationSummary[] = [
  {
    id: 62,
    created_at: "2026-11-17T17:00:00",
    updated_at: "2026-11-17T17:01:00",
    title: "最近会话",
    active_flow: null,
    pending_question: null,
  },
  {
    id: 61,
    created_at: "2026-11-17T10:00:00",
    updated_at: "2026-11-17T10:01:00",
    title: "第二近",
    active_flow: null,
    pending_question: null,
  },
];

describe("chooseBootstrapTarget", () => {
  it("uses preferred conversation when it is in the recent list", () => {
    expect(chooseBootstrapTarget(items, 61)).toBe(61);
  });

  it("falls back to the first recent conversation when preferred is out of range", () => {
    expect(chooseBootstrapTarget(items, 27)).toBe(62);
  });

  it("uses the first item when there is no preferred id", () => {
    expect(chooseBootstrapTarget(items, null)).toBe(62);
  });

  it("returns null when there is no conversation", () => {
    expect(chooseBootstrapTarget([], 27)).toBeNull();
    expect(chooseBootstrapTarget([], null)).toBeNull();
  });
});
