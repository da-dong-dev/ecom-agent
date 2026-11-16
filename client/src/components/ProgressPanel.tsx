import { Card, Flex, Grid, Steps, Tag, Typography } from "antd";

import { formatAmount } from "../lib/format";
import type { Progress } from "../state/turn";

type Props = {
  route: string | null;
  progress: Progress[];
  inFlight: boolean;
};

type StepStatus = "wait" | "process" | "finish" | "error";

type PanelStep = {
  key: string;
  title: string;
  status: StepStatus;
};

const ROUTE_LABELS: Record<string, string> = {
  knowledge: "知识问答",
  data: "订单查询",
  rules: "退款核对",
  human: "转人工",
  fallback: "兜底处理",
};

function statusOf(
  item: Progress | undefined,
  fallback: StepStatus,
): StepStatus {
  if (!item) {
    return fallback;
  }
  if (item.status === "done") {
    return "finish";
  }
  if (item.status === "waiting" || item.status === "running") {
    return "process";
  }
  return "error";
}

function buildSteps(
  route: string | null,
  progress: Progress[],
  inFlight: boolean,
): PanelStep[] {
  const byStage = new Map(progress.map((item) => [item.stage, item]));
  const amount = byStage.get("amount_ready")?.amount;
  const amountTitle =
    amount === undefined ? "已算出金额" : `已算出金额 ${formatAmount(amount)}`;

  if (route === "rules") {
    const steps: PanelStep[] = [];
    if (byStage.has("checking_order") || !byStage.has("waiting_info")) {
      steps.push({
        key: "checking_order",
        title: byStage.has("checking_order") ? "已核对订单" : "正在核对订单",
        status: statusOf(
          byStage.get("checking_order"),
          inFlight ? "process" : "wait",
        ),
      });
    }

    if (byStage.has("waiting_info")) {
      steps.push({
        key: "waiting_info",
        title: "等待您补充信息",
        status: statusOf(byStage.get("waiting_info"), "process"),
      });
      return steps;
    }

    if (byStage.has("handoff")) {
      steps.push({
        key: "handoff",
        title: "正在转接人工",
        status: statusOf(byStage.get("handoff"), "process"),
      });
      return steps;
    }

    if (byStage.has("amount_ready")) {
      steps.push({
        key: "amount_ready",
        title: amountTitle,
        status: statusOf(byStage.get("amount_ready"), "finish"),
      });
      if (byStage.has("waiting_confirm")) {
        steps.push({
          key: "waiting_confirm",
          title: "等待您确认",
          status: statusOf(byStage.get("waiting_confirm"), "process"),
        });
      } else if (inFlight) {
        steps.push({
          key: "waiting_confirm",
          title: "等待您确认",
          status: "process",
        });
      }
      return steps;
    }

    if (byStage.has("answering")) {
      steps.push({
        key: "answering",
        title: "已整理回答",
        status: statusOf(byStage.get("answering"), "finish"),
      });
    }
    return steps;
  }

  if (route === "knowledge") {
    const steps: PanelStep[] = [
      {
        key: "retrieving",
        title: byStage.has("retrieving") ? "已检索知识库" : "正在检索知识库",
        status: statusOf(
          byStage.get("retrieving"),
          inFlight ? "process" : "wait",
        ),
      },
    ];
    if (byStage.has("evidence_ready")) {
      steps.push({
        key: "evidence_ready",
        title: "已找到依据",
        status: "finish",
      });
    }
    if (byStage.has("answering")) {
      steps.push({ key: "answering", title: "已整理回答", status: "finish" });
    }
    if (byStage.has("handoff")) {
      steps.push({
        key: "handoff",
        title: "正在转接人工",
        status: "process",
      });
    }
    return steps;
  }

  if (route === "data") {
    const steps: PanelStep[] = [];
    if (byStage.has("querying_data") || !byStage.has("waiting_info")) {
      steps.push({
        key: "querying_data",
        title: byStage.has("querying_data") ? "已查到数据" : "正在查询数据",
        status: statusOf(
          byStage.get("querying_data"),
          inFlight ? "process" : "wait",
        ),
      });
    }
    if (byStage.has("waiting_info")) {
      steps.push({
        key: "waiting_info",
        title: "等待您补充信息",
        status: "process",
      });
    }
    if (byStage.has("data_ready")) {
      steps.push({ key: "data_ready", title: "已整理回答", status: "finish" });
    }
    if (byStage.has("handoff")) {
      steps.push({
        key: "handoff",
        title: "正在转接人工",
        status: "process",
      });
    }
    return steps;
  }

  if (route === "fallback") {
    const steps: PanelStep[] = [
      {
        key: "working",
        title: byStage.has("working") ? "已处理" : "正在处理",
        status: statusOf(byStage.get("working"), inFlight ? "process" : "wait"),
      },
    ];
    if (byStage.has("handoff")) {
      steps.push({
        key: "handoff",
        title: "正在转接人工",
        status: "process",
      });
    }
    return steps;
  }

  if (route === "human") {
    return [
      {
        key: "handoff",
        title: "正在转接人工",
        status: statusOf(byStage.get("handoff"), "process"),
      },
    ];
  }

  if (inFlight) {
    return [{ key: "understanding", title: "正在理解", status: "process" }];
  }
  return [];
}

export function ProgressPanel({ route, progress, inFlight }: Props) {
  const screens = Grid.useBreakpoint();
  const compact = screens.md === false;
  const steps = buildSteps(route, progress, inFlight);

  if (steps.length === 0) {
    return null;
  }

  const current = steps.findIndex(
    (step) => step.status === "process" || step.status === "error",
  );
  const currentIndex = current >= 0 ? current : steps.length;
  const active = steps.find(
    (step) => step.status === "process" || step.status === "error",
  );

  return (
    <Card
      size="small"
      title="处理进度"
      variant="borderless"
      className="progress-card"
    >
      {compact ? (
        <Flex justify="space-between" align="center" gap={8}>
          <Tag color="blue">{ROUTE_LABELS[route ?? ""] ?? "处理中"}</Tag>
          <Typography.Text>{active?.title ?? "处理中"}</Typography.Text>
        </Flex>
      ) : (
        <Steps
          direction="vertical"
          size="small"
          current={currentIndex}
          items={steps.map((step) => ({
            title: step.title,
            status: step.status,
          }))}
        />
      )}
    </Card>
  );
}
