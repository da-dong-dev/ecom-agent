"""Ch09 数据迁移：给已有的 SQLite 库补飞轮字段与索引。

为什么需要这个脚本：
- `scripts/init_db.py` 用 `Base.metadata.create_all`，它只建缺失的**表**，
  不会给已存在的表加列，也不会补缺失索引；
- Ch09 之前建的 `data/app.db` / `data/eval.db` 没有
  `low_conf_questions.suggested_answer`，也没有飞轮查询索引；
- 直接跑新代码不会报错，但飞轮批处理/审核会缺字段或全表扫描。

迁移内容：
1. `low_conf_questions.suggested_answer TEXT`；
2. `idx_low_conf_status` / `idx_low_conf_merged_into`；
3. `uk_faq_source_question`：同一条 canonical 只能写回一条 flywheel FAQ。

脚本是幂等的：列/索引已存在时直接跳过，可以重复执行。

用法：
    uv run python scripts/migrate_ch09.py --db data/app.db
    uv run python scripts/migrate_ch09.py --db data/eval.db
"""
import argparse
import sqlite3
import sys
from pathlib import Path

COLUMN = "suggested_answer"

LOW_CONF_INDEXES = {
    "idx_low_conf_status": (
        "CREATE INDEX IF NOT EXISTS idx_low_conf_status "
        "ON low_conf_questions(status)"
    ),
    "idx_low_conf_merged_into": (
        "CREATE INDEX IF NOT EXISTS idx_low_conf_merged_into "
        "ON low_conf_questions(merged_into)"
    ),
}

FAQ_INDEX = "uk_faq_source_question"
FAQ_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uk_faq_source_question
  ON faq(source_question_id)
  WHERE source_type = 'flywheel' AND source_question_id IS NOT NULL
"""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {row[1] for row in rows}


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def migrate(db_path: Path) -> bool:
    """执行迁移，返回是否发生了实际变更。

    `True` = 补了列或索引；`False` = 已是最新。
    """
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    changed = False
    with sqlite3.connect(str(db_path)) as conn:
        for table in ("low_conf_questions", "faq"):
            if not _table_exists(conn, table):
                raise RuntimeError(f"{table} 表不存在，先跑 scripts/init_db.py")

        if COLUMN not in _columns(conn, "low_conf_questions"):
            conn.execute(
                f"ALTER TABLE low_conf_questions ADD COLUMN {COLUMN} TEXT"
            )
            changed = True

        for name, sql in LOW_CONF_INDEXES.items():
            if not _index_exists(conn, name):
                conn.execute(sql)
                changed = True

        if not _index_exists(conn, FAQ_INDEX):
            conn.execute(FAQ_INDEX_SQL)
            changed = True

        conn.commit()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Ch09 飞轮迁移（幂等）")
    parser.add_argument(
        "--db",
        default="data/app.db",
        help="SQLite 文件路径（默认 data/app.db）",
    )
    args = parser.parse_args()
    path = Path(args.db)

    try:
        changed = migrate(path)
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一转成可读错误
        print(f"[失败] {path}: {exc}", file=sys.stderr)
        return 1

    print(f"{path}: {'已迁移' if changed else '已是最新'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
