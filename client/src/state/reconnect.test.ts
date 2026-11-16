import { describe, expect, it } from "vitest";

import type { ConversationHistory } from "../lib/api";
import {
  buildHistoryState,
  readConversationId,
  reconcileHistory,
} from "./reconnect";

function storageWith(value: string | null): Pick<Storage, "getItem"> {
  return { getItem: () => value };
}

function historyWith(
  overrides: Partial<ConversationHistory> = {},
): ConversationHistory {
  return {
    conversation_id: 12,
    active_flow: null,
    pending_question: null,
    items: [
      {
        id: 34,
        role: "user",
        content: "订单1002 不想要了",
        created_at: "2026-11-16T08:00:00",
        client_message_id: "cm-1",
        meta: { selected_action: { type: "select_order", order_id: "1002" } },
      },
      {
        id: 35,
        role: "assistant",
        content: "本次应退 1249.00 元。",
        created_at: "2026-11-16T08:00:05",
        status: "completed",
        reply_to_client_message_id: "cm-1",
        meta: {
          route: "rules",
          actions: [],
          signals: {
            amount: 124900,
            pending_question: null,
            refund_eligible: true,
          },
        },
      },
    ],
    ...overrides,
  };
}

describe("readConversationId", () => {
  it("prefers URL over localStorage", () => {
    expect(readConversationId("?c=12", storageWith("34"))).toBe(12);
  });

  it("falls back to localStorage", () => {
    expect(readConversationId("", storageWith("34"))).toBe(34);
  });

  it("ignores invalid ids", () => {
    expect(readConversationId("?c=bad", storageWith(null))).toBeNull();
    expect(readConversationId("?c=0", storageWith("-1"))).toBeNull();
  });
});

describe("buildHistoryState", () => {
  it("restores messages, route, actions and final progress", () => {
    const state = buildHistoryState(historyWith());

    expect(state.conversationId).toBe(12);
    expect(state.messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
    ]);
    expect(state.messages[0].clientMessageId).toBe("cm-1");
    expect(state.route).toBe("rules");
    expect(state.progress).toEqual([
      { stage: "checking_order", status: "done" },
      { stage: "amount_ready", status: "done", amount: 124900 },
      { stage: "waiting_confirm", status: "waiting", amount: 124900 },
    ]);
    expect(state.lastClientMessageId).toBe("cm-1");
    expect(state.lastSelectedAction).toEqual({
      type: "select_order",
      order_id: "1002",
    });
  });

  it("restores waiting_info without an amount", () => {
    const history = historyWith({
      items: [
        {
          id: 1,
          role: "user",
          content: "我想退款",
          created_at: "2026-11-16T08:00:00",
        },
        {
          id: 2,
          role: "assistant",
          content: "请提供订单号。",
          created_at: "2026-11-16T08:00:01",
          meta: {
            route: "rules",
            actions: [],
            signals: { pending_question: "order_id" },
          },
        },
      ],
    });

    const state = buildHistoryState(history);

    expect(state.progress).toEqual([
      {
        stage: "waiting_info",
        status: "waiting",
        pendingQuestion: "order_id",
      },
    ]);
    expect(state.progress.some((step) => step.amount !== undefined)).toBe(false);
  });

  it("restores handoff from actions", () => {
    const history = historyWith({
      items: [
        {
          id: 1,
          role: "user",
          content: "转人工",
          created_at: "2026-11-16T08:00:00",
        },
        {
          id: 2,
          role: "assistant",
          content: "已为您转接人工客服。",
          created_at: "2026-11-16T08:00:01",
          meta: {
            route: "human",
            actions: [{ type: "transfer_human" }],
            signals: {},
          },
        },
      ],
    });

    expect(buildHistoryState(history).progress).toEqual([
      { stage: "handoff", status: "waiting" },
    ]);
  });

  it("does not mix two conversations", () => {
    const first = buildHistoryState(historyWith());
    const second = buildHistoryState(
      historyWith({
        conversation_id: 99,
        items: [
          {
            id: 100,
            role: "user",
            content: "另一个会话",
            created_at: "2026-11-16T09:00:00",
          },
        ],
      }),
    );

    expect(first.conversationId).toBe(12);
    expect(second.conversationId).toBe(99);
    expect(first.messages[0].content).toBe("订单1002 不想要了");
    expect(second.messages[0].content).toBe("另一个会话");
  });
});

describe("reconcileHistory", () => {
  it("uses the server reply when it matches the local client_message_id", () => {
    const result = reconcileHistory(historyWith(), "cm-1");

    expect(result.status).toBe("completed");
    if (result.status === "completed") {
      expect(result.state.messages[1].content).toBe("本次应退 1249.00 元。");
      expect(result.state.messages[1].id).toBe(35);
      expect(result.state.route).toBe("rules");
      expect(result.state.progress).toEqual([
        { stage: "checking_order", status: "done" },
        { stage: "amount_ready", status: "done", amount: 124900 },
        { stage: "waiting_confirm", status: "waiting", amount: 124900 },
      ]);
    }
  });

  it("stays pending when the server has not saved a reply yet", () => {
    const history = historyWith({
      items: [
        {
          id: 34,
          role: "user",
          content: "订单1002 不想要了",
          created_at: "2026-11-16T08:00:00",
          client_message_id: "cm-1",
        },
      ],
    });

    expect(reconcileHistory(history, "cm-1")).toEqual({ status: "pending" });
  });

  it("ignores a reply for a different client_message_id", () => {
    const history = historyWith({
      items: [
        {
          id: 34,
          role: "user",
          content: "订单1002 不想要了",
          created_at: "2026-11-16T08:00:00",
          client_message_id: "cm-1",
        },
        {
          id: 35,
          role: "assistant",
          content: "另一条回答",
          created_at: "2026-11-16T08:00:05",
          reply_to_client_message_id: "cm-2",
        },
      ],
    });

    expect(reconcileHistory(history, "cm-1")).toEqual({ status: "pending" });
  });

  it("does not treat an error assistant message as a completed reply", () => {
    const history = historyWith({
      items: [
        {
          id: 34,
          role: "user",
          content: "订单1002 不想要了",
          created_at: "2026-11-16T08:00:00",
          client_message_id: "cm-1",
        },
        {
          id: 35,
          role: "assistant",
          content: "服务暂时不可用",
          created_at: "2026-11-16T08:00:05",
          status: "error",
          reply_to_client_message_id: "cm-1",
        },
      ],
    });

    expect(reconcileHistory(history, "cm-1")).toEqual({ status: "pending" });
  });
});
