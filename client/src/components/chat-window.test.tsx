import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatWindow } from "./ChatWindow";

describe("ChatWindow", () => {
  it("does not show the chat header above the welcome state", () => {
    render(
      <ChatWindow
        messages={[]}
        inFlight={false}
        error={null}
        suggestions={[{ label: "查退款", text: "订单1002 不想要了" }]}
        onSuggestion={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.queryByText("智能客服 · 通常几秒内回复")).toBeNull();
    expect(screen.getByText("您好，我是售后客服")).toBeTruthy();
  });

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

  it("renders actions inside the chat flow", () => {
    const onAction = vi.fn();
    render(
      <ChatWindow
        messages={[
          { role: "user", content: "我要退款" },
          { role: "assistant", content: "请选择订单" },
        ]}
        actions={[
          {
            type: "select_order",
            candidates: [{ order_id: "1003", title: "CS-04" }],
          },
        ]}
        inFlight={false}
        error={null}
        onAction={onAction}
        onRetry={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /CS-04/ }));
    expect(onAction).toHaveBeenCalledWith({
      type: "select_order",
      order_id: "1003",
    });
  });

  it("does not keep stale options after the user replies", () => {
    render(
      <ChatWindow
        messages={[
          { role: "assistant", content: "请选择订单" },
          { role: "user", content: "选择订单 1003" },
        ]}
        actions={[
          {
            type: "select_order",
            candidates: [{ order_id: "1003", title: "CS-04" }],
          },
        ]}
        inFlight
        error={null}
        onAction={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /CS-04/ })).toBeNull();
  });

  it("shows the current stage in a pending assistant bubble", () => {
    render(
      <ChatWindow
        messages={[{ role: "user", content: "退货政策是什么" }]}
        inFlight
        error={null}
        progressLabel="正在检索知识库"
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("正在检索知识库")).toBeTruthy();
    expect(screen.getByText("客服")).toBeTruthy();
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
