"""
Meta Graph API client - Facebook グループ/ページ監視 + Instagram 広告データ

仕様書「機能2」「機能3」のデータ収集レイヤー。Graph API v18.0 を使用。

必要な権限 (事前確認事項、仕様書4章2):
  - groups_access_member_info, publish_to_groups (グループ監視・返信する場合)
  - pages_read_engagement, pages_manage_engagement (ページ監視)
  - ads_read (Instagram/Facebook 広告データ)
  - instagram_basic, instagram_manage_insights

注意: Facebook グループ API は 2018年以降大幅に制限されており、
「参加グループ」の投稿取得には「グループ管理者アプリ」の Meta 審査
(App Review) 通過が必須。ページ・広告アカウントのインサイトは
通常のアクセス権限で取得可能。本モジュールは審査通過を前提に実装し、
未審査の場合は Graph API が 403 を返すのでその旨をログに出す。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v18.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class MetaAPIError(RuntimeError):
    pass


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.meta_page_access_token:
        raise MetaAPIError(
            "META_PAGE_ACCESS_TOKEN が未設定です。.env に設定してください (.env.example 参照)。"
        )
    params = dict(params or {})
    params["access_token"] = settings.meta_page_access_token
    resp = requests.get(f"{GRAPH_BASE}/{path}", params=params, timeout=30)
    if resp.status_code != 200:
        logger.error("Graph API error [%s]: %s", resp.status_code, resp.text[:500])
        raise MetaAPIError(f"Graph API {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# --- Facebook グループ ----------------------------------------------------

def fetch_group_feed(group_id: str, since_hours: int = 24) -> list[dict[str, Any]]:
    """指定グループの直近投稿+コメントを取得。"""
    since = (datetime.now() - timedelta(hours=since_hours)).timestamp()
    data = _get(
        f"{group_id}/feed",
        {
            "fields": "id,message,from,created_time,comments.limit(50){id,message,from,created_time,like_count},"
                      "reactions.summary(total_count)",
            "since": int(since),
        },
    )
    return data.get("data", [])


def fetch_group_member_requests(group_id: str) -> list[dict[str, Any]]:
    try:
        data = _get(f"{group_id}/member_requests", {"fields": "id,from,requested_at"})
        return data.get("data", [])
    except MetaAPIError:
        logger.warning(
            "member_requests の取得に失敗（審査未通過の可能性）。グループID=%s", group_id
        )
        return []


# --- Facebook ページ -------------------------------------------------------

def fetch_page_feed(page_id: str, since_hours: int = 24) -> list[dict[str, Any]]:
    since = (datetime.now() - timedelta(hours=since_hours)).timestamp()
    data = _get(
        f"{page_id}/feed",
        {
            "fields": "id,message,created_time,comments.limit(50){id,message,from,created_time},"
                      "reactions.summary(total_count),shares",
            "since": int(since),
        },
    )
    return data.get("data", [])


def fetch_page_insights(page_id: str, metrics: tuple[str, ...] = (
    "page_impressions", "page_engaged_users", "page_views_total",
)) -> dict[str, int]:
    data = _get(f"{page_id}/insights", {"metric": ",".join(metrics), "period": "day"})
    out: dict[str, int] = {}
    for item in data.get("data", []):
        values = item.get("values", [])
        out[item["name"]] = values[-1]["value"] if values else 0
    return out


# --- Instagram 広告 (Meta Ads Manager via ad account insights) ------------

def fetch_ad_insights(ad_account_id: str, date_preset: str = "yesterday") -> list[dict[str, Any]]:
    """広告アカウント単位の日次インサイト (impressions/clicks/spend/actions)。"""
    fields = "ad_id,ad_name,impressions,clicks,spend,cpc,ctr,actions,date_start,date_stop"
    data = _get(
        f"{ad_account_id}/insights",
        {
            "level": "ad",
            "fields": fields,
            "date_preset": date_preset,
            "time_increment": 1,
        },
    )
    return data.get("data", [])


def extract_conversions(ad_insight_row: dict[str, Any], action_type: str = "offsite_conversion") -> int:
    for action in ad_insight_row.get("actions", []) or []:
        if action_type in action.get("action_type", ""):
            return int(float(action.get("value", 0)))
    return 0


def is_configured() -> bool:
    return bool(settings.meta_page_access_token)
