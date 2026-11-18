"""会话仓库。

`active_flow / pending_question / draft` 三个字段是跨轮状态锚点（M4）：
- `active_flow`：当前在跑哪个流程（refund / data）
- `pending_question`：正在等哪个槽（order_id / sku_id / spec / reason_code）
- `draft`：已收集的要素，JSON 存
"""
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import NotFound
from app.db.models import Conversation, Message, utcnow
from app.db.session import session_scope


async def create_conversation(user_id: str, *, session: AsyncSession | None = None) -> int:
    async with session_scope(session) as s:
        conv = Conversation(user_id=user_id)
        s.add(conv)
        await s.flush()  # 拿到自增 id；不 flush 则为 None
        return conv.id


async def get_conversation(
    cid: int, *, user_id: str, session: AsyncSession | None = None
) -> Conversation:
    """取会话。已软删、不存在、不属于该用户 —— 一律 `NotFound`（S2）。"""
    async with session_scope(session) as s:
        row = await s.get(Conversation, cid)
        if row is None or row.user_id != user_id or row.deleted_at is not None:
            raise NotFound("conversation not found")
        return row


async def soft_delete_conversation(
    cid: int, *, user_id: str, session: AsyncSession | None = None
) -> None:
    """软删会话：只改 deleted_at，保留消息用于审计/回放。

    不存在、不属于本人、已软删 —— 一律 NotFound（S2）。
    """
    async with session_scope(session) as s:
        row = await s.get(Conversation, cid)
        if row is None or row.user_id != user_id or row.deleted_at is not None:
            raise NotFound("conversation not found")
        now = utcnow()
        row.deleted_at = now
        row.updated_at = now


async def set_flow(
    cid: int,
    *,
    active_flow: str | None,
    pending_question: str | None,
    draft: dict[str, Any] | None,
    session: AsyncSession | None = None,
) -> None:
    """整组写入流程状态。三字段同进同退——分开写容易出现"流程清了、草稿还在"。

    注意：本函数不做归属校验，调用方必须在同一请求内先经 `get_conversation`
    完成 S2 校验，再把 cid 传进来。
    """
    async with session_scope(session) as s:
        conv = await _load(s, cid)
        conv.active_flow = active_flow
        conv.pending_question = pending_question
        conv.draft = None if draft is None else json.dumps(draft, ensure_ascii=False)
        conv.updated_at = utcnow()


async def set_summary(
    cid: int,
    summary: str,
    upto_msg_id: int,
    *,
    session: AsyncSession | None = None,
) -> bool:
    """写滚动摘要与边界，返回是否真的前进。

    **边界只向前移**：并发或重试时，旧的摘要任务不能把边界拉回去，
    否则已经压掉的消息会重新出现在窗口里。
    """
    async with session_scope(session) as s:
        conv = await _load(s, cid)
        current = conv.summary_upto_msg_id or 0
        if upto_msg_id <= current:
            return False
        conv.summary = summary
        conv.summary_upto_msg_id = upto_msg_id
        conv.updated_at = utcnow()
        return True


async def clear_flow(cid: int, *, session: AsyncSession | None = None) -> None:
    """流程结束/取消时清空。与 `append_message` 同事务调用（K3）。"""
    await set_flow(cid, active_flow=None, pending_question=None, draft=None, session=session)


async def list_conversations(*, user_id: str, session: AsyncSession | None = None) -> list[Conversation]:
    """某用户未删除的会话，最近活动在前（前端恢复会话列表用）。"""
    async with session_scope(session) as s:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .order_by(Conversation.updated_at.desc())
        )
        return list((await s.scalars(stmt)).all())


async def list_conversation_summaries(
    *,
    user_id: str,
    limit: int = 20,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """前端会话列表的只读视图。

    标题取“第一条非空的用户消息”，截断 30 字；没有则“新会话”。
    不返回 summary / draft：前端只需要标题、更新时间和未完成流程标记。
    """
    async with session_scope(session) as s:
        first_user_content = (
            select(Message.content)
            .where(
                Message.conversation_id == Conversation.id,
                Message.role == "user",
                Message.content.is_not(None),
                Message.content != "",
            )
            .order_by(Message.id.asc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(
                Conversation.id,
                Conversation.created_at,
                Conversation.updated_at,
                Conversation.active_flow,
                Conversation.pending_question,
                first_user_content.label("title"),
            )
            .where(
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        rows = (await s.execute(stmt)).all()
        return [
            {
                "id": row.id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "title": ((row.title or "新会话").strip()[:30] or "新会话"),
                "active_flow": row.active_flow,
                "pending_question": row.pending_question,
            }
            for row in rows
        ]


async def _load(s: AsyncSession, cid: int) -> Conversation:
    row = await s.get(Conversation, cid)
    if row is None or row.deleted_at is not None:
        raise NotFound("conversation not found")
    return row
