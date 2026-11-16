import type { AssistantAction } from "../state/turn";

/** 动作卡片点击后回填到输入框的文本；结构化动作仍会随 selected_action 一起发送。 */
export function actionLabel(action: AssistantAction): string {
  switch (action.type) {
    case "transfer_human":
      return "转人工";
    case "select_order":
      return `选择订单 ${String(action.order_id ?? "")}`.trim();
    case "select_sku":
      return `选择型号 ${String(action.sku_id ?? "")}`.trim();
    case "select_spec":
      return `选择规格 ${String(action.spec ?? "")}`.trim();
    case "resume_flow":
      return action.flow === "refund" ? "继续退费" : "继续";
    default:
      return action.type;
  }
}
