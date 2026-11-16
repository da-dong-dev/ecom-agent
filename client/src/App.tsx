import { Flex, Tag, Typography } from "antd";

import { ActionBar } from "./components/ActionBar";
import { ChatWindow } from "./components/ChatWindow";
import { Composer } from "./components/Composer";
import { ConversationList } from "./components/ConversationList";
import { ProgressPanel } from "./components/ProgressPanel";
import { useChatSession } from "./hooks/useChatSession";

const ROUTE_LABELS: Record<string, string> = {
  knowledge: "知识问答",
  data: "订单查询",
  rules: "退款核对",
  human: "转人工",
  fallback: "兜底处理",
};

export default function App() {
  const {
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
  } = useChatSession();

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
          <Tag color="blue">{ROUTE_LABELS[state.route] ?? state.route}</Tag>
        ) : null}
      </Flex>

      <div className="workspace">
        <aside className="conversation-panel">
          <ConversationList
            items={conversations}
            activeId={state.conversationId}
            loading={conversationLoading}
            onSelect={handleSelectConversation}
            onNew={handleNewConversation}
          />
        </aside>

        <ChatWindow
          messages={state.messages}
          inFlight={state.inFlight}
          error={state.error}
          interrupted={state.interrupted}
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
