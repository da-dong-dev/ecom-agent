import { Alert, Button, Card, Empty, Flex, Spin, Typography } from "antd";

import type { ChatMessage } from "../state/turn";
import { MessageBubble } from "./MessageBubble";

type Props = {
  messages: ChatMessage[];
  inFlight: boolean;
  error: string | null;
  /** 断流/用户停止：内容保留，按钮文案改为“继续”。 */
  interrupted?: boolean;
  retryDisabled?: boolean;
  onRetry: () => void;
};

export function ChatWindow({
  messages,
  inFlight,
  error,
  interrupted = false,
  retryDisabled = false,
  onRetry,
}: Props) {
  return (
    <Card className="chat-card" variant="borderless">
      <div className="message-list" aria-live="polite">
        {messages.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="有什么可以帮您？"
          />
        ) : (
          messages.map((message, index) => (
            <MessageBubble
              key={message.id ?? `${message.role}-${index}`}
              message={message}
            />
          ))
        )}

        {inFlight ? (
          <Flex className="typing-hint" align="center" gap={8}>
            <Spin size="small" />
            <Typography.Text type="secondary">正在处理…</Typography.Text>
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
