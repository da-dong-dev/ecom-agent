"""Ch09 迁移：老库补 suggested_answer 与飞轮索引，且可重复执行。"""
import sqlite3

import pytest

from scripts.migrate_ch09 import migrate

OLD_SCHEMA = """
CREATE TABLE low_conf_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER,
  question TEXT NOT NULL,
  normalized TEXT,
  source TEXT,
  reason TEXT,
  retrieved TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  merged_into INTEGER,
  occurrence INTEGER NOT NULL DEFAULT 1,
  kb_ref TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at DATETIME
);

CREATE TABLE faq (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_question_id INTEGER,
  reviewed_at DATETIME,
  kb_version TEXT,
  sync_status TEXT NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

OLD_SCHEMA_WITH_COLUMN = OLD_SCHEMA.replace(
    "  normalized TEXT,",
    "  normalized TEXT,\n  suggested_answer TEXT,",
)


def _create_old_db(path, schema: str = OLD_SCHEMA) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO low_conf_questions (question, source, reason, retrieved) "
            "VALUES ('旧问题', 'retrieval_low_conf', 'old', '[]')"
        )
        conn.execute(
            "INSERT INTO faq (question, answer, source_type, source_question_id) "
            "VALUES ('旧 FAQ', '旧答案', 'manual', NULL)"
        )
        conn.commit()


def _columns(path, table: str) -> set[str]:
    with sqlite3.connect(str(path)) as conn:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _indexes(path) -> set[str]:
    with sqlite3.connect(str(path)) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }


def test_migrate_adds_column_and_indexes(tmp_path):
    db = tmp_path / "old.db"
    _create_old_db(db)

    assert migrate(db) is True

    assert "suggested_answer" in _columns(db, "low_conf_questions")
    assert {
        "idx_low_conf_status",
        "idx_low_conf_merged_into",
        "uk_faq_source_question",
    } <= _indexes(db)

    # 旧数据保留，新列为 NULL
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT question, suggested_answer FROM low_conf_questions"
        ).fetchone()
    assert row == ("旧问题", None)

    # 幂等：第二次不再变更
    assert migrate(db) is False


def test_migrate_adds_missing_indexes_when_column_exists(tmp_path):
    """列已经存在、索引缺失时，也要补齐索引。"""
    db = tmp_path / "column-only.db"
    _create_old_db(db, OLD_SCHEMA_WITH_COLUMN)
    assert "suggested_answer" in _columns(db, "low_conf_questions")
    assert "idx_low_conf_status" not in _indexes(db)

    assert migrate(db) is True
    assert {
        "idx_low_conf_status",
        "idx_low_conf_merged_into",
        "uk_faq_source_question",
    } <= _indexes(db)
    assert migrate(db) is False


def test_migrate_requires_flywheel_tables(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()

    with pytest.raises(RuntimeError, match="low_conf_questions"):
        migrate(db)
