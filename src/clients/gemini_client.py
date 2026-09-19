"""
Gemini client - Google Calendar / Gmail 読み込み

仕様書「機能1」のデータソース:
  - Google Calendar（TheVintageSalon / airstobu / GoldenMUGI の予定）
  - Gmail（未返信メール、重要度順）

OAuth はデスクトップアプリフローを使用 (config/google_client_secret.json)。
初回実行時にブラウザが開き、認証後 config/google_token.json にトークンを保存、
以降は自動更新される。

Gemini API 自体 (テキスト分析・生成) は generate_text() で使う。
Calendar/Gmail の読み込みは Google API (google-api-python-client) を使う
— 仕様書は「Gemini が読み込み」と書いているが、Calendar/Gmail への実際の
アクセスは Google OAuth 経由の Calendar API / Gmail API を叩く実装とし、
その結果テキストを Gemini (または Claude) に渡して要約・優先度判定する
構成にしている。これは仕様書 4章「Gemini Calendar/Mail 連携が不安定な
可能性」への対応でもあり、Calendar/Gmail 取得failureとGemini API呼び出し
failureを切り分けられるようにする狙い。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

_creds = None


def _get_credentials():
    """OAuth 認証情報を取得 (トークンがあれば再利用、なければブラウザ認証)。"""
    global _creds
    if _creds and _creds.valid:
        return _creds

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if settings.google_token_path.exists():
        creds = Credentials.from_authorized_user_file(str(settings.google_token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not settings.google_client_secret_path.exists():
                raise RuntimeError(
                    f"Google OAuth クライアントシークレットが見つかりません: "
                    f"{settings.google_client_secret_path}\n"
                    "Google Cloud Console でデスクトップ用 OAuth クライアントを作成し、"
                    "JSON をこのパスに保存してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(settings.google_client_secret_path), SCOPES
            )
            creds = flow.run_local_server(port=0)
        settings.google_token_path.parent.mkdir(parents=True, exist_ok=True)
        settings.google_token_path.write_text(creds.to_json(), encoding="utf-8")

    _creds = creds
    return creds


def fetch_todays_calendar_events() -> list[dict[str, Any]]:
    """今日の Google Calendar 予定を取得 (全カレンダー横断)。

    アクセス失敗時は例外を投げず空リストを返し、呼び出し側 (morning_brief)
    が「カレンダー取得失敗」の注記を出してフォールバックできるようにする。
    """
    try:
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)

        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "Z"
        end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat() + "Z"

        events: list[dict[str, Any]] = []
        calendar_list = service.calendarList().list().execute()
        for cal in calendar_list.get("items", []):
            resp = (
                service.events()
                .list(
                    calendarId=cal["id"],
                    timeMin=start,
                    timeMax=end,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for ev in resp.get("items", []):
                events.append(
                    {
                        "calendar": cal.get("summary", cal["id"]),
                        "summary": ev.get("summary", "(無題)"),
                        "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date")),
                        "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date")),
                    }
                )
        events.sort(key=lambda e: e["start"] or "")
        return events
    except Exception:
        logger.exception("Google Calendar 取得に失敗しました")
        return []


def fetch_unread_important_emails(max_results: int = 15) -> list[dict[str, Any]]:
    """未読メールを取得し、日付降順・重要度優先の簡易並び替えで返す。"""
    try:
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("gmail", "v1", credentials=creds)

        resp = (
            service.users()
            .messages()
            .list(userId="me", q="is:unread", maxResults=max_results)
            .execute()
        )
        messages = []
        for m in resp.get("messages", []):
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=m["id"], format="metadata",
                     metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            is_important = "IMPORTANT" in msg.get("labelIds", [])
            messages.append(
                {
                    "from": headers.get("From", "(不明)"),
                    "subject": headers.get("Subject", "(件名なし)"),
                    "date": headers.get("Date", ""),
                    "important": is_important,
                    "snippet": msg.get("snippet", ""),
                }
            )
        messages.sort(key=lambda m: (not m["important"],))
        return messages
    except Exception:
        logger.exception("Gmail 取得に失敗しました")
        return []


# --- Gemini generative API (テキスト分析・生成) ---------------------------

_genai_client = None


def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY が未設定です。.env に設定してください。")
        from google import genai

        _genai_client = genai.Client(api_key=settings.gemini_api_key)
    return _genai_client


def generate_text(prompt: str) -> str:
    client = _get_genai_client()
    resp = client.models.generate_content(model=settings.gemini_model, contents=prompt)
    return resp.text


def is_configured() -> bool:
    return bool(settings.gemini_api_key)


def is_google_oauth_configured() -> bool:
    return settings.google_client_secret_path.exists()
