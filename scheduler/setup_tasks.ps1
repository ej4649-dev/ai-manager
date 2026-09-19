<#
.SYNOPSIS
    AI Manager の各機能を Windows Task Scheduler に登録する。

.DESCRIPTION
    仕様書の実行頻度に沿って以下のタスクを作成/更新する:
      - AIManager_MorningBrief   : 毎日 06:30 (設定は .env の MORNING_BRIEF_TIME を参照して手動調整可)
      - AIManager_FacebookMonitor: 毎日 09:00 / 12:00 / 15:00 / 19:00 (4トリガー)
      - AIManager_InstagramAds   : 毎日 20:00
      - AIManager_Weekly         : 毎週金曜 19:00
      - AIManager_Monthly        : 毎月末 20:00
      - AIManager_NoteDraft      : 毎週日曜 19:00

.USAGE
    管理者権限の PowerShell で実行:
        cd C:\Users\ej464\Downloads\ai-manager
        .\scheduler\setup_tasks.ps1

    削除する場合:
        .\scheduler\setup_tasks.ps1 -Remove
#>

param(
    [switch]$Remove,
    [string]$PythonPath = "",
    [string]$MorningBriefTime = "06:30"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path

if (-not $PythonPath) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "python が PATH に見つかりません。-PythonPath で明示指定してください。" }
    $PythonPath = $cmd.Source
}

function Remove-TaskIfExists([string]$Name) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "削除: $Name"
    }
}

function Register-AIManagerTask {
    param(
        [string]$Name,
        [string]$Command,
        [Parameter(Mandatory=$false)][object[]]$Triggers,
        [string]$Description
    )
    Remove-TaskIfExists $Name
    $action = New-ScheduledTaskAction -Execute $PythonPath `
        -Argument "main.py $Command" `
        -WorkingDirectory $ProjectRoot
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers `
        -Settings $settings -Description $Description | Out-Null
    Write-Host "登録: $Name"
}

if ($Remove) {
    foreach ($n in @(
        "AIManager_MorningBrief", "AIManager_FacebookMonitor", "AIManager_InstagramAds",
        "AIManager_Weekly", "AIManager_Monthly", "AIManager_NoteDraft"
    )) { Remove-TaskIfExists $n }
    Write-Host "全タスクを削除しました。"
    return
}

# 機能1: 毎朝タスク指示
Register-AIManagerTask -Name "AIManager_MorningBrief" -Command "morning_brief" `
    -Triggers (New-ScheduledTaskTrigger -Daily -At $MorningBriefTime) `
    -Description "AI Manager: 毎朝の指示書生成 (機能1)"

# 機能2: Facebook 監視 (1日4回)
$fbTriggers = @(
    New-ScheduledTaskTrigger -Daily -At "09:00"
    New-ScheduledTaskTrigger -Daily -At "12:00"
    New-ScheduledTaskTrigger -Daily -At "15:00"
    New-ScheduledTaskTrigger -Daily -At "19:00"
)
Register-AIManagerTask -Name "AIManager_FacebookMonitor" -Command "facebook_monitor" `
    -Triggers $fbTriggers -Description "AI Manager: Facebook グループ/ページ監視 (機能2)"

# 機能3: Instagram 広告分析 (夜20時)
Register-AIManagerTask -Name "AIManager_InstagramAds" -Command "instagram_ads" `
    -Triggers (New-ScheduledTaskTrigger -Daily -At "20:00") `
    -Description "AI Manager: Instagram 広告日次分析 (機能3)"

# 機能4: 週次 (金曜19時) / 月次 (月末20時)
Register-AIManagerTask -Name "AIManager_Weekly" -Command "weekly" `
    -Triggers (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "19:00") `
    -Description "AI Manager: 週次戦略会議レポート (機能4)"

# 月末トリガーは Register-ScheduledTask に直接の「LastDay」指定が無いため schtasks.exe を使う
schtasks /Create /TN "AIManager_Monthly" /TR "`"$PythonPath`" `"$ProjectRoot\main.py`" monthly" `
    /SC MONTHLY /MO LASTDAY /M * /ST 20:00 /F | Out-Null
Write-Host "登録: AIManager_Monthly (schtasks /MO LASTDAY)"

# 機能5: NOTE 記事下書き (毎週日曜19時)
Register-AIManagerTask -Name "AIManager_NoteDraft" -Command "note_draft" `
    -Triggers (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "19:00") `
    -Description "AI Manager: NOTE 記事下書き自動生成 (機能5)"

Write-Host "`n全タスクの登録が完了しました。'タスク スケジューラ' アプリで確認できます。"
Write-Host "動作確認には各タスクを右クリック→「実行」、または直接 python main.py <command> を実行してください。"
