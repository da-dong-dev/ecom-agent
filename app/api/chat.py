"""`/api/chat` 的对话轮次：把主图跑一轮，翻译成 SSE 帧。

帧顺序固定（前端按这个顺序渲染）：

```
ack → route → delta → citations → actions → done
                  ↑ 异常时用 error 替换其后所有帧
```

**route 帧在分类完成时就发**，不等生成——前端可以立刻显示"正在查知识库"之类的提示。
这靠 `astream(stream_mode="updates")`：每个节点跑完就吐一次，而不是等全图结束。

## 错误码

D9 定的四类是 `not_found` / `forbidden` / `timeout` / `invalid_arg`，那是**给模型看的工具错误语义**。
HTTP 这一层还需要一个兜底码：

| code | 场景 |
|---|---|
| `invalid_arg` | 空消息、超长、参数不合法 |
| `not_found` | 会话不存在 / 不属于当前用户（同一句，防枚举） |
| `timeout` | 模型或检索超时 |
| `internal_error` | 其余未预期异常（D9 四类之外，仅用于传输层） |

`forbidden` 留给 Ch06 的真实鉴权；现在归属不符统一走 `not_found`。
"""
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import slot_fill
from app.core.history import load_window, save_assistant_message, save_user_message
from app.core.lock import BusyError, conversation_lock
from app.core.output_guard import guard_answer, output_context
from app.core.progress import final_frame, node_frame
from app.core.session_state import (
    FlowDecision,
    apply_flow_decision,
    clear_flow,
    decide_flow,
    load_flow,
)
from app.core.tracer import record_span
from app.db.errors import NotFound
from app.db.models import Message, utcnow
from app.db.repo_conversation import create_conversation, get_conversation
from app.db.repo_message import (
    get_reply_by_client_message_id,
    get_user_message_by_client_id,
)
from app.db.session import session_scope
from app.graph.build import build_graph
from app.graph.nodes import human_exit
from app.graph.routing import INTENT_TO_ROUTE
from app.graph.rules_exit import extract_draft

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500
ERROR_ANSWER = "服务暂时不可用，请稍后重试。"

# 用户直接打「转人工」也要走 D 出口；不能只依赖前端结构化动作。
_TRANSFER_PHRASES = (
    "转人工", "转接人工", "找人工", "要人工", "人工客服", "人工服务",
    "转客服", "找客服", "要客服",
)


def _is_transfer_request(text: str) -> bool:
    compact = re.sub(r"[\s，,。.!！?？]", "", text or "")
    return compact == "人工" or any(phrase in compact for phrase in _TRANSFER_PHRASES)


class ChatError(Exception):
    """带错误码的对话异常，直接映射成 `error` 帧。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


def _only_order_id(text: str, order_id: str | None) -> bool:
    """这句话是不是「只补了订单号」。

    空文本（点选卡片）也算；「1001，尺寸不合适」不算——后面还要抽取新信息。
    """
    if not text.strip():
        return True
    if not order_id:
        return False
    return text.replace(str(order_id), "").strip(" ，,。.!！?？") == ""


FLOW_LABELS = {"refund": "退款申请", "data": "数据查询"}


def _with_park_notice(answer: str, flow: str) -> str:
    """在回答尾部加一句「您还有一笔未完成流程」。"""
    label = FLOW_LABELS.get(flow, flow)
    notice = f"（您还有一笔未完成的{label}，需要继续吗？）"
    return f"{answer}\n\n{notice}" if answer else notice


async def _capture_refund_draft(final: dict) -> dict | None:
    """C 出口缺订单号时不会走 `extract`；把用户已经说过的原因补进 draft。

    只在该轮「问订单号、且 draft 还没有 reason_code」时补一次，
    避免每轮都多调一次抽取模型。
    """
    if final.get("route") != "rules" or final.get("pending_question") != "order_id":
        return None

    draft = dict(final.get("refund_draft") or {})
    if draft.get("reason_code"):
        return None

    query = final.get("resolved_query") or final.get("raw_query") or ""
    try:
        extracted = await extract_draft(query)
    except Exception:
        logger.warning("补齐退款草稿失败", exc_info=True)
        return None
    if not extracted:
        return None
    return {**draft, **extracted}


async def conversation_access(
    conversation_id: int | None,
    *,
    user_id: str,
    session: AsyncSession | None = None,
) -> None:
    """SSE 建立前的会话归属预检。

    必须在返回 `StreamingResponse` 之前调用：状态码一旦发出，后面只能回 `error` 帧。
    不存在 / 越权 / 已软删都抛同一个 `not_found`，不泄露存在性。
    """
    if conversation_id is None:
        return
    try:
        await get_conversation(conversation_id, user_id=user_id, session=session)
    except NotFound as exc:
        raise ChatError("not_found", "会话不存在") from exc


async def _save_error_message(cid: int, session: AsyncSession | None) -> None:
    """图执行失败时补一条 error 助手消息，避免用户消息悬空。

    落库失败只记日志，不能盖住原始异常。
    """
    try:
        async with session_scope(session) as s:
            await save_assistant_message(
                cid,
                ERROR_ANSWER,
                meta={"status": "error"},
                status="error",
                session=s,
            )
    except Exception:
        logger.exception("错误消息落库失败 conv=%s", cid)


def _parse_assistant_meta(raw: str | None) -> dict[str, Any]:
    """解析助手消息 `meta`；坏 JSON 只影响回放展示，不影响回答本身。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("助手消息 meta 不是合法 JSON，回放时忽略")
        return {}
    return value if isinstance(value, dict) else {}


def _citation_details(final: dict[str, Any]) -> list[dict[str, str]]:
    """把 citation_ids 映射回当时的证据，供前端点开看来源。

    只保留展示所需字段，不把相似度/内部信号带出去。
    """
    evidence = final.get("evidence") or []
    by_id = {
        str(item.get("id")): item
        for item in evidence
        if isinstance(item, dict) and item.get("id") is not None
    }
    details: list[dict[str, str]] = []
    for cid in final.get("citation_ids") or []:
        item = by_id.get(str(cid)) or {}
        details.append(
            {
                "id": str(cid),
                "section_path": str(item.get("section") or ""),
                "text": str(item.get("text") or "")[:600],
                "source": str(item.get("source") or ""),
            }
        )
    return details


async def _replay_frames(
    *,
    cid: int,
    reply: Message,
    user_message_id: int,
    trace_id: str,
) -> AsyncIterator[dict]:
    """把已存的助手回复按 SSE 帧回放；不跑图、不改 flow、不再落库。"""
    meta = _parse_assistant_meta(reply.meta)
    yield {
        "event": "ack",
        "conversation_id": cid,
        "user_message_id": user_message_id,
    }

    route = meta.get("route")
    if route is not None:
        yield {
            "event": "route",
            "route": route,
            "intent": meta.get("intent"),
            "resumed": bool(meta.get("resumed")),
        }

    raw_actions = meta.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    raw_signals = meta.get("signals")
    signals = raw_signals if isinstance(raw_signals, dict) else {}
    replay_progress = final_frame(
        route=route,
        actions=actions,
        signals=signals,
        actual_handoff=bool(signals.get("handoff")),
    )
    if replay_progress is not None:
        yield replay_progress

    if reply.content:
        yield {"delta": reply.content}

    citations = meta.get("citation_ids") or []
    raw_details = meta.get("citations")
    citation_details = raw_details if isinstance(raw_details, list) else []
    if citations:
        yield {
            "event": "citations",
            "items": citations,
            "details": citation_details,
        }

    if actions:
        yield {"event": "actions", "items": actions}

    now = utcnow()
    await record_span(
        trace_id=trace_id,
        name="turn:replay",
        start_at=now,
        end_at=now,
        attrs={
            "client_message_id": reply.reply_to_client_message_id,
            "assistant_message_id": reply.id,
        },
    )
    yield {"event": "done", "message_id": reply.id}


