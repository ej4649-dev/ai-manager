"""
Grok (xAI) client - クリエイティブ・シナリオ相談用アシスタント

docs/gemini_context.md の役割分担に対応:
  - Gemini: 大きな判断・優先度付け・意思決定の入口
  - Grok: 創作・シナリオ生成・視覚化・クリエイティブ提案

xAI の Chat Completions API は OpenAI 互換 (POST /v1/chat/completions,
{"model", "messages": [{"role","content"}, ...]}) を使用する。

使い方:
    from src.clients.grok_client import GrokClient
    gc = GrokClient()
    print(gc.chat("次のNOTE記事のシナリオ案を3つ考えて"))
"""
from __future__ import annotations

import logging

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

GROK_API_URL = "https://api.x.ai/v1/chat/completions"

SYSTEM_PROMPT_TEMPLATE = """あなたは Ej のクリエイティブ・ビデオ生成 AI です。

【AI マネージャー コンテキスト】
{context}

【あなたの役割】
- Ej が「Grok、〇〇について相談したい」と言ったら、提案を整理して返す
- 創作・視覚化・シナリオ生成を専門とする
- 大きな判断・優先度付けが必要な場合は「Gemini にも相談すべき」と提案する
- 実行可能性を重視した簡潔な提案を心がける

【役割分担】
- Gemini：大判断・優先度付け・意思決定
- Grok：創作・シナリオ・視覚化・クリエイティブ提案
"""


class GrokAPIError(RuntimeError):
    pass


class GrokClient:
    def __init__(self) -> None:
        if not settings.grok_api_key:
            raise RuntimeError(
                "GROK_API_KEY が未設定です。.env に設定してください (.env.example 参照)。"
            )
        self.api_key = settings.grok_api_key
        self.model = settings.grok_model
        self._context_cache: str | None = None

    def _get_context(self) -> str:
        """docs/gemini_context.md を読み込んでシステムプロンプトに埋め込む。
        ファイルが無い場合もクラッシュさせず、その旨を context として返す。
        """
        if self._context_cache is not None:
            return self._context_cache

        context_path = settings.root_dir / "docs" / "gemini_context.md"
        try:
            self._context_cache = context_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("docs/gemini_context.md が見つかりません: %s", context_path)
            self._context_cache = "(gemini_context.md が未作成のためコンテキストなし)"
        return self._context_cache

    def chat(self, message: str) -> str:
        """Grok にメッセージを送り、応答テキストを返す。"""
        context = self._get_context()
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=context)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        }

        resp = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            logger.error("Grok API error [%s]: %s", resp.status_code, resp.text[:500])
            raise GrokAPIError(f"Grok API -> {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise GrokAPIError(f"Grok API の応答形式が想定外です: {data}") from e


def is_configured() -> bool:
    return bool(settings.grok_api_key)
