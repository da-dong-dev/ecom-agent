"""Ch08 前端历史接口：会话列表与消息回放。

只服务 C 端展示：

- 列表只返回当前用户的未删除会话，最多 20 条；
- 消息只返回 user / assistant，`meta` 走字段白名单；
- 不存在 / 不属于本人 / 已软删统一 404，不泄露存在性（S1/S2）。

**不返回** tool 消息、`tool_calls`、`trace_id`、`draft`、`order_snapshot`、
完整 `refund_judgement` 或任何置信度/规则 id。
"""
import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.db.errors import NotFound
from app.db.models import Message
from app.db.repo_conversation import (
    get_conversation,
    list_conversation_summaries,
    soft_delete_conversation,
)
from app.db.repo_message import list_messages

router = APIRouter()

_ROLE_WHITELIST = frozenset({"user", "assistant"})
_ASSISTANT_META_KEYS = (
    "intent",
    "route",
    "citation_ids",
    "citations",
    "resumed",
    "actions",
)
_SIGNAL_KEYS = ("amount", "pending_question", "refund_eligible", "handoff")


def _parse_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _user_item(row: Message) -> dict[str, Any]:
    meta = _parse_meta(row.meta)
    return {
        "id": row.id,
        "role": "user",
        "content": row.content or "",
        "created_at": row.created_at,
        "client_message_id": row.client_message_id,
        "meta": {"selected_action": meta.get("selected_action")},
    }


def _assistant_item(row: Message) -> dict[str, Any]:
    meta = _parse_meta(row.meta)
    raw_signals = meta.get("signals")
    signals = raw_signals if isinstance(raw_signals, dict) else {}

    item_meta = {key: meta.get(key) for key in _ASSISTANT_META_KEYS}
    item_meta["citation_ids"] = item_meta.get("citation_ids") or []
    item_meta["actions"] = item_meta.get("actions") or []
    item_meta["resumed"] = bool(item_meta.get("resumed"))
    item_meta["signals"] = {
        key: signals.get(key) for key in _SIGNAL_KEYS if key in signals
    }

    return {
        "id": row.id,
        "role": "assistant",
        "content": row.content or "",
        "created_at": row.created_at,
        "status": row.status,
        "reply_to_client_message_id": row.reply_to_client_message_id,
        "meta": item_meta,
    }


@router.get("/api/conversations")
async def list_conversations() -> dict[str, Any]:
    """当前用户的最近会话；没有会话时返回空列表，不返回 404。"""
    user_id = get_settings().demo_user_id
    items = await list_conversation_summaries(user_id=user_id, limit=20)
    return {"items": items}


@router.get("/api/conversations/{cid}/messages")
async def get_conversation_messages(cid: int) -> dict[str, Any]:
    """会话历史；不存在 / 不属于本人 / 已软删统一 404。"""
    user_id = get_settings().demo_user_id
    try:
        conv = await get_conversation(cid, user_id=user_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc

    items: list[dict[str, Any]] = []
    for row in await list_messages(cid):
        if row.role not in _ROLE_WHITELIST:
            continue
        items.append(_user_item(row) if row.role == "user" else _assistant_item(row))

    return {
        "conversation_id": cid,
        "active_flow": conv.active_flow,
        "pending_question": conv.pending_question,
        "items": items,
    }



@router.delete("/api/conversations/{cid}", status_code=204)
async def delete_conversation(cid: int) -> None:
    """软删当前用户的会话；不存在 / 不属于本人 / 已软删统一 404。"""
    user_id = get_settings().demo_user_id
    try:
        await soft_delete_conversation(cid, user_id=user_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc
