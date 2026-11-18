/** 一轮对话的状态 reducer：SSE 帧 → 消息/进度/错误。 */
import type { ChatFrame } from "../lib/sse";

export type Role = "user" | "assistant";

export type AssistantAction = {
  type: string;
  [key: string]: unknown;
};

export type CitationDetail = {
  id: string;
  section_path?: string;
  text?: string;
  source?: string;
};

export type ChatMessage = {
  id?: number;
  clientMessageId?: string;
  role: Role;
  content: string;
  createdAt?: string;
  status?: string | null;
  citationIds?: string[];
  citations?: CitationDetail[];
  actions?: AssistantAction[];
};

export type ProgressStatus = "running" | "done" | "waiting" | "error";

export type Progress = {
  stage: string;
  status: ProgressStatus;
  amount?: number;
  pendingQuestion?: string;
};

export type TurnState = {
  conversationId: number | null;
  inFlight: boolean;
  messages: ChatMessage[];
  progress: Progress[];
  route: string | null;
  error: string | null;
  /** 连接中断/用户停止：内容保留，允许用同一个 client_message_id 继续。 */
  interrupted: boolean;
  /** 本会话已经真正转接人工；后续不再重复展示“转人工”按钮。 */
  handoffStarted: boolean;
  lastClientMessageId: string | null;
  /** 重试结构化动作时，必须复用同一个 selected_action。 */
  lastSelectedAction: AssistantAction | null;
};

export type TurnAction =
  | {
      type: "begin";
      text: string;
      clientMessageId: string;
      selectedAction?: AssistantAction | null;
    }
  | { type: "retry" }
  | { type: "frame"; frame: ChatFrame }
  | { type: "history"; state: TurnState }
  | { type: "fail"; message: string }
  | { type: "interrupt"; message?: string }
  | { type: "abort" }
  | { type: "reset" };

/** 后端 progress 的合法 stage；未知 stage 直接忽略。 */
export const KNOWN_STAGES = new Set<string>([
  "checking_order",
  "amount_ready",
  "waiting_info",
  "waiting_confirm",
  "answering",
  "querying_data",
  "data_ready",
  "retrieving",
  "evidence_ready",
  "working",
  "handoff",
]);

export function initialTurnState(): TurnState {
  return {
    conversationId: null,
    inFlight: false,
    messages: [],
    progress: [],
    route: null,
    error: null,
    interrupted: false,
    handoffStarted: false,
    lastClientMessageId: null,
    lastSelectedAction: null,
  };
}

