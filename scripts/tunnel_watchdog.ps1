# 隧道看门狗：每60秒探测语音隧道端口，断线自动重连
while ($true) {
    $tunnels = @(
        @{Port=9881; Bat="F:\apps\gpt-sovits-work\tunnel.bat"},
        @{Port=9883; Bat="F:\apps\gpt-sovits-work\tunnel_paopao.bat"},
        @{Port=9885; Bat="F:\apps\gpt-sovits-work\tunnel_itts.bat"}
    )
    foreach ($t in $tunnels) {
        $alive = netstat -ano | Select-String ":$($t.Port)\s.*LISTENING"
        if (-not $alive) {
            $ts = Get-Date -Format "HH:mm:ss"
            Add-Content -Path "F:\apps\gpt-sovits-work\watchdog.log" -Value "[$ts] 端口 $($t.Port) 断线，重连 $($t.Bat)"
            Start-Process -FilePath $t.Bat -WindowStyle Hidden
        }
    }
    Start-Sleep -Seconds 60
}
