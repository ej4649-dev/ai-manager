"""
機能3: Instagram Reels 広告の自動分析

実行頻度: 1日1回 (夜20時、Windows Task Scheduler)
監視対象: airstobu + TheVintageSalon のリール広告 (settings.meta_ad_account_id)

フロー:
  1. Meta Graph API (Ads Insights, date_preset=yesterday) から前日実績を取得
  2. DB (ig_ad_metrics) に保存、90日分蓄積 → 週次比較に使う
  3. 前週同曜日/週平均との比較で CPA 変動率を計算
  4. Claude に「変動理由の推測」「クリエイティブ最適化提案」「予算配分提案」を生成させる
  5. 仕様書フォーマットでレポート組み立て → DB(reports) 保存 + 出力
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from config.settings import settings
from src import db
from src.clients import claude_client, meta_client, slack_client

logger = logging.getLogger(__name__)


def _business_for_ad(ad_name: str) -> str:
    name_lower = (ad_name or "").lower()
    for biz in settings.businesses:
        if biz.lower() in name_lower:
            return biz
    return settings.businesses[0] if settings.businesses else "unknown"


def ingest_yesterday() -> list[dict]:
    if not settings.meta_ad_account_id:
        logger.warning("META_AD_ACCOUNT_ID 未設定のため Instagram 広告取得をスキップ")
        return []

    rows = meta_client.fetch_ad_insights(settings.meta_ad_account_id, date_preset="yesterday")
    saved = []
    for row in rows:
        impressions = int(row.get("impressions", 0) or 0)
        clicks = int(row.get("clicks", 0) or 0)
        spend = float(row.get("spend", 0) or 0)
        conversions = meta_client.extract_conversions(row)
        cpc = float(row.get("cpc", 0) or 0)
        ctr = float(row.get("ctr", 0) or 0)
        cpa = (spend / conversions) if conversions else None
        business = _business_for_ad(row.get("ad_name", ""))
        record = {
            "date": row.get("date_start", (date.today() - timedelta(days=1)).isoformat()),
            "business": business,
            "ad_id": row.get("ad_id"),
            "ad_name": row.get("ad_name"),
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "spend": spend,
            "cpc": cpc,
            "cpa": cpa,
            "ctr": ctr,
            "fetched_at": None,
            "raw_json": str(row),
        }
        db.upsert_ig_metric(record)
        saved.append(record)
    return saved


def _aggregate(rows) -> dict:
    impressions = sum(r["impressions"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    conversions = sum(r["conversions"] for r in rows)
    spend = sum(r["spend"] for r in rows)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "spend": spend,
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0,
        "cpa": round(spend / conversions, 0) if conversions else None,
    }


def _week_ago_comparison(today_agg: dict) -> tuple[dict, float | None]:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    last_week_rows = [dict(r) for r in db.ig_metrics_range(start.isoformat(), end.isoformat())]
    last_week_agg = _aggregate(last_week_rows) if last_week_rows else {"cpa": None}
    cpa_change = None
    if today_agg.get("cpa") and last_week_agg.get("cpa"):
        cpa_change = round((today_agg["cpa"] - last_week_agg["cpa"]) / last_week_agg["cpa"] * 100, 1)
    return last_week_agg, cpa_change


def _build_analysis(today_agg: dict, last_week_agg: dict, cpa_change: float | None, rows: list[dict]) -> str:
    if not claude_client.is_configured():
        return "（CLAUDE_API_KEY 未設定のため AI 分析はスキップ。数値のみ表示）"

    per_ad = "\n".join(
        f"- {r['ad_name']}（{r['business']}）: imp={r['impressions']}, clicks={r['clicks']}, "
        f"conv={r['conversions']}, spend={r['spend']}円, CPA={r['cpa']}"
        for r in rows
    ) or "(広告データなし)"

    prompt = f"""あなたは Instagram/Facebook 広告運用の専門コンサルタントです。
以下の実績データから、仕様書フォーマットに沿って分析してください。

# 本日(前日)の集計
{today_agg}

# 直近7日平均との比較
先週集計: {last_week_agg}
CPA変動率: {cpa_change}%

# 広告ごとの内訳
{per_ad}

出力してほしい内容:
1. CPA変動の理由推測（iOSトラッキング変更、季節要因、クリエイティブ疲労など具体的仮説）
2. クリエイティブ最適化の提案（2〜3個、根拠つき）
3. 予算配分の提案（事業ごとのパーセンテージ、根拠つき）
簡潔に、仕様書の【提案】セクションのような箇条書き形式で。
"""
    return claude_client.generate(prompt, max_tokens=1200)


def run() -> str:
    rows = ingest_yesterday()
    today_agg = _aggregate(rows)
    last_week_agg, cpa_change = _week_ago_comparison(today_agg)
    analysis = _build_analysis(today_agg, last_week_agg, cpa_change, rows)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    cpa_str = f"{today_agg['cpa']:.0f}円" if today_agg.get("cpa") else "N/A"
    ctr_str = f"{today_agg['ctr']}%"
    change_str = f"{cpa_change:+.1f}%" if cpa_change is not None else "N/A（先週データ不足）"

    report = f"""【Instagram 広告日次分析】{yesterday}

【本日の成績】
- インプレッション: {today_agg['impressions']:,}
- クリック: {today_agg['clicks']:,}（CTR: {ctr_str}）
- 獲得数: {today_agg['conversions']}（CPA: {cpa_str}）
- 支出: {today_agg['spend']:,.0f}円

【週次との比較】
- CPA変動: {change_str}

【提案】
{analysis}

【即座に実行】
- クリエイティブ更新予定ある？
- 予算配分の変更を承認しますか？
"""
    db.save_report("ig_daily", yesterday, report)
    slack_client.output(report, title="Instagram 広告日次分析")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run()
