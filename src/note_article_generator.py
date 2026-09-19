"""
機能5: 実装プロセスの記事化 (NOTE 記事案生成)

実行頻度:
  - 毎週日曜 19:00: 「今週のプロセス記事」(無料/有料/プロセス記事の3案)
  - 毎月末: 「月間統合記事」

★重要な制約（仕様書には無いが実装上必ず共有すべき事実）:
  note.com には 2026年9月時点で公開の投稿 API が存在しない。
  そのため本 Phase 0 では「NOTE へ自動投稿」ではなく
  「記事下書きを自動生成 → Markdown ファイルとして保存 → Ej が
  note.com に手動でコピペ投稿 (5-10分)」を正式フローとする。
  仕様書の「Ej が5-10分で確認・修正」という記述と実質的に両立する。

  将来 Playwright 等でブラウザ操作を自動化し下書き投稿まで行うことは
  可能だが、ログイン情報を扱う自動化は仕様変更として Ej の承認を得た上で
  Phase 1 以降に別途実装する。

フロー:
  1. content_ideas (未使用ネタ) + 直近の weekly/monthly レポートを収集
  2. Claude に無料記事/有料記事/プロセス記事の3パターンを生成させる
  3. Markdown ファイルとして docs/note_drafts/ に保存
  4. reports(note_draft) に記録、使用したネタは used_in_article=1 に更新
  5. Slack に「下書きができました。確認してください」と通知
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from config.settings import settings
from src import db
from src.clients import claude_client, slack_client

logger = logging.getLogger(__name__)

DRAFTS_DIR = settings.root_dir / "docs" / "note_drafts"


def _collect_ideas_text(limit: int = 10) -> tuple[str, list[int]]:
    ideas = db.unused_content_ideas(limit=limit)
    if not ideas:
        return "(蓄積されたネタなし。最近の Facebook/広告レポートから直接テーマを考えてください)", []
    text = "\n".join(f"- [{i['id']}] ({i['tag']}) {i['theme']}: {i['body'][:120]}" for i in ideas)
    return text, [i["id"] for i in ideas]


def _latest_period_report() -> str:
    today = date.today()
    weekly = db.reports_between("weekly", (today.isoformat())[:7] + "-01", today.isoformat())
    if weekly:
        return weekly[-1]["content"]
    return "(週次レポートなし)"


PROMPT_TEMPLATE = """あなたは TheVintageSalon / airstobu / GoldenMUGI の AI マネージャー実装プロセスを
NOTE(note.com) で発信するライターです。以下の情報から3つの記事案を作成してください。

# 蓄積されたネタ（Facebookの質問傾向・広告データから抽出）
{ideas_text}

# 直近の週次/月次戦略レポート
{period_report}

# 出力してほしい3記事（Markdown、それぞれ見出し付き）

## 無料記事
タイトル案 + 本文（800字程度）。Facebookグループで見えたトレンドを切り口に、
読者に「気づき」を与える内容。

## 有料記事
タイトル案 + 導入部300字程度 + 目次案（本文は有料部分なので概要のみ）。
広告データやターゲット層分析など、マーケティング的な深い分析を予告する内容。

## プロセス記事
タイトル案 + 本文（600字程度）。AIマネージャー実装の進捗・失敗と修正を
正直に書く「舞台裏」記事。実装初期は特に「うまくいかなかったこと」も書く。

各記事の末尾に、Ejが確認・修正しやすいよう「編集メモ」（直すべきかもしれない点）を1-2行添える。
"""


def generate_drafts() -> str:
    ideas_text, idea_ids = _collect_ideas_text()
    period_report = _latest_period_report()

    if not claude_client.is_configured():
        raise RuntimeError("CLAUDE_API_KEY が未設定のため記事案を生成できません。")

    prompt = PROMPT_TEMPLATE.format(ideas_text=ideas_text, period_report=period_report)
    drafts_md = claude_client.generate(prompt, max_tokens=3000)

    for idea_id in idea_ids:
        db.mark_idea_used(idea_id)

    return drafts_md


def save_drafts(drafts_md: str) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{date.today().isoformat()}_note_draft.md"
    path = DRAFTS_DIR / filename
    header = (
        f"<!-- 自動生成: {date.today().isoformat()} -->\n"
        f"<!-- 注意: note.com には公開投稿APIが無いため、この下書きを確認・修正のうえ\n"
        f"     手動で note.com にコピー&ペーストして投稿してください。 -->\n\n"
    )
    path.write_text(header + drafts_md, encoding="utf-8")
    return path


def run() -> str:
    drafts_md = generate_drafts()
    path = save_drafts(drafts_md)
    period_key = date.today().isoformat()
    db.save_report("note_draft", period_key, drafts_md)

    notice = (
        f"NOTE 記事の下書きを生成しました。\n"
        f"保存先: {path}\n\n"
        f"確認・修正のうえ note.com へ手動投稿してください（API 未提供のため自動投稿は不可）。\n\n"
        f"--- 下書きプレビュー（先頭部分） ---\n" + "\n".join(drafts_md.splitlines()[:15])
    )
    slack_client.output(notice, title="NOTE 記事下書き完成")
    return str(path)


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run()
