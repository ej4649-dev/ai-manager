"""
AI Manager - ローカル SQLite データストア

仕様書「ローカルデータベース」章に対応:
  - Facebook グループ/ページの投稿・コメント (過去90日蓄積)
  - Instagram 広告パフォーマンス (過去90日蓄積)
  - タスク完了履歴 (毎朝の指示書に対する実行結果)
  - ネタ (テーマ別タグ付け、週次/月次/NOTE記事化で再利用)

全機能 (morning_brief / facebook_monitor / instagram_ads /
weekly_monthly_analysis / note_article_generator) がこのモジュール経由で
読み書きする。スキーマは init_db() で冪等に作成される。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Sequence

from config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS fb_posts (
    id TEXT PRIMARY KEY,          -- Graph API post/comment id
    source_type TEXT NOT NULL,    -- 'group' | 'page'
    source_id TEXT NOT NULL,      -- group id or page id
    kind TEXT NOT NULL,           -- 'post' | 'comment'
    parent_id TEXT,               -- comment の場合は親 post id
    author TEXT,
    message TEXT,
    reaction_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    is_replied INTEGER DEFAULT 0, -- コメントに Ej/Page から返信済みか
    created_time TEXT NOT NULL,   -- ISO8601 (Graph API created_time)
    fetched_at TEXT NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_fb_posts_created ON fb_posts(created_time);
CREATE INDEX IF NOT EXISTS idx_fb_posts_source ON fb_posts(source_id, kind);

CREATE TABLE IF NOT EXISTS fb_member_requests (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    name TEXT,
    requested_at TEXT,
    status TEXT DEFAULT 'pending', -- pending | approved | declined
    fetched_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS ig_ad_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,           -- YYYY-MM-DD
    business TEXT NOT NULL,       -- 'airstobu' | 'TheVintageSalon' etc.
    ad_id TEXT,
    ad_name TEXT,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    spend REAL DEFAULT 0,
    cpc REAL,
    cpa REAL,
    ctr REAL,
    fetched_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(date, business, ad_id)
);
CREATE INDEX IF NOT EXISTS idx_ig_ad_date ON ig_ad_metrics(date);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,           -- YYYY-MM-DD
    task_text TEXT NOT NULL,
    status TEXT DEFAULT 'proposed', -- proposed | done | skipped
    source TEXT,                  -- which feature generated it
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS content_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme TEXT NOT NULL,          -- 例: '乗り心地改善'
    tag TEXT,                     -- 例: 'note_free' | 'note_paid' | 'process'
    business TEXT,
    body TEXT,                    -- 元ネタ本文/要約
    source TEXT,                  -- 'facebook' | 'instagram' | 'manual'
    used_in_article INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,    -- 'morning_brief' | 'fb_daily' | 'ig_daily' |
                                   -- 'weekly' | 'monthly' | 'note_draft'
    period_key TEXT NOT NULL,     -- 例: 2026-09-21, 2026-W39, 2026-09
    content TEXT NOT NULL,        -- 生成されたレポート本文 (Markdown)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_type_period ON reports(report_type, period_key);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """スキーマを作成 (既存テーブルはスキップ)。初回セットアップ時に呼ぶ。"""
    path = db_path or settings.db_path
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- fb_posts -----------------------------------------------------------

def upsert_fb_post(row: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO fb_posts (id, source_type, source_id, kind, parent_id, author,
                                   message, reaction_count, comment_count, is_replied,
                                   created_time, fetched_at, raw_json)
            VALUES (:id, :source_type, :source_id, :kind, :parent_id, :author,
                    :message, :reaction_count, :comment_count, :is_replied,
                    :created_time, :fetched_at, :raw_json)
            ON CONFLICT(id) DO UPDATE SET
                reaction_count=excluded.reaction_count,
                comment_count=excluded.comment_count,
                is_replied=excluded.is_replied,
                fetched_at=excluded.fetched_at,
                raw_json=excluded.raw_json
            """,
            {**row, "fetched_at": row.get("fetched_at") or _now_iso()},
        )


