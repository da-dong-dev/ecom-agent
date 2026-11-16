"""`/api/chat` 的帧顺序、消息落库与错误映射。

两层：

1. `run_turn`（业务层）——注入假图，验证帧顺序、会话/消息 id、落库
2. HTTP 层——验证 SSE 编码、错误帧、以及异常不外泄

**都离线**：假图不调模型，`record_span` 被替换成记录器；消息写临时库。
"""
import asyncio
import json
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import chat as chat_module
from app.api.chat import ChatError, run_turn
from app.core.history import load_messages
from app.core.lock import conversation_lock as real_lock
from app.db.models import Ticket
from app.db.repo_conversation import create_conversation, get_conversation, set_flow
from app.db.repo_message import (
    append_message,
    get_reply_by_client_message_id,
)

# ── 假图 ────────────────────────────────────────────────────


class FakeGraph:
    """只做一件事：按给定顺序吐出 update chunk，模拟 LangGraph 的 astream。"""

    def __init__(self, chunks: list) -> None:
        self.chunks = chunks
        self.calls = 0
        self.seen_state: dict | None = None
        self.seen_mode: str | None = None
        self.seen_subgraphs: bool | None = None

    async def astream(self, state, stream_mode="updates", subgraphs=False):
        self.calls += 1
        self.seen_state = state
        self.seen_mode = stream_mode
        self.seen_subgraphs = subgraphs
        for chunk in self.chunks:
            if isinstance(chunk, tuple):
                yield chunk
            elif subgraphs:
                yield ((), chunk)
            else:
                yield chunk


class BoomGraph:
    async def astream(self, state, stream_mode="updates", subgraphs=False):
        raise RuntimeError("boom")
        yield  # pragma: no cover


@pytest.fixture(autouse=True)
def spans(monkeypatch):
    """把 trace 写入换成内存记录，避免测试碰真库。

    autouse：凡是跑 `run_turn` 的测试都会经过 `record_span`，
    漏打桩就会写到 `data/app.db`。
    """
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(chat_module, "record_span", fake_record)
    return recorded


@pytest.fixture
def frames(session, spans):
    """跑一轮并返回帧；每次调用自动建一个会话，不依赖外部数据。"""

    async def _frames(
        chunks: list[dict],
        *,
        message: str = "退货运费谁出",
        conversation_id: int | None = None,
    ) -> list[dict]:
        return [
            f
            async for f in run_turn(
                message,
                conversation_id,
                "t-1",
                graph=FakeGraph(chunks),
                session=session,
            )
        ]

    return _frames


STRONG_CHUNKS = [
    {"resolve_reference": {"resolved_query": "退货运费谁出"}},
    {"classify_intent": {"intent": "政策问答", "route": "knowledge"}},
    {
        "knowledge": {
            "answer": "质量问题由商家承担[1]。",
            "citation_ids": ["returns#退货运费由谁承担"],
            "evidence": [
                {
                    "id": "returns#退货运费由谁承担",
                    "text": "质量问题由商家承担运费。",
                    "source": "returns.md",
                    "section": "退货运费由谁承担",
                }
            ],
            "is_placeholder": False,
        }
    },
]

REFUND_CHUNKS = [
    {"resolve_reference": {"resolved_query": "订单1001 尺寸不合适"}},
    {"classify_intent": {"intent": "退款申请", "route": "rules"}},
    {"validate": {"order_snapshot": {"order_id": "1001", "amount": 124900}}},
    {"judge": {"refund_judgement": {"eligible": True, "reason_code": "size"}}},
    {"compute": {"refund_amount": 124900}},
    {"compliance": {"compliance_violation": None}},
    {
        "respond": {
            "answer": "本次应退金额：1249.00 元。",
            "is_placeholder": False,
        }
    },
]


# ── 帧顺序 ──────────────────────────────────────────────────


async def test_frame_order(frames):
    out = await frames(STRONG_CHUNKS)
    events = [f.get("event") or "delta" for f in out]
    assert events == [
        "ack",
        "route",
        "progress",
        "delta",
        "citations",
        "done",
    ]


