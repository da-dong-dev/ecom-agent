"""Ch09 人工审核：队列、详情、通过/驳回。

规则（对齐 spec §3.3 / §4.4）：

- 只有 `pending + normalized IS NOT NULL + merged_into IS NULL` 的 canonical 可流转；
- approve：写 `faq(pending)`，canonical → `approved`；
- reject：canonical → `rejected`，终态，不复活；
- 空答案 422；不存在/非 canonical 404；状态不可流转 409；
- 本模块不触发 KB 同步；同步是 Task 4。
"""
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Faq, LowConfQuestion, utcnow
from app.db.session import async_session
from app.kb.loader import load_chunks

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = frozenset(
    {"pending", "merged", "approved", "written_back", "rejected"}
)


class ReviewError(Exception):
    """审核错误的基类。"""


class ReviewNotFound(ReviewError):
    """canonical 不存在，或 id 指向的是原始/已归并行。"""


class ReviewConflict(ReviewError):
    """当前状态不允许这次流转。"""


class ReviewInvalid(ReviewError):
    """入参不合法，例如空答案。"""


@asynccontextmanager
async def _session(
    session: AsyncSession | None,
) -> AsyncIterator[AsyncSession]:
    """有传入 session 就复用；否则自开一个，用完关闭。"""
    own = session is None
    s = session if session is not None else async_session()
    try:
        yield s
    finally:
        if own:
            await s.close()


def _queue_item(row: LowConfQuestion) -> dict[str, Any]:
    return {
        "id": row.id,
        "question": row.question,
        "normalized": row.normalized,
        "suggested_answer": row.suggested_answer,
        "occurrence": row.occurrence,
        "status": row.status,
        "source": row.source,
        "reason": row.reason,
        "created_at": row.created_at,
        "reviewed_at": row.reviewed_at,
    }


async def _canonical(
    session: AsyncSession, review_id: int
) -> LowConfQuestion:
    row = await session.get(LowConfQuestion, review_id)
    if (
        row is None
        or row.normalized is None
        or row.merged_into is not None
    ):
        raise ReviewNotFound(f"审核项不存在：{review_id}")
    return row


def _retrieved_items(
    raw: str | None, chunks: dict[str, dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """把 retrieved 的 id/score 映射回 section/text。三态保留。"""
    if raw is None:
        return None
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []

    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chunk_id = item.get("id")
        chunk = chunks.get(str(chunk_id), {})
        result.append(
            {
                "id": chunk_id,
                "similarity": item.get("similarity"),
                "section": chunk.get("section", ""),
                "text": chunk.get("text", ""),
                "source": chunk.get("source", ""),
            }
        )
    return result


async def list_queue(
    status: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """审核队列。默认只返回待审 canonical；status 可切到其他状态。"""
    if status is not None and status not in ALLOWED_STATUSES:
        raise ReviewInvalid(f"未知状态：{status}")

    async with _session(session) as s:
        stmt = select(LowConfQuestion).where(
            LowConfQuestion.normalized.is_not(None),
            LowConfQuestion.merged_into.is_(None),
        )
        if status is None:
            stmt = stmt.where(LowConfQuestion.status == "pending")
        else:
            stmt = stmt.where(LowConfQuestion.status == status)
        stmt = stmt.order_by(
            LowConfQuestion.occurrence.desc(),
            LowConfQuestion.id.desc(),
        )
        rows = (await s.scalars(stmt)).all()
        return [_queue_item(row) for row in rows]


async def get_detail(
    review_id: int, *, session: AsyncSession | None = None
) -> dict[str, Any]:
    """canonical 详情：归并原话 + retrieved 快照 + 建议答案。"""
    async with _session(session) as s:
        row = await _canonical(s, review_id)
        merged = (
            await s.scalars(
                select(LowConfQuestion)
                .where(LowConfQuestion.merged_into == review_id)
                .order_by(LowConfQuestion.id)
            )
        ).all()
        chunks = {item["id"]: item for item in load_chunks()}

        return {
            **_queue_item(row),
            "retrieved": _retrieved_items(row.retrieved, chunks),
            "merged_questions": [
                {
                    "id": item.id,
                    "question": item.question,
                    "source": item.source,
                    "reason": item.reason,
                    "retrieved": _retrieved_items(item.retrieved, chunks),
                    "created_at": item.created_at,
                }
                for item in merged
            ],
        }


async def approve(
    review_id: int,
    answer: str,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """审核通过：写一条 pending FAQ，canonical → approved。"""
    text = (answer or "").strip()
    if not text:
        raise ReviewInvalid("答案不能为空")

    async with _session(session) as s:
        row = await _canonical(s, review_id)
        if row.status != "pending":
            raise ReviewConflict(f"当前状态不可通过：{row.status}")

        now = utcnow()
        faq = Faq(
            question=row.normalized or row.question,
            answer=text,
            source_type="flywheel",
            source_question_id=row.id,
            reviewed_at=now,
            sync_status="pending",
        )
        s.add(faq)
        row.status = "approved"
        row.reviewed_at = now

        try:
            await s.commit()
        except IntegrityError as exc:
            await s.rollback()
            raise ReviewConflict("该问题已经写过 FAQ") from exc

        logger.info("审核通过 review_id=%s faq_id=%s", review_id, faq.id)
        return {
            "id": row.id,
            "status": row.status,
            "faq_id": faq.id,
            "question": faq.question,
            "answer": faq.answer,
        }


async def reject(
    review_id: int,
    reason: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """审核驳回：canonical → rejected，终态。"""
    async with _session(session) as s:
        row = await _canonical(s, review_id)
        if row.status != "pending":
            raise ReviewConflict(f"当前状态不可驳回：{row.status}")

        row.status = "rejected"
        row.reviewed_at = utcnow()
        await s.commit()

        logger.info("审核驳回 review_id=%s reason=%s", review_id, reason)
        return {
            "id": row.id,
            "status": row.status,
            "reason": reason,
        }
