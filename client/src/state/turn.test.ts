import { describe, expect, it } from "vitest";

import type { ChatFrame } from "../lib/sse";
import {
  applyFrame,
  initialTurnState,
  newClientMessageId,
  turnReducer,
  type TurnState,
} from "./turn";

function begin(text = "订单1002 不想要了"): TurnState {
  return turnReducer(initialTurnState(), {
    type: "begin",
    text,
    clientMessageId: "cm-1",
  });
}

function frame(state: TurnState, payload: ChatFrame): TurnState {
  return applyFrame(state, payload);
}

describe("turnReducer", () => {
  it("begin adds a user bubble and marks in flight", () => {
    const state = begin();

    expect(state.inFlight).toBe(true);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      role: "user",
      content: "订单1002 不想要了",
      clientMessageId: "cm-1",
      createdAt: expect.any(String),
    });
    expect(state.lastClientMessageId).toBe("cm-1");
  });

  it("marks handoffStarted when route is human", () => {
    let state = begin();
    state = frame(state, { event: "route", route: "human" });
    state = frame(state, {
      event: "actions",
      items: [{ type: "transfer_human" }],
    });

    expect(state.handoffStarted).toBe(true);
  });

  it("folds ack/route/progress/delta/citations/actions/done", () => {
    let state = begin();

    state = frame(state, {
      event: "ack",
      conversation_id: 12,
      user_message_id: 34,
    });
    state = frame(state, {
      event: "route",
      route: "rules",
      intent: "退款申请",
      resumed: false,
    });
    state = frame(state, {
      event: "progress",
      stage: "checking_order",
      status: "done",
    });
    state = frame(state, {
      event: "progress",
      stage: "amount_ready",
      status: "done",
      amount: 124900,
    });
    state = frame(state, { delta: "本次应退 1249.00 元。" });
    state = frame(state, {
      event: "citations",
      items: ["returns#时效"],
    });
    state = frame(state, {
      event: "actions",
      items: [{ type: "transfer_human" }],
    });
    state = frame(state, { event: "done", message_id: 35 });

    expect(state.conversationId).toBe(12);
    expect(state.messages[0].id).toBe(34);
    expect(state.route).toBe("rules");
    expect(state.progress).toEqual([
      { stage: "checking_order", status: "done" },
      { stage: "amount_ready", status: "done", amount: 124900 },
    ]);
    expect(state.messages[1]).toMatchObject({
      role: "assistant",
      content: "本次应退 1249.00 元。",
      citationIds: ["returns#时效"],
      actions: [{ type: "transfer_human" }],
      id: 35,
    });
    expect(state.inFlight).toBe(false);
  });

  it("appends multiple deltas to the same assistant bubble", () => {
    let state = begin();

    state = frame(state, { delta: "你" });
    state = frame(state, { delta: "好" });

    expect(state.messages).toHaveLength(2);
    expect(state.messages[1].content).toBe("你好");
  });

  it("starts a new assistant bubble after the latest user message", () => {
    let state = begin();
    state = frame(state, { delta: "第一轮回答" });
    state = frame(state, { event: "done", message_id: 35 });

    state = turnReducer(state, {
      type: "begin",
      text: "订单1003 到哪了",
      clientMessageId: "cm-2",
    });
    state = frame(state, { delta: "第二轮回答" });

    expect(state.messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(state.messages[1].content).toBe("第一轮回答");
    expect(state.messages[3].content).toBe("第二轮回答");
  });

  it("ignores unknown frames and unknown progress stages", () => {
    const state = begin();

    expect(applyFrame(state, { event: "unknown" })).toBe(state);

    const withUnknownStage = frame(state, {
      event: "progress",
      stage: "internal_node",
      status: "done",
    });
    expect(withUnknownStage.progress).toEqual([]);
  });

  it("ignores unsupported progress status", () => {
    const state = frame(begin(), {
      event: "progress",
      stage: "checking_order",
      status: "running",
    });
    expect(state.progress).toEqual([]);
  });

  it("does not regress a waiting stage", () => {
    let state = begin();
    state = frame(state, {
      event: "progress",
      stage: "waiting_info",
      status: "waiting",
      pending_question: "order_id",
    });
    state = frame(state, {
      event: "progress",
      stage: "waiting_info",
      status: "done",
    });

    expect(state.progress).toEqual([
      {
        stage: "waiting_info",
        status: "waiting",
        pendingQuestion: "order_id",
      },
    ]);
  });

  it("error stops in flight and keeps the user message", () => {
    let state = begin();
    state = frame(state, {
      event: "error",
      code: "timeout",
      message: "服务响应超时",
    });

    expect(state.inFlight).toBe(false);
    expect(state.error).toBe("服务响应超时");
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].role).toBe("user");
  });

  it("retry reuses the last user message and client id", () => {
    let state = begin();
    state = frame(state, { event: "error", message: "网络异常" });

    state = turnReducer(state, { type: "retry" });

    expect(state.inFlight).toBe(true);
    expect(state.error).toBeNull();
    expect(state.interrupted).toBe(false);
    expect(state.messages).toHaveLength(1);
    expect(state.lastClientMessageId).toBe("cm-1");
  });

  it("retry drops the unfinished assistant draft of the current turn", () => {
    let state = begin();
    state = frame(state, {
      event: "ack",
      conversation_id: 12,
      user_message_id: 34,
    });
    state = frame(state, { delta: "部分回答" });
    state = turnReducer(state, {
      type: "interrupt",
      message: "连接中断，请点击继续",
    });

    state = turnReducer(state, { type: "retry" });

    expect(state.messages.map((message) => message.role)).toEqual(["user"]);
    expect(state.messages[0].content).toBe("订单1002 不想要了");
    expect(state.interrupted).toBe(false);
  });

  it("interrupt keeps local content and offers continue", () => {
    let state = begin();
    state = frame(state, { delta: "部分回答" });

    state = turnReducer(state, {
      type: "interrupt",
      message: "连接中断，请点击继续",
    });

    expect(state.inFlight).toBe(false);
    expect(state.interrupted).toBe(true);
    expect(state.error).toBe("连接中断，请点击继续");
    expect(state.messages[1].content).toBe("部分回答");
  });

  it("keeps selected_action for retry", () => {
    const selected = { type: "select_order", order_id: "1002" };
    let state = turnReducer(initialTurnState(), {
      type: "begin",
      text: "选择订单 1002",
      clientMessageId: "cm-2",
      selectedAction: selected,
    });

    expect(state.lastSelectedAction).toEqual(selected);

    state = frame(state, { event: "error", message: "网络异常" });
    state = turnReducer(state, { type: "retry" });

    expect(state.lastSelectedAction).toEqual(selected);
    expect(state.messages).toHaveLength(1);
  });

  it("abort stops without an error banner", () => {
    let state = begin();
    state = turnReducer(state, { type: "abort" });

    expect(state.inFlight).toBe(false);
    expect(state.error).toBeNull();
  });

  it("reset clears the conversation", () => {
    let state = begin();
    state = frame(state, { event: "error", message: "会话不存在" });

    state = turnReducer(state, { type: "reset" });

    expect(state).toEqual(initialTurnState());
  });
});

describe("newClientMessageId", () => {
  it("returns non-empty unique ids", () => {
    const first = newClientMessageId();
    const second = newClientMessageId();

    expect(first).toBeTruthy();
    expect(second).toBeTruthy();
    expect(first).not.toBe(second);
  });
});
