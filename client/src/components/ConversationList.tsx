import {
  DeleteOutlined,
  HistoryOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Card, List, Popconfirm, Space, Tag, Typography } from "antd";

import { formatConversationTime } from "../lib/time";

import type { ConversationSummary } from "../lib/api";

type Props = {
  items: ConversationSummary[];
  activeId: number | null;
  loading: boolean;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete?: (id: number) => void;
  /** 回复中：列表只读，避免切换会话打断当前 SSE。 */
  disabled?: boolean;
};

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
  onDelete,
  disabled = false,
}: Props) {
  return (
    <Card
      size="small"
      title={
        <Space size={8}>
          <HistoryOutlined />
          历史会话
        </Space>
      }
      variant="borderless"
      className="conversation-card"
      extra={
        <Button
          type="primary"
          size="small"
          icon={<PlusOutlined />}
          aria-label="新对话"
          disabled={disabled}
          onClick={onNew}
          className="new-conversation-btn"
        >
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
            } ${disabled ? "disabled" : ""}`}
            aria-disabled={disabled}
            title={disabled ? "正在回复中，请稍候" : undefined}
            onClick={() => {
              if (!disabled) {
                onSelect(item.id);
              }
            }}
          >
            <div className="conversation-body">
              <span className="conversation-title" data-testid="conversation-title">
                {item.title}
              </span>
              <div className="conversation-meta">
                <Typography.Text type="secondary" className="conversation-time">
                  {formatConversationTime(item.updated_at)}
                </Typography.Text>
                {flowLabel(item) ? (
                  <Tag color="orange">{flowLabel(item)}</Tag>
                ) : null}
              </div>
            </div>

            {onDelete ? (
              <span
                className="conversation-actions"
                onClick={(event) => event.stopPropagation()}
              >
                <Popconfirm
                  title="删除这个会话？"
                  description="删除后不会出现在历史列表。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onDelete(item.id)}
                >
                  <Button
                    type="text"
                    size="small"
                    danger
                    disabled={disabled}
                    aria-label="删除会话"
                    icon={<DeleteOutlined aria-hidden />}
                  />
                </Popconfirm>
              </span>
            ) : null}
          </List.Item>
        )}
      />
    </Card>
  );
}
