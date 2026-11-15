import { Button, Card, Space } from "antd";
import { Fragment, type ReactNode } from "react";

import { formatAmount } from "../lib/format";
import type { AssistantAction } from "../state/turn";

type Props = {
  actions: AssistantAction[];
  disabled?: boolean;
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

function renderAction(
  action: AssistantAction,
  disabled: boolean,
  onAction: (action: AssistantAction) => void,
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
        const amount =
          typeof item.amount_text === "string" ? ` · ${item.amount_text}` : "";
        return (
          <Button
            key={`order-${orderId}-${index}`}
            disabled={disabled}
            onClick={() =>
              onAction({ type: "select_order", order_id: orderId })
            }
          >
            {title}
            {amount}
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

export function ActionBar({ actions, disabled = false, onAction }: Props) {
  const nodes = actions.flatMap((action, index) =>
    renderAction(action, disabled, onAction).map((node, nodeIndex) => (
      <Fragment key={`${index}-${nodeIndex}`}>{node}</Fragment>
    )),
  );

  if (nodes.length === 0) {
    return null;
  }

  return (
    <Card size="small" title="下一步" variant="borderless" className="action-card">
      <Space wrap>{nodes}</Space>
    </Card>
  );
}
