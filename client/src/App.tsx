import { Flex, Tag, Typography } from "antd";
import { useReducer, useRef, useState } from "react";

import { ActionBar } from "./components/ActionBar";
import { ChatWindow } from "./components/ChatWindow";
import { Composer } from "./components/Composer";
import { ProgressPanel } from "./components/ProgressPanel";
import { ApiError } from "./lib/base";
import { sendChat } from "./lib/sse";
import {
  initialTurnState,
  newClientMessageId,
  turnReducer,
  type AssistantAction,
} from "./state/turn";

const ROUTE_LABELS: Record<string, string> = {
  knowledge: "知识问答",
  data: "订单查询",
  rules: "退款核对",
  human: "转人工",
  fallback: "兜底处理",
};

export default function App() {
  const [state, dispatch] = useReducer(turnReducer, initialTurnState());
  const [cooldown, setCooldown] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  async function runChat(
    text: string,
    clientMessageId: string,
    retry: boolean,
    selectedAction: AssistantAction | null = null,
  ): Promise<void> {
    if (retry) {
      dispatch({ type: "retry" });
    } else {
      dispatch({ type: "begin", text, clientMessageId, selectedAction });
    }

    const controller = new AbortController();
    abortRef.current = controller;
    let streamError: string | null = null;

    try {
      await sendChat({
        message: text,
        conversationId: state.conversationId,
        clientMessageId,
        selectedAction,
        signal: controller.signal,
        onFrame: (frame) => {
          if (frame.event === "error" && typeof frame.message === "string") {
            streamError = frame.message;
          }
          dispatch({ type: "frame", frame });
        },
      });
      if (streamError !== null) {
        throw new ApiError(0, "stream_error", streamError);
      }
    } catch (error) {
      if (controller.signal.aborted) {
        dispatch({ type: "abort" });
        throw error;
      }

      if (error instanceof ApiError && error.status === 404) {
        dispatch({ type: "reset" });
        dispatch({ type: "fail", message: "会话不存在，请重新开始。" });
        throw error;
      }

      if (error instanceof ApiError && error.status === 429) {
        setCooldown(true);
        window.setTimeout(() => setCooldown(false), 5000);
      }

      dispatch({ type: "fail", message: errorMessage(error) });
      throw error;
    } finally {
      abortRef.current = null;
    }
  }

  async function handleSend(text: string): Promise<void> {
    const clientMessageId = newClientMessageId();
    rememberClientMessageId(clientMessageId);
    await runChat(text, clientMessageId, false, null);
  }

  async function handleRetry(): Promise<void> {
    const lastUser = [...state.messages]
      .reverse()
      .find((message) => message.role === "user");
    if (!lastUser || !state.lastClientMessageId) {
      return;
    }

    try {
      await runChat(
        lastUser.content,
        state.lastClientMessageId,
        true,
        state.lastSelectedAction,
      );
    } catch {
      // runChat 已经写入 error；这里只需要避免未处理的 Promise rejection。
    }
  }

  async function handleAction(action: AssistantAction): Promise<void> {
    if (state.inFlight) {
      return;
    }

    const text = actionLabel(action);
    const clientMessageId = newClientMessageId();
    rememberClientMessageId(clientMessageId);
    const selectedAction = action.type === "resume_flow" ? null : action;

    try {
      await runChat(text, clientMessageId, false, selectedAction);
    } catch {
      // runChat 已经写入 error；动作卡片没有输入框需要恢复。
    }
  }

  function handleStop() {
    abortRef.current?.abort();
    dispatch({ type: "abort" });
  }

  const lastAssistant = [...state.messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const lastUser = [...state.messages]
    .reverse()
    .find((message) => message.role === "user");
  const visibleActions = (lastAssistant?.actions ?? []).filter((action) => {
    // 转人工成功后的回复仍带 transfer_human；用户消息已经是“转人工”时不再重复展示按钮。
    if (action.type !== "transfer_human") {
      return true;
    }
    return lastUser?.content !== "转人工";
  });
  const showSide =
    Boolean(state.route) ||
    state.progress.length > 0 ||
    state.inFlight ||
    visibleActions.length > 0;

  return (
    <main className="app-shell">
      <Flex
        className="app-header"
        justify="space-between"
        align="flex-start"
        gap={16}
      >
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            电商客服 Agent
          </Typography.Title>
          <Typography.Text type="secondary">
            退款 / 订单 / 政策咨询
          </Typography.Text>
        </div>
        {state.route ? (
          <Tag color="blue">
            {ROUTE_LABELS[state.route] ?? state.route}
          </Tag>
        ) : null}
      </Flex>

      <div className="workspace">
        <ChatWindow
          messages={state.messages}
          inFlight={state.inFlight}
          error={state.error}
          retryDisabled={cooldown || state.messages.length === 0}
          onRetry={() => {
            void handleRetry();
          }}
        />

        {showSide ? (
          <aside className="side-panel">
            <ProgressPanel
              route={state.route}
              progress={state.progress}
              inFlight={state.inFlight}
            />
            <ActionBar
              actions={visibleActions}
              disabled={state.inFlight || cooldown}
              onAction={(action) => {
                void handleAction(action);
              }}
            />
          </aside>
        ) : null}
      </div>

      <Composer
        inFlight={state.inFlight}
        disabled={cooldown}
        onSend={handleSend}
        onStop={handleStop}
      />
    </main>
  );
}

function rememberClientMessageId(clientMessageId: string): void {
  try {
    localStorage.setItem("lastClientMessageId", clientMessageId);
  } catch {
    // localStorage 不可用时只影响调试，不影响发送
  }
}

function actionLabel(action: AssistantAction): string {
  switch (action.type) {
    case "transfer_human":
      return "转人工";
    case "select_order":
      return `选择订单 ${String(action.order_id ?? "")}`.trim();
    case "select_sku":
      return `选择型号 ${String(action.sku_id ?? "")}`.trim();
    case "select_spec":
      return `选择规格 ${String(action.spec ?? "")}`.trim();
    case "resume_flow":
      return action.flow === "refund" ? "继续退费" : "继续";
    default:
      return action.type;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return "已停止";
  }
  return "网络异常，请稍后重试";
}
