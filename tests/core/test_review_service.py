"""Ch09 审核服务：队列、详情、approve/reject 状态机。"""
import json

import pytest
from sqlalchemy import select

from app.core.review import (
    ReviewConflict,
    ReviewInvalid,
    ReviewNotFound,
    ReviewSyncError,
    approve,
    get_detail,
    list_queue,
    reject,
    retry_sync,
)
from app.db.models import Faq, LowConfQuestion


@pytest.fixture(autouse=True)
def fake_kb_sync(monkeypatch):
    """审核服务测试不连 Chroma/Ollama；同步由 Task 4 的 build 测试覆盖。"""
    async def fake_build(*, session=None):
        return {
            "version": "test-version",
            "chunks": 1,
            "faq": 1,
            "deleted": 0,
        }

    monkeypatch.setattr("app.core.review.build_kb", fake_build)


async def _add(
    session,
    *,
    question: str,
    normalized: str | None = None,
    status: str = "pending",
    occurrence: int = 1,
    merged_into: int | None = None,
    retrieved: str | None = "[]",
) -> LowConfQuestion:
    row = LowConfQuestion(
        question=question,
        normalized=normalized,
        status=status,
        occurrence=occurrence,
        merged_into=merged_into,
        source="test",
        reason="test",
        retrieved=retrieved,
    )
    session.add(row)
    await session.commit()
    return row


async def test_list_queue_defaults_to_pending_canonical(session):
    pending = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )
    await _add(
        session,
        question="支持以旧换新吗",
        normalized="支持以旧换新吗",
        status="approved",
    )
    raw = await _add(session, question="原始问题")
    await _add(
        session,
        question="同义问题",
        normalized=None,
        status="merged",
        merged_into=pending.id,
    )

    items = await list_queue(session=session)

    assert [item["id"] for item in items] == [pending.id]
    assert items[0]["normalized"] == "价保规则是什么"
    assert raw.normalized is None


async def test_list_queue_sorts_by_occurrence_desc(session):
    low = await _add(
        session, question="低", normalized="低", occurrence=1
    )
    high = await _add(
        session, question="高", normalized="高", occurrence=5
    )

    items = await list_queue(session=session)

    assert [item["id"] for item in items] == [high.id, low.id]


async def test_list_queue_rejects_unknown_status(session):
    with pytest.raises(ReviewInvalid):
        await list_queue("done", session=session)


async def test_detail_includes_merged_questions_and_chunk_snapshot(session):
    chunk_id = "returns#退货运费由谁承担"
    canonical = await _add(
        session,
        question="退货运费谁出",
        normalized="退货运费由谁承担",
        retrieved=json.dumps(
            [{"id": chunk_id, "similarity": 0.5321}], ensure_ascii=False
        ),
    )
    await _add(
        session,
        question="退货邮费谁付",
        status="merged",
        merged_into=canonical.id,
        retrieved="[]",
    )

    detail = await get_detail(canonical.id, session=session)

    assert detail["normalized"] == "退货运费由谁承担"
    assert detail["retrieved"][0]["id"] == chunk_id
    assert detail["retrieved"][0]["section"] == "退货运费由谁承担"
    assert "质量问题" in detail["retrieved"][0]["text"]
    assert detail["merged_questions"][0]["question"] == "退货邮费谁付"
    assert detail["merged_questions"][0]["retrieved"] == []


async def test_detail_preserves_null_and_empty_retrieved(session):
    null_row = await _add(
        session, question="没检索", normalized="没检索", retrieved=None
    )
    empty_row = await _add(
        session, question="零命中", normalized="零命中", retrieved="[]"
    )

    null_detail = await get_detail(null_row.id, session=session)
    empty_detail = await get_detail(empty_row.id, session=session)

    assert null_detail["retrieved"] is None
    assert empty_detail["retrieved"] == []


async def test_detail_missing_or_merged_id_is_not_found(session):
    merged = await _add(
        session,
        question="同义原话",
        normalized=None,
        status="merged",
        merged_into=1,
    )

    with pytest.raises(ReviewNotFound):
        await get_detail(9999, session=session)
    with pytest.raises(ReviewNotFound):
        await get_detail(merged.id, session=session)