def unreplied_comments(source_id: str, since_days: int = 7) -> list[sqlite3.Row]:
    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM fb_posts
            WHERE source_id = ? AND kind = 'comment' AND is_replied = 0
              AND created_time >= ?
            ORDER BY created_time DESC
            """,
            (source_id, cutoff),
        ).fetchall()


def fb_daily_counts(source_id: str, since_hours: int = 24) -> dict[str, int]:
    cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN kind='post' THEN 1 ELSE 0 END) AS new_posts,
                SUM(CASE WHEN kind='comment' THEN 1 ELSE 0 END) AS new_comments,
                SUM(CASE WHEN kind='comment' AND is_replied=0 THEN 1 ELSE 0 END) AS unreplied
            FROM fb_posts
            WHERE source_id = ? AND fetched_at >= ?
            """,
            (source_id, cutoff),
        ).fetchone()
        return dict(row) if row else {"new_posts": 0, "new_comments": 0, "unreplied": 0}


def prune_old_fb_posts(keep_days: int = 90) -> int:
    cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM fb_posts WHERE created_time < ?", (cutoff,))
        return cur.rowcount


# --- ig_ad_metrics --------------------------------------------------------

def upsert_ig_metric(row: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ig_ad_metrics (date, business, ad_id, ad_name, impressions,
                                        clicks, conversions, spend, cpc, cpa, ctr,
                                        fetched_at, raw_json)
            VALUES (:date, :business, :ad_id, :ad_name, :impressions, :clicks,
                    :conversions, :spend, :cpc, :cpa, :ctr, :fetched_at, :raw_json)
            ON CONFLICT(date, business, ad_id) DO UPDATE SET
                impressions=excluded.impressions, clicks=excluded.clicks,
                conversions=excluded.conversions, spend=excluded.spend,
                cpc=excluded.cpc, cpa=excluded.cpa, ctr=excluded.ctr,
                fetched_at=excluded.fetched_at, raw_json=excluded.raw_json
            """,
            {**row, "fetched_at": row.get("fetched_at") or _now_iso()},
        )


def ig_metrics_range(start_date: str, end_date: str, business: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if business:
            return conn.execute(
                "SELECT * FROM ig_ad_metrics WHERE date BETWEEN ? AND ? AND business = ? ORDER BY date",
                (start_date, end_date, business),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM ig_ad_metrics WHERE date BETWEEN ? AND ? ORDER BY date",
            (start_date, end_date),
        ).fetchall()


def prune_old_ig_metrics(keep_days: int = 90) -> int:
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM ig_ad_metrics WHERE date < ?", (cutoff,))
        return cur.rowcount


# --- task_history -----------------------------------------------------

def add_tasks(date: str, tasks: Sequence[str], source: str) -> None:
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO task_history (date, task_text, source, created_at) VALUES (?, ?, ?, ?)",
            [(date, t, source, _now_iso()) for t in tasks],
        )


def tasks_for_date(date: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM task_history WHERE date = ? ORDER BY id", (date,)
        ).fetchall()


# --- content_ideas ----------------------------------------------------

def add_content_idea(theme: str, tag: str, business: str | None, body: str, source: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO content_ideas (theme, tag, business, body, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (theme, tag, business, body, source, _now_iso()),
        )


def unused_content_ideas(limit: int = 20) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM content_ideas WHERE used_in_article = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def mark_idea_used(idea_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE content_ideas SET used_in_article = 1 WHERE id = ?", (idea_id,))


# --- reports ------------------------------------------------------------

def save_report(report_type: str, period_key: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reports (report_type, period_key, content, created_at) VALUES (?, ?, ?, ?)",
            (report_type, period_key, content, _now_iso()),
        )
        return cur.lastrowid


def latest_report(report_type: str, period_key: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reports WHERE report_type = ? AND period_key = ? ORDER BY id DESC LIMIT 1",
            (report_type, period_key),
        ).fetchone()


def reports_between(report_type: str, start_period: str, end_period: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reports WHERE report_type = ? AND period_key BETWEEN ? AND ? ORDER BY period_key",
            (report_type, start_period, end_period),
        ).fetchall()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {settings.db_path}")