async def test_route_frame_carries_intent_and_route(frames):
    out = await frames(STRONG_CHUNKS)
    route = next(f for f in out if f.get("event") == "route")
    assert route["route"] == "knowledge"
    assert route["intent"] == "政策问答"
    assert route["resumed"] is False


async def test_route_frame_comes_before_delta(frames):
    """route 在分类完成时就发，前端可以先显示"正在查知识库"。"""
    out = await frames(STRONG_CHUNKS)
    kinds = [f.get("event") or "delta" for f in out]
    assert kinds.index("route") < kinds.index("delta")


async def test_actions_frame_only_when_present(frames):
    chunks = [
        STRONG_CHUNKS[1],
        {"human": {"answer": "已转接", "actions": [{"type": "transfer_human"}]}},
    ]
    out = await frames(chunks)
    actions = next(f for f in out if f.get("event") == "actions")
    assert actions["items"] == [{"type": "transfer_human"}]

    # 没有动作时不该发空帧
    out_without = await frames(STRONG_CHUNKS)
    assert not any(f.get("event") == "actions" for f in out_without)


async def test_fallback_transfer_action_is_not_actual_handoff(frames):
    """兜底只是给出转人工按钮，用户没点，进度不能显示“已转接”。"""
    chunks = [
        {"resolve_reference": {"resolved_query": "******"}},
        {"classify_intent": {"intent": "闲聊", "route": "fallback"}},
        {
            "fallback": {
                "answer": "抱歉，这个问题我暂时没能理解。",
                "actions": [{"type": "transfer_human"}],
                "is_placeholder": False,
            }
        },
    ]
    out = await frames(chunks, message="******")
    progress = [f["stage"] for f in out if f.get("event") == "progress"]
    assert progress == ["working"]
    assert any(f.get("event") == "actions" for f in out)


async def test_citations_frame_carries_source_details(frames):
    out = await frames(STRONG_CHUNKS)
    citations = next(f for f in out if f.get("event") == "citations")
    assert citations["items"] == ["returns#退货运费由谁承担"]
    assert citations["details"][0]["section_path"] == "退货运费由谁承担"
    assert citations["details"][0]["text"] == "质量问题由商家承担运费。"
    assert citations["details"][0]["source"] == "returns.md"


async def test_no_citations_frame_when_empty(frames):
    chunks = [STRONG_CHUNKS[1], {"fallback": {"answer": "没理解", "citation_ids": []}}]
    out = await frames(chunks)
    assert not any(f.get("event") == "citations" for f in out)


# ── 会话与消息落库 ──────────────────────────────────────────


async def test_ack_and_done_carry_ids(session, spans):
    cid = await create_conversation("u1", session=session)
    out = [
        f
        async for f in run_turn(
            "你好", cid, "t-1", graph=FakeGraph(STRONG_CHUNKS), session=session
        )
    ]

    ack = out[0]
    assert ack["event"] == "ack"
    assert ack["conversation_id"] == cid
    assert isinstance(ack["user_message_id"], int)

    done = out[-1]
    assert done["event"] == "done"
    assert isinstance(done["message_id"], int)


async def test_messages_are_persisted(session, spans):
    cid = await create_conversation("u1", session=session)
    _ = [
        f
        async for f in run_turn(
            "退货运费谁出",
            cid,
            "t-1",
            graph=FakeGraph(STRONG_CHUNKS),
            session=session,
        )
    ]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "退货运费谁出"
    assert rows[1]["meta"]["route"] == "knowledge"
    assert rows[1]["meta"]["citation_ids"] == ["returns#退货运费由谁承担"]


