"""Ch08 迁移：老库补列/补索引，且可重复执行。"""
import sqlite3

from scripts.migrate_ch08 import migrate

OLD_SCHEMA = """
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT,
  status TEXT,
  client_message_id TEXT,
  meta TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

OLD_SCHEMA_WITH_COLUMN = OLD_SCHEMA.replace(
    "  client_message_id TEXT,",
    "  client_message_id TEXT,\n  reply_to_client_message_id TEXT,",
)


def _create_old_db(path, schema: str = OLD_SCHEMA) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (1, 'user', '迁移前的旧消息')"
        )
        conn.commit()


def _columns(path) -> set[str]:
    with sqlite3.connect(str(path)) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(messages)")}


def _indexes(path) -> set[str]:
    with sqlite3.connect(str(path)) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }


def test_migrate_adds_column_and_index(tmp_path):
    db = tmp_path / "old.db"
    _create_old_db(db)

    assert migrate(db) is True

    assert "reply_to_client_message_id" in _columns(db)
    assert "uk_msg_reply" in _indexes(db)

    # 旧数据保留，新列为 NULL
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT content, reply_to_client_message_id FROM messages"
        ).fetchone()
    assert row == ("迁移前的旧消息", None)

    # 幂等：第二次不再变更
    assert migrate(db) is False


def test_migrate_adds_missing_index_when_column_exists(tmp_path):
    """列已经存在、索引缺失时，也要补齐索引。"""
    db = tmp_path / "column-only.db"
    _create_old_db(db, OLD_SCHEMA_WITH_COLUMN)
    assert "uk_msg_reply" not in _indexes(db)

    assert migrate(db) is True
    assert "uk_msg_reply" in _indexes(db)
    assert migrate(db) is False
