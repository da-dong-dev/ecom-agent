import { Typography } from "antd";

import { ChatWindow } from "./components/ChatWindow";
import { Composer } from "./components/Composer";
import { ConversationList } from "./components/ConversationList";
import {
  inlineProgressLabel,
  ProgressPanel,
} from "./components/ProgressPanel";
import { useChatSession } from "./hooks/useChatSession";

const SUGGESTIONS = [
  { label: "查退款", text: "订单1002 不想要了" },
  { label: "问政策", text: "退货政策是什么" },
  { label: "找人工", text: "我要投诉你们的客服" },
];

export default function App() {
  const {
    state,
    conversations,
    conversationLoading,
    cooldown,
    visibleActions,
    handleSend,
    handleRetry,
    handleAction,
    handleStop,
    handleNewConversation,
    handleSelectConversation,
    handleDeleteConversation,
  } = useChatSession();

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand">
          <div className="brand-logo">客</div>
          <div>
            <Typography.Title level={3} className="brand-title">
              电商客服 Agent
            </Typography.Title>
            <Typography.Text className="brand-subtitle">
              退款 · 订单 · 政策咨询，一次说清
            </Typography.Text>
          </div>
        </div>
      </header>

      <div className="workspace">
        <aside className="conversation-panel">
          <ConversationList
            items={conversations}
            activeId={state.conversationId}
            loading={conversationLoading}
            onSelect={handleSelectConversation}
            onNew={handleNewConversation}
            onDelete={(id) => {
              void handleDeleteConversation(id);
            }}
          />
        </aside>

        <section className="chat-column">
          <ChatWindow
            messages={state.messages}
            inFlight={state.inFlight}
            error={state.error}
            interrupted={state.interrupted}
            retryDisabled={cooldown || state.messages.length === 0}
            suggestions={SUGGESTIONS}
            progress={
              <ProgressPanel
                route={state.route}
                progress={state.progress}
                inFlight={state.inFlight}
                embedded
              />
            }
            progressLabel={inlineProgressLabel(
              state.route,
              state.progress,
              state.inFlight,
            )}
            actions={visibleActions}
            actionDisabled={state.inFlight || cooldown}
            onSuggestion={(text) => {
              void handleSend(text);
            }}
            onAction={(action) => {
              void handleAction(action);
            }}
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
        </section>

      </div>
    </main>
  );
}
