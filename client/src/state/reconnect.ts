/** 历史恢复：URL/localStorage 选会话 + 接口历史 → TurnState。 */
import type {
  ChatMessage as ApiChatMessage,
  ConversationHistory,
} from "../lib/api";
import {
  initialTurnState,
  isAssistantAction,
  isCitationDetail,
  type AssistantAction,
  type ChatMessage,
  type CitationDetail,
  type Progress,
  type TurnState,
} from "./turn";

function parseId(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** URL `?c=` 优先，其次 localStorage（Task 11 的断流恢复会复用）。 */
export function readConversationId(
  search: string,
  storage: Pick<Storage, "getItem"> | null,
): number | null {
  const params = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  return (
    parseId(params.get("c")) ??
    parseId(storage?.getItem("lastConversationId") ?? null)
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function toActions(value: unknown): AssistantAction[] {
  return Array.isArray(value) ? value.filter(isAssistantAction) : [];
}

function toCitations(value: unknown): CitationDetail[] {
  return Array.isArray(value) ? value.filter(isCitationDetail) : [];
}

function toUiMessage(message: ApiChatMessage): ChatMessage {
  if (message.role === "user") {
    return {
      id: message.id,
      role: "user",
      content: message.content,
      createdAt: message.created_at,
      clientMessageId: message.client_message_id ?? undefined,
    };
  }

  const meta = asRecord(message.meta);
  return {
    id: message.id,
    role: "assistant",
    content: message.content,
    createdAt: message.created_at,
    status: message.status ?? undefined,
    citationIds: toStringArray(meta.citation_ids),
    citations: toCitations(meta.citations),
    actions: toActions(meta.actions),
  };
}

function progressFromAssistant(
  route: string | null,
  meta: Record<string, unknown>,
): Progress[] {
  const actions = toActions(meta.actions);
  const signals = asRecord(meta.signals);
  const actualHandoff =
    signals.handoff === true ||
    route === "human" ||
    // 旧历史没有 signals.handoff：rules + transfer 动作视为已实际转接；
    // fallback 的 transfer 只是按钮，不算。
    (route === "rules" &&
      actions.some((action) => action.type === "transfer_human"));
  if (actualHandoff) {
    return [{ stage: "handoff", status: "waiting" }];
  }

  const pending =
    typeof signals.pending_question === "string"
      ? signals.pending_question
      : undefined;
  const amount =
    typeof signals.amount === "number" && Number.isFinite(signals.amount)
      ? signals.amount
      : undefined;

  if (route === "rules") {
    if (pending) {
      // 还停在追问阶段，订单可能还没查；不要伪造 checking_order done。
      return [
        { stage: "waiting_info", status: "waiting", pendingQuestion: pending },
      ];
    }
    if (amount !== undefined && amount > 0) {
      return [
        { stage: "checking_order", status: "done" },
        { stage: "amount_ready", status: "done", amount },
        { stage: "waiting_confirm", status: "waiting", amount },
      ];
    }
    return [
      { stage: "checking_order", status: "done" },
      { stage: "answering", status: "done" },
    ];
  }

  if (route === "knowledge") {
    return [
      { stage: "retrieving", status: "done" },
      { stage: "evidence_ready", status: "done" },
      { stage: "answering", status: "done" },
    ];
  }

  if (route === "data") {
    if (pending) {
      return [
        { stage: "waiting_info", status: "waiting", pendingQuestion: pending },
      ];
    }
    return [
      { stage: "querying_data", status: "done" },
      { stage: "data_ready", status: "done" },
    ];
  }

  if (route === "fallback") {
    return [{ stage: "working", status: "done" }];
  }
  if (route === "human") {
    return [{ stage: "handoff", status: "waiting" }];
  }
  return [];
}

export type ReconcileResult =
  | { status: "completed"; state: TurnState }
  | { status: "pending" };

/**
 * 断流后的服务端对账。
 *
 * 只认 `reply_to_client_message_id === 本地 client_message_id` 的成功助手消息：
 * 有就整段替换本地草稿（保留服务端最终措辞/动作/金额）；
 * 没有就返回 pending，由 UI 保留本地内容并显示“继续”。
 */
export function reconcileHistory(
  history: ConversationHistory,
  clientMessageId: string | null | undefined,
): ReconcileResult {
  if (!clientMessageId) {
    return { status: "pending" };
  }

  const reply = history.items.find(
    (message) =>
      message.role === "assistant" &&
      message.reply_to_client_message_id === clientMessageId &&
      message.status !== "error",
  );
  return reply
    ? { status: "completed", state: buildHistoryState(history) }
    : { status: "pending" };
}

/** 历史接口返回的消息 → 前端状态；路由/动作/最终进度都从最后一条 assistant meta 还原。 */
export function buildHistoryState(history: ConversationHistory): TurnState {
  const messages = history.items.map(toUiMessage);
  const lastAssistant = [...history.items]
    .reverse()
    .find((message) => message.role === "assistant");
  const lastUser = [...history.items]
    .reverse()
    .find((message) => message.role === "user");
  const assistantMeta = asRecord(lastAssistant?.meta);
  const route =
    typeof assistantMeta.route === "string" ? assistantMeta.route : null;

  const userMeta = asRecord(lastUser?.meta);
  const selected = userMeta.selected_action;
  const selectedAction = isAssistantAction(selected) ? selected : null;
  const handoffStarted = history.items.some((message) => {
    if (message.role !== "assistant") {
      return false;
    }
    const meta = asRecord(message.meta);
    const signals = asRecord(meta.signals);
    if (signals.handoff === true) {
      return true;
    }
    if (meta.route === "human") {
      return true;
    }
    // 旧历史没有 signals.handoff：rules + transfer 动作视为已实际转接；
    // fallback 的 transfer 只是按钮，不算。
    return (
      meta.route === "rules" &&
      toActions(meta.actions).some((action) => action.type === "transfer_human")
    );
  });

  return {
    ...initialTurnState(),
    conversationId: history.conversation_id,
    messages,
    route,
    progress: progressFromAssistant(route, assistantMeta),
    handoffStarted,
    lastClientMessageId: lastUser?.client_message_id ?? null,
    lastSelectedAction: selectedAction,
  };
}
