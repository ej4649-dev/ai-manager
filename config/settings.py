"""
AI Manager - 設定ローダー

.env を読み込み、全モジュールから使う設定値を一箇所に集約する。
値が未設定の場合はそのモジュールを「スキップ」できるよう None を許容し、
使う側 (src/*.py) が明示的にチェックしてフォールバックする設計にしている
(仕様書 4章「既知の注意点」への対応: 1つの API が落ちても全体を止めない)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


# .env にまだ実キーが入っていない箇所に書く「後で提供します」等のプレースホルダー文言。
# これが値に含まれる場合は「未設定」として扱い、Claude/Gemini/Meta へ
# 意味の無い文字列を渡して分かりにくいエラーになるのを防ぐ。
_PLACEHOLDER_MARKERS = ("後で提供", "TODO", "REPLACE_ME", "your_key_here")


def _is_placeholder(val: str) -> bool:
    return any(marker in val for marker in _PLACEHOLDER_MARKERS)


def _get(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name, default)
    if val in ("", None):
        return default
    if isinstance(val, str) and _is_placeholder(val):
        return default
    return val


def _get_any(names: tuple[str, ...], default: str | None = None) -> str | None:
    """複数の候補名から最初に見つかった値を返す (Ej が指定した短縮名を優先し、
    旧来の META_ プレフィックス付き名前もフォールバックとして受け付ける)。"""
    for name in names:
        val = os.getenv(name)
        if val not in ("", None) and not _is_placeholder(val):
            return val
    return default


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    logs_dir: Path = ROOT_DIR / "logs"
    db_path: Path = ROOT_DIR / "data" / "ai_manager.db"

    # Claude
    anthropic_api_key: str | None = field(
        default_factory=lambda: _get_any(("CLAUDE_API_KEY", "ANTHROPIC_API_KEY"))
    )
    claude_model: str = field(default_factory=lambda: _get("CLAUDE_MODEL", "claude-sonnet-5"))

    # Gemini
    gemini_api_key: str | None = field(default_factory=lambda: _get("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _get("GEMINI_MODEL", "gemini-2.5-flash"))

    # Google OAuth
    google_client_secret_path: Path = field(
        default_factory=lambda: ROOT_DIR / _get("GOOGLE_OAUTH_CLIENT_SECRET_PATH", "config/google_client_secret.json")
    )
    google_token_path: Path = field(
        default_factory=lambda: ROOT_DIR / _get("GOOGLE_OAUTH_TOKEN_PATH", "config/google_token.json")
    )

    # Meta
    meta_app_id: str | None = field(default_factory=lambda: _get("META_APP_ID"))
    meta_app_secret: str | None = field(default_factory=lambda: _get("META_APP_SECRET"))
    meta_page_access_token: str | None = field(
        default_factory=lambda: _get_any(("META_ACCESS_TOKEN", "META_PAGE_ACCESS_TOKEN"))
    )
    meta_group_id: str | None = field(
        default_factory=lambda: _get_any(("FACEBOOK_GROUP_ID", "META_FACEBOOK_GROUP_ID"))
    )
    meta_page_id: str | None = field(
        default_factory=lambda: _get_any(("FACEBOOK_PAGE_ID", "META_FACEBOOK_PAGE_ID"))
    )
    meta_ig_business_id: str | None = field(default_factory=lambda: _get("META_INSTAGRAM_BUSINESS_ACCOUNT_ID"))
    meta_ad_account_id: str | None = field(
        default_factory=lambda: _get_any(("INSTAGRAM_AD_ACCOUNT_ID", "META_AD_ACCOUNT_ID"))
    )

    # Slack
    slack_webhook_url: str | None = field(default_factory=lambda: _get("SLACK_WEBHOOK_URL"))

    # NOTE (no public API - see .env.example)
    note_email: str | None = field(default_factory=lambda: _get("NOTE_EMAIL"))
    note_password: str | None = field(default_factory=lambda: _get("NOTE_PASSWORD"))

    # Operational
    businesses: tuple[str, ...] = field(
        default_factory=lambda: tuple(b.strip() for b in _get("BUSINESSES", "TheVintageSalon,airstobu,GoldenMUGI").split(","))
    )
    morning_brief_time: str = field(default_factory=lambda: _get("MORNING_BRIEF_TIME", "06:30"))
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))
    output_channel: str = field(default_factory=lambda: _get("OUTPUT_CHANNEL", "both"))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