async def test_retry_replays_without_running_graph(session, spans):
    """Ch08：同一 client_message_id 重试直接回放，不再跑图、不再写第二条回答。"""
    cid = await create_conversation("u1", session=session)
    first_graph = FakeGraph(REFUND_CHUNKS)
    first = [
        f
        async for f in run_turn(
            "订单1001 尺寸不合适",
            cid,
            "t-1",
            client_message_id="cm-replay-1",
            graph=first_graph,
            session=session,
        )
    ]
    assert first_graph.calls == 1

    retry_graph = FakeGraph(REFUND_CHUNKS)
    retry = [
        f
        async for f in run_turn(
            "订单1001 尺寸不合适",
            cid,
            "t-2",
            client_message_id="cm-replay-1",
            graph=retry_graph,
            session=session,
        )
    ]

    assert retry_graph.calls == 0
    assert [f.get("event") or "delta" for f in retry] == [
        "ack",
        "route",
        "progress",
        "delta",
        "done",
    ]
    assert retry[0]["user_message_id"] == first[0]["user_message_id"]
    route = next(f for f in retry if f.get("event") == "route")
    assert route["route"] == "rules"
    assert route["intent"] == "退款申请"
    assert route["resumed"] is False
    replay_progress = next(
        f for f in retry if f.get("event") == "progress"
    )
    assert replay_progress["stage"] == "waiting_confirm"
    assert replay_progress["amount"] == 124900
    assert next(f["delta"] for f in retry if "delta" in f) == next(
        f["delta"] for f in first if "delta" in f
    )
    assert retry[-1]["message_id"] == first[-1]["message_id"]

    replay_span = next(s for s in spans if s["name"] == "turn:replay")
    assert replay_span["attrs"]["assistant_message_id"] == retry[-1]["message_id"]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]


async def test_retry_without_reply_reruns_graph(session, spans):
    """用户消息存在、但还没有助手回复：应该重跑，而不是误判成已完成。"""
    cid = await create_conversation("u1", session=session)
    await append_message(
        cid,
        "user",
        "退货运费谁出",
        client_message_id="cm-no-reply",
        session=session,
    )

    graph = FakeGraph(STRONG_CHUNKS)
    out = [
        f
        async for f in run_turn(
            "退货运费谁出",
            cid,
            "t-1",
            client_message_id="cm-no-reply",
            graph=graph,
            session=session,
        )
    ]

    assert graph.calls == 1
    assert out[-1]["event"] == "done"
    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]


async def test_error_assistant_does_not_block_retry(session, spans):
    """错误消息没有 reply_to，不能被当成“已完成”回放。"""
    cid = await create_conversation("u1", session=session)
    await append_message(
        cid,
        "user",
        "退货运费谁出",
        client_message_id="cm-error-replay",
        session=session,
    )
    await append_message(
        cid,
        "assistant",
        "服务暂时不可用",
        status="error",
        session=session,
    )

    graph = FakeGraph(STRONG_CHUNKS)
    _ = [
        f
        async for f in run_turn(
            "退货运费谁出",
            cid,
            "t-1",
            client_message_id="cm-error-replay",
            graph=graph,
            session=session,
        )
    ]

    assert graph.calls == 1


async def test_progress_frames_for_refund(session, spans):
    """退款：核对订单 → 算出金额 → 等待确认；最终进度在 delta 之前。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        (
            ("rules:1",),
            {"query_data": {"order_snapshot": {"order_id": "1001", "amount": 124900}}},
        ),
        (
            ("rules:1",),
            {"judge": {"refund_judgement": {"eligible": True, "reason_code": "size"}}},
        ),
        (("rules:1",), {"compute": {"refund_amount": 124900}}),
        (("rules:1",), {"compliance": {"compliance_violation": None}}),
        {
            "rules": {
                "answer": "本次应退金额：1249.00 元。",
                "refund_amount": 124900,
                "refund_judgement": {"eligible": True, "reason_code": "size"},
                "compliance_violation": None,
                "order_snapshot": {"order_id": "1001", "amount": 124900},
                "route": "rules",
                "intent": "退款申请",
            }
        },
    ]

    out = [
        f
        async for f in run_turn(
            "订单1001 尺寸不合适",
            cid,
            "t-1",
            graph=FakeGraph(chunks),
            session=session,
        )
    ]

    progress = [f for f in out if f.get("event") == "progress"]
    assert [f["stage"] for f in progress] == [
        "checking_order",
        "amount_ready",
        "waiting_confirm",
    ]
    assert progress[0]["status"] == "done"
    assert progress[1]["amount"] == 124900
    assert progress[2]["status"] == "waiting"
    assert progress[2]["amount"] == 124900

    kinds = [f.get("event") or "delta" for f in out]
    last_progress = max(
        index for index, f in enumerate(out) if f.get("event") == "progress"
    )
    assert last_progress < kinds.index("delta")


async def test_progress_waiting_info_is_not_duplicated(session, spans):
    """need_info 已经发过 waiting_info；最终状态相同就不重复发。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        (
            ("rules:1",),
            {
                "need_info": {
                    "pending_question": "order_id",
                    "answer": "请提供订单号。",
                    "is_placeholder": False,
                }
            },
        ),
        {
            "rules": {
                "answer": "请提供订单号。",
                "pending_question": "order_id",
                "route": "rules",
                "intent": "退款申请",
                "is_placeholder": False,
            }
        },
    ]

    out = [
        f
        async for f in run_turn(
            "我想退款",
            cid,
            "t-1",
            graph=FakeGraph(chunks),
            session=session,
        )
    ]

    progress = [f for f in out if f.get("event") == "progress"]
    assert progress == [
        {
            "event": "progress",
            "stage": "waiting_info",
            "status": "waiting",
            "pending_question": "order_id",
        }
    ]


