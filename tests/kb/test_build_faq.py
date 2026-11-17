"""FAQ 合并进知识库：版本、upsert、stale 删除、失败不假成功。"""
import pytest
from sqlalchemy import select

from app.core.kb_version import current_kb_version, kb_version
from app.db.models import Faq, LowConfQuestion, utcnow
from app.kb.build import build_kb
from app.kb.loader import faq_chunks, load_chunks


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeClient:
    """记录 Chroma/Ollama 调用；不连真实服务。"""

    def __init__(self, *, existing: set[str] | None = None, fail_upsert: bool = False):
        self.existing = existing or set()
        self.fail_upsert = fail_upsert
        self.upserted: list[str] = []
        self.deleted: list[str] = []
        self.aclosed = False

    async def post(self, url, json=None, timeout=None):
        if url.endswith("/collections"):
            return FakeResponse(
                {
                    "id": "col-1",
                    "configuration_json": {"hnsw": {"space": "cosine"}},
                }
            )
        if "api/embed" in url:
            inputs = json["input"]
            return FakeResponse(
                {"embeddings": [[float(index + 1)] for index, _ in enumerate(inputs)]}
            )
        if url.endswith("/get"):
            return FakeResponse({"ids": sorted(self.existing)})
        if url.endswith("/upsert"):
            if self.fail_upsert:
                raise RuntimeError("upsert failed")
            self.upserted = list(json["ids"])
            return FakeResponse({})
        if url.endswith("/delete"):
            self.deleted = list(json["ids"])
            return FakeResponse({})
        raise AssertionError(f"unexpected url: {url}")

    async def aclose(self) -> None:
        self.aclosed = True


async def _approved_faq(session):
    canonical = LowConfQuestion(
        question="价保规则是什么",
        normalized="价保规则是什么",
        source="test",
        reason="test",
        retrieved="[]",
        status="approved",
        occurrence=1,
    )
    session.add(canonical)
    await session.flush()
    faq = Faq(
        question=canonical.normalized,
        answer="签收后 7 天内可申请补差。",
        source_type="flywheel",
        source_question_id=canonical.id,
        reviewed_at=utcnow(),
        sync_status="pending",
    )
    session.add(faq)
    await session.commit()
    return canonical, faq


async def test_build_merges_faq_and_marks_written_back(session):
    canonical, faq = await _approved_faq(session)
    client = FakeClient(existing={"returns#旧块", "faq-1"})

    result = await build_kb(session=session, client=client)

    expected_version = kb_version(
        load_chunks()
        + faq_chunks(
            [
                {
                    "id": faq.id,
                    "question": faq.question,
                    "answer": faq.answer,
                }
            ]
        )
    )
    assert result["version"] == expected_version
    assert "faq-1" in client.upserted
    assert "returns#退货运费由谁承担" in client.upserted
    assert client.deleted == ["returns#旧块"]

    await session.refresh(faq)
    await session.refresh(canonical)
    assert faq.sync_status == "synced"
    assert faq.kb_version == expected_version
    assert canonical.status == "written_back"
    assert canonical.kb_ref == f"faq-{faq.id}"


async def test_build_failure_keeps_pending_and_approved(session):
    canonical, faq = await _approved_faq(session)
    client = FakeClient(fail_upsert=True)

    with pytest.raises(RuntimeError, match="upsert failed"):
        await build_kb(session=session, client=client)

    await session.refresh(faq)
    await session.refresh(canonical)
    assert faq.sync_status == "pending"
    assert faq.kb_version is None
    assert canonical.status == "approved"
    assert canonical.kb_ref is None


async def test_build_includes_manual_faq_but_does_not_touch_canonical(session):
    manual = Faq(
        question="手工 FAQ",
        answer="手工答案",
        source_type="manual",
        source_question_id=None,
        reviewed_at=utcnow(),
        sync_status="pending",
    )
    session.add(manual)
    await session.commit()

    client = FakeClient()
    await build_kb(session=session, client=client)

    await session.refresh(manual)
    assert manual.sync_status == "synced"
    assert f"faq-{manual.id}" in client.upserted

    rows = (await session.scalars(select(LowConfQuestion))).all()
    assert rows == []


async def test_current_kb_version_includes_faq(session):
    baseline = await current_kb_version(session=session)
    _, faq = await _approved_faq(session)
    after = await current_kb_version(session=session)

    expected = kb_version(
        load_chunks()
        + faq_chunks(
            [{"id": faq.id, "question": faq.question, "answer": faq.answer}]
        )
    )
    assert baseline == kb_version()
    assert after == expected
    assert after != baseline
