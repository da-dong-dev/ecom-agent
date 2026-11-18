"""知识库内容哈希（K6）。

用途有两个：

1. **判可比性**：评测报告里带上 kb_version，改过知识库之后的指标不能和之前直接比。
2. **判是否需要重建**：`build_kb.py` 跑完打印它，两次一致说明语料没变。

算法：把所有 chunk 的 `(id, text)` 按 id 排序后拼接，取 sha256 前 12 位。

**为什么要排序**：`load_chunks()` 已经排好序了，但哈希不能依赖调用方的顺序——
排序是哈希稳定的前提，放在这里再排一次是有意的防御。
"""
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Faq
from app.db.session import async_session
from app.kb.loader import faq_chunks, load_chunks


def kb_version(chunks: list[dict] | None = None) -> str:
    """返回知识库内容哈希（12 位十六进制）。"""
    items = chunks if chunks is not None else load_chunks()
    payload = "\n".join(
        f"{chunk['id']}\t{chunk['text']}" for chunk in sorted(items, key=lambda c: c["id"])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


async def current_kb_version(
    session: AsyncSession | None = None,
) -> str:
    """当前知识库版本：静态语料 + 已审核且待同步/已同步的 FAQ。

    与 `build_kb.py` 的版本口径一致；`/api/meta` 用它判断评测是否可比。
    """
    own = session is None
    s = session if session is not None else async_session()
    try:
        rows = list(
            (
                await s.scalars(
                    select(Faq)
                    .where(
                        Faq.reviewed_at.is_not(None),
                        Faq.sync_status.in_(("pending", "synced")),
                    )
                    .order_by(Faq.id)
                )
            ).all()
        )
        faq_rows: list[dict[str, Any]] = [
            {"id": row.id, "question": row.question, "answer": row.answer}
            for row in rows
        ]
        return kb_version(load_chunks() + faq_chunks(faq_rows))
    finally:
        if own:
            await s.close()


def cached_kb_version() -> str:
    """静态语料版本（兼容旧调用）；`/api/meta` 已改用 `current_kb_version`。"""
    return kb_version()