async def test_assistant_meta_persists_actions_and_signals(session, spans):
    """Ch08：刷新后要能恢复金额/追问/动作，所以这些要落进 assistant meta。"""
    cid = await create_conversation("u1", session=session)
    _ = [
        f
        async for f in run_turn(
            "订单1001 尺寸不合适",
            cid,
            "t-1",
            client_message_id="cm-refund-1",
            graph=FakeGraph(REFUND_CHUNKS),
            session=session,
        )
    ]

    rows = await load_messages(cid, session=session)
    meta = rows[1]["meta"]
    assert meta["route"] == "rules"
    assert meta["actions"] == []
    assert meta["signals"] == {
        "amount": 124900,
        "pending_question": None,
        "refund_eligible": True,
        "handoff": False,
    }

    # 助手回复要能按 client_message_id 精确找回
    reply = await get_reply_by_client_message_id(
        cid, "cm-refund-1", session=session
    )
    assert reply is not None
    assert reply.content == rows[1]["content"]


async def test_meta_amount_is_none_when_compliance_fails(session, spans):
    """不可展示的金额不能进 meta；否则刷新后前端会显示一个不该出现的数字。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {"validate": {"order_snapshot": {"order_id": "1001", "amount": 124900}}},
        {"judge": {"refund_judgement": {"eligible": True, "reason_code": "size"}}},
        {"compute": {"refund_amount": 124900}},
        {
            "compliance": {
                "compliance_violation": "amount_exceeds_paid",
                "answer": "这个问题我这边处理不了，已经为您转接人工客服。",
                "actions": [{"type": "transfer_human"}],
            }
        },
    ]

    _ = [
        f
        async for f in run_turn(
            "订单1001 尺寸不合适",
            cid,
            "t-1",
            graph=FakeGraph(chunks),
            session=session,
        )
    ]

    rows = await load_messages(cid, session=session)
    meta = rows[1]["meta"]
    assert meta["signals"]["amount"] is None
    assert meta["actions"] == [{"type": "transfer_human"}]


async def test_persist_failure_fails_closed(session, spans, monkeypatch):
    """保存失败不能先展示回答；否则刷新后回答消失，重试还会换措辞。"""
    cid = await create_conversation("u1", session=session)

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_module, "save_assistant_message", boom)

    frames = []
    with pytest.raises(ChatError) as exc:
        async for frame in run_turn(
            "退货运费谁出",
            cid,
            "t-1",
            graph=FakeGraph(STRONG_CHUNKS),
            session=session,
        ):
            frames.append(frame)

    assert exc.value.code == "internal_error"
    assert [f.get("event") or "delta" for f in frames] == ["ack", "route"]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user"]

    persist = next(s for s in spans if s["name"] == "turn:persist")
    assert persist["attrs"]["persisted"] is False
    assert persist["attrs"]["error"] == "RuntimeError"

    total = next(s for s in spans if s["name"] == "turn:total")
    assert total["attrs"]["persisted"] is False


async def test_selected_action_allows_empty_message(session, spans):
    cid = await create_conversation("u1", session=session)
    _ = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-1",
            selected_action={"type": "select_order", "order_id": "1001"},
            graph=FakeGraph(STRONG_CHUNKS),
            session=session,
        )
    ]

    rows = await load_messages(cid, session=session)
    assert rows[0]["content"] == ""
    assert rows[0]["meta"]["selected_action"]["order_id"] == "1001"


async def test_transfer_action_shortcuts_graph(session, spans):
    """转人工 = 结构化动作：不跑图、不建工单、清空当前流程。"""
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )
    graph = FakeGraph(STRONG_CHUNKS)
    out = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-1",
            client_message_id="cm-transfer-1",
            selected_action={"type": "transfer_human"},
            graph=graph,
            session=session,
        )
    ]

    assert graph.calls == 0
    assert [f.get("event") or "delta" for f in out] == [
        "ack",
        "route",
        "progress",
        "delta",
        "actions",
        "done",
    ]
    route = next(f for f in out if f.get("event") == "route")
    assert route == {
        "event": "route",
        "route": "human",
        "intent": "其他",
        "resumed": False,
    }
    progress = next(f for f in out if f.get("event") == "progress")
    assert progress == {
        "event": "progress",
        "stage": "handoff",
        "status": "waiting",
    }
    delta = next(f["delta"] for f in out if "delta" in f)
    assert delta == "已为您转接人工客服，请稍候。人工客服会尽快与您联系。"
    actions = next(f for f in out if f.get("event") == "actions")
    assert actions["items"] == [{"type": "transfer_human"}]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["meta"]["selected_action"] == {"type": "transfer_human"}
    assert rows[1]["meta"]["actions"] == [{"type": "transfer_human"}]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert conv.pending_question is None
    assert (await session.execute(select(Ticket))).scalars().all() == []


@pytest.mark.parametrize("text", ["转人工", "我要找人工客服", "人工"])
async def test_typed_transfer_shortcuts_graph(session, spans, text: str):
    """用户直接打“转人工”也要走 D 出口，不能落到 UNKNOWN_REPLY。"""
    cid = await create_conversation("u1", session=session)
    graph = FakeGraph(STRONG_CHUNKS)
    out = [
        f
        async for f in run_turn(
            text,
            cid,
            "t-1",
            graph=graph,
            session=session,
        )
    ]

    assert graph.calls == 0
    route = next(f for f in out if f.get("event") == "route")
    assert route == {
        "event": "route",
        "route": "human",
        "intent": "其他",
        "resumed": False,
    }
    actions = next(f for f in out if f.get("event") == "actions")
    assert actions["items"] == [{"type": "transfer_human"}]
    assert (await session.execute(select(Ticket))).scalars().all() == []


async def test_transfer_action_retry_replays(session, spans):
    """转人工也能走同一 client_message_id 回放，不重复写。"""
    cid = await create_conversation("u1", session=session)
    first = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-1",
            client_message_id="cm-transfer-2",
            selected_action={"type": "transfer_human"},
            graph=FakeGraph(STRONG_CHUNKS),
            session=session,
        )
    ]

    retry_graph = FakeGraph(STRONG_CHUNKS)
    retry = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-2",
            client_message_id="cm-transfer-2",
            selected_action={"type": "transfer_human"},
            graph=retry_graph,
            session=session,
        )
    ]

    assert retry_graph.calls == 0
    assert [f.get("event") or "delta" for f in retry] == [
        "ack",
        "route",
        "progress",
        "delta",
        "actions",
        "done",
    ]
    assert retry[-1]["message_id"] == first[-1]["message_id"]
    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]


async def test_unknown_conversation_is_not_found(session, spans):
    with pytest.raises(ChatError) as exc:
        _ = [
            f
            async for f in run_turn(
                "你好", 9999, "t-1", graph=FakeGraph([]), session=session
            )
        ]
    assert exc.value.code == "not_found"


async def test_flow_state_persisted_parked_and_cleared(session, spans):
    cid = await create_conversation("u1", session=session)
    pending_chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {
            "rules": {
                "intent": "退款申请",
                "pending_question": "order_id",
                "answer": "请提供订单号",
                "refund_draft": {"reason_code": "no_reason"},
            }
        },
    ]

    _ = [
        f
        async for f in run_turn(
            "我要退款", cid, "t-1", graph=FakeGraph(pending_chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    assert json.loads(conv.draft)["refund"] == {"reason_code": "no_reason"}

    # 下一轮完成别的话题 → park：保留旧流程 + 提示 + resume_flow 动作
    done_chunks = [
        {"classify_intent": {"intent": "政策问答", "route": "knowledge"}},
        {"knowledge": {"answer": "质量问题由商家承担[1]。"}},
    ]
    out = [
        f
        async for f in run_turn(
            "退货政策", cid, "t-1", graph=FakeGraph(done_chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    delta = next(f["delta"] for f in out if "delta" in f)
    assert "未完成的退款申请" in delta
    actions = next(f for f in out if f.get("event") == "actions")
    assert {"type": "resume_flow", "flow": "refund"} in actions["items"]

    # 转人工 → 清空，且不再提示 park
    human_chunks = [
        {"classify_intent": {"intent": "投诉", "route": "human"}},
        {"human": {"answer": "已为您转接人工。"}},
    ]
    out = [
        f
        async for f in run_turn(
            "我要投诉", cid, "t-1", graph=FakeGraph(human_chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert conv.pending_question is None
    assert not any("未完成的退款申请" in f.get("delta", "") for f in out)


async def test_capture_refund_draft_when_order_missing(session, spans, monkeypatch):
    """第一轮没有订单号时 C 出口不走 extract；轮末要把原因补进 draft。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {
            "rules": {
                "intent": "退款申请",
                "pending_question": "order_id",
                "answer": "请提供订单号",
            }
        },
    ]

    async def fake_extract(text):
        return {"reason_code": "no_reason"}

    monkeypatch.setattr(chat_module, "extract_draft", fake_extract)

    _ = [
        f
        async for f in run_turn(
            "我要退款，尺寸不合适", cid, "t-1", graph=FakeGraph(chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert json.loads(conv.draft)["refund"] == {"reason_code": "no_reason"}


async def test_resume_refund_from_text(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={
            "intent": "退款申请",
            "refund": {"reason_code": "no_reason"},
            "validate_retry": 1,
        },
        session=session,
    )
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules", "resumed": True}},
        {
            "rules": {
                "refund_draft": {"reason_code": "no_reason"},
                "refund_amount": 124900,
                "answer": "可退 1249.00 元。",
            }
        },
    ]
    fake = FakeGraph(chunks)

    out = [
        f
        async for f in run_turn("1001", cid, "t-1", graph=fake, session=session)
    ]

    route_frame = next(f for f in out if f.get("event") == "route")
    assert route_frame["resumed"] is True
    assert fake.seen_state["resume_route"] == "rules"
    assert fake.seen_state["intent"] == "退款申请"
    assert fake.seen_state["order_id"] == "1001"
    assert fake.seen_state["refund_draft"] == {"reason_code": "no_reason"}
    assert fake.seen_state["validate_retry"] == 0      # 有进展，重试计数重置
    assert fake.seen_state["skip_extract"] is True


async def test_resume_with_extra_info_still_extracts(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请", "refund": {"reason_code": "no_reason"}},
        session=session,
    )
    fake = FakeGraph(
        [{"classify_intent": {"intent": "退款申请", "route": "rules"}}]
    )

    _ = [
        f
        async for f in run_turn(
            "1001，尺寸不合适", cid, "t-1", graph=fake, session=session
        )
    ]

    assert fake.seen_state["order_id"] == "1001"
    assert "skip_extract" not in fake.seen_state


async def test_resume_from_selected_action(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请", "refund": {"reason_code": "no_reason"}},
        session=session,
    )
    fake = FakeGraph(
        [{"classify_intent": {"intent": "退款申请", "route": "rules"}}]
    )

    _ = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-1",
            selected_action={"type": "select_order", "order_id": "1001"},
            graph=fake,
            session=session,
        )
    ]

    assert fake.seen_state["resume_route"] == "rules"
    assert fake.seen_state["order_id"] == "1001"
    assert fake.seen_state["skip_extract"] is True


