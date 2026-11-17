"""Ch09 人工审核 CLI（MVP）。

用法：
    uv run python scripts/review.py list
    uv run python scripts/review.py list --status approved
    uv run python scripts/review.py show 12
    uv run python scripts/review.py approve 12 --answer "可以水洗，注意阴干"
    uv run python scripts/review.py retry 12
    uv run python scripts/review.py reject 12 --reason "活动已过期"

输出统一为 JSON，方便复制到验收报告。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/review.py` 直接跑

from app.core.review import (
    ReviewError,
    approve,
    get_detail,
    list_queue,
    reject,
    retry_sync,
)


def _dump(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def main(args: argparse.Namespace) -> int:
    if args.command == "list":
        _dump(await list_queue(args.status))
    elif args.command == "show":
        _dump(await get_detail(args.review_id))
    elif args.command == "approve":
        _dump(await approve(args.review_id, args.answer))
    elif args.command == "retry":
        _dump(await retry_sync(args.review_id))
    elif args.command == "reject":
        _dump(await reject(args.review_id, args.reason))
    else:  # pragma: no cover - argparse 保证不会到这里
        raise SystemExit(f"未知命令：{args.command}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ch09 飞轮人工审核")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="查看审核队列")
    list_parser.add_argument("--status", default=None, help="按状态筛选")

    show_parser = sub.add_parser("show", help="查看审核详情")
    show_parser.add_argument("review_id", type=int)

    approve_parser = sub.add_parser("approve", help="审核通过并写 FAQ")
    approve_parser.add_argument("review_id", type=int)
    approve_parser.add_argument("--answer", required=True, help="人工核准答案")

    retry_parser = sub.add_parser("retry", help="重试 FAQ → KB 同步")
    retry_parser.add_argument("review_id", type=int)

    reject_parser = sub.add_parser("reject", help="审核驳回")
    reject_parser.add_argument("review_id", type=int)
    reject_parser.add_argument("--reason", default=None, help="驳回原因")

    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(_parse_args())))
    except ReviewError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
