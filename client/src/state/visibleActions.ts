import type { AssistantAction, ChatMessage } from "./turn";

/**
 * 当前轮要展示的动作。
 *
 * 转人工只展示一次：
 * - 已经走过 D 出口（route=human）时，不再给“转人工”按钮；
 * - 用户已经手动发过「转人工」时，也不重复。
 */
export function visibleActionsFor(
  messages: ChatMessage[],
  route: string | null,
  handoffStarted = false,
): AssistantAction[] {
  const lastAssistant = [...messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const lastUser = [...messages]
    .reverse()
    .find((message) => message.role === "user");

  return (lastAssistant?.actions ?? []).filter((action) => {
    if (action.type !== "transfer_human") {
      return true;
    }
    if (handoffStarted || route === "human") {
      return false;
    }
    return lastUser?.content !== "转人工";
  });
}