async def _finalize_answer(
    *,
    cid: int,
    trace_id: str,
    final: dict[str, Any],
    decision: FlowDecision,
    client_message_id: str | None,
    capture: dict | None,
    session: AsyncSession | None,
    turn_start: datetime,
    wall_start: float,
    last_progress: dict | None,
) -> AsyncIterator[dict]:
    """输出守卫 → 落库 → 发进度/回答/引用/动作/done。

    正常图和转人工短路共用这一段，避免两套保存/失败/span 逻辑漂移。
    """
    answer = final.get("answer") or ""
    actions = list(final.get("actions") or [])

    # 输出兜底：金额只能来自 compute / 工具结果，承诺和系统提示泄露一律整段替换。
    # 必须在 delta 之前、落库之前——用户看到和回放的都得是 guard 之后的回答。
    guarded = guard_answer(answer, output_context(final))
    answer = guarded.answer
    # actions 里可能有 `transfer_human`，也可能是 `resume_flow` / `park_flow` / `transfer_human` 的组合。
    if guarded.action and guarded.action not in actions:
        # 兜底动作：如果输出被替换成兜底回答，必须加一个转人工动作，避免用户看了回答却没出口。
        actions.append(guarded.action)

    # 本轮没接回、旧流程还在、又答完了新话题 → park：保留旧流程并提示
    if decision.parked_flow:
        answer = _with_park_notice(answer, decision.parked_flow)
        actions.append({"type": "resume_flow", "flow": decision.parked_flow})
    if decision.replaced_flow:
        logger.info(
            "流程被替换 conv=%s old=%s new=%s",
            cid,
            decision.replaced_flow,
            decision.active_flow,
        )

    if capture is not None:
        capture["final"] = final
        capture["answer"] = answer
        capture["actions"] = list(actions)
        capture["violations"] = list(guarded.violations)

    citations = final.get("citation_ids") or []
    route = final.get("route")
    judgement = final.get("refund_judgement") or {}
    refund_amount = final.get("refund_amount")
    # 金额只在“退款可退 + 合规通过”时对外展示；转人工/不可退不展示金额。
    amount_ok = (
        route == "rules"
        and bool(judgement.get("eligible"))
        and isinstance(refund_amount, int)
        and not isinstance(refund_amount, bool)
        and refund_amount > 0
        and final.get("compliance_violation") is None
    )
    # route=human 是明确转接；rules 的 transfer() 带 transfer_reason；
    # fallback 里的 transfer_human 只是“提供按钮”，不算已经转接。
    actual_handoff = route == "human" or bool(final.get("transfer_reason"))
    assistant_meta = {
        "intent": final.get("intent"),
        "route": route,
        "citation_ids": citations,
        "citations": _citation_details(final),
        "resumed": bool(final.get("resumed")),
        "actions": actions,
        "signals": {
            "amount": refund_amount if amount_ok else None,
            "pending_question": final.get("pending_question"),
            "refund_eligible": (
                bool(judgement.get("eligible")) if route == "rules" else None
            ),
            "handoff": actual_handoff,
        },
    }

    # 先落库再展示：C2-lite 的不变式是“用户看到的回答一定已落库”。
    # 保存失败时不回 delta，只回 error 帧；用户消息已经写过，重试可重跑。
    persist_start = utcnow()
    persist_wall = time.perf_counter()
    try:
        async with session_scope(session) as s:
            assistant_message_id = await save_assistant_message(
                cid,
                answer,
                meta=assistant_meta,
                reply_to_client_message_id=client_message_id,
                session=s,
            )
            # 与助手消息同事务：不能出现「消息写了、流程没写」的半截状态
            await apply_flow_decision(cid, decision, session=s)
    except Exception as exc:
        persist_end = utcnow()
        persist_wall_ms = int((time.perf_counter() - persist_wall) * 1000)
        logger.exception("助手消息/流程落库失败 conv=%s", cid)
        await record_span(
            trace_id=trace_id,
            name="turn:persist",
            start_at=persist_start,
            end_at=persist_end,
            attrs={
                "duration_ms": _elapsed_ms(persist_start, persist_end),
                "wall_ms": persist_wall_ms,
                "persisted": False,
                "flow_action": decision.action,
                "error": type(exc).__name__,
            },
        )
        await record_span(
            trace_id=trace_id,
            name="turn:total",
            start_at=turn_start,
            end_at=persist_end,
            attrs={
                "wall_ms": int((time.perf_counter() - wall_start) * 1000),
                "route": route,
                "has_answer": bool(answer),
                "citations": len(citations),
                "parked": decision.parked_flow is not None,
                "flow_replaced": decision.replaced_flow is not None,
                "output_violations": list(guarded.violations),
                "persisted": False,
            },
        )
        raise ChatError(
            "internal_error", "服务暂时不可用，请稍后重试。"
        ) from exc

    persist_end = utcnow()
    persist_wall_ms = int((time.perf_counter() - persist_wall) * 1000)
    persist_ms = _elapsed_ms(persist_start, persist_end)

    final_progress = final_frame(
        route=route,
        actions=actions,
        signals=assistant_meta["signals"],
        actual_handoff=actual_handoff,
    )
    if final_progress is not None and final_progress != last_progress:
        yield final_progress

    if answer:
        yield {"delta": answer}

    if citations:
        yield {
            "event": "citations",
            "items": citations,
            "details": assistant_meta["citations"],
        }

    if actions:
        yield {"event": "actions", "items": actions}

    await record_span(
        trace_id=trace_id,
        name="turn:persist",
        start_at=persist_start,
        end_at=persist_end,
        attrs={
            "duration_ms": persist_ms,
            "wall_ms": persist_wall_ms,
            "persisted": True,
            "assistant_message_id": assistant_message_id,
            "flow_action": decision.action,
        },
    )

    await record_span(
        trace_id=trace_id,
        name="turn:total",
        start_at=turn_start,
        end_at=persist_end,
        attrs={
            "wall_ms": int((time.perf_counter() - wall_start) * 1000),
            "route": route,
            "has_answer": bool(answer),
            "citations": len(citations),
            "parked": decision.parked_flow is not None,
            "flow_replaced": decision.replaced_flow is not None,
            "output_violations": list(guarded.violations),
            "persisted": True,
            "persist_ms": persist_ms,
        },
    )

    yield {"event": "done", "message_id": assistant_message_id}


