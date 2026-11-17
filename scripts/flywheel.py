"""Ch09 飞轮批处理 CLI。

用法：
    uv run python scripts/flywheel.py --seed-fixtures
    uv run python scripts/flywheel.py --limit 100
    uv run python scripts/flywheel.py --dry-run

`--seed-fixtures` 只把 `flywheel_badcases.yaml` 的 badcase 插入低置信池，
不处理；随后再跑 `--limit` 做标准化/查重。
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/flywheel.py` 直接跑

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fallback import record_low_conf
from app.core.flywheel import process_pending
from app.db.models import LowConfQuestion
from app.db.session import session_scope

FIXTURE_SOURCE = "offline_flywheel_fixture"


async def seed_fixtures(
    fixture: Path, *, session: AsyncSession | None = None
) -> dict[str, int]:
    """把 YAML 的 badcase 录入池子；按 question + source 去重，可重复执行。"""
    data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    badcases = data.get("badcases") or []
    inserted = 0
    skipped = 0

    async with session_scope(session) as s:
        for item in badcases:
            question = str(item.get("question") or "").strip()
            if not question:
                skipped += 1
                continue
            exists = await s.scalar(
                select(LowConfQuestion.id)
                .where(
                    LowConfQuestion.question == question,
                    LowConfQuestion.source == FIXTURE_SOURCE,
                )
                .limit(1)
            )
            if exists is not None:
                skipped += 1
                continue
            await record_low_conf(
                question=question,
                reason="offline_flywheel_fixture",
                hits=[],
                source=FIXTURE_SOURCE,
                session=s,
            )
            inserted += 1

    return {"inserted": inserted, "skipped": skipped}


async def main(args: argparse.Namespace) -> int:
    if args.seed_fixtures:
        stats = await seed_fixtures(Path(args.fixture))
        print(
            f"fixture: inserted={stats['inserted']} skipped={stats['skipped']}"
        )
        print("下一步：uv run python scripts/flywheel.py --limit 100")
        return 0

    stats = await process_pending(args.limit, dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}processed={stats['processed']} merged={stats['merged']} "
        f"created={stats['created']} skipped={stats['skipped']}"
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ch09 飞轮标准化/查重批处理")
    parser.add_argument("--limit", type=int, default=100, help="本轮最多处理多少条")
    parser.add_argument("--dry-run", action="store_true", help="只判断，不写库")
    parser.add_argument(
        "--seed-fixtures",
        action="store_true",
        help="把 badcase fixture 插入池子后退出",
    )
    parser.add_argument(
        "--fixture",
        default="data/eval/flywheel_badcases.yaml",
        help="badcase fixture 路径",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(_parse_args())))
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
