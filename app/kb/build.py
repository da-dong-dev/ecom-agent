"""知识库构建：静态语料 + 已审核 FAQ → 向量化 → Chroma。

从 `scripts/build_kb.py` 抽出来，供两处复用：

- CLI：`uv run python scripts/build_kb.py`
- 审核通过后的同步：`app/core/review.py › approve/retry_sync`

**顺序不能反**：先完成 Chroma upsert/删除，成功后才把 `faq.sync_status`
置为 `synced`、canonical 置为 `written_back`。否则会出现“数据库说写回成功，
检索却查不到”的假成功。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.kb_version import kb_version
from app.db.models import Faq, LowConfQuestion
from app.db.session import async_session
from app.kb.loader import faq_chunks, load_chunks

logger = logging.getLogger(__name__)

TENANT = "default_tenant"
DATABASE = "default_database"
EMBED_BATCH = 16
TIMEOUT = 120.0


def chroma_base() -> str:
    settings = get_settings()
    return f"http://{settings.chroma_host}:{settings.chroma_port}/api/v2"


def collections_url() -> str:
    return f"{chroma_base()}/tenants/{TENANT}/databases/{DATABASE}/collections"


async def ensure_collection(client: httpx.AsyncClient) -> str:
    """取或建集合，并校验空间确实是 cosine。返回 collection id。"""
    name = get_settings().chroma_collection
    response = await client.post(
        collections_url(),
        json={
            "name": name,
            "configuration": {"hnsw": {"space": "cosine"}},
            "get_or_create": True,
        },
    )
    response.raise_for_status()
    collection = response.json()

    space = (collection.get("configuration_json") or {}).get("hnsw", {}).get("space")
    if space != "cosine":
        raise RuntimeError(
            f"集合 {name} 的空间是 {space!r}，不是 cosine。\n"
            "证据闸依赖 cosine 相似度，用 l2 距离会让语义反转。\n"
            f"请先删除该集合再重跑：curl -X DELETE {collections_url()}/{name}"
        )
    return collection["id"]


async def embed_all(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """调 Ollama 批量取向量（bge-m3，1024 维）。"""
    settings = get_settings()
    url = f"{settings.ollama_base_url}/api/embed"
    vectors: list[list[float]] = []

    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        response = await client.post(
            url,
            json={"model": settings.embed_model, "input": batch},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        vectors.extend(response.json()["embeddings"])
        logger.info(
            "向量化 %s/%s", min(start + EMBED_BATCH, len(texts)), len(texts)
        )
    return vectors


async def existing_ids(
    client: httpx.AsyncClient, collection_id: str
) -> set[str]:
    response = await client.post(
        f"{collections_url()}/{collection_id}/get", json={"include": []}
    )
    response.raise_for_status()
    return set(response.json().get("ids") or [])


def _faq_dict(row: Faq) -> dict[str, Any]:
    return {
        "id": row.id,
        "question": row.question,
        "answer": row.answer,
    }


async def build_kb(
    *,
    session: AsyncSession | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """构建/同步知识库，返回版本与数量统计。

    传 `session` 时复用调用方事务；传 `client` 时复用 HTTP client（测试注入）。
    """
    own_session = session is None
    own_client = client is None
    s = session if session is not None else async_session()
    c = client if client is not None else httpx.AsyncClient(timeout=TIMEOUT)

    try:
        static_chunks = load_chunks()
        if not static_chunks:
            raise RuntimeError("没有读到任何 chunk，检查 data/kb/ 下是否有 .md")

        faq_rows = list(
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
        chunks = static_chunks + faq_chunks([_faq_dict(row) for row in faq_rows])
        version = kb_version(chunks)

        collection_id = await ensure_collection(c)
        before = await existing_ids(c, collection_id)
        embeddings = await embed_all(c, [chunk["text"] for chunk in chunks])

        response = await c.post(
            f"{collections_url()}/{collection_id}/upsert",
            json={
                "ids": [chunk["id"] for chunk in chunks],
                "embeddings": embeddings,
                "documents": [chunk["text"] for chunk in chunks],
                "metadatas": [
                    {
                        "source": chunk["source"],
                        "section": chunk["section"],
                        "kind": chunk["kind"],
                        "kb_version": version,
                        **(
                            {"article_no": chunk["article_no"]}
                            if "article_no" in chunk
                            else {}
                        ),
                    }
                    for chunk in chunks
                ],
            },
        )
        response.raise_for_status()

        stale = before - {chunk["id"] for chunk in chunks}
        if stale:
            delete = await c.post(
                f"{collections_url()}/{collection_id}/delete",
                json={"ids": sorted(stale)},
            )
            delete.raise_for_status()

        for row in faq_rows:
            row.sync_status = "synced"
            row.kb_version = version
            if row.source_type == "flywheel" and row.source_question_id is not None:
                await s.execute(
                    update(LowConfQuestion)
                    .where(LowConfQuestion.id == row.source_question_id)
                    .values(
                        status="written_back",
                        kb_ref=f"faq-{row.id}",
                    )
                )
        await s.commit()

        logger.info(
            "知识库已更新 version=%s chunks=%s faq=%s stale=%s",
            version,
            len(chunks),
            len(faq_rows),
            len(stale),
        )
        return {
            "version": version,
            "chunks": len(chunks),
            "faq": len(faq_rows),
            "deleted": len(stale),
        }
    finally:
        if own_client:
            await c.aclose()
        if own_session:
            await s.close()
