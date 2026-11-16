import { useReducer, useRef, useState } from "react";

import { getMessages } from "../lib/api";
import { actionLabel } from "../lib/actionLabel";
import { ApiError } from "../lib/base";
import { sendChat } from "../lib/sse";
import {
  forgetConversationId,
  rememberClientMessageId,
  rememberConversationId,
} from "../lib/storage";
import { reconcileHistory } from "../state/reconnect";
import {
  initialTurnState,
  newClientMessageId,
  turnReducer,
  type AssistantAction,
} from "../state/turn";
import { useConversations } from "./useConversations";

/**
 * 一轮聊天会话的状态与副作用：
 * SSE 发送、断流对账、停止/重试/动作。
 *
 * 会话列表与历史加载在 `useConversations`；App 只负责布局。
 */
export function useChatSession() {
  const [state, dispatch] = useReducer(turnReducer, initialTurnState());
  const [cooldown, setCooldown] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const {
    conversations,
    conversationLoading,
    refreshConversations,
    loadConversation,
  } = useConversations(dispatch);

  /**
   * 断流对账：服务端已存回复就整段替换本地草稿；没有就保留本地并提示“继续”。
   * 历史接口失败时只报网络错误，不丢本地消息。
   */
  async function recoverAfterDisconnect(
    conversationId: number | null,
    clientMessageId: string,
  ): Promise<"completed" | "pending" | "failed"> {
    if (conversationId === null) {
      dispatch({ type: "interrupt", message: "连接中断，请点击继续" });
      return "pending";
    }

    try {
      const history = await getMessages(conversationId);
      const result = reconcileHistory(history, clientMessageId);
      if (result.status === "completed") {
        dispatch({ type: "history", state: result.state });
        rememberConversationId(history.conversation_id);
        void refreshConversations();
        return "completed";
      }

      // 服务端还没有这条回复：保留本地已收到的内容，等用户点“继续”。
      dispatch({ type: "interrupt", message: "连接中断，请点击继续" });
      return "pending";
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        dispatch({ type: "reset" });
        dispatch({ type: "fail", message: "会话不存在，请重新开始。" });
        forgetConversationId();
        void refreshConversations();
        return "failed";
      }
      dispatch({ type: "fail", message: "连接中断，历史恢复失败，请重试" });
      return "failed";
    }
  }

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
    let activeConversationId = state.conversationId;
    let sawDone = false;
    let streamError: string | null = null;

    try {
      await sendChat({
        message: text,
        conversationId: state.conversationId,
        clientMessageId,
        selectedAction,
        signal: controller.signal,
        onFrame: (frame) => {
          if (frame.event === "ack" && typeof frame.conversation_id === "number") {
            activeConversationId = frame.conversation_id;
            rememberConversationId(frame.conversation_id);
          }
          if (frame.event === "error" && typeof frame.message === "string") {
            streamError = frame.message;
          }
          if (frame.event === "done") {
            sawDone = true;
          }
          dispatch({ type: "frame", frame });
          if (frame.event === "done") {
            void refreshConversations();
          }
        },
      });
      if (streamError !== null) {
        throw new ApiError(0, "stream_error", streamError);
      }
      if (!sawDone) {
        // 流提前结束（没有 done）：先向服务端对账，再决定替换草稿还是显示“继续”。
        await recoverAfterDisconnect(activeConversationId, clientMessageId);
      }
    } catch (error) {
      if (controller.signal.aborted) {
        dispatch({ type: "interrupt", message: "已停止，可继续" });
        return;
      }

      if (error instanceof ApiError && error.status === 404) {
        dispatch({ type: "reset" });
        dispatch({ type: "fail", message: "会话不存在，请重新开始。" });
        forgetConversationId();
        void refreshConversations();
        throw error;
      }

      if (error instanceof ApiError && error.status === 429) {
        setCooldown(true);
        window.setTimeout(() => setCooldown(false), 5000);
        dispatch({ type: "fail", message: error.message });
        throw error;
      }

      if (error instanceof ApiError) {
        // HTTP 错误 / 显式 error 帧：后端给了明确失败，不要伪装成“断流可继续”。
        dispatch({ type: "fail", message: error.message });
        throw error;
      }

      // 网络断开：本地内容保留，尝试拉历史对账；失败时 recoverAfterDisconnect 已写 error。
      await recoverAfterDisconnect(activeConversationId, clientMessageId);
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
    if (abortRef.current) {
      abortRef.current.abort();
    } else {
      dispatch({ type: "interrupt", message: "已停止，可继续" });
    }
  }

  function handleNewConversation() {
    if (state.inFlight) {
      return;
    }
    dispatch({ type: "reset" });
    forgetConversationId();
  }

  function handleSelectConversation(id: number) {
    if (state.inFlight || id === state.conversationId) {
      return;
    }
    void loadConversation(id);
  }

  const lastAssistant = [...state.messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const lastUser = [...state.messages]
    .reverse()
    .find((message) => message.role === "user");
  const visibleActions = (lastAssistant?.actions ?? []).filter((action) => {
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

  return {
    state,
    conversations,
    conversationLoading,
    cooldown,
    visibleActions,
    showSide,
    handleSend,
    handleRetry,
    handleAction,
    handleStop,
    handleNewConversation,
    handleSelectConversation,
  };
}
