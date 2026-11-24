"""API 入口。

设计依据：
  · L1：暴露 /api/chat（流式）与 /api/meta（评测快照用）
  · L7：每次请求一个 trace_id，贯穿日志
  · C6：chat 接收 client_message_id 用于幂等（Ch05 Task 1 已接）
"""
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APITimeoutError
from pydantic import BaseModel, Field

from app.api.chat import ChatError, conversation_access, run_turn
from app.api.conversations import router as conversations_router
from app.api.eval import router as eval_router
from app.api.feedback import router as feedback_router
from app.api.review import router as review_router
from app.config import get_settings, validate_or_die
from app.core import limits as limit_guard
from app.core.kb_version import current_kb_version
from app.core.logging import new_trace_id, setup_logging, trace_id_var

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    validate_or_die()          # L6：缺必填项直接失败，不带着空配置跑
    yield


app = FastAPI(title="ecom-agent", version="0.1.0", lifespan=lifespan)
app.include_router(eval_router)
app.include_router(conversations_router)
app.include_router(feedback_router)
app.include_router(review_router)


# 会消耗 LLM 额度的入口；限流和预算只保护这些路径，
# 健康检查 / meta 不消耗模型，保持可用。
_COST_PATHS = {"/api/chat", "/api/eval/chat"}


@app.middleware("http")
async def cost_guard(request: Request, call_next):
    """按真实 IP 限流 + 每日成本熔断。

    顺序：先限流（便宜，直接拒），再查预算（读内存计数）。
    两个都只作用于 POST 的聊天入口，避免影响页面和监控探测。
    """
    if request.method != "POST" or request.url.path not in _COST_PATHS:
        return await call_next(request)

    s = get_settings()
    ip = limit_guard.client_ip(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
    )

    # getattr：测试里常用 SimpleNamespace 替换 settings，缺字段时退化为「不限制」
    retry_after = limit_guard.rate_limited(
        ip, getattr(s, "chat_rate_limit_per_minute", 0)
    )
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "请求过于频繁，请稍后再试。",
                "retry_after_seconds": round(retry_after, 1),
            },
            headers={"Retry-After": str(int(retry_after))},
        )

    budget = getattr(s, "daily_cost_limit_yuan", 0)
    if limit_guard.over_budget(budget):
        return JSONResponse(
            status_code=429,
            content={
                "detail": "今日演示额度已用完，请明天再试。",
                "cost_yuan": limit_guard.daily_cost_yuan(),
                "limit_yuan": budget,
            },
        )

    return await call_next(request)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    tid = request.headers.get("X-Trace-Id") or new_trace_id()
    token = trace_id_var.set(tid)
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        return response
    finally:
        trace_id_var.reset(token)


class ChatRequest(BaseModel):
    message: str = ""
    conversation_id: int | None = None
    client_message_id: str | None = Field(
        default=None, description="幂等键，同一会话内唯一（K4）")
    selected_action: dict | None = Field(
        default=None,
        description="结构化点选，如 {type: select_order, order_id: 1001}；与 message 至少给一个")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta")
async def meta() -> dict:
    """评测快照用（L1 / K6）：不含任何密钥值。"""
    return {
        "version": app.version,
        **get_settings().safe_summary(),
        # 放在后面覆盖：safe_summary 里的 kb_version 来自配置（永远是空串），
        # 真正该报的是**当前语料算出来的**哈希
        "kb_version": await current_kb_version(),  # K6：含已审核 FAQ
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """跑一轮对话，以 SSE 输出。

    帧顺序：`ack → route → delta → citations → actions → done`；
    异常时用 `error` 帧替换后续内容。

    **不把内部异常信息透给用户**——只给一句标准话术，细节进日志（带 trace_id）。
    """
    # 会话归属必须在 SSE 建立之前判断：状态码一旦发出，后面只能回 error 帧。
    try:
        await conversation_access(
            req.conversation_id, user_id=get_settings().demo_user_id
        )
    except ChatError as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc

    trace_id = trace_id_var.get()

    async def event_stream():
        try:
            async for frame in run_turn(
                req.message,
                req.conversation_id,
                trace_id,
                client_message_id=req.client_message_id,
                selected_action=req.selected_action,
            ):
                yield _sse(frame)
        except ChatError as exc:
            # 校验类错误：可以告诉用户具体原因
            yield _sse({"event": "error", "code": exc.code, "message": exc.message})
        except (TimeoutError, httpx.TimeoutException, APITimeoutError):
            logger.warning("对话超时", exc_info=True)
            yield _sse({
                "event": "error",
                "code": "timeout",
                "message": "服务响应超时，请稍后重试或转人工。",
            })
        except Exception:
            logger.exception("对话失败")
            yield _sse({
                "event": "error",
                "code": "internal_error",
                "message": "服务暂时不可用，请稍后重试或转人工。",
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
