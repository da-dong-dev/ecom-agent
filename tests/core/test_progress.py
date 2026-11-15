"""Ch08 进度映射：节点 → 用户可理解的语义 stage。

只测纯函数；不发 SSE、不查库。
"""
from app.core.progress import STAGE_LABELS, final_frame, node_frame


def test_stage_labels_cover_every_stage():
    """中文对照是文档/前端文案的参考，不能漏 stage。"""
    assert STAGE_LABELS == {
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


def test_rules_nodes_map_to_three_states():
    state = {
        "refund_judgement": {"eligible": True},
        "refund_amount": 124900,
    }

    assert node_frame(
        "query_data", {"order_snapshot": {"order_id": "1002"}}, state
    ) == {
        "event": "progress",
        "stage": "checking_order",
        "status": "done",
    }
    assert node_frame("compliance", {"compliance_violation": None}, state) == {
        "event": "progress",
        "stage": "amount_ready",
        "status": "done",
        "amount": 124900,
    }
    assert node_frame(
        "need_info", {"pending_question": "order_id"}, state
    ) == {
        "event": "progress",
        "stage": "waiting_info",
        "status": "waiting",
        "pending_question": "order_id",
    }


def test_amount_frame_is_suppressed_on_violation_or_ineligible():
    eligible = {
        "refund_judgement": {"eligible": True},
        "refund_amount": 124900,
    }
    assert (
        node_frame("compliance", {"compliance_violation": "red_line"}, eligible)
        is None
    )
    assert (
        node_frame(
            "compliance",
            {"compliance_violation": None},
            {"refund_judgement": {"eligible": False}, "refund_amount": 124900},
        )
        is None
    )
    assert (
        node_frame(
            "compliance",
            {"compliance_violation": None},
            {"refund_judgement": {"eligible": True}, "refund_amount": 0},
        )
        is None
    )


def test_other_routes_map_to_semantic_stages():
    assert node_frame("retrieve", {"evidence": []}, {}) == {
        "event": "progress",
        "stage": "retrieving",
        "status": "done",
    }
    assert node_frame("judge_evidence", {"evidence_strong": True}, {}) == {
        "event": "progress",
        "stage": "evidence_ready",
        "status": "done",
    }
    assert node_frame("judge_evidence", {"evidence_strong": False}, {}) is None
    assert node_frame("generate", {"answer": "回答"}, {}) == {
        "event": "progress",
        "stage": "answering",
        "status": "done",
    }
    assert node_frame("query", {"data_result": {"order_id": "1"}}, {}) == {
        "event": "progress",
        "stage": "querying_data",
        "status": "done",
    }
    assert node_frame("format", {"answer": "回答"}, {}) == {
        "event": "progress",
        "stage": "data_ready",
        "status": "done",
    }
    assert node_frame("gate", {"needs_tools": False}, {}) == {
        "event": "progress",
        "stage": "working",
        "status": "done",
    }
    assert node_frame("human", {}, {}) == {
        "event": "progress",
        "stage": "handoff",
        "status": "waiting",
    }


def test_final_frame_precedence():
    amount_signals = {
        "amount": 124900,
        "pending_question": None,
        "refund_eligible": True,
    }

    assert final_frame(
        route="rules",
        actions=[{"type": "transfer_human"}],
        signals=amount_signals,
    ) == {
        "event": "progress",
        "stage": "handoff",
        "status": "waiting",
    }
    assert final_frame(
        route="rules",
        actions=[],
        signals={"pending_question": "order_id"},
    ) == {
        "event": "progress",
        "stage": "waiting_info",
        "status": "waiting",
        "pending_question": "order_id",
    }
    assert final_frame(
        route="rules", actions=[], signals=amount_signals
    ) == {
        "event": "progress",
        "stage": "waiting_confirm",
        "status": "waiting",
        "amount": 124900,
    }
    assert final_frame(route="rules", actions=[], signals={}) == {
        "event": "progress",
        "stage": "answering",
        "status": "done",
    }
    assert final_frame(route="data", actions=[], signals={}) == {
        "event": "progress",
        "stage": "data_ready",
        "status": "done",
    }
    assert final_frame(route="knowledge", actions=[], signals={}) == {
        "event": "progress",
        "stage": "answering",
        "status": "done",
    }
    assert final_frame(route="fallback", actions=[], signals={}) == {
        "event": "progress",
        "stage": "working",
        "status": "done",
    }
    assert final_frame(route="human", actions=[], signals={}) == {
        "event": "progress",
        "stage": "handoff",
        "status": "waiting",
    }
    assert final_frame(route=None, actions=[], signals={}) is None


def test_bool_amount_is_not_treated_as_money():
    assert final_frame(
        route="rules", actions=[], signals={"amount": True}
    ) == {
        "event": "progress",
        "stage": "answering",
        "status": "done",
    }


def test_frames_only_contain_whitelisted_keys():
    frame = node_frame("query_data", {"order_snapshot": {"order_id": "1"}}, {})
    assert frame is not None
    assert set(frame) == {"event", "stage", "status"}

    frame = final_frame(route="rules", actions=[], signals={"amount": 100})
    assert frame is not None
    assert set(frame) == {"event", "stage", "status", "amount"}
