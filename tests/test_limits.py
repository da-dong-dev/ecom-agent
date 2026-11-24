"""限流 / 成本熔断的单元测试（不联网）。"""
from app.core import limits


def test_client_ip_prefers_first_forwarded_address():
    assert limits.client_ip("203.0.113.9, 10.0.0.1", "127.0.0.1") == "203.0.113.9"
    assert limits.client_ip(None, "127.0.0.1") == "127.0.0.1"
    assert limits.client_ip("", None) == "unknown"


def test_rate_limit_allows_then_blocks():
    ip = "test-rate-1"
    assert limits.rate_limited(ip, 3, now=0.0) is None
    assert limits.rate_limited(ip, 3, now=0.1) is None
    assert limits.rate_limited(ip, 3, now=0.2) is None
    retry = limits.rate_limited(ip, 3, now=0.3)
    assert retry is not None and retry > 0


def test_rate_limit_window_slides():
    ip = "test-rate-2"
    for i in range(2):
        limits.rate_limited(ip, 2, now=float(i))
    assert limits.rate_limited(ip, 2, now=1.0) is not None
    # 第一个请求滑出 60s 窗口后恢复
    assert limits.rate_limited(ip, 2, now=60.5) is None


def test_rate_limit_zero_disables():
    ip = "test-rate-3"
    for _ in range(100):
        assert limits.rate_limited(ip, 0) is None


def test_cost_accumulates_and_budget_triggers(monkeypatch):
    monkeypatch.setattr(limits, "_cost_yuan", 0.0, raising=False)
    monkeypatch.setattr(limits, "_cost_day", limits.date.today().isoformat(), raising=False)

    limits.record_usage(1_000_000, 0, price_input_per_1m=2.0, price_output_per_1m=8.0)
    assert limits.daily_cost_yuan() == 2.0
    assert not limits.over_budget(2.5)

    limits.record_usage(0, 1_000_000, price_input_per_1m=2.0, price_output_per_1m=8.0)
    assert limits.daily_cost_yuan() == 10.0
    assert limits.over_budget(2.5)
    assert not limits.over_budget(0)
