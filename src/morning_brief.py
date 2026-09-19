"""
機能1: 毎朝「今日のタスク」自動指示

実行時刻: settings.morning_brief_time (デフォルト 06:30、Windows Task
Scheduler から1日1回起動する想定。scheduler/setup_tasks.ps1 参照)

フロー:
  1. Gemini 経由で Google Calendar の今日の予定・Gmail 未読メールを取得
  2. 前日夜間の Instagram 広告レポート・Facebook 日次レポートを DB から参照
  3. Claude が優先度判定し、仕様書フォーマットに沿った指示書テキストを生成
  4. task_history に提案タスクを記録 (完了トラッキング用)
  5. Slack / console に出力

Calendar/Gmail の取得に失敗しても (仕様書4章1: 連携が不安定な可能性)
処理を止めず、「取得できませんでした」という注記付きで残りの情報だけで
レポートを生成する。
"""
from __future__ import annotations

import logging
from datetime import date

from config.settings import settings
from src import db
from src.clients import claude_client, gemini_client, slack_client

logger = logging.getLogger(__name__)


def _format_events(events: list[dict]) -> str:
    if not events:
        return "（本日の予定は取得できませんでした。手動で Google Calendar を確認してください）"
    lines = []
    for ev in events:
        start = (ev.get("start") or "")[11:16] or ev.get("start", "終日")
        end = (ev.get("end") or "")[11:16] or ""
        time_range = f"{start}-{end}" if end else start
        lines.append(f"- {time_range}：{ev['summary']}（{ev['calendar']}）")
    return "\n".join(lines)


def _format_emails(emails: list[dict]) -> str:
    if not emails:
        return "（未読メールなし、または取得できませんでした）"
    lines = []
    for m in emails[:10]:
        flag = "🔴" if m["important"] else "・"
        lines.append(f"{flag} {m['subject']}（{m['from']}）")
    return "\n".join(lines)


def _latest_ig_note() -> str:
    today = date.today().isoformat()
    report = db.latest_report("ig_daily", today) or db.latest_report(
        "ig_daily", (date.today()).isoformat()
    )
    if not report:
        return "（Instagram 広告の最新レポートはまだありません。機能3を先に実行してください）"
    # 要約は先頭数行のみ抜粋 (詳細は当該レポートを参照)
    return "\n".join(report["content"].splitlines()[:6])


def _latest_fb_note() -> str:
    today = date.today().isoformat()
    report = db.latest_report("fb_daily", today)
    if not report:
        return "（Facebook 日次レポートはまだありません。機能2を先に実行してください）"
    return "\n".join(report["content"].splitlines()[:6])


def build_context() -> dict[str, str]:
    events = gemini_client.fetch_todays_calendar_events() if gemini_client.is_google_oauth_configured() else []
    emails = gemini_client.fetch_unread_important_emails() if gemini_client.is_google_oauth_configured() else []
    return {
        "today": date.today().strftime("%Y年%m月%d日"),
        "events_text": _format_events(events),
        "emails_text": _format_emails(emails),
        "ig_note": _latest_ig_note(),
        "fb_note": _latest_fb_note(),
        "businesses": "、".join(settings.businesses),
    }


PROMPT_TEMPLATE = """あなたは Ej の複数事業（{businesses}）を統括する AI マネージャーです。
以下の情報から、仕様書の【朝の指示書】フォーマットに厳密に従って本日のタスク指示書を作成してください。

# 今日の日付
{today}

# 本日の Google Calendar 予定
{events_text}

# 未読メール（重要度順）
{emails_text}

# 直近の Instagram 広告レポート要約
{ig_note}

# 直近の Facebook 日次レポート要約
{fb_note}

# 出力フォーマット（この構造を厳守すること）
【朝の指示書】{today}

【Ej の予定】
(空き時間・予定を時系列で箇条書き)

【推奨タスク】
①②③...(優先度順、時間帯の目安つきで3〜5個)

【注意】
(広告のCPA異常、新規メンバー、要対応の問い合わせなど、見逃すとまずい事項)

これで OK？ 修正が必要な箇所を言ってください。
"""


def generate_morning_brief() -> str:
    ctx = build_context()
    if not claude_client.is_configured():
        raise RuntimeError(
            "ANTHROPIC_API_KEY が未設定のため朝の指示書を生成できません。.env を設定してください。"
        )
    prompt = PROMPT_TEMPLATE.format(**ctx)
    brief = claude_client.generate(prompt, max_tokens=1500, temperature=0.5)
    return brief


def _extract_tasks(brief_text: str) -> list[str]:
    """生成テキストの【推奨タスク】セクションから ①②③... の行だけ抜き出す。"""
    lines = brief_text.splitlines()
    tasks: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("【推奨タスク】"):
            in_section = True
            continue
        if in_section and stripped.startswith("【"):
            break
        if in_section and stripped:
            tasks.append(stripped)
    return tasks


def run() -> str:
    today = date.today().isoformat()
    brief = generate_morning_brief()
    db.save_report("morning_brief", today, brief)
    tasks = _extract_tasks(brief)
    if tasks:
        db.add_tasks(today, tasks, source="morning_brief")
    slack_client.output(brief, title=f"朝の指示書 {today}")
    return brief


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run()
