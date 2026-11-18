"""Ch09 飞轮补库前后评测。

对 `data/eval/flywheel_badcases.yaml` 逐条跑真实对话，输出：

- badcase：`hit@3`、答案关键词覆盖率、FAQ 引用命中；
- out_of_kb：拒答率；
- `kb_version`、`dataset_hash`、样本量；
- JSON + Markdown 两份报告。

用法（before / after 要用不同的库/集合）：

    DATABASE_URL=sqlite+aiosqlite:////tmp/ch09-eval.db \
    CHROMA_COLLECTION=ecom_kb_ch09_before \
    uv run python scripts/eval_flywheel.py --phase before

    DATABASE_URL=sqlite+aiosqlite:////tmp/ch09-eval.db \
    CHROMA_COLLECTION=ecom_kb_ch09_after \
    uv run python scripts/eval_flywheel.py --phase after

**不要在已补库的库上重跑 before**；前后对比会失真。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from app.api.chat import run_turn
from app.core.kb_version import current_kb_version
from app.core.logging import new_trace_id

DEFAULT_FIXTURE = ROOT / "data" / "eval" / "flywheel_badcases.yaml"
DEFAULT_OUT_DIR = ROOT / "docs" / "reports"

REFUSAL_MARKERS = (
    "没有找到",
    "没有相关",
    "不能给您结论",
    "查不到",
    "暂时没有",
    "转人工",
    "联系人工",
    "无法回答",
    "建议您联系",
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _hit_at_3(evidence: list[dict], keywords: list[str]) -> bool:
    top3 = evidence[:3]
    haystack = _compact(" ".join(str(item.get("text") or "") for item in top3))
    return any(_compact(keyword) in haystack for keyword in keywords)


def _keyword_recall(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    compact = _compact(answer)
    return sum(1 for keyword in keywords if _compact(keyword) in compact) / len(keywords)


def _citation_hit(citations: list[str]) -> bool:
    return any(str(cid).startswith("faq-") for cid in citations)


def _is_refusal(route: str | None, answer: str, actions: list[dict]) -> bool:
    if route in {"human", "fallback"}:
        return True
    if any(str(action.get("type")) == "transfer_human" for action in actions):
        return True
    return any(marker in (answer or "") for marker in REFUSAL_MARKERS)


def _summarize(
    badcases: list[dict], out_of_kb: list[dict]
) -> dict[str, Any]:
    n = len(badcases)
    m = len(out_of_kb)
    return {
        "badcases": n,
        "out_of_kb": m,
        "hit_at_3": (
            sum(1 for case in badcases if case["hit_at_3"]) / n if n else 0.0
        ),
        "recall": (
            sum(case["recall"] for case in badcases) / n if n else 0.0
        ),
        "citation_hit": (
            sum(1 for case in badcases if case["citation_hit"]) / n if n else 0.0
        ),
        "refusal_rate": (
            sum(1 for case in out_of_kb if case["refused"]) / m if m else 0.0
        ),
    }


def _dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


async def _run_case(query: str, case_id: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    answer_parts: list[str] = []
    error = ""
    try:
        async for frame in run_turn(
            query,
            None,
            f"flywheel-{case_id}-{new_trace_id()}",
            capture=captured,
        ):
            if isinstance(frame.get("delta"), str):
                answer_parts.append(frame["delta"])
    except Exception as exc:  # noqa: BLE001 - 单条失败不能中断整轮
        error = f"{type(exc).__name__}: {exc}"

    final = captured.get("final") or {}
    answer = captured.get("answer") or "".join(answer_parts)
    evidence = final.get("evidence") or []
    citations = final.get("citation_ids") or []
    actions = final.get("actions") or captured.get("actions") or []
    return {
        "answer": answer,
        "route": final.get("route"),
        "evidence": [
            {"id": item.get("id"), "text": item.get("text")}
            for item in evidence[:3]
        ],
        "citations": citations,
        "actions": actions,
        "error": error,
    }


async def evaluate(
    fixture: Path, *, phase: str, out_dir: Path
) -> dict[str, Any]:
    data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    badcases: list[dict[str, Any]] = []
    out_cases: list[dict[str, Any]] = []

    for index, item in enumerate(data.get("badcases") or [], start=1):
        raw = await _run_case(item["question"], f"B{index:02d}")
        keywords = [str(k) for k in item.get("expected_keywords") or []]
        record = {
            "id": item["id"],
            "question": item["question"],
            "expected_keywords": keywords,
            "route": raw["route"],
            "answer": raw["answer"],
            "evidence": raw["evidence"],
            "citations": raw["citations"],
            "hit_at_3": _hit_at_3(raw["evidence"], keywords),
            "recall": _keyword_recall(raw["answer"], keywords),
            "citation_hit": _citation_hit(raw["citations"]),
            "error": raw["error"],
        }
        badcases.append(record)
        print(
            f"[badcase {item['id']}] route={record['route']} "
            f"hit@3={record['hit_at_3']} recall={record['recall']:.2f} "
            f"citation={record['citation_hit']}"
        )

    for index, item in enumerate(data.get("out_of_kb") or [], start=1):
        raw = await _run_case(item["question"], f"O{index:02d}")
        record = {
            "id": item["id"],
            "question": item["question"],
            "route": raw["route"],
            "answer": raw["answer"],
            "actions": raw["actions"],
            "refused": _is_refusal(raw["route"], raw["answer"], raw["actions"]),
            "error": raw["error"],
        }
        out_cases.append(record)
        print(
            f"[out_of_kb {item['id']}] route={record['route']} "
            f"refused={record['refused']}"
        )

    metrics = _summarize(badcases, out_cases)
    version = await current_kb_version()
    report = {
        "phase": phase,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fixture": str(fixture),
        "dataset_hash": _dataset_hash(fixture),
        "kb_version": version,
        "metrics": metrics,
        "badcases": badcases,
        "out_of_kb": out_cases,
    }
    _write_report(report, out_dir)
    return report


def _write_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    phase = report["phase"]
    json_path = out_dir / f"ch09-flywheel-{phase}.json"
    md_path = out_dir / f"ch09-flywheel-{phase}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metrics = report["metrics"]
    lines = [
        f"# Ch09 飞轮评测 · {phase}",
        "",
        (
            f"> 生成时间：{report['generated_at']} ｜ "
            f"fixture：`{report['fixture']}` ｜ "
            f"dataset_hash=`{report['dataset_hash']}` ｜ "
            f"kb_version=`{report['kb_version']}`"
        ),
        (
            f"> 样本：badcases={metrics['badcases']}，"
            f"out_of_kb={metrics['out_of_kb']}"
        ),
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| hit@3 | {metrics['hit_at_3']:.3f} |",
        f"| recall | {metrics['recall']:.3f} |",
        f"| citation_hit | {metrics['citation_hit']:.3f} |",
        f"| refusal_rate | {metrics['refusal_rate']:.3f} |",
        "",
        "## Badcases",
        "",
        "| id | route | hit@3 | recall | citation | answer |",
        "|---|---|---|---:|---|---|",
    ]
    for case in report["badcases"]:
        answer = str(case["answer"]).replace("\n", " ").replace("|", "\\|")[:100]
        lines.append(
            f"| {case['id']} | {case['route']} | {case['hit_at_3']} | "
            f"{case['recall']:.2f} | {case['citation_hit']} | {answer} |"
        )

    lines += [
        "",
        "## Out-of-KB",
        "",
        "| id | route | refused | answer |",
        "|---|---|---|---|",
    ]
    for case in report["out_of_kb"]:
        answer = str(case["answer"]).replace("\n", " ").replace("|", "\\|")[:100]
        lines.append(
            f"| {case['id']} | {case['route']} | {case['refused']} | {answer} |"
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写入 {json_path}")
    print(f"写入 {md_path}")


async def main(args: argparse.Namespace) -> int:
    if args.phase not in {"before", "after"}:
        raise SystemExit("--phase 只能是 before / after")
    report = await evaluate(
        Path(args.fixture), phase=args.phase, out_dir=Path(args.out_dir)
    )
    metrics = report["metrics"]
    print(
        f"{args.phase}: hit@3={metrics['hit_at_3']:.3f} "
        f"recall={metrics['recall']:.3f} "
        f"citation_hit={metrics['citation_hit']:.3f} "
        f"refusal_rate={metrics['refusal_rate']:.3f} "
        f"kb_version={report['kb_version']}"
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ch09 飞轮补库前后评测")
    parser.add_argument("--phase", required=True, choices=("before", "after"))
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(_parse_args())))