async def run_turn(
    message: str,
    conversation_id: int | None,
    trace_id: str,
    *,
    client_message_id: str | None = None,
    selected_action: dict | None = None,
    graph: Any | None = None,
    session: AsyncSession | None = None,
    capture: dict | None = None,
) -> AsyncIterator[dict]:
    """跑一轮对话，逐个产出 SSE 帧（dict）。

    `graph` 可注入，测试用假图替换；生产走 `build_graph()`。
    `session` 可注入，测试指向临时库；生产不传，走模块级 engine。
    `capture` 只给评测入口用：图跑完后把终态、guard 后的回答和动作放进去，
    避免为了评测复制一套 `run_turn`。
    """
    text = (message or "").strip()
    # 结构化点选（选单/选规格）可以没有文字内容
    if not text and not selected_action:
        raise ChatError("invalid_arg", "消息不能为空")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ChatError("invalid_arg", f"消息过长（上限 {MAX_MESSAGE_CHARS} 字）")

    # Ch02 没有登录体系；订单归属校验需要一个固定的演示身份。
    # Ch06 接真实鉴权后改成从请求里取登录用户。
    user_id = get_settings().demo_user_id
    if conversation_id is None:
        cid = await create_conversation(user_id, session=session)
    else:
        cid = conversation_id

    # 先拿锁再读会话：否则两个并发请求会各自读到「上一轮还没写库」的旧状态
    try:
        async with conversation_lock(cid):
            async for frame in _run_turn_locked(
                cid=cid,
                conversation_id=conversation_id,
                user_id=user_id,
                text=text,
                selected_action=selected_action,
                client_message_id=client_message_id,
                trace_id=trace_id,
                graph=graph,
                session=session,
                capture=capture,
            ):
                yield frame
    except BusyError as exc:
        raise ChatError("busy", "上一条消息还在处理中，请稍后再试") from exc


