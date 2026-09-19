"""
Slack 通知 client - Incoming Webhook 経由

仕様書の「出力先: Slack / メール」「出力先: Slack / Dashboard」に対応。
OUTPUT_CHANNEL=console|slack|both で出力先を切り替え可能
(まず console だけでテストしてから Slack を有効化する運用を想定)。
"""
from __future__ import annotations

import logging

import requests

from config.settings import settings

logger = logging.getLogger(__name__)


def send(text: str, title: str | None = None) -> bool:
    """Slack Webhook にテキストを送信。設定が無ければ何もせず False を返す。"""
    if not settings.slack_webhook_url:
        logger.info("SLACK_WEBHOOK_URL 未設定のため Slack 送信をスキップしました。")
        return False

    payload = {"text": f"*{title}*\n{text}" if title else text}
    resp = requests.post(settings.slack_webhook_url, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error("Slack 送信失敗 [%s]: %s", resp.status_code, resp.text[:300])
        return False
    return True


def output(text: str, title: str | None = None) -> None:
    """settings.output_channel に応じて console / Slack / 両方に出力する共通関数。"""
    channel = settings.output_channel
    if channel in ("console", "both"):
        print("\n" + ("=" * 60))
        if title:
            print(title)
            print("-" * 60)
        print(text)
        print("=" * 60 + "\n")
    if channel in ("slack", "both"):
        send(text, title=title)
