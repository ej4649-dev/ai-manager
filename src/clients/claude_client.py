"""
Claude client - 優先度判定・テキスト生成・分析

仕様書全体で「Claude が生成/分析」とされている箇所はすべてここを経由する:
  - 機能1: 朝の指示書のタスク優先度判定・文面生成
  - 機能2: コメント優先度リスト・返信テンプレ生成
  - 機能3: CPA 変動の理由推測・最適化提案
  - 機能4: 週次/月次の統合分析・戦略提案
  - 機能5: NOTE 記事案生成
"""
from __future__ import annotations

import logging

from config.settings import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "CLAUDE_API_KEY が未設定です。.env に設定してください (.env.example 参照)。"
            )
        import anthropic

        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def generate(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 2000,
) -> str:
    """単発プロンプト → テキスト応答。全機能の共通エントリーポイント。

    注: 以前は temperature 引数でサンプリングのランダム性を調整していたが、
    現行の Messages API (anthropic SDK >= 1.7) では temperature パラメータが
    廃止されている（実機で確認済み: TypeError: unexpected keyword 'temperature'）。
    代替として output_config.effort（low/medium/high/xhigh/max）があるが、これは
    応答のランダム性ではなく推論の深さ/コストを制御するもので意味が異なるため、
    安易に温度の代替として使わず、必要になったら明示的に追加すること。
    """
    client = _get_client()
    kwargs = dict(
        model=settings.claude_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)
