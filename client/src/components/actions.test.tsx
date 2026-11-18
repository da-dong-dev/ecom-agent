import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActionBar } from "./ActionBar";

describe("ActionBar", () => {
  it("emits transfer_human", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[{ type: "transfer_human" }]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "转人工" }));

    expect(onAction).toHaveBeenCalledWith({ type: "transfer_human" });
  });

  it("emits select_order with the chosen order id", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[
          {
            type: "select_order",
            candidates: [
              {
                order_id: "1002",
                title: "婴儿推车 石墨灰",
                amount_text: "¥1249.00",
              },
            ],
          },
        ]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /婴儿推车/ }));

    expect(onAction).toHaveBeenCalledWith({
      type: "select_order",
      order_id: "1002",
    });
  });

  it("emits select_sku with the chosen sku", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[
          {
            type: "select_sku",
            candidates: [{ sku_id: "ST-20" }],
          },
        ]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "ST-20" }));

    expect(onAction).toHaveBeenCalledWith({
      type: "select_sku",
      sku_id: "ST-20",
    });
  });

  it("emits select_spec with the chosen spec", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[
          {
            type: "select_spec",
            candidates: [
              { sku_id: "ST-20", spec: "石墨灰", price: 129900, stock: 3 },
            ],
          },
        ]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /石墨灰/ }));

    expect(onAction).toHaveBeenCalledWith({
      type: "select_spec",
      spec: "石墨灰",
    });
  });

  it("emits select_reason for a fixed reason option", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[
          {
            type: "select_reason",
            reason_code: "quality",
            label: "商品有质量问题",
          },
        ]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "商品有质量问题" }));

    expect(onAction).toHaveBeenCalledWith({
      type: "select_reason",
      reason_code: "quality",
      label: "商品有质量问题",
    });
  });

  it("asks for text when choosing the other reason", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[
          { type: "select_reason", reason_code: "other", label: "其他原因" },
        ]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "其他原因" }));
    fireEvent.change(
      screen.getByPlaceholderText("例如：尺码不合适、颜色和图片不一致、买重复了…"),
      { target: { value: "尺码不合适" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /提\s*交/ }));

    expect(onAction).toHaveBeenCalledWith({
      type: "select_reason",
      reason_code: "other",
      label: "其他原因",
      reason_text: "尺码不合适",
    });
  });

  it("rejects a generic other reason", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[
          { type: "select_reason", reason_code: "other", label: "其他原因" },
        ]}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "其他原因" }));
    fireEvent.change(
      screen.getByPlaceholderText("例如：尺码不合适、颜色和图片不一致、买重复了…"),
      { target: { value: "其他" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /提\s*交/ }));

    expect(onAction).not.toHaveBeenCalled();
    expect(
      screen.getByText("请补充具体原因，不要只填「其他」"),
    ).toBeTruthy();
  });

  it("passes resume_flow through to the app", () => {
    const onAction = vi.fn();
    const action = { type: "resume_flow", flow: "refund" };
    render(<ActionBar actions={[action]} onAction={onAction} />);

    fireEvent.click(screen.getByRole("button", { name: "继续退款" }));

    expect(onAction).toHaveBeenCalledWith(action);
  });

  it("disables buttons when disabled", () => {
    const onAction = vi.fn();
    render(
      <ActionBar
        actions={[{ type: "transfer_human" }]}
        disabled
        onAction={onAction}
      />,
    );

    const button = screen.getByRole("button", {
      name: "转人工",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onAction).not.toHaveBeenCalled();
  });

  it("renders nothing when there are no supported actions", () => {
    const { container } = render(
      <ActionBar actions={[{ type: "unknown_action" }]} onAction={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
