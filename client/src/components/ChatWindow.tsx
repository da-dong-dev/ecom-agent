import {
  Alert,
  Avatar,
  Button,
  Card,
  Empty,
  Flex,
  Typography,
} from "antd";
import { CustomerServiceOutlined } from "@ant-design/icons";
import { Fragment, useEffect, useRef, type ReactNode } from "react";

import type { FeedbackRating } from "../lib/api";
import type { AssistantAction, ChatMessage } from "../state/turn";
import { ActionBar } from "./ActionBar";
import { MessageBubble } from "./MessageBubble";

export type ChatSuggestion = {
  label: string;
  text: string;
};

type Props = {
  messages: ChatMessage[];
  inFlight: boolean;
  error: string | null;
  /** 断流/用户停止：内容保留，按钮文案改为“继续”。 */
  interrupted?: boolean;
  retryDisabled?: boolean;
  suggestions?: ChatSuggestion[];
  progress?: ReactNode;
  progressLabel?: string | null;
  actions?: AssistantAction[];
  actionDisabled?: boolean;
  feedbackByMessage?: Record<number, FeedbackRating>;
  onSuggestion?: (text: string) => void;
  onAction?: (action: AssistantAction) => void;
  onFeedback?: (message: ChatMessage, rating: FeedbackRating) => void;
  onRetry: () => void;
};

export function ChatWindow({
  messages,
  inFlight,
  error,
  interrupted = false,
  retryDisabled = false,
  suggestions = [],
  progress = null,
  progressLabel = null,
  actions = [],
  actionDisabled = false,
  feedbackByMessage = {},
  onSuggestion,
  onAction,
  onFeedback,
  onRetry,
}: Props) {
  const showWelcome = messages.length === 0 && suggestions.length > 0;
  const listRef = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);
  const lastAssistantIndex = messages.reduce(
    (found, message, index) =>
      message.role === "assistant" ? index : found,
    -1,
  );
  const lastMessageIsAssistant =
    messages[messages.length - 1]?.role === "assistant";
  const showActions =
    actions.length > 0 && onAction !== undefined && lastMessageIsAssistant;
  const showInlineProgress =
    Boolean(progress) && !inFlight && lastMessageIsAssistant;

  useEffect(() => {
    const list = listRef.current;
    if (!list) {
      return;
    }
    // 用户刚发出消息时强制回到最新位置；否则只在接近底部时跟随。
    if (messages[messages.length - 1]?.role === "user") {
      stickToBottom.current = true;
    }
    if (stickToBottom.current) {
      list.scrollTop = list.scrollHeight;
    }
  }, [messages, inFlight, actions, progress, progressLabel]);

  function handleScroll() {
    const list = listRef.current;
    if (!list) {
      return;
    }
    stickToBottom.current =
      list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  }

  return (
    <Card className="chat-card" variant="borderless">
      {!showWelcome ? (
        <div className="chat-header">
          <Avatar
            size={42}
            className="chat-header-avatar"
            icon={<CustomerServiceOutlined />}
          />
          <div className="chat-header-copy">
            <Typography.Text strong className="chat-header-title">
              售后客服
            </Typography.Text>
            <Typography.Text className="chat-header-sub">
              <span className="status-dot" />
              智能客服 · 通常几秒内回复
            </Typography.Text>
          </div>
        </div>
      ) : null}

      <div
        className="message-list"
        aria-live="polite"
        ref={listRef}
        onScroll={handleScroll}
      >
        {showWelcome ? (
          <div className="welcome">
            <div className="welcome-avatar">
              <CustomerServiceOutlined />
            </div>
            <Typography.Title level={4} className="welcome-title">
              您好，我是售后客服
            </Typography.Title>
            <Typography.Text type="secondary" className="welcome-sub">
              可以帮您查订单、算退款、解释售后政策。
            </Typography.Text>
            <div className="suggestion-grid">
              {suggestions.map((item) => (
                <Button
                  key={item.label}
                  className="suggestion-card"
                  disabled={inFlight}
                  onClick={() => onSuggestion?.(item.text)}
                >
                  <span className="suggestion-label">{item.label}</span>
                  <span className="suggestion-text">{item.text}</span>
                </Button>
              ))}
            </div>
          </div>
        ) : messages.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="有什么可以帮您？"
          />
        ) : (
          messages.map((message, index) => (
            <Fragment key={message.id ?? `${message.role}-${index}`}>
              <MessageBubble
                message={message}
                feedback={
                  message.id === undefined
                    ? undefined
                    : feedbackByMessage[message.id]
                }
                feedbackDisabled={inFlight}
                onFeedback={
                  onFeedback
                    ? (rating) => onFeedback(message, rating)
                    : undefined
                }
              />
              {showActions && index === lastAssistantIndex ? (
                <ActionBar
                  actions={actions}
                  disabled={actionDisabled}
                  embedded
                  onAction={onAction}
                />
              ) : null}
              {showInlineProgress && index === lastAssistantIndex ? (
                <div className="inline-progress">{progress}</div>
              ) : null}
            </Fragment>
          ))
        )}

        {inFlight ? (
          <Flex
            className="message-row pending-row"
            justify="flex-start"
            align="flex-start"
            gap={8}
          >
            <Avatar size="small">客</Avatar>
            <div className="message-bubble message-assistant pending-bubble">
              <div className="message-meta">
                <span className="message-role">客服</span>
                <span className="message-time">正在处理</span>
              </div>
              <div className="pending-content">
                <span className="typing-dots" aria-hidden>
                  <i />
                  <i />
                  <i />
                </span>
                <span>{progressLabel ?? "正在处理…"}</span>
              </div>
            </div>
          </Flex>
        ) : null}
      </div>

      {error ? (
        <Alert
          type={interrupted ? "warning" : "error"}
          showIcon
          message={error}
          action={
            <Button size="small" onClick={onRetry} disabled={retryDisabled}>
              {interrupted ? "继续" : "重试"}
            </Button>
          }
        />
      ) : null}
    </Card>
  );
}
