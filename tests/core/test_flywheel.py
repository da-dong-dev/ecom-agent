"""Ch09 飞轮批处理：标准化、查重、合并、失败跳过。

全部 stub 掉模型与 embedding，不打真实服务。
"""
from sqlalchemy import select

from app.core import flywheel
from app.core.flywheel import NormalizeResult, process_pending
from app.core.llm import LLMFormatError
from app.db.models import LowConfQuestion
from scripts.flywheel import seed_fixtures


async def _add_pending(session, question: str) -> LowConfQuestion:
    row = LowConfQuestion(
        question=question,
        source="test",
        reason="test",
        retrieved="[]",
    )
    session.add(row)
    await session.commit()
    return row


async def _add_canonical(
    session,
    question: str,
    *,
    normalized: str | None = None,
    status: str = "pending",
    occurrence: int = 1,
) -> LowConfQuestion:
    row = LowConfQuestion(
        question=question,
        normalized=normalized or question,
        source="test",
        reason="test",
        retrieved="[]",
        status=status,
        occurrence=occurrence,
    )
    session.add(row)
    await session.commit()
    return row


async def test_new_question_creates_canonical(session, monkeypatch):
    row = await _add_pending(session, "价保规则是什么")

    async def fake_normalize(raw, candidates):
        return NormalizeResult(
            normalized_question="价保规则是什么",
            matched_id=None,
            suggested_answer="签收后 7 天内同款降价可申请补差。",
        )

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)

    stats = await process_pending(session=session)

    assert stats == {"processed": 1, "merged": 0, "created": 1, "skipped": 0}
    await session.refresh(row)
    assert row.normalized == "价保规则是什么"
    assert row.suggested_answer == "签收后 7 天内同款降价可申请补差。"
    assert row.status == "pending"


async def test_match_merges_and_increments_occurrence(session, monkeypatch):
    canonical = await _add_canonical(
        session, "价保规则是什么", occurrence=2
    )
    raw = await _add_pending(session, "商品降价了能补差吗")

    async def fake_normalize(raw_question, candidates):
        return NormalizeResult(
            normalized_question="价保规则是什么",
            matched_id=canonical.id,
            suggested_answer="",
        )

    async def fake_similarity(a, b):
        return 0.95

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)
    monkeypatch.setattr(flywheel, "_embedding_similarity", fake_similarity)

    stats = await process_pending(session=session)

    assert stats["merged"] == 1
    await session.refresh(raw)
    await session.refresh(canonical)
    assert raw.status == "merged"
    assert raw.merged_into == canonical.id
    assert canonical.occurrence == 3
    assert canonical.status == "pending"


async def test_rejected_candidate_is_not_revived(session, monkeypatch):
    canonical = await _add_canonical(
        session, "价保规则是什么", status="rejected", occurrence=1
    )
    raw = await _add_pending(session, "价保规则是什么")

    async def fake_normalize(raw_question, candidates):
        return NormalizeResult(
            normalized_question="价保规则是什么",
            matched_id=canonical.id,
        )

    async def fake_similarity(a, b):
        return 1.0

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)
    monkeypatch.setattr(flywheel, "_embedding_similarity", fake_similarity)

    stats = await process_pending(session=session)

    assert stats["merged"] == 1
    await session.refresh(canonical)
    await session.refresh(raw)
    assert canonical.status == "rejected"
    assert canonical.occurrence == 2
    assert raw.status == "merged"


async def test_hallucinated_candidate_is_skipped(session, monkeypatch):
    raw = await _add_pending(session, "价保规则是什么")

    async def fake_normalize(raw_question, candidates):
        return NormalizeResult(
            normalized_question="价保规则是什么",
            matched_id=9999,
        )

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)

    stats = await process_pending(session=session)

    assert stats == {"processed": 1, "merged": 0, "created": 0, "skipped": 1}
    await session.refresh(raw)
    assert raw.normalized is None
    assert raw.status == "pending"


