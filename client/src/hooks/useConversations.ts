import { useEffect, useState, type Dispatch } from "react";

import {
  getMessages,
  listConversations,
  type ConversationSummary,
} from "../lib/api";
import { ApiError } from "../lib/base";
import {
  forgetConversationId,
  getStorage,
  rememberConversationId,
} from "../lib/storage";
import { buildHistoryState, readConversationId } from "../state/reconnect";
import type { TurnAction } from "../state/turn";

/**
 * 会话列表 + 历史加载：
 * 启动时恢复上次会话；切换会话时把历史灌进聊天状态。
 */
export function useConversations(dispatch: Dispatch<TurnAction>) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationLoading, setConversationLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        const list = await listConversations();
        if (cancelled) {
          return;
        }
        setConversations(list.items);

        const preferred = readConversationId(
          window.location.search,
          getStorage(),
        );
        const target = preferred ?? list.items[0]?.id ?? null;
        if (target === null) {
          return;
        }

        const history = await getMessages(target);
        if (!cancelled) {
          dispatch({ type: "history", state: buildHistoryState(history) });
          rememberConversationId(target);
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          forgetConversationId();
        }
        // 历史接口不可用时不阻塞新对话。
      } finally {
        if (!cancelled) {
          setConversationLoading(false);
        }
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  async function refreshConversations(): Promise<void> {
    try {
      const list = await listConversations();
      setConversations(list.items);
    } catch {
      // 列表是辅助信息；失败不影响当前对话。
    }
  }

  async function loadConversation(id: number): Promise<void> {
    setConversationLoading(true);
    try {
      const history = await getMessages(id);
      dispatch({ type: "history", state: buildHistoryState(history) });
      rememberConversationId(id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        dispatch({ type: "reset" });
        dispatch({ type: "fail", message: "会话不存在，请重新开始。" });
        forgetConversationId();
        void refreshConversations();
      } else {
        dispatch({ type: "fail", message: "历史会话加载失败，请稍后重试。" });
      }
    } finally {
      setConversationLoading(false);
    }
  }

  return {
    conversations,
    conversationLoading,
    refreshConversations,
    loadConversation,
  };
}
