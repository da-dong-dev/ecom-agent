import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatAmount } from "../lib/format";
import { ProgressPanel } from "./ProgressPanel";

describe("ProgressPanel", () => {
  it("shows the refund three states and formatted amount", () => {
    render(
      <ProgressPanel
        route="rules"
        inFlight={false}
        progress={[
          { stage: "checking_order", status: "done" },
          { stage: "amount_ready", status: "done", amount: 124900 },
          {
            stage: "waiting_confirm",
            status: "waiting",
            amount: 124900,
          },
        ]}
      />,
    );

    expect(screen.getByText("已核对订单")).toBeTruthy();
    expect(screen.getByText("已算出金额 ¥1249.00")).toBeTruthy();
    expect(screen.getByText("等待您确认")).toBeTruthy();
  });

  it("shows waiting_info instead of amount", () => {
    render(
      <ProgressPanel
        route="rules"
        inFlight={false}
        progress={[
          { stage: "checking_order", status: "done" },
          {
            stage: "waiting_info",
            status: "waiting",
            pendingQuestion: "order_id",
          },
        ]}
      />,
    );

    expect(screen.getByText("等待您补充信息")).toBeTruthy();
    expect(screen.queryByText(/已算出金额/)).toBeNull();
  });

  it("shows handoff for transfer", () => {
    render(
      <ProgressPanel
        route="rules"
        inFlight={false}
        progress={[
          { stage: "checking_order", status: "done" },
          { stage: "handoff", status: "waiting" },
        ]}
      />,
    );

    expect(screen.getByText("正在转接人工")).toBeTruthy();
  });

  it("shows knowledge stages", () => {
    render(
      <ProgressPanel
        route="knowledge"
        inFlight={false}
        progress={[
          { stage: "retrieving", status: "done" },
          { stage: "evidence_ready", status: "done" },
          { stage: "answering", status: "done" },
        ]}
      />,
    );

    expect(screen.getByText("已检索知识库")).toBeTruthy();
    expect(screen.getByText("已找到依据")).toBeTruthy();
    expect(screen.getByText("已整理回答")).toBeTruthy();
  });

  it("formats fen to yuan", () => {
    expect(formatAmount(124900)).toBe("¥1249.00");
    expect(formatAmount(0)).toBe("¥0.00");
  });
});
