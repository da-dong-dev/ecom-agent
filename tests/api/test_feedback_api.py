"""Ch09 反馈入口：点踩落池、点赞不落库、归属/消息校验。"""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.feedback import FeedbackRequest, feedback
from app.db.models import LowConfQuestion
from app.db.repo_conversation import create_conversation
from app.db.repo_message import append_message


async def _seed(session, *, user_id: str = "u1"):
    cid = await create_conversation(user_id, session=session)
    user_id_msg = await append_message(
        cid, "user", "订单1002 不想要了", session=session
    )
    assistant_id = await append_message(
        cid,
        "assistant",
        "订单1002 还在途中，可以拒收后全额退款。",
        session=session,
    )
    return cid, user_id_msg, assistant_id


async def test_down_records_low_conf(session):
    cid, _, assistant_id = await _seed(session)

    result = await feedback(
        FeedbackRequest(
            conversation_id=cid,
            message_id=assistant_id,
            rating="down",
        ),
        session=session,
    )

    assert result["recorded"] is True
    row = (
        await session.scalars(
            select(LowConfQuestion).where(LowConfQuestion.id == result["id"])
        )
    ).one()
    assert row.question == "订单1002 不想要了"
    assert row.source == "feedback"
    assert row.reason == f"feedback_down:{assistant_id}"
    assert row.retrieved is None
    assert row.conversation_id == cid


async def test_down_uses_provided_question(session):
    cid, _, assistant_id = await _seed(session)

    result = await feedback(
        FeedbackRequest(
            conversation_id=cid,
            message_id=assistant_id,
            rating="down",
            question="退款到账太慢",
        ),
        session=session,
    )

    row = (
        await session.scalars(
            select(LowConfQuestion).where(LowConfQuestion.id == result["id"])
        )
    ).one()
    assert row.question == "退款到账太慢"


async def test_down_is_idempotent_per_message(session):
    cid, _, assistant_id = await _seed(session)
    req = FeedbackRequest(
        conversation_id=cid, message_id=assistant_id, rating="down"
    )

    first = await feedback(req, session=session)
    second = await feedback(req, session=session)

    assert first["recorded"] is True
    assert second == {"ok": True, "recorded": False, "id": first["id"]}
    rows = (await session.scalars(select(LowConfQuestion))).all()
    assert len(rows) == 1


async def test_up_does_not_write(session):
    cid, _, assistant_id = await _seed(session)

    result = await feedback(
        FeedbackRequest(
            conversation_id=cid,
            message_id=assistant_id,
            rating="up",
        ),
        session=session,
    )

    assert result == {"ok": True, "recorded": False}
    rows = (await session.scalars(select(LowConfQuestion))).all()
    assert rows == []


async def test_other_users_conversation_is_404(session):
    cid, _, assistant_id = await _seed(session, user_id="u2")

    with pytest.raises(HTTPException) as exc:
        await feedback(
            FeedbackRequest(
                conversation_id=cid,
                message_id=assistant_id,
                rating="down",
            ),
            session=session,
        )

    assert exc.value.status_code == 404


async def test_message_outside_conversation_is_404(session):
    cid, _, _ = await _seed(session)
    _, _, other_assistant = await _seed(session)

    with pytest.raises(HTTPException) as exc:
        await feedback(
            FeedbackRequest(
                conversation_id=cid,
                message_id=other_assistant,
                rating="down",
            ),
            session=session,
        )

    assert exc.value.status_code == 404


async def test_empty_question_without_user_message_is_422(session):
    cid = await create_conversation("u1", session=session)
    assistant_id = await append_message(
        cid, "assistant", "只有助手消息", session=session
    )

    with pytest.raises(HTTPException) as exc:
        await feedback(
            FeedbackRequest(
                conversation_id=cid,
                message_id=assistant_id,
                rating="down",
            ),
            session=session,
        )

    assert exc.value.status_code == 422
