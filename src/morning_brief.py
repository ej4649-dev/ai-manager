"""
機能1: 毎朝「今日のタスク」自動指示

実行時刻: settings.morning_brief_time (デフォルト 06:30、Windows Task
Scheduler から1日1回起動する想定。scheduler/setup_tasks.ps1 参照)

フロー:
  1. Gemini 経由で Google Calendar の今日の予定・Gmail 未読メールを取得
  2. 前日夜間の Instagram 広告レポート・Facebook 日次レポートを DB から参照
  3. docs/gemini_context.md（運用哲学・判定基準・現在の運用状態）を読み込み
  4. Claude が上記すべてを踏まえて優先度判定し、指示書テキストを生成
     （生成エンジンは仕様書どおり Claude。gemini_context.md の内容と
     判定基準をプロンプトに組み込むことで、Ej の「Gemini＝意思決定の
     入口」という運用哲学を反映させている）
  5. task_history に提案タスクを記録 (完了トラッキング用)
  6. Slack / console に出力

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


def _load_gemini_context() -> str:
    """docs/gemini_context.md を読み込む。

    Ej の運用哲学（Gemini=意思決定の入口、判定基準、NOTE戦略、アイデア
    ストックなど）をプロンプトに含めることで、単発のタスクリストではなく
    その背景にある狙いを踏まえた優先度判定をさせる狙い。
    ファイルが無くても処理は止めない。
    """
    path = settings.root_dir / "docs" / "gemini_context.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("docs/gemini_context.md が見つかりません: %s", path)
        return "(gemini_context.md が未作成)"


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
        "gemini_context": _load_gemini_context(),
    }


PROMPT_TEMPLATE = """あなたは Ej の複数事業（{businesses}）を統括する AI マネージャーです。
以下の運用コンテキストと本日の情報から、今日のタスク指示書を作成してください。

# 運用コンテキスト（docs/gemini_context.md）
{gemini_context}

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

# 判定基準（運用コンテキストの「Gemini がすべき判定」に基づく）
1. Instagram 投稿が必要か？
2. Facebook グループ対応が必要か？
3. 売上レポート準備が必要か？
4. その他の優先タスク

# 出力フォーマット（この構造を厳守すること）
## 今日のタスク（{today}）

### 予定
(空き時間・予定を時系列で箇条書き。取得できていなければその旨を書く)

### 優先度1：[タスク名]
- 所要時間：X分
- 理由：なぜこれが優先なのか（上記の判定基準のどれに該当するか含む）
- 方法：具体的な実行手順

### 優先度2：[タスク名]
（同様の形式）

### 優先度3：[タスク名]
（同様の形式。優先度4・5も該当する内容があれば追加）

### 注意事項
(広告のCPA異常、新規メンバー、要対応の問い合わせなど、見逃すとまずい事項)

これで OK？ 修正が必要な箇所を言ってください。
"""


def generate_morning_brief() -> str:
    ctx = build_context()
    if not claude_client.is_configured():
        raise RuntimeError(
            "CLAUDE_API_KEY が未設定のため朝の指示書を生成できません。.env を設定してください。"
        )
    prompt = PROMPT_TEMPLATE.format(**ctx)

    # gemini_context.md を含めた新フォーマット（各タスクに所要時間/理由/方法）は
    # 2500 トークンでも実際に打ち切られる事例が複数回発生したため、
    # note_article_generator.py と同様に1段階だけ自動で引き上げて再試行する。
    for max_tokens in (2500, 4500):
        try:
            return claude_client.generate(prompt, max_tokens=max_tokens)
        except claude_client.TruncatedResponseError:
            logger.warning("朝の指示書生成が max_tokens=%d で打ち切られたため再試行します", max_tokens)
    raise claude_client.TruncatedResponseError(
        "max_tokens=4500 でも朝の指示書の生成が完了しませんでした。プロンプトを見直してください。"
    )


def _extract_tasks(brief_text: str) -> list[str]:
    """生成テキストの「### 優先度N：[タスク名]」の行だけ抜き出す。"""
    tasks: list[str] = []
    for line in brief_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### 優先度"):
            tasks.append(stripped.lstrip("#").strip())
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
