import { Button, Card, List, Tag, Typography } from "antd";

import type { ConversationSummary } from "../lib/api";

type Props = {
  items: ConversationSummary[];
  activeId: number | null;
  loading: boolean;
  onSelect: (id: number) => void;
  onNew: () => void;
};

function formatUpdated(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function flowLabel(item: ConversationSummary): string | null {
  if (item.active_flow === "refund") {
    return "未完成退款";
  }
  if (item.active_flow === "data") {
    return "未完成查询";
  }
  if (item.pending_question) {
    return "等待补充";
  }
  return null;
}

export function ConversationList({
  items,
  activeId,
  loading,
  onSelect,
  onNew,
}: Props) {
  return (
    <Card
      size="small"
      title="历史会话"
      variant="borderless"
      className="conversation-card"
      extra={
        <Button type="link" size="small" onClick={onNew}>
          新对话
        </Button>
      }
    >
      <List
        size="small"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: "暂无历史会话" }}
        renderItem={(item) => (
          <List.Item
            className={`conversation-item ${
              item.id === activeId ? "active" : ""
            }`}
            onClick={() => onSelect(item.id)}
          >
            <div className="conversation-body">
              <span className="conversation-title" data-testid="conversation-title">
                {item.title}
              </span>
              <div className="conversation-meta">
                <Typography.Text type="secondary" className="conversation-time">
                  {formatUpdated(item.updated_at)}
                </Typography.Text>
                {flowLabel(item) ? (
                  <Tag color="orange">{flowLabel(item)}</Tag>
                ) : null}
              </div>
            </div>
          </List.Item>
        )}
      />
    </Card>
  );
}
