"""Ch08 数据迁移：给已有的 SQLite 库补 `messages.reply_to_client_message_id`。

为什么需要这个脚本：
- `scripts/init_db.py` 用 `Base.metadata.create_all`，它只建缺失的**表**，
  不会给已存在的表加列；
- Ch08 之前建的 `data/app.db` / `data/eval.db` 没有这个字段；
- 直接跑新代码会报 `no such column: reply_to_client_message_id`。

脚本是幂等的：列/索引已存在时直接跳过，可以重复执行。

用法：
    uv run python scripts/migrate_ch08.py --db data/app.db
    uv run python scripts/migrate_ch08.py --db data/eval.db
"""
import argparse
import sqlite3
import sys
from pathlib import Path

COLUMN = "reply_to_client_message_id"
INDEX = "uk_msg_reply"

ALTER_SQL = f"ALTER TABLE messages ADD COLUMN {COLUMN} TEXT"

INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX}
  ON messages(conversation_id, {COLUMN})
  WHERE {COLUMN} IS NOT NULL
"""


def _column_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(messages)").fetchall()
    if not rows:
        raise RuntimeError("messages 表不存在，先跑 scripts/init_db.py")
    return {row[1] for row in rows}


def _index_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX,),
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
        columns = _column_names(conn)
        if COLUMN not in columns:
            conn.execute(ALTER_SQL)
            changed = True

        if not _index_exists(conn):
            conn.execute(INDEX_SQL)
            changed = True

        conn.commit()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Ch08 messages 迁移（幂等）")
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
