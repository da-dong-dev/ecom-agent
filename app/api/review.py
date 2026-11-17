"""Ch09 人工审核 API。

默认关闭：`ENABLE_REVIEW_API=false` 时所有接口 404。
审核会写 FAQ，属于写操作，不能默认暴露。

| 方法 | 路径 |
|---|---|
| GET | `/api/review/queue` |
| GET | `/api/review/{id}` |
| POST | `/api/review/{id}/approve` |
| POST | `/api/review/{id}/reject` |
"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.review import (
    ReviewConflict,
    ReviewInvalid,
    ReviewNotFound,
    approve,
    get_detail,
    list_queue,
    reject,
)
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _require_enabled() -> None:
    if not get_settings().enable_review_api:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(dependencies=[Depends(_require_enabled)])


class ApproveRequest(BaseModel):
    answer: str = ""


class RejectRequest(BaseModel):
    reason: str | None = None


def _raise_mapped(exc: Exception) -> None:
    if isinstance(exc, ReviewNotFound):
        raise HTTPException(status_code=404, detail="Not Found") from exc
    if isinstance(exc, ReviewConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, ReviewInvalid):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_arg", "message": str(exc)},
        ) from exc
    raise exc


@router.get("/api/review/queue")
async def review_queue(
    session: SessionDep,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        items = await list_queue(status, session=session)
    except ReviewInvalid as exc:
        _raise_mapped(exc)
    return {"items": items}


@router.get("/api/review/{review_id}")
async def review_detail(
    review_id: int,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return await get_detail(review_id, session=session)
    except ReviewNotFound as exc:
        _raise_mapped(exc)


@router.post("/api/review/{review_id}/approve")
async def review_approve(
    review_id: int,
    req: ApproveRequest,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return await approve(review_id, req.answer, session=session)
    except (ReviewNotFound, ReviewConflict, ReviewInvalid) as exc:
        _raise_mapped(exc)


@router.post("/api/review/{review_id}/reject")
async def review_reject(
    review_id: int,
    req: RejectRequest,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return await reject(review_id, req.reason, session=session)
    except (ReviewNotFound, ReviewConflict) as exc:
        _raise_mapped(exc)
