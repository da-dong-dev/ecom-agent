import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
} from "@ant-design/icons";
import { Card, Flex, Grid, Space, Steps, Tag, Typography } from "antd";
import { Fragment } from "react";

import { formatAmount } from "../lib/format";
import type { Progress } from "../state/turn";

type Props = {
  route: string | null;
  progress: Progress[];
  inFlight: boolean;
  /** 嵌在聊天消息下方时使用紧凑横排，不渲染 Card。 */
  embedded?: boolean;
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
        title: "已转接人工，等待接入",
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
        title: "已转接人工，等待接入",
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
        title: "已转接人工，等待接入",
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
        title: "已转接人工，等待接入",
        status: "process",
      });
    }
    return steps;
  }

  if (route === "human") {
    return [
      {
        key: "handoff",
        title: "已转接人工",
        status: "finish",
      },
    ];
  }

  if (inFlight) {
    return [{ key: "understanding", title: "正在理解", status: "process" }];
  }
  return [];
}

export function inlineSteps(
  route: string | null,
  progress: Progress[],
  inFlight: boolean,
): PanelStep[] {
  const steps = buildSteps(route, progress, inFlight);
  if (steps.length === 0) {
    return [];
  }
  const embeddedSteps = steps.filter(
    (step) => step.key !== "working" && step.key !== "understanding",
  );
  if (
    embeddedSteps.length === 0 ||
    embeddedSteps.some((step) => step.key === "waiting_info")
  ) {
    return [];
  }
  return embeddedSteps;
}

export function hasInlineProgress(
  route: string | null,
  progress: Progress[],
  inFlight: boolean,
): boolean {
  return inlineSteps(route, progress, inFlight).length > 0;
}

export function inlineProgressLabel(
  route: string | null,
  progress: Progress[],
  inFlight: boolean,
): string | null {
  const steps = inlineSteps(route, progress, inFlight);
  if (steps.length === 0) {
    return null;
  }
  return (
    steps.find((step) => step.status === "process")?.title ??
    steps[steps.length - 1].title
  );
}

export function ProgressPanel({
  route,
  progress,
  inFlight,
  embedded = false,
}: Props) {
  const screens = Grid.useBreakpoint();
  const compact = screens.md === false;
  const steps = buildSteps(route, progress, inFlight);

  if (steps.length === 0) {
    return null;
  }

  if (embedded) {
    // “等待您补充信息”已经由助手问题和选项表达；
    // “正在理解/正在处理”与打字提示重复。内联进度只展示真正的处理步骤。
    const embeddedSteps = inlineSteps(route, progress, inFlight);
    if (embeddedSteps.length === 0) {
      return null;
    }

    return (
      <div className="progress-inline">
        <span className="progress-inline-label">处理进度</span>
        <div className="progress-inline-steps">
          {embeddedSteps.map((step, index) => (
            <Fragment key={step.key}>
              {index > 0 ? (
                <span className="progress-inline-sep">→</span>
              ) : null}
              <span className={`progress-inline-step ${step.status}`}>
                {step.status === "finish" ? (
                  <CheckCircleOutlined />
                ) : step.status === "process" ? (
                  <LoadingOutlined />
                ) : (
                  <ClockCircleOutlined />
                )}
                {step.title}
              </span>
            </Fragment>
          ))}
        </div>
      </div>
    );
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
      title={
        <Space size={8}>
          {inFlight ? <LoadingOutlined /> : <CheckCircleOutlined />}
          处理进度
        </Space>
      }
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
      {route === "human" ? (
        <Typography.Text type="secondary" className="progress-note">
          转接请求已提交，等待人工接入期间可以继续描述问题。
        </Typography.Text>
      ) : null}
    </Card>
  );
}