async def test_cancel_clears_flow(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )
    fake = FakeGraph(
        [
            {"classify_intent": {"intent": "政策问答", "route": "knowledge"}},
            {"knowledge": {"answer": "质量问题由商家承担[1]。"}},
        ]
    )

    _ = [
        f
        async for f in run_turn("算了", cid, "t-1", graph=fake, session=session)
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert fake.seen_state["resume_route"] is None


async def test_resume_data_flow(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="data",
        pending_question="order_id",
        draft={"intent": "订单查询"},
        session=session,
    )
    fake = FakeGraph(
        [{"classify_intent": {"intent": "订单查询", "route": "data"}}]
    )

    _ = [
        f
        async for f in run_turn("1001", cid, "t-1", graph=fake, session=session)
    ]

    assert fake.seen_state["resume_route"] == "data"
    assert fake.seen_state["order_id"] == "1001"
    assert "skip_extract" not in fake.seen_state


async def test_resume_refund_reason_skips_second_extract(
    session, spans, monkeypatch
):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="reason_code",
        draft={"intent": "退款申请", "refund": {"item_count": 1}},
        session=session,
    )

    async def fake_extract(text):
        return {"reason_code": "quality"}

    monkeypatch.setattr("app.core.slot_fill.extract_draft", fake_extract)

    fake = FakeGraph(
        [{"classify_intent": {"intent": "退款申请", "route": "rules"}}]
    )
    _ = [
        f
        async for f in run_turn(
            "有质量问题", cid, "t-1", graph=fake, session=session
        )
    ]

    assert fake.seen_state["refund_draft"] == {
        "item_count": 1,
        "reason_code": "quality",
    }
    assert fake.seen_state["skip_extract"] is True


