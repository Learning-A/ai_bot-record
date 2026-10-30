# astrbot_watchdog.ps1 — AstrBot 崩溃自愈看门狗
# 职责：每30s检查 OneBot WS 端口(6199)；进程没了→按标准姿势(ffmpeg PATH前缀+递增日志)拉起；
#       进程在但端口5分钟没监听→判卡死，杀树重启；存在 WATCHDOG_STOP.flag→静默(用户主动停机)。
# 约定：看门狗是唯一拉起者——手动重启时只需杀进程，看门狗150s内自动接手。

$exe   = "C:\Users\<你的Windows用户名>\.local\bin\astrbot.exe"
$work  = "F:\sessions\26-10-28直播姬\qq-ai-bot\astrbot"
$ffbin = "C:\Users\<你的Windows用户名>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"
$flag  = Join-Path $work "WATCHDOG_STOP.flag"
$wlog  = Join-Path $work "astrbot_watchdog.log"

function Log($m) {
    try { Add-Content -Path $wlog -Value ("[{0}] {1}" -f (Get-Date -Format "MM-dd HH:mm:ss"), $m) } catch {}
}
function PortUp {
    [bool](Get-NetTCPConnection -LocalPort 6199 -State Listen -ErrorAction SilentlyContinue)
}
function Procs {
    $a = @(Get-CimInstance Win32_Process -Filter "Name='astrbot.exe'" -ErrorAction SilentlyContinue)
    $p = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -like '*astrbot*' })
    $a + $p
}
function NextRun {
    $max = 0
    Get-ChildItem (Join-Path $work "astrbot_run*.log") -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -match 'astrbot_run(\d+)\.log$') {
            $n = [int]$Matches[1]; if ($n -gt $max) { $max = $n }
        }
    }
    $max + 1
}
function Launch {
    $n = NextRun
    $env:Path = "$ffbin;" + $env:Path
    Start-Process -FilePath $exe -ArgumentList "run" -WorkingDirectory $work -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $work ("astrbot_run{0}.log" -f $n)) `
        -RedirectStandardError  (Join-Path $work ("astrbot_run{0}.err.log" -f $n))
    Log "AstrBot 不在运行 → 已拉起 run$n"
}

Log "看门狗启动 (pid=$PID)"
Set-Content -Path (Join-Path $work "astrbot_watchdog.pid") -Value $PID
$lastLaunch = Get-Date
$downSince  = $null

while ($true) {
    try {
        if (Test-Path $flag) { Start-Sleep 30; continue }
        if (-not (PortUp)) {
            $ps = Procs
            if ($ps.Count -eq 0) {
                if (((Get-Date) - $lastLaunch).TotalSeconds -gt 150) {
                    Launch; $lastLaunch = Get-Date; $downSince = $null
                    Start-Sleep 90; continue
                }
            } else {
                if (-not $downSince) { $downSince = Get-Date }
                elseif (((Get-Date) - $downSince).TotalSeconds -gt 300) {
                    Log "端口6199持续5分钟未监听且进程存在 → 判定卡死，杀树重启"
                    $ps | ForEach-Object { cmd /c "taskkill /PID $($_.ProcessId) /T /F" 2>$null | Out-Null }
                    Start-Sleep 3
                    if (((Get-Date) - $lastLaunch).TotalSeconds -gt 150) {
                        Launch; $lastLaunch = Get-Date
                    }
                    $downSince = $null; Start-Sleep 90; continue
                }
            }
        } else { $downSince = $null }
    } catch { Log "异常: $_" }
    Start-Sleep 30
}
