"""
機能2: Facebook グループ・ページの監視

実行頻度: 1日4回 (9時/12時/15時/19時、Windows Task Scheduler で登録)
監視対象:
  - ビンテージモーターホームファンクラブ (Facebook グループ) = settings.meta_group_id
  - The Vintage Salon Mobile (Facebook ページ) = settings.meta_page_id

フロー:
  1. Meta Graph API からグループ/ページの投稿・コメント・メンバー申請を取得
  2. DB (fb_posts / fb_member_requests) に upsert して90日蓄積
  3. 未返信コメントを抽出し、Claude に優先度リスト + 返信テンプレを生成させる
  4. 仕様書フォーマットでレポートを組み立て、DB(reports) に保存 + 出力
  5. 反応の多いテーマは content_ideas に記録 (機能5 NOTE 記事化のネタ元)
"""
from __future__ import annotations

import logging
from datetime import datetime

from config.settings import settings
from src import db
from src.clients import claude_client, meta_client, slack_client

logger = logging.getLogger(__name__)


def _ingest_group(group_id: str, since_hours: int = 24) -> None:
    posts = meta_client.fetch_group_feed(group_id, since_hours=since_hours)
    for post in posts:
        reactions = (post.get("reactions") or {}).get("summary", {}).get("total_count", 0)
        comments = post.get("comments", {}).get("data", [])
        db.upsert_fb_post(
            {
                "id": post["id"],
                "source_type": "group",
                "source_id": group_id,
                "kind": "post",
                "parent_id": None,
                "author": (post.get("from") or {}).get("name"),
                "message": post.get("message", ""),
                "reaction_count": reactions,
                "comment_count": len(comments),
                "is_replied": 0,
                "created_time": post["created_time"],
                "fetched_at": None,
                "raw_json": str(post),
            }
        )
        for c in comments:
            is_admin_reply = (c.get("from") or {}).get("name") in (None,)  # 明確化不可なら未返信扱い
            db.upsert_fb_post(
                {
                    "id": c["id"],
                    "source_type": "group",
                    "source_id": group_id,
                    "kind": "comment",
                    "parent_id": post["id"],
                    "author": (c.get("from") or {}).get("name"),
                    "message": c.get("message", ""),
                    "reaction_count": c.get("like_count", 0),
                    "comment_count": 0,
                    "is_replied": 0,
                    "created_time": c["created_time"],
                    "fetched_at": None,
                    "raw_json": str(c),
                }
            )

    for req in meta_client.fetch_group_member_requests(group_id):
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO fb_member_requests (id, group_id, name, requested_at, fetched_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    req["id"],
                    group_id,
                    (req.get("from") or {}).get("name"),
                    req.get("requested_at"),
                    datetime.now().isoformat(timespec="seconds"),
                    str(req),
                ),
            )


def _ingest_page(page_id: str, since_hours: int = 24) -> dict:
    posts = meta_client.fetch_page_feed(page_id, since_hours=since_hours)
    for post in posts:
        reactions = (post.get("reactions") or {}).get("summary", {}).get("total_count", 0)
        comments = post.get("comments", {}).get("data", [])
        db.upsert_fb_post(
            {
                "id": post["id"],
                "source_type": "page",
                "source_id": page_id,
                "kind": "post",
                "parent_id": None,
                "author": None,
                "message": post.get("message", ""),
                "reaction_count": reactions,
                "comment_count": len(comments),
                "is_replied": 0,
                "created_time": post["created_time"],
                "fetched_at": None,
                "raw_json": str(post),
            }
        )
        for c in comments:
            db.upsert_fb_post(
                {
                    "id": c["id"],
                    "source_type": "page",
                    "source_id": page_id,
                    "kind": "comment",
                    "parent_id": post["id"],
                    "author": (c.get("from") or {}).get("name"),
                    "message": c.get("message", ""),
                    "reaction_count": 0,
                    "comment_count": 0,
                    "is_replied": 0,
                    "created_time": c["created_time"],
                    "fetched_at": None,
                    "raw_json": str(c),
                }
            )
    try:
        insights = meta_client.fetch_page_insights(page_id)
    except meta_client.MetaAPIError:
        logger.warning("ページインサイト取得失敗: %s", page_id)
        insights = {}
    return insights


def _build_priority_and_templates(unreplied: list) -> str:
    if not unreplied:
        return "（未返信コメントなし）"
    if not claude_client.is_configured():
        # フォールバック: 生の一覧だけ返す
        return "\n".join(f"- {r['message'][:80]}" for r in unreplied[:10])

    joined = "\n".join(f"- 「{r['message']}」（投稿者: {r['author'] or '不明'}）" for r in unreplied[:15])
    prompt = f"""以下は Facebook グループ/ページで未返信のコメント一覧です。
1) 返信優先度の高い順にリスト化 (①②③...)、理由も一言添える
2) それぞれについて、丁寧で親しみやすい日本語の返信テンプレ案を書く
3) 全体を通してのトレンドキーワード（何についての質問が多いか）を3つ挙げる

未返信コメント:
{joined}
"""
    return claude_client.generate(prompt, max_tokens=1500)


def run() -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = [f"【Facebook 定期レポート】{today}"]

    if settings.meta_group_id:
        try:
            _ingest_group(settings.meta_group_id)
            counts = db.fb_daily_counts(settings.meta_group_id)
            unreplied = db.unreplied_comments(settings.meta_group_id)
            analysis = _build_priority_and_templates([dict(r) for r in unreplied])
            sections.append(
                f"\n【グループ】\n"
                f"- 新規投稿: {counts['new_posts'] or 0}件\n"
                f"- コメント: {counts['new_comments'] or 0}件（未返信: {counts['unreplied'] or 0}件）\n\n"
                f"【優先度リスト・返信テンプレ】\n{analysis}"
            )
        except meta_client.MetaAPIError as e:
            logger.error("グループ取得失敗: %s", e)
            sections.append(f"\n【グループ】取得失敗: {e}")
    else:
        sections.append("\n【グループ】META_FACEBOOK_GROUP_ID 未設定のためスキップ")

    if settings.meta_page_id:
        try:
            insights = _ingest_page(settings.meta_page_id)
            # page_impressions/page_engaged_users は Meta 側で廃止済みのため
            # meta_client.fetch_page_insights() のデフォルトを変更済み
            # (page_views_total / page_post_engagements)
            sections.append(
                f"\n【ページ】\n"
                f"- ページ訪問: {insights.get('page_views_total', 'N/A')}\n"
                f"- 投稿エンゲージメント: {insights.get('page_post_engagements', 'N/A')}"
            )
        except meta_client.MetaAPIError as e:
            logger.error("ページ取得失敗: %s", e)
            sections.append(f"\n【ページ】取得失敗: {e}")
    else:
        sections.append("\n【ページ】META_FACEBOOK_PAGE_ID 未設定のためスキップ")

    report = "\n".join(sections)
    period_key = datetime.now().strftime("%Y-%m-%d")
    db.save_report("fb_daily", period_key, report)
    slack_client.output(report, title="Facebook 定期レポート")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run()