async def test_busy_conversation_raises_busy(session, spans, monkeypatch):
    cid = await create_conversation("u1", session=session)
    entered = asyncio.Event()

    async def hold():
        async with real_lock(cid):
            entered.set()
            await asyncio.sleep(0.2)

    task = asyncio.create_task(hold())
    await entered.wait()
    monkeypatch.setattr(
        chat_module,
        "conversation_lock",
        partial(real_lock, timeout=0.01),
    )

    with pytest.raises(ChatError) as exc:
        _ = [
            f
            async for f in run_turn(
                "你好", cid, "t-1", graph=FakeGraph([]), session=session
            )
        ]

    assert exc.value.code == "busy"
    await task


async def test_graph_error_persists_error_message(session, spans):
    cid = await create_conversation("u1", session=session)
    with pytest.raises(RuntimeError):
        _ = [
            f
            async for f in run_turn(
                "你好", cid, "t-1", graph=BoomGraph(), session=session
            )
        ]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[1]["meta"] == {"status": "error"}


# ── 输出检查 ────────────────────────────────────────────────


async def test_output_guard_rewrites_before_delta_and_persist(session, spans):
    """金额/措辞违规在 delta 之前整段替换，落库的也是替换后的回答。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {"validate": {"order_snapshot": {"order_id": "1001", "amount": 124900}}},
        {"judge": {"refund_judgement": {"eligible": True, "reason_code": "size"}}},
        {"compute": {"refund_amount": 50000}},
        {
            "respond": {
                "answer": "可以全额退款，本次退 500.00 元，另补偿 200.00 元。",
                "is_placeholder": False,
            }
        },
    ]

    out = [
        f
        async for f in run_turn(
            "尺寸不合适", cid, "t-1", graph=FakeGraph(chunks), session=session
        )
    ]

    delta = next(f["delta"] for f in out if "delta" in f)
    assert delta == "本次应退金额：500.00 元，具体以平台售后规则为准。"
    assert "全额" not in delta
    assert "200.00" not in delta

    total = next(s for s in spans if s["name"] == "turn:total")
    assert set(total["attrs"]["output_violations"]) == {"amount", "full_refund"}

    rows = await load_messages(cid, session=session)
    assert rows[1]["content"] == delta


# ── trace ──────────────────────────────────────────────────


async def test_one_span_per_node_plus_total(spans, frames):
    await frames(STRONG_CHUNKS)
    names = [s["name"] for s in spans]
    assert names == [
        "node:resolve_reference",
        "node:classify_intent",
        "node:knowledge",
        "turn:persist",
        "turn:total",
    ]


async def test_spans_share_trace_id(spans, frames):
    await frames(STRONG_CHUNKS)
    assert {s["trace_id"] for s in spans} == {"t-1"}


async def test_span_has_duration_and_keys(spans, frames):
    await frames(STRONG_CHUNKS)
    node_span = next(s for s in spans if s["name"] == "node:classify_intent")
    assert "duration_ms" in node_span["attrs"]
    assert node_span["attrs"]["route"] == "knowledge"


# ── 入参校验 ────────────────────────────────────────────────


@pytest.mark.parametrize("message", ["", "   "])
async def test_empty_message_is_invalid_arg(message: str):
    with pytest.raises(ChatError) as exc:
        _ = [f async for f in run_turn(message, None, "t-1", graph=FakeGraph([]))]
    assert exc.value.code == "invalid_arg"


async def test_too_long_message_is_invalid_arg():
    with pytest.raises(ChatError) as exc:
        _ = [
            f
            async for f in run_turn(
                "啊" * 501, None, "t-1", graph=FakeGraph([])
            )
        ]
    assert exc.value.code == "invalid_arg"


async def test_state_passed_to_graph(session, spans):
    cid = await create_conversation("u1", session=session)
    fake = FakeGraph(STRONG_CHUNKS)
    _ = [
        f
        async for f in run_turn(
            "你好", cid, "t-9", graph=fake, session=session
        )
    ]
    assert fake.seen_state == {
        "raw_query": "你好",
        "conversation_id": cid,
        "trace_id": "t-9",
        "user_id": "u1",       # 演示身份，Ch06 换成真实登录用户
        "history": [],         # 新会话没有历史
        "summary": None,
        "resume_route": None,
        "resumed": False,
    }
    assert fake.seen_mode == "updates"
    assert fake.seen_subgraphs is True


# ── HTTP 层 ────────────────────────────────────────────────


def _sse_frames(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)          # 不用 with：跳过 lifespan，避免校验密钥


def test_http_streams_sse_frames(client, spans, monkeypatch):
    async def fake_run_turn(message, conversation_id, trace_id, **kwargs):
        for frame in [{"event": "ack"}, {"event": "done"}]:
            yield frame

    monkeypatch.setattr("app.main.run_turn", fake_run_turn)

    response = client.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [f["event"] for f in _sse_frames(response.text)] == ["ack", "done"]


def test_http_other_conversation_returns_404(client, spans, monkeypatch):
    async def deny(*args, **kwargs):
        raise ChatError("not_found", "会话不存在")

    monkeypatch.setattr("app.main.conversation_access", deny)

    response = client.post(
        "/api/chat", json={"message": "你好", "conversation_id": 999}
    )

    assert response.status_code == 404


def test_http_error_frame_hides_internal_detail(client, spans, monkeypatch):
    """内部异常只回一句标准话术，不能把堆栈或原始错误信息透给用户。"""

    async def boom(message, conversation_id, trace_id, **kwargs):
        raise RuntimeError("数据库连接串 postgres://secret")
        yield  # pragma: no cover

    monkeypatch.setattr("app.main.run_turn", boom)

    frames = _sse_frames(client.post("/api/chat", json={"message": "你好"}).text)
    assert frames[0]["event"] == "error"
    assert frames[0]["code"] == "internal_error"
    assert "secret" not in frames[0]["message"]


def test_http_invalid_arg_keeps_readable_message(client, spans, monkeypatch):
    async def bad(message, conversation_id, trace_id, **kwargs):
        raise ChatError("invalid_arg", "消息不能为空")
        yield  # pragma: no cover

    monkeypatch.setattr("app.main.run_turn", bad)

    frames = _sse_frames(client.post("/api/chat", json={"message": "x"}).text)
    assert frames[0]["code"] == "invalid_arg"
    assert frames[0]["message"] == "消息不能为空"
