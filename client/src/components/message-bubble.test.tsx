import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
