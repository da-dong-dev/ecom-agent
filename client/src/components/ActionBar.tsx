import {
  ArrowRightOutlined,
  RightOutlined,
  ShoppingOutlined,
} from "@ant-design/icons";
import { Button, Card, Input, Modal, Space, Tag } from "antd";
import { Fragment, useState, type ReactNode } from "react";

import { formatAmount } from "../lib/format";
import type { AssistantAction } from "../state/turn";

type Props = {
  actions: AssistantAction[];
  disabled?: boolean;
  /** 嵌在聊天气泡下方时去掉 Card 外壳和“下一步”标题。 */
  embedded?: boolean;
  onAction: (action: AssistantAction) => void;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function candidates(action: AssistantAction): Record<string, unknown>[] {
  return Array.isArray(action.candidates)
    ? action.candidates.filter(isRecord)
    : [];
}

function flowLabel(flow: unknown): string {
  if (flow === "refund") {
    return "退款";
  }
  if (flow === "data") {
    return "查询";
  }
  return "";
}

const GENERIC_REASONS = new Set([
  "其他",
  "其他原因",
  "别的",
  "无",
  "没有",
  "不知道",
  "随便",
  "不想说",
]);

function reasonCode(action: AssistantAction): string {
  return typeof action.reason_code === "string" ? action.reason_code : "";
}

function statusColor(status: string): string {
  if (status === "待发货") {
    return "orange";
  }
  if (status === "已发货") {
    return "blue";
  }
  if (status === "已签收") {
    return "green";
  }
  return "default";
}

function reasonLabel(action: AssistantAction): string {
  if (typeof action.label === "string") {
    return action.label;
  }
  if (reasonCode(action) === "quality") {
    return "商品有质量问题";
  }
  if (reasonCode(action) === "no_reason") {
    return "不想要了";
  }
  return "其他原因";
}

function renderAction(
  action: AssistantAction,
  disabled: boolean,
  onAction: (action: AssistantAction) => void,
  openOtherReason: () => void,
): ReactNode[] {
  switch (action.type) {
    case "transfer_human":
      return [
        <Button
          key="transfer"
          type="primary"
          danger
          disabled={disabled}
          onClick={() => onAction({ type: "transfer_human" })}
        >
          转人工
        </Button>,
      ];

    case "resume_flow":
      return [
        <Button
          key="resume"
          type="primary"
          disabled={disabled}
          onClick={() => onAction(action)}
        >
          继续{flowLabel(action.flow)}
        </Button>,
      ];

    case "select_reason": {
      const code = reasonCode(action);
      const label = reasonLabel(action);
      if (code === "other") {
        return [
          <Button
            key="reason-other"
            disabled={disabled}
            onClick={openOtherReason}
            className="option-card option-reason reason-other"
          >
            <span className="option-title">{label}</span>
            <RightOutlined aria-hidden className="option-arrow" />
          </Button>,
        ];
      }
      return [
        <Button
          key={`reason-${code}`}
          disabled={disabled}
          onClick={() =>
            onAction({
              type: "select_reason",
              reason_code: code,
              label,
            })
          }
          className="option-card option-reason"
        >
          <span className="option-title">{label}</span>
          <RightOutlined aria-hidden className="option-arrow" />
        </Button>,
      ];
    }

    case "select_order": {
      const items = candidates(action);
      if (items.length === 0) {
        return [
          <Button key="order" disabled={disabled} onClick={() => onAction(action)}>
            选择订单
          </Button>,
        ];
      }
      return items.map((item, index) => {
        const orderId = String(item.order_id ?? "");
        const title = typeof item.title === "string" ? item.title : orderId;
        const status = typeof item.status === "string" ? item.status : "";
        const amount =
          typeof item.amount_text === "string" ? item.amount_text : "";
        return (
          <Button
            key={`order-${orderId}-${index}`}
            disabled={disabled}
            onClick={() =>
              onAction({ type: "select_order", order_id: orderId })
            }
            className="option-card option-order"
          >
            <span className="option-icon">
              <ShoppingOutlined aria-hidden />
            </span>
            <span className="option-copy">
              <span className="option-title">{title}</span>
              <span className="option-meta">
                {status ? (
                  <Tag color={statusColor(status)} bordered={false}>
                    {status}
                  </Tag>
                ) : null}
                {amount ? (
                  <span className="option-amount">{amount}</span>
                ) : null}
              </span>
            </span>
            <RightOutlined aria-hidden className="option-arrow" />
          </Button>
        );
      });
    }

    case "select_sku": {
      const items = candidates(action);
      if (items.length === 0) {
        return [
          <Button key="sku" disabled={disabled} onClick={() => onAction(action)}>
            选择型号
          </Button>,
        ];
      }
      return items.map((item, index) => {
        const skuId = String(item.sku_id ?? "");
        return (
          <Button
            key={`sku-${skuId}-${index}`}
            disabled={disabled}
            onClick={() => onAction({ type: "select_sku", sku_id: skuId })}
            className="option-card"
          >
            {skuId}
          </Button>
        );
      });
    }

    case "select_spec": {
      const items = candidates(action);
      if (items.length === 0) {
        return [
          <Button
            key="spec"
            disabled={disabled}
            onClick={() => onAction(action)}
          >
            选择规格
          </Button>,
        ];
      }
      return items.map((item, index) => {
        const spec = String(item.spec ?? "");
        const price =
          typeof item.price === "number" ? ` · ${formatAmount(item.price)}` : "";
        const stock =
          typeof item.stock === "number" ? ` · 库存 ${item.stock}` : "";
        return (
          <Button
            key={`spec-${spec}-${index}`}
            disabled={disabled}
            onClick={() => onAction({ type: "select_spec", spec })}
            className="option-card"
          >
            {spec}
            {price}
            {stock}
          </Button>
        );
      });
    }

    default:
      return [];
  }
}

export function ActionBar({
  actions,
  disabled = false,
  embedded = false,
  onAction,
}: Props) {
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherReason, setOtherReason] = useState("");
  const [otherError, setOtherError] = useState("");

  function submitOtherReason() {
    const reason = otherReason.trim();
    if (!reason) {
      return;
    }
    const normalized = reason.replace(/[\s，,。.!！?？]/g, "");
    if (GENERIC_REASONS.has(normalized)) {
      setOtherError("请补充具体原因，不要只填「其他」");
      return;
    }
    setOtherError("");
    setOtherOpen(false);
    setOtherReason("");
    onAction({
      type: "select_reason",
      reason_code: "other",
      label: "其他原因",
      reason_text: reason,
    });
  }

  const nodes = actions.flatMap((action, index) =>
    renderAction(action, disabled, onAction, () => setOtherOpen(true)).map(
      (node, nodeIndex) => (
        <Fragment key={`${index}-${nodeIndex}`}>{node}</Fragment>
      ),
    ),
  );

  if (nodes.length === 0) {
    return null;
  }

  const body = embedded ? (
    <Space direction="vertical" size={8} className="action-list">
      {nodes}
    </Space>
  ) : (
    <Space wrap>{nodes}</Space>
  );

  return (
    <>
      {embedded ? (
        <div className="action-inline">{body}</div>
      ) : (
        <Card
          size="small"
          title={
            <Space size={8}>
              <ArrowRightOutlined aria-hidden />
              下一步
            </Space>
          }
          variant="borderless"
          className="action-card"
        >
          {body}
        </Card>
      )}

      <Modal
        title="请说明其他原因"
        className="reason-modal"
        open={otherOpen}
        onCancel={() => {
          setOtherOpen(false);
          setOtherReason("");
          setOtherError("");
        }}
        onOk={submitOtherReason}
        okText="提交"
        cancelText="取消"
        okButtonProps={{ disabled: !otherReason.trim() }}
      >
        <Input.TextArea
          autoFocus
          value={otherReason}
          onChange={(event) => {
            setOtherReason(event.target.value);
            setOtherError("");
          }}
          placeholder="例如：尺码不合适、颜色和图片不一致、买重复了…"
          maxLength={200}
          status={otherError ? "error" : undefined}
          autoSize={{ minRows: 3, maxRows: 5 }}
        />
        <div className="reason-counter">{otherReason.length} / 200</div>
        {otherError ? <div className="reason-error">{otherError}</div> : null}
      </Modal>
    </>
  );
}
