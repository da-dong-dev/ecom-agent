"""进程内限流 + 每日成本熔断。

为什么不用 slowapi：
  ecom 是单进程 uvicorn，一个进程内用滑动窗口足够；引入 slowapi 要改
  pyproject/lock，收益不大。**如果以后扩到多 worker/多实例，必须换成
  Redis 共享计数**，否则每个进程各算各的，限流失效。

两个保护：
  1. 按真实 IP 限流：防止单个客户端刷接口；
  2. 每日成本熔断：当天累计 token 费用超过阈值后，业务接口直接 429。
     token 用量由 app/core/llm.py 在每次调用后回传。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date
from typing import Optional

_lock = threading.Lock()

# IP -> 最近一分钟的请求时间戳
_hits: dict[str, deque[float]] = {}

_cost_day: str = date.today().isoformat()
_cost_yuan: float = 0.0


def client_ip(forwarded_for: Optional[str], peer_host: Optional[str]) -> str:
    """取真实客户端 IP。

    线上链路：浏览器 → Caddy → frontend nginx → backend。
    Caddy 用 `header_up X-Forwarded-For {remote_host}` 覆盖客户端自带的伪造值，
    所以第一个地址可信。取不到就退回 TCP 对端地址。
    """
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return peer_host or "unknown"


def rate_limited(ip: str, limit_per_minute: int, *, now: Optional[float] = None) -> Optional[float]:
    """滑动窗口限流。返回 None 表示放行，否则返回建议的 Retry-After 秒数。

    limit_per_minute <= 0 表示关闭限流。
    """
    if limit_per_minute <= 0:
        return None

    ts = time.monotonic() if now is None else now
    window = 60.0

    with _lock:
        q = _hits.setdefault(ip, deque())
        while q and ts - q[0] >= window:
            q.popleft()

        if len(q) >= limit_per_minute:
            # 最早那次请求滑出窗口后就能继续
            return max(1.0, window - (ts - q[0]))

        q.append(ts)
        return None


def _reset_daily_if_needed() -> None:
    global _cost_day, _cost_yuan
    today = date.today().isoformat()
    if today != _cost_day:
        _cost_day = today
        _cost_yuan = 0.0


def record_usage(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    price_input_per_1m: float,
    price_output_per_1m: float,
) -> None:
    """累加一次 LLM 调用的估算成本（单位：元）。"""
    global _cost_yuan
    delta = (
        prompt_tokens * price_input_per_1m
        + completion_tokens * price_output_per_1m
    ) / 1_000_000
    with _lock:
        _reset_daily_if_needed()
        _cost_yuan += delta


def daily_cost_yuan() -> float:
    with _lock:
        _reset_daily_if_needed()
        return round(_cost_yuan, 6)


def over_budget(limit_yuan: float) -> bool:
    """limit_yuan <= 0 表示关闭预算熔断。"""
    if limit_yuan <= 0:
        return False
    return daily_cost_yuan() >= limit_yuan