async def test_approve_writes_pending_faq_and_marks_approved(session):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )

    result = await approve(
        canonical.id, "签收后 7 天内可申请补差。", session=session
    )

    assert result["status"] == "approved"
    assert result["faq_id"] is not None
    faq = (
        await session.scalars(
            select(Faq).where(Faq.source_question_id == canonical.id)
        )
    ).one()
    assert faq.question == "价保规则是什么"
    assert faq.answer == "签收后 7 天内可申请补差。"
    assert faq.source_type == "flywheel"
    assert faq.reviewed_at is not None
    assert faq.sync_status == "pending"

    await session.refresh(canonical)
    assert canonical.status == "approved"
    assert canonical.reviewed_at is not None


async def test_approve_rejects_empty_answer(session):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )

    with pytest.raises(ReviewInvalid):
        await approve(canonical.id, "   ", session=session)

    await session.refresh(canonical)
    assert canonical.status == "pending"


async def test_second_approve_is_conflict_and_does_not_duplicate_faq(session):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )
    await approve(canonical.id, "第一次答案", session=session)

    with pytest.raises(ReviewConflict):
        await approve(canonical.id, "第二次答案", session=session)

    count = (
        await session.scalars(
            select(Faq).where(Faq.source_question_id == canonical.id)
        )
    ).all()
    assert len(count) == 1


async def test_reject_is_terminal(session):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )

    result = await reject(canonical.id, "活动已过期", session=session)

    assert result == {
        "id": canonical.id,
        "status": "rejected",
        "reason": "活动已过期",
    }
    await session.refresh(canonical)
    assert canonical.status == "rejected"
    assert canonical.reviewed_at is not None

    with pytest.raises(ReviewConflict):
        await approve(canonical.id, "答案", session=session)


async def test_sync_failure_keeps_approved_and_faq_pending(
    session, monkeypatch
):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )

    async def failing_build(*, session=None):
        raise RuntimeError("chroma down")

    monkeypatch.setattr("app.core.review.build_kb", failing_build)

    with pytest.raises(ReviewSyncError):
        await approve(canonical.id, "答案", session=session)

    await session.refresh(canonical)
    faq = (
        await session.scalars(
            select(Faq).where(Faq.source_question_id == canonical.id)
        )
    ).one()
    assert canonical.status == "approved"
    assert canonical.kb_ref is None
    assert faq.sync_status == "pending"


async def test_retry_sync_marks_written_back_after_success(
    session, monkeypatch
):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )

    async def failing_build(*, session=None):
        raise RuntimeError("chroma down")

    monkeypatch.setattr("app.core.review.build_kb", failing_build)
    with pytest.raises(ReviewSyncError):
        await approve(canonical.id, "答案", session=session)

    async def success_build(*, session):
        faq = (
            await session.scalars(
                select(Faq).where(Faq.source_question_id == canonical.id)
            )
        ).one()
        faq.sync_status = "synced"
        faq.kb_version = "v2"
        row = await session.get(LowConfQuestion, canonical.id)
        row.status = "written_back"
        row.kb_ref = f"faq-{faq.id}"
        await session.commit()
        return {
            "version": "v2",
            "chunks": 1,
            "faq": 1,
            "deleted": 0,
        }

    monkeypatch.setattr("app.core.review.build_kb", success_build)

    result = await retry_sync(canonical.id, session=session)

    assert result["status"] == "written_back"
    assert result["kb_version"] == "v2"
    await session.refresh(canonical)
    assert canonical.status == "written_back"
    assert canonical.kb_ref is not None
    faq = (
        await session.scalars(
            select(Faq).where(Faq.source_question_id == canonical.id)
        )
    ).one()
    assert faq.sync_status == "synced"
    assert faq.kb_version == "v2"

    # 已 written_back 再 retry 是幂等的，不再触发 build
    async def boom(*, session=None):
        raise AssertionError("written_back 不应再次同步")

    monkeypatch.setattr("app.core.review.build_kb", boom)
    again = await retry_sync(canonical.id, session=session)
    assert again["status"] == "written_back"


async def test_retry_sync_requires_approved(session):
    canonical = await _add(
        session, question="价保规则是什么", normalized="价保规则是什么"
    )

    with pytest.raises(ReviewConflict):
        await retry_sync(canonical.id, session=session)
