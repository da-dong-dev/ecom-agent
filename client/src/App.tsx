import { Flex, Tag, Typography } from "antd";
import { useReducer, useRef, useState } from "react";

import { ChatWindow } from "./components/ChatWindow";
import { Composer } from "./components/Composer";
import { ApiError } from "./lib/base";
import { sendChat } from "./lib/sse";
import {
  initialTurnState,
  newClientMessageId,
  turnReducer,
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
  ): Promise<void> {
    if (retry) {
      dispatch({ type: "retry" });
    } else {
      dispatch({ type: "begin", text, clientMessageId });
    }

    const controller = new AbortController();
    abortRef.current = controller;
    let streamError: string | null = null;

    try {
      await sendChat({
        message: text,
        conversationId: state.conversationId,
        clientMessageId,
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
    try {
      localStorage.setItem("lastClientMessageId", clientMessageId);
    } catch {
      // localStorage 不可用时只影响调试，不影响发送
    }
    await runChat(text, clientMessageId, false);
  }

  async function handleRetry(): Promise<void> {
    const lastUser = [...state.messages]
      .reverse()
      .find((message) => message.role === "user");
    if (!lastUser || !state.lastClientMessageId) {
      return;
    }
    await runChat(lastUser.content, state.lastClientMessageId, true);
  }

  function handleStop() {
    abortRef.current?.abort();
    dispatch({ type: "abort" });
  }

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

      <ChatWindow
        messages={state.messages}
        inFlight={state.inFlight}
        error={state.error}
        retryDisabled={cooldown || state.messages.length === 0}
        onRetry={() => {
          void handleRetry();
        }}
      />

      <Composer
        inFlight={state.inFlight}
        disabled={cooldown}
        onSend={handleSend}
        onStop={handleStop}
      />
    </main>
  );
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