async def test_llm_error_is_skipped_without_change(session, monkeypatch):
    raw = await _add_pending(session, "价保规则是什么")

    async def boom(raw_question, candidates):
        raise LLMFormatError("bad json")

    monkeypatch.setattr(flywheel, "normalize_and_match", boom)

    stats = await process_pending(session=session)

    assert stats["skipped"] == 1
    await session.refresh(raw)
    assert raw.normalized is None
    assert raw.status == "pending"


async def test_same_batch_synonyms_create_one_canonical(session, monkeypatch):
    first = await _add_pending(session, "价保规则是什么")
    second = await _add_pending(session, "价保规则是什么")

    async def fake_normalize(raw_question, candidates):
        return NormalizeResult(
            normalized_question="价保规则是什么",
            matched_id=None,
            suggested_answer="建议答案",
        )

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)

    stats = await process_pending(session=session)

    assert stats == {"processed": 2, "merged": 1, "created": 1, "skipped": 0}
    await session.refresh(first)
    await session.refresh(second)

    canonical = (
        await session.scalars(
            select(LowConfQuestion).where(
                LowConfQuestion.normalized.is_not(None),
                LowConfQuestion.merged_into.is_(None),
            )
        )
    ).all()
    assert len(canonical) == 1
    assert canonical[0].occurrence == 2
    assert first.status == "pending"
    assert second.status == "merged"
    assert second.merged_into == first.id

    # 再跑一次不会重复处理已归并/已标准化的行
    again = await process_pending(session=session)
    assert again["processed"] == 0


async def test_low_similarity_creates_new_canonical(session, monkeypatch):
    canonical = await _add_canonical(session, "价保规则是什么", occurrence=1)
    raw = await _add_pending(session, "商品降价能补差吗")

    async def fake_normalize(raw_question, candidates):
        return NormalizeResult(
            normalized_question="商品降价能否补差",
            matched_id=canonical.id,
        )

    async def fake_similarity(a, b):
        return 0.5

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)
    monkeypatch.setattr(flywheel, "_embedding_similarity", fake_similarity)

    stats = await process_pending(session=session)

    assert stats == {"processed": 1, "merged": 0, "created": 1, "skipped": 0}
    await session.refresh(raw)
    await session.refresh(canonical)
    assert raw.normalized == "商品降价能否补差"
    assert canonical.occurrence == 1


async def test_dry_run_does_not_write(session, monkeypatch):
    raw = await _add_pending(session, "价保规则是什么")

    async def fake_normalize(raw_question, candidates):
        return NormalizeResult(
            normalized_question="价保规则是什么",
            matched_id=None,
            suggested_answer="建议答案",
        )

    monkeypatch.setattr(flywheel, "normalize_and_match", fake_normalize)

    stats = await process_pending(session=session, dry_run=True)

    assert stats == {"processed": 1, "merged": 0, "created": 1, "skipped": 0}
    await session.refresh(raw)
    assert raw.normalized is None
    assert raw.suggested_answer is None
    assert raw.status == "pending"


async def test_seed_fixtures_is_idempotent_and_skips_out_of_kb(
    session, tmp_path
):
    fixture = tmp_path / "badcases.yaml"
    fixture.write_text(
        """
badcases:
  - id: F01
    question: "价保规则是什么"
    expected_keywords: ["价保"]
  - id: F02
    question: "支持以旧换新吗"
    expected_keywords: ["以旧换新"]
out_of_kb:
  - id: O01
    question: "你们有成人轮椅吗"
""",
        encoding="utf-8",
    )

    first = await seed_fixtures(fixture, session=session)
    second = await seed_fixtures(fixture, session=session)

    assert first == {"inserted": 2, "skipped": 0}
    assert second == {"inserted": 0, "skipped": 2}

    rows = (await session.scalars(select(LowConfQuestion))).all()
    assert {row.question for row in rows} == {
        "价保规则是什么",
        "支持以旧换新吗",
    }
    assert all(row.status == "pending" for row in rows)
    assert all(row.retrieved == "[]" for row in rows)
