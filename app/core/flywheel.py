"""Ch09 飞轮：低置信问题标准化、查重与池子批处理。

边界（对齐 spec D-F1/D-F2/D-F6）：

- 只处理 `low_conf_questions` 里 `status='pending' AND normalized IS NULL` 的原话；
- 先让模型标准化并给候选 id，再用 embedding 做二次校验（阈值 0.9）；
- 命中候选 → 原子累加 `occurrence`，原话 `status='merged'`；
- 未命中 → 写 `normalized/suggested_answer`，保持 `pending`，等人工审核；
- 任何异常/幻觉 id → 跳过该行，不改数据，下一轮可重试。

**不自动写 FAQ、不自动审核、不碰知识库**——那些是 Task 3/4 的事。
"""
import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import LLMFormatError, structured_call
from app.core.prompts import (
    FLYWHEEL_NORMALIZE_PARAMETERS,
    FLYWHEEL_NORMALIZE_TOOL_NAME,
    flywheel_normalize_messages,
)
from app.core.retrieval import cosine, embed
from app.db.models import LowConfQuestion
from app.db.session import async_session

logger = logging.getLogger(__name__)

EMBED_MATCH_THRESHOLD = 0.9
CANDIDATE_LIMIT = 200


class NormalizeResult(BaseModel):
    normalized_question: str
    matched_id: int | None = None
    suggested_answer: str = ""


class HallucinatedCandidate(Exception):
    """模型返回了候选列表里不存在的 id。"""


def _parse_result(data: dict[str, Any]) -> NormalizeResult:
    normalized = str(data.get("normalized_question") or "").strip()
    if not normalized:
        raise LLMFormatError("normalized_question 为空")

    raw_id = data.get("matched_id")
    if raw_id in (None, "", "null"):
        matched_id = None
    else:
        try:
            matched_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise LLMFormatError(f"matched_id 不是整数: {raw_id!r}") from exc

    suggested = str(data.get("suggested_answer") or "").strip()
    return NormalizeResult(
        normalized_question=normalized,
        matched_id=matched_id,
        suggested_answer=suggested,
    )


async def normalize_and_match(
    raw_question: str, candidates: list[dict[str, Any]]
) -> NormalizeResult:
    """让模型标准化原话并选择候选；不做 DB 操作。"""
    data = await structured_call(
        FLYWHEEL_NORMALIZE_TOOL_NAME,
        FLYWHEEL_NORMALIZE_PARAMETERS,
        flywheel_normalize_messages(raw_question, candidates),
        temperature=0.0,
    )
    return _parse_result(data)


async def _embedding_similarity(a: str, b: str) -> float:
    """两次文本一起 embedding，返回余弦相似度。"""
    vectors = await embed([a, b])
    if len(vectors) != 2:
        raise RuntimeError("embedding 返回数量不匹配")
    return cosine(vectors[0], vectors[1])


async def _resolve_candidate(
    result: NormalizeResult,
    candidates: list[dict[str, Any]],
    by_id: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """把模型候选变成可合并的 canonical；不通过就返回 None。

    - 模型给了 id：必须存在，且 embedding 相似度 >= 0.9；
    - 模型没给 id：先做精确标准问题匹配，避免同批新 canonical 被漏合并。
    """
    if result.matched_id is not None:
        candidate = by_id.get(result.matched_id)
        if candidate is None:
            raise HallucinatedCandidate(str(result.matched_id))
        similarity = await _embedding_similarity(
            result.normalized_question, candidate["normalized"]
        )
        if similarity < EMBED_MATCH_THRESHOLD:
            logger.info(
                "飞轮候选相似度不足 id=%s sim=%.3f", result.matched_id, similarity
            )
            return None
        return candidate

    for candidate in candidates:
        if candidate["normalized"].strip() == result.normalized_question:
            return candidate
    return None


async def process_pending(
    limit: int = 100,
    *,
    session: AsyncSession | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """处理一批 pending 原话，返回 `{processed, merged, created, skipped}`。

    `dry_run=True` 仍然调用模型判断，但不写库；因此同批新建的 canonical
    不会出现在后续行的候选里，dry-run 统计只用于预览。
    """
    stats = {"processed": 0, "merged": 0, "created": 0, "skipped": 0}
    if limit <= 0:
        return stats

    own_session = session is None
    s = session if session is not None else async_session()
    try:
        pending = list(
            (
                await s.scalars(
                    select(LowConfQuestion)
                    .where(
                        LowConfQuestion.status == "pending",
                        LowConfQuestion.normalized.is_(None),
                    )
                    .order_by(LowConfQuestion.id)
                    .limit(limit)
                )
            ).all()
        )
        canonical_rows = list(
            (
                await s.scalars(
                    select(LowConfQuestion)
                    .where(
                        LowConfQuestion.normalized.is_not(None),
                        LowConfQuestion.merged_into.is_(None),
                    )
                    .order_by(
                        LowConfQuestion.occurrence.desc(),
                        LowConfQuestion.id.desc(),
                    )
                    .limit(CANDIDATE_LIMIT)
                )
            ).all()
        )
        candidates: list[dict[str, Any]] = [
            {
                "id": row.id,
                "normalized": row.normalized or "",
                "occurrence": row.occurrence,
                "status": row.status,
            }
            for row in canonical_rows
        ]
        by_id = {item["id"]: item for item in candidates}

        for row in pending:
            stats["processed"] += 1
            try:
                result = await normalize_and_match(row.question, candidates)
                candidate = await _resolve_candidate(result, candidates, by_id)

                if candidate is not None:
                    if not dry_run:
                        # 原子累加，避免并发批处理时 read-modify-write 丢计数。
                        await s.execute(
                            update(LowConfQuestion)
                            .where(LowConfQuestion.id == candidate["id"])
                            .values(
                                occurrence=LowConfQuestion.occurrence + 1
                            )
                        )
                        row.merged_into = candidate["id"]
                        row.status = "merged"
                        await s.commit()
                    candidate["occurrence"] += 1
                    stats["merged"] += 1
                    continue

                if not dry_run:
                    row.normalized = result.normalized_question
                    row.suggested_answer = result.suggested_answer or None
                    await s.flush()
                    await s.commit()
                    new_candidate = {
                        "id": row.id,
                        "normalized": result.normalized_question,
                        "occurrence": 1,
                        "status": row.status,
                    }
                    candidates.append(new_candidate)
                    by_id[row.id] = new_candidate
                stats["created"] += 1
            except Exception:
                logger.warning(
                    "飞轮处理跳过 row=%s question=%r",
                    row.id,
                    row.question,
                    exc_info=True,
                )
                if not dry_run:
                    await s.rollback()
                stats["skipped"] += 1
    finally:
        if own_session:
            await s.close()
    return stats
