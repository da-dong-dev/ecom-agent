"""Ch08 进度帧：把 LangGraph 子图节点映射成用户能理解的语义阶段。

设计约束（L4）：

- 后端只发语义 `stage`，**不发节点名、规则 id、置信度、工具链**；
- 前端负责中文文案；
- 进度只描述“已经发生的事”，不用定时器假装；
- 映射失败返回 `None`，不能因为一条进度让聊天失败。
"""
from typing import Any

# 后端只发语义 `stage`；这份中文对照给文档、测试和前端文案参考，
# **不随 SSE 下发**——前端仍然只认 stage，避免把展示文案和后端耦合。
STAGE_LABELS: dict[str, str] = {
    "checking_order": "已核对订单",
    "amount_ready": "已算出金额",
    "waiting_info": "等待您补充信息",
    "waiting_confirm": "等待您确认",
    "answering": "已整理回答",
    "querying_data": "已查到数据",
    "data_ready": "已整理回答",
    "retrieving": "已检索知识库",
    "evidence_ready": "已找到依据",
    "working": "已处理",
    "handoff": "正在转接人工",
}


def _frame(
    stage: str,
    *,
    status: str = "done",
    amount: int | None = None,
    pending_question: str | None = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "event": "progress",
        "stage": stage,
        "status": status,
    }
    if amount is not None:
        frame["amount"] = amount
    if pending_question is not None:
        frame["pending_question"] = pending_question
    return frame


def _eligible_amount(state: dict[str, Any]) -> int | None:
    """退款可退且金额有效时才返回金额（单位：分）。"""
    judgement = state.get("refund_judgement") or {}
    amount = state.get("refund_amount")
    if not judgement.get("eligible"):
        return None
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        return None
    return amount


def node_frame(
    node_name: str,
    update: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """一个子图节点完成后对应的 progress 帧；没有就返回 `None`。"""
    if node_name == "query_data":
        # 已核对订单
        return _frame("checking_order") if update.get("order_snapshot") else None

    if node_name == "compliance":
        # 已算出金额（只有可退且合规才发）
        if update.get("compliance_violation") is not None:
            return None
        amount = _eligible_amount(state)
        return _frame("amount_ready", amount=amount) if amount is not None else None

    if node_name == "need_info":
        # 等待您补充信息
        pending = update.get("pending_question") or state.get("pending_question")
        if pending:
            return _frame(
                "waiting_info", status="waiting", pending_question=pending
            )
        return None

    if node_name == "query":
        # 等待补充规格 / 已查到数据
        pending = update.get("pending_question")
        if pending:
            return _frame(
                "waiting_info", status="waiting", pending_question=pending
            )
        return _frame("querying_data") if update.get("data_result") else None

    if node_name == "format":
        # 已整理回答
        return _frame("data_ready") if update.get("answer") else None

    if node_name == "retrieve":
        # 已检索知识库
        return _frame("retrieving") if "evidence" in update else None

    if node_name == "judge_evidence":
        # 已找到依据（只有强证据才发）
        return (
            _frame("evidence_ready")
            if update.get("evidence_strong") is True
            else None
        )

    if node_name == "generate":
        # 已整理回答
        return _frame("answering") if update.get("answer") else None

    if node_name in {"gate", "agent", "scripted"}:
        # 已处理
        return _frame("working")

    if node_name == "human":
        # 正在转接人工
        return _frame("handoff", status="waiting")

    return None


def final_frame(
    *,
    route: str | None,
    actions: list[dict[str, Any]] | None,
    signals: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """回答就绪、落库成功后的最终进度；只发一条。"""
    actions = actions or []
    signals = signals or {}

    if any(action.get("type") == "transfer_human" for action in actions):
        # 正在转接人工
        return _frame("handoff", status="waiting")

    pending = signals.get("pending_question")
    if pending:
        # 等待您补充信息
        return _frame("waiting_info", status="waiting", pending_question=pending)

    amount = signals.get("amount")
    amount_ok = (
        isinstance(amount, int) and not isinstance(amount, bool) and amount > 0
    )

    if route == "rules":
        if amount_ok:
            # 等待您确认
            return _frame("waiting_confirm", status="waiting", amount=amount)
        # 已整理回答（不可退 / 无金额）
        return _frame("answering")
    if route == "data":
        # 已整理回答
        return _frame("data_ready")
    if route == "knowledge":
        # 已整理回答
        return _frame("answering")
    if route == "fallback":
        # 已处理
        return _frame("working")
    if route == "human":
        # 正在转接人工
        return _frame("handoff", status="waiting")
    return None
