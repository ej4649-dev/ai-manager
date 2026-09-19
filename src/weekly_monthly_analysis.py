"""
機能4: 週別・月別統合分析

実行頻度:
  - 週別: 毎週金曜 19:00
  - 月別: 毎月末 20:00

フロー:
  1. DB から対象期間の Facebook日次レポート / Instagram広告日次レポート を集計
  2. Claude にトレンド抽出・来週/来月への戦略提案を生成させる
  3. content_ideas に「ネタ」として記録 (機能5 NOTE記事化で再利用)
  4. reports に保存 + 出力
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from config.settings import settings
from src import db
from src.clients import claude_client, slack_client

logger = logging.getLogger(__name__)


def _week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _collect_period_reports(report_type: str, start: date, end: date) -> list[str]:
    rows = db.reports_between(report_type, start.isoformat(), end.isoformat())
    return [r["content"] for r in rows]


def _ig_summary_for_period(start: date, end: date) -> dict:
    rows = [dict(r) for r in db.ig_metrics_range(start.isoformat(), end.isoformat())]
    spend = sum(r["spend"] for r in rows)
    conversions = sum(r["conversions"] for r in rows)
    cpa = round(spend / conversions, 0) if conversions else None
    return {
        "spend": spend,
        "conversions": conversions,
        "cpa": cpa,
        "impressions": sum(r["impressions"] for r in rows),
        "clicks": sum(r["clicks"] for r in rows),
    }


def _generate_analysis(period_label: str, fb_reports: list[str], ig_summary: dict, is_monthly: bool) -> str:
    if not claude_client.is_configured():
        return "（CLAUDE_API_KEY 未設定のため AI 分析はスキップ）"

    fb_joined = "\n---\n".join(fb_reports[-14:]) or "(Facebook日次レポートなし)"
    scope = "月別振り返り会議" if is_monthly else "週次の戦略会議"

    prompt = f"""あなたは複数事業（{'、'.join(settings.businesses)}）を統括する AI マネージャーです。
{period_label} の{scope}レポートを、仕様書フォーマットに沿って作成してください。

# 期間中の Facebook 日次レポート群
{fb_joined}

# 期間中の Instagram 広告サマリー
{ig_summary}

出力構成（この順序で、簡潔かつ具体的に）:
【{'先月' if is_monthly else '先週'}の成果サマリー】
【トレンド分析】(Facebookでの質問傾向の変化、反応の良いコンテンツ傾向、ターゲット層の動きなど)
【{'10月' if is_monthly else '来週'}への提案】(ネタ・戦略・コンテンツを①②③形式で、根拠つき)
{'【Ej からのコメント求む】' if is_monthly else ''}
"""
    return claude_client.generate(prompt, max_tokens=1800)


def _extract_ideas_from_analysis(analysis: str, source: str) -> None:
    """トレンド分析/提案セクションから簡易的にネタを content_ideas に保存する。"""
    for line in analysis.splitlines():
        stripped = line.strip()
        if stripped.startswith(("①", "②", "③", "④", "⑤")) and len(stripped) > 3:
            theme = stripped[1:].split("：")[0].split(":")[0].strip()[:60]
            db.add_content_idea(theme=theme, tag="candidate", business=None, body=stripped, source=source)


def run_weekly(target_date: date | None = None) -> str:
    target_date = target_date or date.today()
    end = target_date
    start = end - timedelta(days=6)
    period_key = _week_key(end)

    fb_reports = _collect_period_reports("fb_daily", start, end)
    ig_summary = _ig_summary_for_period(start, end)
    analysis = _generate_analysis(f"{start.isoformat()}〜{end.isoformat()}", fb_reports, ig_summary, is_monthly=False)
    _extract_ideas_from_analysis(analysis, source="weekly_analysis")

    report = f"【金曜の戦略会議】{end.strftime('%Y年%m月%d日')}\n\n{analysis}"
    db.save_report("weekly", period_key, report)
    slack_client.output(report, title="週次戦略会議")
    return report


def run_monthly(target_date: date | None = None) -> str:
    target_date = target_date or date.today()
    start = target_date.replace(day=1)
    # 月末日を計算
    if target_date.month == 12:
        end = target_date.replace(day=31)
    else:
        end = target_date.replace(month=target_date.month + 1, day=1) - timedelta(days=1)
    period_key = start.strftime("%Y-%m")

    fb_reports = _collect_period_reports("fb_daily", start, end)
    ig_summary = _ig_summary_for_period(start, end)
    analysis = _generate_analysis(f"{start.strftime('%Y年%m月')}", fb_reports, ig_summary, is_monthly=True)
    _extract_ideas_from_analysis(analysis, source="monthly_analysis")

    report = f"【月末振り返り会議】{end.strftime('%Y年%m月%d日')}\n\n{analysis}"
    db.save_report("monthly", period_key, report)
    slack_client.output(report, title="月末振り返り会議")
    return report


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=settings.log_level)
    mode = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if mode == "monthly":
        run_monthly()
    else:
        run_weekly()
