"""飞轮评测的纯指标函数。"""
from scripts.eval_flywheel import (
    _citation_hit,
    _hit_at_3,
    _is_refusal,
    _keyword_recall,
    _summarize,
)


def test_hit_at_3_only_counts_top_three():
    evidence = [
        {"text": "无关"},
        {"text": "无关"},
        {"text": "价保规则"},
        {"text": "补差"},
    ]
    assert _hit_at_3(evidence, ["价保"]) is True
    assert _hit_at_3(evidence, ["补差"]) is False


def test_keyword_recall_ignores_whitespace():
    assert _keyword_recall("支持 价保，但不支持 补差", ["价保", "补差"]) == 1.0
    assert _keyword_recall("只有价保", ["价保", "补差"]) == 0.5
    assert _keyword_recall("", ["价保"]) == 0.0


def test_citation_hit_requires_faq_prefix():
    assert _citation_hit(["faq-3"]) is True
    assert _citation_hit(["returns#退货"]) is False
    assert _citation_hit([]) is False


def test_refusal_route_or_marker():
    assert _is_refusal("human", "", []) is True
    assert _is_refusal("fallback", "随便聊聊", []) is True
    assert _is_refusal("knowledge", "没有找到相关依据", []) is True
    assert _is_refusal(
        "knowledge", "你可以看这里", [{"type": "transfer_human"}]
    ) is True
    assert _is_refusal("knowledge", "可以退货", []) is False


def test_summarize_aggregates():
    badcases = [
        {"hit_at_3": True, "recall": 1.0, "citation_hit": True},
        {"hit_at_3": False, "recall": 0.0, "citation_hit": False},
    ]
    out = [{"refused": True}, {"refused": False}]

    metrics = _summarize(badcases, out)

    assert metrics == {
        "badcases": 2,
        "out_of_kb": 2,
        "hit_at_3": 0.5,
        "recall": 0.5,
        "citation_hit": 0.5,
        "refusal_rate": 0.5,
    }
