"""
DB レイヤーのスモークテスト。外部 API キー無しで実行可能
(全機能共通の src/db.py だけを検証する)。

実行: python -m pytest tests/ -v
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import db as db_module


@pytest.fixture()
def temp_db(monkeypatch):
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir) / "test.db"
    # Settings is a frozen dataclass, so replace the module-level instance
    # rather than mutating an attribute on it.
    test_settings = dataclasses.replace(db_module.settings, db_path=tmp_path, data_dir=Path(tmp_dir))
    monkeypatch.setattr(db_module, "settings", test_settings)
    db_module.init_db(tmp_path)
    yield tmp_path


def test_init_db_creates_tables(temp_db):
    import sqlite3

    conn = sqlite3.connect(temp_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    expected = {
        "fb_posts", "fb_member_requests", "ig_ad_metrics",
        "task_history", "content_ideas", "reports",
    }
    assert expected.issubset(tables)


def test_upsert_fb_post_and_unreplied(temp_db):
    db_module.upsert_fb_post(
        {
            "id": "post_1", "source_type": "group", "source_id": "g1", "kind": "post",
            "parent_id": None, "author": "Ej", "message": "hello",
            "reaction_count": 0, "comment_count": 1, "is_replied": 0,
            "created_time": "2026-09-20T10:00:00", "fetched_at": None, "raw_json": "{}",
        }
    )
    db_module.upsert_fb_post(
        {
            "id": "comment_1", "source_type": "group", "source_id": "g1", "kind": "comment",
            "parent_id": "post_1", "author": "member", "message": "question?",
            "reaction_count": 0, "comment_count": 0, "is_replied": 0,
            "created_time": "2026-09-20T11:00:00", "fetched_at": None, "raw_json": "{}",
        }
    )
    unreplied = db_module.unreplied_comments("g1")
    assert len(unreplied) == 1
    assert unreplied[0]["id"] == "comment_1"


def test_ig_metrics_upsert_and_range(temp_db):
    db_module.upsert_ig_metric(
        {
            "date": "2026-09-19", "business": "airstobu", "ad_id": "ad1", "ad_name": "reel_a",
            "impressions": 1000, "clicks": 50, "conversions": 2, "spend": 2000.0,
            "cpc": 40.0, "cpa": 1000.0, "ctr": 5.0, "fetched_at": None, "raw_json": "{}",
        }
    )
    rows = db_module.ig_metrics_range("2026-09-01", "2026-09-30")
    assert len(rows) == 1
    assert rows[0]["business"] == "airstobu"


def test_task_history(temp_db):
    db_module.add_tasks("2026-09-20", ["タスクA", "タスクB"], source="morning_brief")
    tasks = db_module.tasks_for_date("2026-09-20")
    assert len(tasks) == 2


def test_content_ideas_lifecycle(temp_db):
    db_module.add_content_idea("乗り心地改善", "note_free", "TheVintageSalon", "本文...", "facebook")
    ideas = db_module.unused_content_ideas()
    assert len(ideas) == 1
    db_module.mark_idea_used(ideas[0]["id"])
    assert len(db_module.unused_content_ideas()) == 0


def test_reports_save_and_fetch(temp_db):
    db_module.save_report("morning_brief", "2026-09-20", "本文テスト")
    r = db_module.latest_report("morning_brief", "2026-09-20")
    assert r is not None
    assert r["content"] == "本文テスト"
