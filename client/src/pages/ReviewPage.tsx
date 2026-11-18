import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../lib/base";
import {
  approveReview,
  getReviewDetail,
  listReviewQueue,
  rejectReview,
  retryReview,
  type ReviewDetail,
  type ReviewItem,
  type ReviewStatus,
  type RetrievedHit,
} from "../lib/api";

const STATUS_LABEL: Record<ReviewStatus, string> = {
  pending: "待审核",
  merged: "已合并",
  approved: "已通过",
  written_back: "已写回",
  rejected: "已驳回",
};

const STATUS_COLOR: Record<ReviewStatus, string> = {
  pending: "gold",
  merged: "default",
  approved: "blue",
  written_back: "green",
  rejected: "red",
};

const STATUS_OPTIONS = [
  { label: "待审核", value: "pending" },
  { label: "已通过", value: "approved" },
  { label: "已写回", value: "written_back" },
  { label: "已驳回", value: "rejected" },
] as const;

function statusTag(status: ReviewStatus) {
  return <Tag color={STATUS_COLOR[status]}>{STATUS_LABEL[status]}</Tag>;
}

function RetrievedHits({ hits }: { hits: RetrievedHit[] | null }) {
  if (hits === null) {
    return (
      <Alert
        type="info"
        showIcon
        message="未走检索（NULL）"
        description="不是知识缺口，通常是规则类/反馈类问题。"
      />
    );
  }
  if (hits.length === 0) {
    return (
      <Alert
        type="warning"
        showIcon
        message="检索零命中（[]）"
        description="真正的知识缺口，应优先补 FAQ。"
      />
    );
  }
  return (
    <List
      size="small"
      dataSource={hits}
      renderItem={(hit) => (
        <List.Item>
          <div className="review-hit">
            <Space size={8} wrap>
              <Tag>{hit.id}</Tag>
              {hit.similarity !== null ? (
                <Typography.Text type="secondary">
                  similarity {hit.similarity.toFixed(3)}
                </Typography.Text>
              ) : null}
              <Typography.Text strong>{hit.section}</Typography.Text>
            </Space>
            <Typography.Paragraph
              type="secondary"
              ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}
              className="review-hit-text"
            >
              {hit.text}
            </Typography.Paragraph>
            <Typography.Text type="secondary">
              来源：{hit.source || "未知"}
            </Typography.Text>
          </div>
        </List.Item>
      )}
    />
  );
}

