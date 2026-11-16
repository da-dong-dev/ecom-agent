import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatWindow } from "./ChatWindow";

describe("ChatWindow", () => {
  it("uses 重试 for a normal error", () => {
    render(
      <ChatWindow
        messages={[{ role: "user", content: "你好" }]}
        inFlight={false}
        error="网络异常"
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeTruthy();
  });

  it("uses 继续 for an interrupted stream", () => {
    render(
      <ChatWindow
        messages={[{ role: "user", content: "你好" }]}
        inFlight={false}
        error="连接中断，请点击继续"
        interrupted
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /继\s*续/ })).toBeTruthy();
  });
});
