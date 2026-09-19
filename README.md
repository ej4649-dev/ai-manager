# AI Manager - Phase 0 実装

Ej の複数事業（TheVintageSalon / airstobu / GoldenMUGI）を統合管理する AI マネージャー。
仕様: [`docs/Phase0_AI_Manager_Implementation_Spec.md`](docs/Phase0_AI_Manager_Implementation_Spec.md)

## 実装状況

| 機能 | 状態 | 備考 |
|---|---|---|
| 機能1: 毎朝タスク指示 | ✅ 実装済み | `src/morning_brief.py`。Calendar/Gmail 取得失敗時はフォールバック |
| 機能2: Facebook 監視 | ✅ 実装済み | `src/facebook_monitor.py`。要 Meta App Review（下記参照） |
| 機能3: Instagram 広告分析 | ✅ 実装済み | `src/instagram_ads.py` |
| 機能4: 週次/月次統合分析 | ✅ 実装済み | `src/weekly_monthly_analysis.py` + Streamlit dashboard |
| 機能5: NOTE 記事化 | ⚠️ 下書き自動生成まで | 下記「既知の制約」参照 |
| Windows Task Scheduler 登録 | ✅ スクリプト済み | `scheduler/setup_tasks.ps1` |
| SQLite ローカル DB (90日蓄積) | ✅ 実装済み | `src/db.py` |

**まだ出来ていないこと（Ej の環境で必要な作業）**:
- 各種 API キー/トークンの取得と `.env` への設定（下記セットアップ手順）
- Meta の App Review 通過（Facebook グループの投稿取得に必須）
- 実際の Google Calendar / Gmail アカウントでの認証テスト
- Windows Task Scheduler への実登録（管理者権限で `setup_tasks.ps1` を実行するだけ）
- 数日間の実運用でのプロンプト調整（レポート文面の細かい言い回しなど）

---

## ⚠️ 実装にあたっての既知の制約（要確認）

1. **NOTE (note.com) に公開の投稿 API は存在しません。**
   仕様書は「NOTE へ自動投稿」と書かれていますが、2026年9月時点で note.com は
   サードパーティが記事を自動投稿できる公式 API を提供していません。
   そのため機能5は **「記事下書きを自動生成 → `docs/note_drafts/` に Markdown 保存
   → Ej が確認・修正して手動でコピペ投稿」** というフローにしています。
   仕様書内の「Ej が5-10分で確認・修正」という工程とはほぼ一致するので、
   運用上のインパクトは小さいはずですが、"完全自動投稿"を期待していた場合はご注意ください。
   (将来的に Playwright 等でブラウザ操作による下書き投稿の自動化は可能。ログイン情報を
   扱う自動化になるため、やる場合は Phase 1 で別途スコープ合意してから実装します。)

2. **Facebook グループ API はアプリ審査が必須です。**
   個人の参加グループから投稿を取得するには、Meta の「App Review」で
   `groups_access_member_info` 等の審査を通過する必要があります(通常1〜2週間)。
   審査未通過の間は `facebook_monitor.py` の「ページ」部分（The Vintage Salon Mobile）
   だけが動作し、グループ監視部分はエラーメッセージ付きでスキップされます。

3. **各 API は個別に「未設定ならスキップ」する設計にしています。**
   Gemini が失敗しても Facebook 監視は動く、Meta が失敗しても朝の指示書は
   （カレンダー無しの注記付きで）生成される、というように、1つの連携不調で
   全体が止まらないようにしてあります（仕様書4章の懸念への対応）。

---

## セットアップ手順

### 1. Python 環境