export function ReviewPage() {
  const [status, setStatus] = useState<ReviewStatus>("pending");
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [modalMode, setModalMode] = useState<"approve" | "reject" | null>(
    null,
  );
  const [answer, setAnswer] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadQueue = useCallback(async (nextStatus: ReviewStatus) => {
    setLoading(true);
    setError(null);
    try {
      const data = await listReviewQueue(nextStatus);
      setItems(data.items);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 404) {
        setError(
          "审核 API 未开启。请在 .env 设置 ENABLE_REVIEW_API=true 并重启后端。",
        );
      } else {
        setError(exc instanceof Error ? exc.message : "加载审核队列失败");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadQueue(status);
  }, [loadQueue, status]);

  async function openDetail(id: number) {
    setDrawerOpen(true);
    setDetailLoading(true);
    try {
      setDetail(await getReviewDetail(id));
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : "加载详情失败");
      setDrawerOpen(false);
    } finally {
      setDetailLoading(false);
    }
  }

  function openAction(mode: "approve" | "reject") {
    if (!detail) {
      return;
    }
    setModalMode(mode);
    setAnswer(detail.suggested_answer ?? "");
    setReason("");
  }

  async function refreshDetail(id: number) {
    try {
      setDetail(await getReviewDetail(id));
    } catch {
      setDrawerOpen(false);
    }
  }

  async function submitAction() {
    if (!detail || !modalMode) {
      return;
    }
    if (modalMode === "approve" && !answer.trim()) {
      message.error("请填写核准答案");
      return;
    }
    setSubmitting(true);
    try {
      const result =
        modalMode === "approve"
          ? await approveReview(detail.id, answer.trim())
          : await rejectReview(detail.id, reason.trim());
      if (result.status === "written_back") {
        message.success("已写回知识库");
      } else if (result.status === "rejected") {
        message.success("已驳回");
      } else {
        message.success("操作完成");
      }
      setModalMode(null);
      await loadQueue(status);
      await refreshDetail(detail.id);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : "操作失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function retrySync() {
    if (!detail) {
      return;
    }
    setSubmitting(true);
    try {
      const result = await retryReview(detail.id);
      message.success(
        result.status === "written_back" ? "同步成功" : "已是最新状态",
      );
      await loadQueue(status);
      await refreshDetail(detail.id);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : "重试失败");
    } finally {
      setSubmitting(false);
    }
  }

  const columns = [
    {
      title: "ID",
      dataIndex: "id",
      width: 70,
    },
    {
      title: "次数",
      dataIndex: "occurrence",
      width: 70,
    },
    {
      title: "标准问题",
      key: "question",
      render: (_: unknown, row: ReviewItem) => (
        <div>
          <Typography.Text strong>{row.normalized || row.question}</Typography.Text>
          <Typography.Text type="secondary" className="review-original">
            {row.normalized ? `原话：${row.question}` : "尚未标准化"}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: "建议答案",
      dataIndex: "suggested_answer",
      render: (value: string | null) => (
        <Typography.Text type="secondary" ellipsis>
          {value || "—"}
        </Typography.Text>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: ReviewStatus) => statusTag(value),
    },
    {
      title: "操作",
      width: 100,
      render: (_: unknown, row: ReviewItem) => (
        <Button type="link" onClick={() => void openDetail(row.id)}>
          {row.status === "pending" ? "审核" : "查看"}
        </Button>
      ),
    },
  ];

  return (
    <main className="review-shell">
      <header className="review-header">
        <div>
          <Typography.Title level={3} className="review-title">
            数据飞轮审核
          </Typography.Title>
          <Typography.Text type="secondary">
            低置信问题 → 标准化 → 人工审核 → FAQ 写回
          </Typography.Text>
        </div>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void loadQueue(status)}
          >
            刷新
          </Button>
          <Button href="/">返回客服</Button>
        </Space>
      </header>

      <Card className="review-card" variant="borderless">
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Segmented
            options={[...STATUS_OPTIONS]}
            value={status}
            onChange={(value) => setStatus(value as ReviewStatus)}
          />
          {error ? (
            <Alert
              type="error"
              showIcon
              message={error}
              action={
                <Button size="small" onClick={() => void loadQueue(status)}>
                  重试
                </Button>
              }
            />
          ) : null}
          <Table<ReviewItem>
            rowKey="id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            scroll={{ x: 860 }}
            locale={{ emptyText: <Empty description="没有待处理的问题" /> }}
          />
        </Space>
      </Card>

      <Drawer
        title={detail ? `审核 #${detail.id}` : "审核详情"}
        width="min(620px, 100vw)"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        loading={detailLoading}
        extra={
          detail ? (
            <Space>
              {detail.status === "pending" ? (
                <>
                  <Button type="primary" onClick={() => openAction("approve")}>
                    通过并写回
                  </Button>
                  <Button danger onClick={() => openAction("reject")}>
                    驳回
                  </Button>
                </>
              ) : null}
              {detail.status === "approved" ? (
                <Button loading={submitting} onClick={() => void retrySync()}>
                  重试同步
                </Button>
              ) : null}
            </Space>
          ) : null
        }
      >
        {detail ? (
          <Space direction="vertical" size={20} style={{ width: "100%" }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="状态">
                {statusTag(detail.status)}
              </Descriptions.Item>
              <Descriptions.Item label="标准问题">
                {detail.normalized || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="原始问题">
                {detail.question}
              </Descriptions.Item>
              <Descriptions.Item label="出现次数">
                {detail.occurrence}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {detail.source || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="进入原因">
                {detail.reason || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="建议答案">
                {detail.suggested_answer || "—"}
              </Descriptions.Item>
            </Descriptions>

            <div>
              <Typography.Title level={5}>检索快照</Typography.Title>
              <RetrievedHits hits={detail.retrieved} />
            </div>

            <div>
              <Typography.Title level={5}>
                归并原话（{detail.merged_questions.length}）
              </Typography.Title>
              {detail.merged_questions.length === 0 ? (
                <Typography.Text type="secondary">没有归并原话</Typography.Text>
              ) : (
                <List
                  dataSource={detail.merged_questions}
                  renderItem={(item) => (
                    <List.Item>
                      <div className="review-merged">
                        <Typography.Text strong>{item.question}</Typography.Text>
                        <Typography.Text type="secondary">
                          来源：{item.source || "—"} ｜ 原因：{item.reason || "—"}
                        </Typography.Text>
                        <RetrievedHits hits={item.retrieved} />
                      </div>
                    </List.Item>
                  )}
                />
              )}
            </div>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title={modalMode === "approve" ? "审核通过并写回" : "驳回问题"}
        open={modalMode !== null}
        onCancel={() => setModalMode(null)}
        onOk={() => void submitAction()}
        okText={modalMode === "approve" ? "通过并写回" : "确认驳回"}
        okButtonProps={{ danger: modalMode === "reject" }}
        confirmLoading={submitting}
        destroyOnHidden
      >
        {modalMode === "approve" ? (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              会写入 faq 并同步到 Chroma；同步失败可重试。
            </Typography.Text>
            <Input.TextArea
              rows={6}
              value={answer}
              maxLength={500}
              showCount
              placeholder="填写人工核准答案"
              onChange={(event) => setAnswer(event.target.value)}
            />
          </Space>
        ) : (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              驳回是终态，后续同义问题只累加次数，不会复活。
            </Typography.Text>
            <Input.TextArea
              rows={3}
              value={reason}
              maxLength={200}
              showCount
              placeholder="驳回原因（可选）"
              onChange={(event) => setReason(event.target.value)}
            />
          </Space>
        )}
      </Modal>
    </main>
  );
}
