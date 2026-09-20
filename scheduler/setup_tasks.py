"""
AI Manager - Windows Task Scheduler 本番運用セットアップ (Python版)

Ej が用意したセットアップスクリプトを、実際の main.py CLI 仕様と
schtasks.exe の実際の構文に合わせて修正したもの。

修正点（元スクリプトとの差分）:
  1. main.py は `--morning-brief` のようなフラグではなく
     `morning_brief` のような位置引数（サブコマンド）を取る仕様のため、
     引数をサブコマンド形式に変更。
  2. システム標準の `python` には anthropic 等の依存パッケージが
     入っていないため、venv (.venv\\Scripts\\python.exe) を明示指定。
  3. /TR に "cd X && python ..." を直接書くと、タスクスケジューラは
     これをシェル経由で実行しないため正しく動作しない。
     venv の python.exe をフルパスで直接呼ぶ形にし、cd を不要にした
     （main.py 側は自分のファイル位置から絶対パスを解決するので
     作業ディレクトリに依存しない）。
  4. MONTHLY トリガーは `/MO LASTDAY /M *` が正しい構文
     （`/L LASTDAY` は無効なオプションで、実機で確認済み）。
"""
import os
import subprocess
from pathlib import Path

PROJECT_PATH = Path(r"C:\AI-Manager")
PYTHON_EXE = str(PROJECT_PATH / ".venv" / "Scripts" / "python.exe")
MAIN_PY = str(PROJECT_PATH / "main.py")

# 元スクリプトの --xxx フラグ → main.py の実際のサブコマンド名への対応表
tasks = [
    {
        "name": "AI-Manager-MorningBrief",
        "description": "毎朝6:30 - 朝の指示書生成",
        "command": "morning_brief",
        "sc_args": ["/SC", "DAILY"],
        "time": "06:30",
    },
    {
        "name": "AI-Manager-InstagramAds",
        "description": "毎日20:00 - Instagram広告分析",
        "command": "instagram_ads",
        "sc_args": ["/SC", "DAILY"],
        "time": "20:00",
    },
    {
        "name": "AI-Manager-WeeklyAnalysis",
        "description": "毎週金曜18:00 - 週次分析",
        "command": "weekly",
        "sc_args": ["/SC", "WEEKLY", "/D", "FRI"],
        "time": "18:00",
    },
    {
        "name": "AI-Manager-MonthlyAnalysis",
        "description": "毎月末19:00 - 月次分析",
        "command": "monthly",
        "sc_args": ["/SC", "MONTHLY", "/MO", "LASTDAY", "/M", "*"],
        "time": "19:00",
    },
    {
        "name": "AI-Manager-NoteDraft",
        "description": "毎月末23:00 - NOTE記事生成",
        "command": "note_draft",
        "sc_args": ["/SC", "MONTHLY", "/MO", "LASTDAY", "/M", "*"],
        "time": "23:00",
    },
]

# Facebook監視は「毎日固定4時刻」ではなく HOURLY /MO 6（6時間ごと）方式で
# 別途追加登録（Ej の指定どおり）。/ST は初回起動時刻の基準。
FACEBOOK_MONITOR_TASK = {
    "name": "AI-Manager-FacebookMonitor",
    "description": "6時間ごと - Facebook グループ/ページ監視",
    "command": "facebook_monitor",
    "sc_args": ["/SC", "HOURLY", "/MO", "6"],
    "time": "00:00",
}

print("=" * 60)
print("Phase 0 AI Manager - 本番運用セットアップ")
print("=" * 60)

ALL_TASKS = tasks + [FACEBOOK_MONITOR_TASK]

print("\n[1/3] 既存タスク削除...")
for task in ALL_TASKS:
    cmd = ["schtasks", "/delete", "/tn", task["name"], "/f"]
    subprocess.run(cmd, capture_output=True, text=True, encoding="cp932", errors="replace")
    print(f"  削除試行: {task['name']}")

print("\n[2/3] 新規タスク登録...")
for task in ALL_TASKS:
    tr_value = f'"{PYTHON_EXE}" "{MAIN_PY}" {task["command"]}'
    cmd = (
        ["schtasks", "/create", "/tn", task["name"], "/tr", tr_value]
        + task["sc_args"]
        + ["/st", task["time"], "/f"]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp932", errors="replace")
    if result.returncode == 0:
        print(f"  OK: {task['name']} ({task['description']})")
    else:
        print(f"  NG: {task['name']}: {result.stderr.strip() or result.stdout.strip()}")

print("\n[3/3] テスト実行 (morning_brief を1回だけ手動起動)...")
try:
    result = subprocess.run(
        [PYTHON_EXE, MAIN_PY, "morning_brief"],
        cwd=str(PROJECT_PATH),
        capture_output=True,
        text=True,
        encoding="cp932",
        errors="replace",
        timeout=60,
    )
    if result.returncode == 0:
        print("  OK: テスト成功")
    else:
        print(f"  NG: テスト失敗 (exit={result.returncode})")
        print("  " + (result.stderr.strip() or result.stdout.strip())[:500])
except Exception as e:
    print(f"  NG: テスト実行中に例外: {e}")

print("\n" + "=" * 60)
print("Phase 0 本番運用セットアップ 完了")
print("=" * 60)

print("\n登録済みタスク:")
# schtasks /query の /tn はワイルドカード非対応なので、タスクごとに個別照会する
for task in ALL_TASKS:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", task["name"], "/fo", "LIST", "/v"],
        capture_output=True,
        text=True,
        encoding="cp932",
        errors="replace",
    )
    if result.returncode == 0:
        # 主要な行だけ抜粋。schtasks の出力ラベルは Windows のロケールに依存する
        # （日本語版では「タスク名:」「次回の実行時刻:」「状態:」等）ため両対応。
        wanted_prefixes = (
            "TaskName:", "Next Run Time:", "Status:",
            "タスク名:", "次回の実行時刻:", "状態:",
        )
        for line in result.stdout.splitlines():
            if line.strip().startswith(wanted_prefixes):
                print(f"  {line.strip()}")
    else:
        print(f"  照会失敗: {task['name']}: {result.stderr.strip()}")
