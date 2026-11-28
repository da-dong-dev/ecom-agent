import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConversationSummary } from "../lib/api";
import { ConversationList } from "./ConversationList";

const items: ConversationSummary[] = [
  {
    id: 1,
    created_at: "2026-11-16T08:00:00",
    updated_at: "2026-11-16T08:00:00",
    title: "第一个会话",
    active_flow: "refund",
    pending_question: "order_id",
  },
  {
    id: 2,
    created_at: "2026-11-16T09:00:00",
    updated_at: "2026-11-16T09:00:00",
    title: "第二个会话",
    active_flow: null,
    pending_question: null,
  },
];

describe("ConversationList", () => {
  it("renders in the API order and selects a conversation", () => {
    const onSelect = vi.fn();
    render(
      <ConversationList
        items={items}
        activeId={1}
        loading={false}
        onSelect={onSelect}
        onNew={vi.fn()}
      />,
    );

    const titles = screen
      .getAllByTestId("conversation-title")
      .map((element) => element.textContent);
    expect(titles).toEqual(["第一个会话", "第二个会话"]);
    expect(screen.getByText("未完成退款")).toBeTruthy();

    fireEvent.click(screen.getByText("第二个会话"));
    expect(onSelect).toHaveBeenCalledWith(2);

    const active = screen.getByText("第一个会话").closest("li");
    expect(active?.className).toContain("active");
    expect(active?.getAttribute("aria-current")).toBe("true");
  });

  it("disables switching and new conversation while replying", () => {
    const onSelect = vi.fn();
    render(
      <ConversationList
        items={items}
        activeId={1}
        loading={false}
        disabled
        onSelect={onSelect}
        onNew={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("第二个会话"));
    expect(onSelect).not.toHaveBeenCalled();
    const newButton = screen.getByRole("button", {
      name: "新对话",
    }) as HTMLButtonElement;
    expect(newButton.disabled).toBe(true);
  });

  it("asks for confirmation before deleting a conversation", () => {
    const onDelete = vi.fn();
    render(
      <ConversationList
        items={items}
        activeId={1}
        loading={false}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        onDelete={onDelete}
      />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "删除会话" })[0]);
    fireEvent.click(screen.getByRole("button", { name: /^删\s*除$/ }));

    expect(onDelete).toHaveBeenCalledWith(1);
  });

  it("emits new conversation", () => {
    const onNew = vi.fn();
    render(
      <ConversationList
        items={[]}
        activeId={null}
        loading={false}
        onSelect={vi.fn()}
        onNew={onNew}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    expect(onNew).toHaveBeenCalledTimes(1);
    expect(screen.getByText("暂无历史会话")).toBeTruthy();
  });
});
