import { Avatar, Flex, Popover } from "antd";

import { formatMessageTime } from "../lib/time";
import type { ChatMessage, CitationDetail } from "../state/turn";

type Props = {
  message: ChatMessage;
};

export function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";
  const citationIds = message.citationIds ?? [];
  const citations = message.citations ?? [];
  const time = formatMessageTime(message.createdAt);

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
        <div className="message-meta">
          <span className="message-role">{isUser ? "我" : "客服"}</span>
          {time ? <span className="message-time">{time}</span> : null}
        </div>
        <div className="message-content">
          {renderContent(message.content, citationIds, citations)}
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

/** 纯文本渲染；[1] 角标点开可以看来源，不解析 HTML。 */
function renderContent(
  content: string,
  citationIds: string[],
  citations: CitationDetail[],
) {
  const parts = content.split(/(\[\d+\])/g);
  return parts.map((part, index) => {
    const match = /^\[(\d+)\]$/.exec(part);
    if (!match) {
      return <span key={index}>{part}</span>;
    }

    const number = Number(match[1]);
    const citationId = citationIds[number - 1] ?? "";
    const detail = citations.find((item) => item.id === citationId);
    const title =
      detail?.section_path || citationId.split("#")[1] || citationId || "来源";
    const contentNode = detail?.text ? (
      <div className="citation-popover">
        <p>{formatCitationText(detail.text, title)}</p>
        {detail.source ? (
          <span className="citation-source">来源：{detail.source}</span>
        ) : null}
      </div>
    ) : (
      <span>来源：{citationId || "知识库"}</span>
    );

    return (
      <Popover
        key={index}
        trigger={["hover", "click"]}
        title={title}
        content={contentNode}
        styles={{ body: { maxWidth: 420, maxHeight: 320, overflow: "auto" } }}
      >
        <sup className="citation-ref" role="button" tabIndex={0}>
          {part}
        </sup>
      </Popover>
    );
  });
}


function formatCitationText(text: string, title: string): string {
  const cleaned = text.replace(/\*\*/g, "").replace(/\n- /g, "\n• ");
  const lines = cleaned.split("\n");
  if (lines[0]?.trim() === title.trim()) {
    lines.shift();
  }
  return lines.join("\n").trim();
}
