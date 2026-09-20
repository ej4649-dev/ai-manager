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


def _get(path: str, params: dict[str, Any] | None = None, access_token: str | None = None) -> dict[str, Any]:
    token = access_token or settings.meta_page_access_token
    if not token:
        raise MetaAPIError(
            "META_ACCESS_TOKEN が未設定です。.env に設定してください (.env.example 参照)。"
        )
    params = dict(params or {})
    params["access_token"] = token
    resp = requests.get(f"{GRAPH_BASE}/{path}", params=params, timeout=30)
    if resp.status_code != 200:
        logger.error("Graph API error [%s]: %s", resp.status_code, resp.text[:500])
        raise MetaAPIError(f"Graph API {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


_page_token_cache: dict[str, str] = {}


def _get_page_access_token(page_id: str) -> str:
    """ページ専用アクセストークンを取得する。

    Page Insights など一部のエンドポイントはユーザートークンでは呼べず、
    /me/accounts から取得できるページ固有のトークンが必要
    (実機で確認済み: ユーザートークンで呼ぶと
    "This method must be called with a Page Access Token" になる)。
    /me/accounts は一度に全ページ分のトークンを返すので、プロセス内で
    キャッシュして毎回呼び直さないようにする。
    """
    if page_id in _page_token_cache:
        return _page_token_cache[page_id]

    data = _get("me/accounts", {"fields": "id,name,access_token"})
    for page in data.get("data", []):
        _page_token_cache[page["id"]] = page["access_token"]

    if page_id not in _page_token_cache:
        raise MetaAPIError(
            f"ページ {page_id} が /me/accounts に見つかりません"
            "（このアカウントがページの管理者として登録されていない可能性があります）。"
        )
    return _page_token_cache[page_id]


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
    "page_views_total", "page_post_engagements",
)) -> dict[str, int]:
    """ページ インサイトを取得。

    注: page_impressions / page_engaged_users / page_fans は実機確認の結果
    "The value must be a valid insights metric" で拒否された（Meta側で
    廃止/改称された模様）。page_views_total / page_post_engagements は
    動作確認済み。新しいメトリクスを追加する際は事前に単体で疎通確認する
    こと（Meta は Insights メトリクスを予告なく変更することがある）。
    """
    page_token = _get_page_access_token(page_id)
    data = _get(f"{page_id}/insights", {"metric": ",".join(metrics), "period": "day"}, access_token=page_token)
    out: dict[str, int] = {}
    for item in data.get("data", []):
        values = item.get("values", [])
        out[item["name"]] = values[-1]["value"] if values else 0
    return out


# --- Instagram 広告 (Meta Ads Manager via ad account insights) ------------

def _normalize_ad_account_id(ad_account_id: str) -> str:
    """Graph API の広告アカウントエンドポイントは 'act_<数字>' 形式を要求する。
    .env には数字IDだけが入っていることが多いので、無ければ自動で付与する。"""
    return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"


def fetch_ad_insights(ad_account_id: str, date_preset: str = "yesterday") -> list[dict[str, Any]]:
    """広告アカウント単位の日次インサイト (impressions/clicks/spend/actions)。"""
    fields = "ad_id,ad_name,impressions,clicks,spend,cpc,ctr,actions,date_start,date_stop"
    data = _get(
        f"{_normalize_ad_account_id(ad_account_id)}/insights",
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
