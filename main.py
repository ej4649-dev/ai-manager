"""
AI Manager - 統合 CLI エントリーポイント

Windows Task Scheduler からはこのファイルを直接呼ぶ:
  python main.py morning_brief
  python main.py facebook_monitor
  python main.py instagram_ads
  python main.py weekly
  python main.py monthly
  python main.py note_draft
  python main.py init_db      (初回のみ / DB再作成したいとき)

使い方は README.md 参照。scheduler/setup_tasks.ps1 が Task Scheduler への
登録を自動化する。
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import settings

COMMANDS = {
    "init_db": lambda: __import__("src.db", fromlist=["init_db"]).init_db(),
    "morning_brief": lambda: __import__("src.morning_brief", fromlist=["run"]).run(),
    "facebook_monitor": lambda: __import__("src.facebook_monitor", fromlist=["run"]).run(),
    "instagram_ads": lambda: __import__("src.instagram_ads", fromlist=["run"]).run(),
    "weekly": lambda: __import__("src.weekly_monthly_analysis", fromlist=["run_weekly"]).run_weekly(),
    "monthly": lambda: __import__("src.weekly_monthly_analysis", fromlist=["run_monthly"]).run_monthly(),
    "note_draft": lambda: __import__("src.note_article_generator", fromlist=["run"]).run(),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Manager CLI")
    parser.add_argument("command", choices=sorted(COMMANDS.keys()))
    args = parser.parse_args()

    log_dir = settings.logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / f"{args.command}.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("main")

    try:
        logger.info("=== %s 開始 ===", args.command)
        COMMANDS[args.command]()
        logger.info("=== %s 完了 ===", args.command)
        return 0
    except Exception:
        logger.error("=== %s 失敗 ===\n%s", args.command, traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
