"""Ch09 用户反馈入口（可选）。

只处理助手消息的点赞/点踩：

- `down` → 写一条 `low_conf_questions(source='feedback', hits=NULL)`；
- `up` → 只记日志，不落库；
- 会话/消息归属校验复用 Ch06，越权与不存在统一 404；
- 同一 `message_id` 重复点踩幂等，不重复落池。

`hits=None` 表示“不是检索弱证据导致”，审核时不要把它当成知识缺口统计。
"""
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.chat import ChatError, conversation_access
from app.config import get_settings
from app.core.fallback import record_low_conf
from app.db.models import LowConfQuestion, Message
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class FeedbackRequest(BaseModel):
    conversation_id: int
    message_id: int
    rating: Literal["up", "down"]
    question: str = ""


async def _assistant_message(
    session: AsyncSession, conversation_id: int, message_id: int
) -> Message:
    row = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return row


async def _question_for(
    session: AsyncSession,
    conversation_id: int,
    provided: str,
) -> str:
    text = (provided or "").strip()
    if text:
        return text
    content = await session.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    return (content or "").strip()


@router.post("/api/feedback")
async def feedback(
    req: FeedbackRequest,
    session: SessionDep,
) -> dict:
    try:
        await conversation_access(
            req.conversation_id,
            user_id=get_settings().demo_user_id,
            session=session,
        )
    except ChatError as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc

    await _assistant_message(session, req.conversation_id, req.message_id)
    question = await _question_for(session, req.conversation_id, req.question)
    if not question:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_arg", "message": "question 不能为空"},
        )

    if req.rating == "up":
        logger.info(
            "用户点赞 conversation_id=%s message_id=%s",
            req.conversation_id,
            req.message_id,
        )
        return {"ok": True, "recorded": False}

    reason = f"feedback_down:{req.message_id}"
    existing = await session.scalar(
        select(LowConfQuestion.id)
        .where(
            LowConfQuestion.conversation_id == req.conversation_id,
            LowConfQuestion.source == "feedback",
            LowConfQuestion.reason == reason,
        )
        .limit(1)
    )
    if existing is not None:
        return {"ok": True, "recorded": False, "id": existing}

    row_id = await record_low_conf(
        question=question,
        reason=reason,
        hits=None,
        conversation_id=req.conversation_id,
        source="feedback",
        session=session,
    )
    await session.commit()
    logger.info(
        "用户点踩 conversation_id=%s message_id=%s low_conf_id=%s",
        req.conversation_id,
        req.message_id,
        row_id,
    )
    return {"ok": True, "recorded": True, "id": row_id}
