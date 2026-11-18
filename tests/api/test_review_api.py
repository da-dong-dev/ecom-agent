"""Ch09 审核 API：开关、错误码、请求/响应接线。

服务层行为由 test_review_service.py 覆盖；这里只验证 HTTP 映射。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import review as review_api
from app.core.review import (
    ReviewConflict,
    ReviewInvalid,
    ReviewNotFound,
    ReviewSyncError,
)
from app.db.session import get_session


@pytest.fixture
def client():
    from app.main import app

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(
        review_api, "get_settings", lambda: SimpleNamespace(enable_review_api=True)
    )


def test_disabled_returns_404(client, monkeypatch):
    monkeypatch.setattr(
        review_api, "get_settings", lambda: SimpleNamespace(enable_review_api=False)
    )

    resp = client.get("/api/review/queue")

    assert resp.status_code == 404


def test_queue_returns_items(client, enabled, monkeypatch):
    async def fake_list(status, *, session=None):
        assert status is None
        return [{"id": 12, "normalized": "价保规则是什么", "occurrence": 3}]

    monkeypatch.setattr(review_api, "list_queue", fake_list)

    resp = client.get("/api/review/queue")

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [{"id": 12, "normalized": "价保规则是什么", "occurrence": 3}]
    }


def test_queue_invalid_status_returns_422(client, enabled, monkeypatch):
    async def fake_list(status, *, session=None):
        raise ReviewInvalid("未知状态")

    monkeypatch.setattr(review_api, "list_queue", fake_list)

    resp = client.get("/api/review/queue?status=done")

    assert resp.status_code == 422


def test_detail_not_found_returns_404(client, enabled, monkeypatch):
    async def fake_detail(review_id, *, session=None):
        raise ReviewNotFound("missing")

    monkeypatch.setattr(review_api, "get_detail", fake_detail)

    resp = client.get("/api/review/9999")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_approve_empty_answer_returns_422(client, enabled, monkeypatch):
    async def fake_approve(review_id, answer, *, session=None):
        raise ReviewInvalid("答案不能为空")

    monkeypatch.setattr(review_api, "approve", fake_approve)

    resp = client.post("/api/review/12/approve", json={"answer": ""})

    assert resp.status_code == 422


def test_approve_conflict_returns_409(client, enabled, monkeypatch):
    async def fake_approve(review_id, answer, *, session=None):
        raise ReviewConflict("当前状态不可通过：approved")

    monkeypatch.setattr(review_api, "approve", fake_approve)

    resp = client.post("/api/review/12/approve", json={"answer": "答案"})

    assert resp.status_code == 409


def test_approve_sync_failure_returns_502(client, enabled, monkeypatch):
    async def fake_approve(review_id, answer, *, session=None):
        raise ReviewSyncError("FAQ 已写入，但知识库同步失败，请重试")

    monkeypatch.setattr(review_api, "approve", fake_approve)

    resp = client.post("/api/review/12/approve", json={"answer": "答案"})

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "sync_failed"


def test_retry_success(client, enabled, monkeypatch):
    async def fake_retry(review_id, *, session=None):
        assert review_id == 12
        return {
            "id": 12,
            "status": "written_back",
            "faq_id": 3,
            "kb_ref": "faq-3",
            "kb_version": "v2",
        }

    monkeypatch.setattr(review_api, "retry_sync", fake_retry)

    resp = client.post("/api/review/12/retry")

    assert resp.status_code == 200
    assert resp.json()["status"] == "written_back"


def test_approve_and_reject_success(client, enabled, monkeypatch):
    async def fake_approve(review_id, answer, *, session=None):
        assert review_id == 12
        assert answer == "可以水洗，注意阴干"
        return {"id": 12, "status": "approved", "faq_id": 3}

    async def fake_reject(review_id, reason, *, session=None):
        assert review_id == 13
        assert reason == "活动已过期"
        return {"id": 13, "status": "rejected", "reason": reason}

    monkeypatch.setattr(review_api, "approve", fake_approve)
    monkeypatch.setattr(review_api, "reject", fake_reject)

    approved = client.post(
        "/api/review/12/approve", json={"answer": "可以水洗，注意阴干"}
    )
    rejected = client.post(
        "/api/review/13/reject", json={"reason": "活动已过期"}
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