/** 同一条消息重试必须复用这个 id；不要每次重试重新生成。 */
export function newClientMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `cm-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function lastUserIndex(messages: ChatMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "user") {
      return index;
    }
  }
  return -1;
}

/** 只找“当前用户消息之后”的助手消息，避免把新一轮 delta 追加到上一轮回答上。 */
function lastAssistantAfterLastUserIndex(messages: ChatMessage[]): number {
  const userIndex = lastUserIndex(messages);
  if (userIndex < 0) {
    return -1;
  }
  for (let index = messages.length - 1; index > userIndex; index -= 1) {
    if (messages[index].role === "assistant") {
      return index;
    }
  }
  return -1;
}

/** 重试前丢掉当前轮未完成的助手草稿，避免回放 delta 时叠在旧草稿上。 */
function dropAssistantAfterLastUser(messages: ChatMessage[]): ChatMessage[] {
  const userIndex = lastUserIndex(messages);
  return userIndex < 0 ? messages : messages.slice(0, userIndex + 1);
}

function appendDelta(state: TurnState, delta: string): TurnState {
  const index = lastAssistantAfterLastUserIndex(state.messages);
  if (index < 0) {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          role: "assistant",
          content: delta,
          createdAt: new Date().toISOString(),
        },
      ],
    };
  }

  const messages = [...state.messages];
  messages[index] = {
    ...messages[index],
    content: messages[index].content + delta,
  };
  return { ...state, messages };
}

function patchAssistant(
  state: TurnState,
  patch: Partial<ChatMessage>,
): TurnState {
  const index = lastAssistantAfterLastUserIndex(state.messages);
  if (index < 0) {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          role: "assistant",
          content: "",
          createdAt: new Date().toISOString(),
          ...patch,
        },
      ],
    };
  }

  const messages = [...state.messages];
  messages[index] = { ...messages[index], ...patch };
  return { ...state, messages };
}

export function isCitationDetail(value: unknown): value is CitationDetail {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { id?: unknown }).id === "string"
  );
}

export function isAssistantAction(
  value: unknown,
): value is AssistantAction {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { type?: unknown }).type === "string"
  );
}

export function applyFrame(state: TurnState, frame: ChatFrame): TurnState {
  if (typeof frame.delta === "string") {
    return appendDelta(state, frame.delta);
  }

  switch (frame.event) {
    case "ack": {
      const conversationId =
        typeof frame.conversation_id === "number"
          ? frame.conversation_id
          : state.conversationId;
      const userMessageId =
        typeof frame.user_message_id === "number"
          ? frame.user_message_id
          : undefined;
      const index = lastUserIndex(state.messages);
      const messages =
        index < 0 || userMessageId === undefined
          ? state.messages
          : state.messages.map((message, messageIndex) =>
              messageIndex === index
                ? { ...message, id: userMessageId }
                : message,
            );
      return { ...state, conversationId, messages };
    }

    case "route": {
      const route = typeof frame.route === "string" ? frame.route : state.route;
      return {
        ...state,
        route,
        handoffStarted: state.handoffStarted || route === "human",
      };
    }

    case "progress": {
      const stage = typeof frame.stage === "string" ? frame.stage : null;
      const status = frame.status;
      if (
        stage === null ||
        !KNOWN_STAGES.has(stage) ||
        (status !== "done" && status !== "waiting" && status !== "error")
      ) {
        return state;
      }

      const existing = state.progress.find((item) => item.stage === stage);
      // waiting 是终态：后续同 stage 的更新不再覆盖它。
      if (existing?.status === "waiting") {
        return state;
      }

      const next: Progress = {
        stage,
        status,
        amount:
          typeof frame.amount === "number" && Number.isFinite(frame.amount)
            ? frame.amount
            : existing?.amount,
        pendingQuestion:
          typeof frame.pending_question === "string"
            ? frame.pending_question
            : existing?.pendingQuestion,
      };
      const progress = existing
        ? state.progress.map((item) => (item.stage === stage ? next : item))
        : [...state.progress, next];
      return { ...state, progress };
    }

    case "citations": {
      const citationIds = Array.isArray(frame.items)
        ? frame.items.filter((item): item is string => typeof item === "string")
        : [];
      const citations = Array.isArray(frame.details)
        ? frame.details.filter(isCitationDetail)
        : undefined;
      return patchAssistant(state, {
        citationIds,
        ...(citations ? { citations } : {}),
      });
    }

    case "actions": {
      const actions = Array.isArray(frame.items)
        ? frame.items.filter(isAssistantAction)
        : [];
      // fallback 的 transfer_human 只是“提供按钮”，点之前不算已转接；
      // rules/human 的 transfer_human 才是实际转接。
      const actualHandoff =
        actions.some((action) => action.type === "transfer_human") &&
        state.route !== "fallback";
      return {
        ...patchAssistant(state, { actions }),
        handoffStarted: state.handoffStarted || actualHandoff,
      };
    }

    case "done": {
      const id =
        typeof frame.message_id === "number" ? frame.message_id : undefined;
      return patchAssistant(
        { ...state, inFlight: false, error: null, interrupted: false },
        { id },
      );
    }

    case "error": {
      const message =
        typeof frame.message === "string"
          ? frame.message
          : "服务暂时不可用，请稍后重试";
      return {
        ...state,
        inFlight: false,
        error: message,
        interrupted: false,
      };
    }

    default:
      return state;
  }
}

export function turnReducer(state: TurnState, action: TurnAction): TurnState {
  switch (action.type) {
    case "begin":
      return {
        ...state,
        inFlight: true,
        error: null,
        interrupted: false,
        route: null,
        progress: [],
        lastClientMessageId: action.clientMessageId,
        lastSelectedAction: action.selectedAction ?? null,
        messages: [
          ...state.messages,
          {
            role: "user",
            content: action.text,
            clientMessageId: action.clientMessageId,
            createdAt: new Date().toISOString(),
          },
        ],
      };

    case "retry":
      // 重试复用最后一条用户消息，不再新增气泡；丢掉上一轮未完成的助手草稿。
      return {
        ...state,
        inFlight: true,
        error: null,
        interrupted: false,
        route: null,
        progress: [],
        messages: dropAssistantAfterLastUser(state.messages),
      };

    case "frame":
      return applyFrame(state, action.frame);

    case "history":
      return action.state;

    case "fail":
      return {
        ...state,
        inFlight: false,
        error: action.message,
        interrupted: false,
      };

    case "interrupt":
      return {
        ...state,
        inFlight: false,
        error: action.message ?? "连接中断，请点击继续",
        interrupted: true,
      };

    case "abort":
      return { ...state, inFlight: false, interrupted: false };

    case "reset":
      return initialTurnState();

    default:
      return state;
  }
}
