import { Avatar, Flex, Typography } from "antd";

import type { ChatMessage } from "../state/turn";

type Props = {
  message: ChatMessage;
};

export function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";
  const citationIds = message.citationIds ?? [];

  return (
    <Flex
      className="message-row"
      justify={isUser ? "flex-end" : "flex-start"}
      align="flex-start"
      gap={8}
    >
      {isUser ? null : <Avatar size="small">客</Avatar>}

      <div
        className={`message-bubble ${
          isUser ? "message-user" : "message-assistant"
        }`}
      >
        <Typography.Text
          className="message-role"
          type={isUser ? undefined : "secondary"}
        >
          {isUser ? "我" : "客服"}
        </Typography.Text>
        <div className="message-content">
          {renderContent(message.content, citationIds)}
        </div>
      </div>

      {isUser ? (
        <Avatar size="small" style={{ backgroundColor: "#2563eb" }}>
          我
        </Avatar>
      ) : null}
    </Flex>
  );
}

/** 纯文本渲染；[1] 角标用 citation_ids 的来源标题做 tooltip，不解析 HTML。 */
function renderContent(content: string, citationIds: string[]) {
  const parts = content.split(/(\[\d+\])/g);
  return parts.map((part, index) => {
    const match = /^\[(\d+)\]$/.exec(part);
    if (!match) {
      return <span key={index}>{part}</span>;
    }

    const citation = citationIds[Number(match[1]) - 1] ?? "";
    const title = citation.split("#")[1] ?? citation;
    return (
      <sup key={index} title={title}>
        {part}
      </sup>
    );
  });
}