```powershell
cd C:\Users\ej464\Downloads\ai-manager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. `.env` を作成

```powershell
Copy-Item .env.example .env
notepad .env
```

必要なキーの取得先は `.env.example` のコメントに記載済み。最低限、
`ANTHROPIC_API_KEY` だけ設定すれば `morning_brief`（カレンダー無し版）や
`note_draft` の動作確認ができます。

### 3. DB 初期化

```powershell
python main.py init_db
```

### 4. Google OAuth（機能1: Calendar/Gmail）

1. https://console.cloud.google.com/apis/credentials でプロジェクト作成
2. OAuth クライアント ID（種類: デスクトップ アプリ）を作成
3. ダウンロードした JSON を `config/google_client_secret.json` として保存
4. `python main.py morning_brief` を初回実行するとブラウザが開き認証を求められる
   → 認証後 `config/google_token.json` が自動生成され、以降は自動更新

### 5. Meta Graph API（機能2・機能3）

1. https://developers.facebook.com/apps/ でアプリ作成（種類: ビジネス）
2. Graph API Explorer で対象ページの長期アクセストークンを発行
3. `.env` に `META_PAGE_ACCESS_TOKEN` / `META_FACEBOOK_PAGE_ID` / `META_FACEBOOK_GROUP_ID` /
   `META_AD_ACCOUNT_ID` を設定
4. グループ監視を有効にする場合は App Review を申請（上記「既知の制約」参照）

### 6. Slack 通知（任意）

https://api.slack.com/apps → Incoming Webhooks を有効化 → `.env` の `SLACK_WEBHOOK_URL` に設定。
未設定でも `OUTPUT_CHANNEL=console`（デフォルトは `both`）にしておけばターミナル出力で確認できます。

### 7. 動作確認（個別実行）

```powershell
python main.py morning_brief
python main.py facebook_monitor
python main.py instagram_ads
python main.py weekly
python main.py monthly
python main.py note_draft
```

### 8. ダッシュボード

```powershell
streamlit run dashboard/app.py
```

### 9. Windows Task Scheduler へ登録

管理者権限の PowerShell で:

```powershell
cd C:\Users\ej464\Downloads\ai-manager
.\scheduler\setup_tasks.ps1
```

削除する場合は `.\scheduler\setup_tasks.ps1 -Remove`。

登録される内容は仕様書の実行頻度どおり:
- 朝の指示書: 毎日 06:30
- Facebook 監視: 毎日 9/12/15/19時
- Instagram 広告分析: 毎日 20時
- 週次戦略会議: 毎週金曜 19時
- 月次振り返り: 毎月末 20時
- NOTE 記事下書き: 毎週日曜 19時

---

## テスト

外部 API キー無しで実行できる DB レイヤーのスモークテスト:

```powershell
python -m pytest tests/ -v
```

---

## ディレクトリ構成

```
ai-manager/
├── config/
│   └── settings.py          # .env ローダー、全モジュール共通設定
├── src/
│   ├── db.py                 # SQLite スキーマ・CRUD（全機能共通）
│   ├── clients/
│   │   ├── claude_client.py  # Claude API (優先度判定・生成・分析)
│   │   ├── gemini_client.py  # Gemini API + Google Calendar/Gmail OAuth
│   │   ├── meta_client.py    # Meta Graph API (Facebook/Instagram)
│   │   └── slack_client.py   # Slack Webhook 出力
│   ├── morning_brief.py            # 機能1
│   ├── facebook_monitor.py         # 機能2
│   ├── instagram_ads.py            # 機能3
│   ├── weekly_monthly_analysis.py  # 機能4
│   └── note_article_generator.py   # 機能5
├── dashboard/
│   └── app.py                # Streamlit ダッシュボード
├── scheduler/
│   └── setup_tasks.ps1       # Windows Task Scheduler 登録スクリプト
├── docs/
│   ├── Phase0_AI_Manager_Implementation_Spec.md
│   └── note_drafts/          # 生成された NOTE 記事下書き (git 管理外推奨)
├── tests/
│   └── test_db.py
├── data/                      # SQLite DB (git 管理外)
├── logs/                      # 実行ログ (git 管理外)
├── main.py                   # 統合 CLI エントリーポイント
├── requirements.txt
└── .env.example
```

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `ANTHROPIC_API_KEY が未設定です` | `.env` を確認。値の前後に余計な空白/引用符が無いか確認 |
| Calendar/Gmail が毎回ブラウザ認証を求める | `config/google_token.json` が保存されているか確認。無ければ書き込み権限を確認 |
| Graph API が 403 を返す | トークンの有効期限切れ、または該当権限の審査未通過 |
| Facebook グループ部分だけ失敗する | App Review 未通過の可能性が高い。ページ部分は動作するはず |
| `main.py monthly` が期待の日に動かない | `schtasks /Query /TN AIManager_Monthly /V` で `MO LASTDAY` が登録されているか確認 |
| Streamlit が重い（Ej の懸念: OpenWebUI が重い） | ダッシュボードは読み取り専用で DB を直接見るだけなので OpenWebUI とは独立プロセス。それでも重い場合は `streamlit run --server.headless true` でバックグラウンド実行 |
