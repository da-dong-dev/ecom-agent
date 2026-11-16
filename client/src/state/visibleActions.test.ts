import { describe, expect, it } from "vitest";

import type { ChatMessage } from "./turn";
import { visibleActionsFor } from "./visibleActions";

function messages(user: string): ChatMessage[] {
  return [
    { role: "user", content: user },
    {
      role: "assistant",
      content: "已为您转接人工客服。",
      actions: [{ type: "transfer_human" }, { type: "resume_flow", flow: "refund" }],
    },
  ];
}

describe("visibleActionsFor", () => {
  it("shows transfer before a handoff", () => {
    expect(visibleActionsFor(messages("我要投诉"), "fallback")).toEqual([
      { type: "transfer_human" },
      { type: "resume_flow", flow: "refund" },
    ]);
  });

  it("hides transfer after the conversation has already handed off", () => {
    expect(visibleActionsFor(messages("我要投诉"), "human")).toEqual([
      { type: "resume_flow", flow: "refund" },
    ]);
  });

  it("hides transfer when the user already typed it", () => {
    expect(visibleActionsFor(messages("转人工"), "fallback")).toEqual([
      { type: "resume_flow", flow: "refund" },
    ]);
  });

  it("hides transfer after an earlier handoff even on a fallback turn", () => {
    expect(visibleActionsFor(messages("******"), "fallback", true)).toEqual([
      { type: "resume_flow", flow: "refund" },
    ]);
  });
});
