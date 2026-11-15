"""会话/消息仓库：幂等与事务复用。"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.errors import NotFound
from app.db.models import Conversation, Message, utcnow
from app.db.repo_conversation import (
    create_conversation,
    get_conversation,
    list_conversation_summaries,
    set_flow,
)
from app.db.repo_message import (
    append_message,
    get_reply_by_client_message_id,
    list_messages,
)


async def test_create_and_get_conversation(session):
    cid = await create_conversation("u1", session=session)
    assert cid is not None

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.user_id == "u1"


async def test_other_users_conversation_raises_not_found(session):
    """S2：会话归属也在数据层校验，不靠调用方自觉。"""
    cid = await create_conversation("u1", session=session)
    with pytest.raises(NotFound):
        await get_conversation(cid, user_id="u2", session=session)


async def test_set_flow_round_trip(session):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"reason": "尺寸不合适"},
        session=session,
    )
    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    assert conv.draft == '{"reason": "尺寸不合适"}'


async def test_duplicate_client_message_id_returns_same_row(session):
    """K4：重试不该写出两条消息，也不该抛错——返回首次的 id。"""
    cid = await create_conversation("u1", session=session)

    first = await append_message(cid, "user", "退货怎么弄", client_message_id="cm-1", session=session)
    second = await append_message(cid, "user", "退货怎么弄", client_message_id="cm-1", session=session)

    assert first == second
    count = (
        await session.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == cid)
        )
    ).scalar()
    assert count == 1


async def test_messages_without_idempotency_key_accumulate(session):
    cid = await create_conversation("u1", session=session)
    await append_message(cid, "user", "第一句", session=session)
    await append_message(cid, "assistant", "第二句", session=session)

    msgs = await list_messages(cid, session=session)
    assert [m.role for m in msgs] == ["user", "assistant"]


async def test_reply_to_client_message_id_round_trip(session):
    """Ch08：助手消息能精确关联到触发它的用户消息。"""
    cid = await create_conversation("u1", session=session)
    await append_message(
        cid,
        "user",
        "订单1002 不想要了",
        client_message_id="cm-reply-1",
        session=session,
    )
    reply_id = await append_message(
        cid,
        "assistant",
        "本次应退 1249.00 元。",
        reply_to_client_message_id="cm-reply-1",
        session=session,
    )

    reply = await get_reply_by_client_message_id(
        cid, "cm-reply-1", session=session
    )
    assert reply is not None
    assert reply.id == reply_id
    assert reply.role == "assistant"
    assert reply.content == "本次应退 1249.00 元。"

    # 用户消息用 client_message_id，助手消息用 reply_to_client_message_id；
    # 两列都存同一个值，互不冲突（两个唯一索引分别约束不同列）。
    msgs = await list_messages(cid, session=session)
    assert [m.role for m in msgs] == ["user", "assistant"]


async def test_duplicate_reply_to_client_message_id_returns_same_row(session):
    """Ch08：同一条用户消息并发重试，助手回复只留一条。"""
    cid = await create_conversation("u1", session=session)

    first = await append_message(
        cid,
        "assistant",
        "回答 A",
        reply_to_client_message_id="cm-reply-2",
        session=session,
    )
    second = await append_message(
        cid,
        "assistant",
        "回答 B",
        reply_to_client_message_id="cm-reply-2",
        session=session,
    )

    assert first == second
    count = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.reply_to_client_message_id == "cm-reply-2")
        )
    ).scalar()
    assert count == 1


async def test_reply_lookup_is_scoped_to_conversation(session):
    cid1 = await create_conversation("u1", session=session)
    cid2 = await create_conversation("u1", session=session)
    await append_message(
        cid1,
        "assistant",
        "回答",
        reply_to_client_message_id="cm-reply-3",
        session=session,
    )

    assert (
        await get_reply_by_client_message_id(
            cid2, "cm-reply-3", session=session
        )
        is None
    )


async def test_list_conversation_summaries_title_order_and_scope(session):
    cid1 = await create_conversation("u1", session=session)
    cid2 = await create_conversation("u1", session=session)
    cid_other = await create_conversation("u2", session=session)
    cid_deleted = await create_conversation("u1", session=session)

    # 第一条空的用户消息不能当标题；取下一条非空的
    await append_message(
        cid1, "user", None, client_message_id="empty-title", session=session
    )
    await append_message(
        cid1,
        "user",
        "订单1002 不想要了",
        client_message_id="cm-title",
        session=session,
    )
    await append_message(cid2, "user", "退货政策是什么", session=session)

    # 更新 cid1 的 updated_at，让它排到最前
    await set_flow(
        cid1,
        active_flow="refund",
        pending_question="order_id",
        draft={},
        session=session,
    )

    deleted = await session.get(Conversation, cid_deleted)
    deleted.deleted_at = utcnow()
    await session.flush()

    items = await list_conversation_summaries(user_id="u1", session=session)

    assert [item["id"] for item in items] == [cid1, cid2]
    assert items[0]["title"] == "订单1002 不想要了"
    assert items[0]["active_flow"] == "refund"
    assert items[0]["pending_question"] == "order_id"
    assert cid_other not in [item["id"] for item in items]
    assert cid_deleted not in [item["id"] for item in items]

    limited = await list_conversation_summaries(
        user_id="u1", limit=1, session=session
    )
    assert [item["id"] for item in limited] == [cid1]


async def test_list_conversation_summaries_truncates_title(session):
    cid = await create_conversation("u1", session=session)
    await append_message(cid, "user", "嗯" * 40, session=session)

    items = await list_conversation_summaries(user_id="u1", session=session)
    assert items[0]["title"] == "嗯" * 30


async def test_shared_session_writes_are_atomic(engine, session):
    """K3：传同一个 session 写会话 + 消息，回滚后两条都不该留下。"""
    cid = await create_conversation("u1", session=session)
    await append_message(cid, "user", "要退款", session=session)

    await session.rollback()

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as fresh:
        assert (await fresh.execute(select(func.count()).select_from(Conversation))).scalar() == 0
        assert (await fresh.execute(select(func.count()).select_from(Message))).scalar() == 0
