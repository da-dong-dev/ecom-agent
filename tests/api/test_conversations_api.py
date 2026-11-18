"""Ch08 历史接口：会话列表 / 消息序列化 / 归属 404。

HTTP 层用 TestClient；数据层用 monkeypatch 注入，避免碰 `data/app.db`。
序列化器只允许白名单字段，这条测试是防止内部字段泄露的守卫。
"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api import conversations as conversations_module
from app.db.errors import NotFound
from app.db.models import Conversation, Message

CREATED = datetime(2026, 11, 15, 8, 0)
UPDATED = datetime(2026, 11, 15, 8, 5)


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)          # 不用 with：跳过 lifespan，避免校验密钥


def test_list_conversations(client, monkeypatch):
    async def fake_list(*, user_id, limit=20, session=None):
        assert user_id == "u1"
        assert limit == 20
        return [
            {
                "id": 12,
                "created_at": CREATED,
                "updated_at": UPDATED,
                "title": "订单1002 不想要了",
                "active_flow": "refund",
                "pending_question": "order_id",
            }
        ]

    monkeypatch.setattr(
        conversations_module, "list_conversation_summaries", fake_list
    )

    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    assert resp.json() == {
        "items": [
            {
                "id": 12,
                "created_at": "2026-11-15T08:00:00",
                "updated_at": "2026-11-15T08:05:00",
                "title": "订单1002 不想要了",
                "active_flow": "refund",
                "pending_question": "order_id",
            }
        ]
    }


def test_messages_missing_or_foreign_returns_404(client, monkeypatch):
    calls: list[tuple[int, str]] = []

    async def fake_get(cid, *, user_id, session=None):
        calls.append((cid, user_id))
        raise NotFound("conversation not found")

    monkeypatch.setattr(conversations_module, "get_conversation", fake_get)

    resp = client.get("/api/conversations/999/messages")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert calls == [(999, "u1")]


def test_messages_serialize_whitelist_and_skip_tool(client, monkeypatch):
    conv = Conversation(
        id=12,
        user_id="u1",
        created_at=CREATED,
        updated_at=UPDATED,
        active_flow="refund",
        pending_question="order_id",
    )
    user = Message(
        id=34,
        conversation_id=12,
        role="user",
        content="订单1002 不想要了",
        client_message_id="uuid-1",
        meta=json.dumps(
            {
                "selected_action": {
                    "type": "select_order",
                    "order_id": "1002",
                },
                "secret": "should_not_leak",
            }
        ),
        created_at=CREATED,
    )
    assistant = Message(
        id=35,
        conversation_id=12,
        role="assistant",
        content="本次应退 1249.00 元。",
        status="completed",
        reply_to_client_message_id="uuid-1",
        meta=json.dumps(
            {
                "intent": "退款申请",
                "route": "rules",
                "citation_ids": [],
                "resumed": False,
                "actions": [{"type": "transfer_human"}],
                "signals": {
                    "amount": 124900,
                    "pending_question": None,
                    "refund_eligible": True,
                },
                "tool_code": "should_not_leak",
                "trace_id": "should_not_leak",
            }
        ),
        created_at=UPDATED,
    )
    error = Message(
        id=36,
        conversation_id=12,
        role="assistant",
        content="服务暂时不可用",
        status="error",
        meta=json.dumps({"status": "error"}),
        created_at=UPDATED,
    )
    tool = Message(
        id=37,
        conversation_id=12,
        role="tool",
        content="内部工具结果",
        meta=json.dumps({"tool_code": "ok"}),
        created_at=UPDATED,
    )

    async def fake_get(cid, *, user_id, session=None):
        return conv

    async def fake_list(cid, *, session=None):
        return [user, assistant, tool, error]

    monkeypatch.setattr(conversations_module, "get_conversation", fake_get)
    monkeypatch.setattr(conversations_module, "list_messages", fake_list)

    resp = client.get("/api/conversations/12/messages")
    assert resp.status_code == 200
    body = resp.json()

    assert body["conversation_id"] == 12
    assert body["active_flow"] == "refund"
    assert body["pending_question"] == "order_id"
    assert [item["role"] for item in body["items"]] == [
        "user",
        "assistant",
        "assistant",
    ]

    user_item, assistant_item, error_item = body["items"]
    assert user_item["client_message_id"] == "uuid-1"
    assert user_item["meta"] == {
        "selected_action": {"type": "select_order", "order_id": "1002"}
    }

    assert assistant_item["reply_to_client_message_id"] == "uuid-1"
    assert assistant_item["meta"]["actions"] == [{"type": "transfer_human"}]
    assert assistant_item["meta"]["signals"] == {
        "amount": 124900,
        "pending_question": None,
        "refund_eligible": True,
    }
    assert "tool_code" not in assistant_item["meta"]
    assert "trace_id" not in assistant_item["meta"]
    assert "should_not_leak" not in json.dumps(body)

    assert error_item["status"] == "error"
    assert error_item["reply_to_client_message_id"] is None
    assert error_item["meta"]["signals"] == {}


def test_delete_conversation_soft_deletes_own(client, monkeypatch):
    calls: list[tuple[int, str]] = []

    async def fake_delete(cid, *, user_id, session=None):
        calls.append((cid, user_id))

    monkeypatch.setattr(conversations_module, "soft_delete_conversation", fake_delete)

    resp = client.delete("/api/conversations/12")
    assert resp.status_code == 204
    assert calls == [(12, "u1")]


def test_delete_missing_or_foreign_returns_404(client, monkeypatch):
    async def fake_delete(cid, *, user_id, session=None):
        raise NotFound("conversation not found")

    monkeypatch.setattr(conversations_module, "soft_delete_conversation", fake_delete)

    resp = client.delete("/api/conversations/999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
