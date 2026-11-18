import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MessageBubble } from "./MessageBubble";

describe("MessageBubble citations", () => {
  it("opens the source when a citation number is clicked", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "质量问题由商家承担[1]。",
          citationIds: ["returns#退货运费由谁承担"],
          citations: [
            {
              id: "returns#退货运费由谁承担",
              section_path: "退货运费由谁承担",
              text: "质量问题由商家承担运费。",
              source: "returns.md",
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByText("[1]"));

    expect(screen.getByText("质量问题由商家承担运费。")).toBeTruthy();
    expect(screen.getByText("来源：returns.md")).toBeTruthy();
  });
});

describe("MessageBubble feedback", () => {
  it("emits down when the assistant answer is not helpful", () => {
    const onFeedback = vi.fn();
    render(
      <MessageBubble
        message={{ id: 12, role: "assistant", content: "回答" }}
        onFeedback={onFeedback}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "回答没帮助" }));

    expect(onFeedback).toHaveBeenCalledWith("down");
  });

  it("shows thanks and hides buttons after feedback", () => {
    render(
      <MessageBubble
        message={{ id: 12, role: "assistant", content: "回答" }}
        feedback="up"
        onFeedback={() => undefined}
      />,
    );

    expect(screen.getByText("感谢反馈")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "回答有帮助" }),
    ).toBeNull();
  });

  it("does not show feedback on user messages", () => {
    render(
      <MessageBubble
        message={{ id: 11, role: "user", content: "我的问题" }}
        onFeedback={() => undefined}
      />,
    );

    expect(screen.queryByText("有帮助吗？")).toBeNull();
  });
});