async def _run_turn_locked(
    *,
    cid: int,
    conversation_id: int | None,
    user_id: str,
    text: str,
    selected_action: dict | None,
    client_message_id: str | None,
    trace_id: str,
    graph: Any | None,
    session: AsyncSession | None,
    capture: dict | None = None,
) -> AsyncIterator[dict]:
    """锁内的一轮：读会话与流程、槽位填充、跑图、写库。"""
    # 锁内再读会话：并发请求在等锁期间，上一轮可能已经改了 summary / 流程
    conv = None
    summary: str | None = None
    summary_upto = 0
    flow = None
    if conversation_id is not None:
        try:
            conv = await get_conversation(
                conversation_id, user_id=user_id, session=session
            )
        except NotFound as exc:
            # 不存在与不属于本人同一句，避免会话 id 成为枚举 oracle（S1/S2）
            raise ChatError("not_found", "会话不存在") from exc
        summary = conv.summary
        summary_upto = conv.summary_upto_msg_id or 0
        flow = await load_flow(cid, conversation=conv, session=session)
        if flow.expired:
            logger.info("会话流程已过期 conv=%s", cid)

    # Ch08：同一个 client_message_id 重试时，先看这一轮是否已经回答过。
    # 命中就直接回放已存内容——不跑图、不改 flow、不再写第二条助手消息。
    if client_message_id is not None:
        reply = await get_reply_by_client_message_id(
            cid, client_message_id, session=session
        )
        if reply is not None:
            user_message = await get_user_message_by_client_id(
                cid, client_message_id, session=session
            )
            if user_message is not None:
                async for frame in _replay_frames(
                    cid=cid,
                    reply=reply,
                    user_message_id=user_message.id,
                    trace_id=trace_id,
                ):
                    yield frame
                return
            logger.warning(
                "找到助手回放但缺少用户消息 conv=%s client_message_id=%s",
                cid,
                client_message_id,
            )

    # Ch08：前端点「转人工」= 结构化动作；用户直接打“转人工”也走同一短路。
    # 不跑分类/图，也不建工单；复用 D 出口固定话术，decide_flow 清空当前流程。
    if (selected_action or {}).get("type") == "transfer_human" or _is_transfer_request(text):
        user_message_id = await save_user_message(
            cid,
            text or None,
            client_message_id=client_message_id,
            meta={"selected_action": selected_action} if selected_action else None,
            session=session,
        )
        yield {
            "event": "ack",
            "conversation_id": cid,
            "user_message_id": user_message_id,
        }
        yield {
            "event": "route",
            "route": "human",
            "intent": "其他",
            "resumed": False,
        }

        final = {
            "route": "human",
            "intent": "其他",
            **await human_exit({}),
        }
        decision = decide_flow(final, parked=flow)
        async for frame in _finalize_answer(
            cid=cid,
            trace_id=trace_id,
            final=final,
            decision=decision,
            client_message_id=client_message_id,
            capture=capture,
            session=session,
            turn_start=utcnow(),
            wall_start=time.perf_counter(),
            last_progress=None,
        ):
            yield frame
        return

    # 槽位填充：先按「当前在等什么」解析这句话，命中就直接接回原出口
    slot = await slot_fill.fill(
        flow.pending_question if flow else None,
        text,
        draft=flow.draft if flow else {},
        selected=selected_action,
    )
    if slot.cancel:
        await clear_flow(cid, session=session)

    resume_route: str | None = None
    restored: dict = {}
    if flow is not None and not slot.cancel and (slot.resume or slot.filled):
        intent = flow.draft.get("intent")
        if intent in INTENT_TO_ROUTE:          # draft 损坏时不硬接
            resume_route = INTENT_TO_ROUTE[intent]
            restored = {
                "intent": intent,
                "order_id": flow.draft.get("order_id"),
                "sku_id": flow.draft.get("sku_id"),
                "refund_draft": flow.draft.get("refund") or {},
                "validate_retry": flow.draft.get("validate_retry", 0),
            }
            if slot.slot == "order_id":
                restored["order_id"] = slot.value
            elif slot.slot == "sku_id":
                restored["sku_id"] = slot.value
            elif slot.slot in {"reason_code", "refund_extract"}:
                restored["refund_draft"] = slot.value

    # 用户答了一个槽位 / 说「继续」= 有进展，重试计数重新开始；
    # 否则「问订单号 → 用户答 → 问原因」会被算成两次失败，直接转人工。
    if resume_route == "rules" and (slot.resume or slot.filled):
        restored["validate_retry"] = 0

    skip_extract = resume_route == "rules" and (
        slot.resume
        # 退款槽位在 slot_fill 里已经调用过抽取器，出口里不能再调一次
        or slot.slot in {"reason_code", "refund_extract"}
        or (
            slot.slot == "order_id"
            and _only_order_id(text, restored.get("order_id"))
        )
    )

    # 窗口在本轮用户消息落库之前构建：history 只放上一轮及更早，
    # 本轮输入由 raw_query 单独传，避免同一句话出现两次。
    window = await load_window(
        cid,
        summary=summary,
        summary_upto_msg_id=summary_upto,
        session=session,
    )

    user_meta = {"selected_action": selected_action} if selected_action else None
    user_message_id = await save_user_message(
        cid,
        text or None,
        client_message_id=client_message_id,
        meta=user_meta,
        session=session,
    )

    engine = graph if graph is not None else build_graph()
    state = {
        "raw_query": text,
        "conversation_id": cid,
        "trace_id": trace_id,
        "user_id": user_id,
        "history": window.messages,
        "summary": window.summary,
        "resume_route": resume_route,
        "resumed": bool(resume_route),
    }
    state.update(restored)
    if skip_extract:
        state["skip_extract"] = True

    yield {
        "event": "ack",
        "conversation_id": cid,
        "user_message_id": user_message_id,
    }

    final: dict = {}
    seen: dict = {}
    last_progress: dict | None = None
    turn_start = utcnow()          # 整个轮次的起点，`turn:total` 用它
    node_started = turn_start
    wall_start = time.perf_counter()

    try:
        # subgraphs=True：拿到子图内部节点事件，用于 progress。
        # 顶层 update 仍写 final；内部 update 只写 seen，不污染最终状态。
        async for chunk in engine.astream(
            state, stream_mode="updates", subgraphs=True
        ):
            node_ended = utcnow()
            namespace, chunk_update = chunk
            for node_name, update in (chunk_update or {}).items():
                update = update or {}

                if namespace:
                    seen.update(update)
                    frame = node_frame(node_name, update, seen)
                    if frame is not None and frame != last_progress:
                        last_progress = frame
                        yield frame
                    continue

                final.update(update)
                seen.update(update)
                await record_span(
                    trace_id=trace_id,
                    name=f"node:{node_name}",
                    start_at=node_started,
                    end_at=node_ended,
                    attrs={
                        "duration_ms": _elapsed_ms(node_started, node_ended),
                        "keys": sorted(update.keys()),
                        "route": update.get("route"),
                        "placeholder": update.get("is_placeholder"),
                    },
                )
                # 分类一完成就把分流结果发出去，前端不用等生成
                if node_name == "classify_intent":
                    yield {
                        "event": "route",
                        "route": update.get("route"),
                        "intent": update.get("intent"),
                        "resumed": bool(update.get("resumed")),
                    }

                frame = node_frame(node_name, update, final)
                if frame is not None and frame != last_progress:
                    last_progress = frame
                    yield frame

            # 只有顶层节点推进 span 计时；子图内部 chunk 不能把 duration 截短
            if not namespace:
                node_started = node_ended
    except Exception:
        await _save_error_message(cid, session)
        raise

    captured = await _capture_refund_draft(final)
    if captured is not None:
        final["refund_draft"] = captured

    decision = decide_flow(
        final,
        parked=flow if flow is not None and not slot.cancel else None,
        resumed=bool(resume_route),
    )
    async for frame in _finalize_answer(
        cid=cid,
        trace_id=trace_id,
        final=final,
        decision=decision,
        client_message_id=client_message_id,
        capture=capture,
        session=session,
        turn_start=turn_start,
        wall_start=wall_start,
        last_progress=last_progress,
    ):
        yield frame
