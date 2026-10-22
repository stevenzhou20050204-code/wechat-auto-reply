$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$node = Join-Path $root 'runtime\node.exe'
$entry = Join-Path $root 'app\node_modules\@wonderwhy-er\desktop-commander\dist\index.js'
$network = Join-Path $root 'dc-network.cjs'
$logRoot = Join-Path $root 'logs'
$managerLog = Join-Path $logRoot 'supervisor.log'
$statePath = Join-Path $root 'status.json'
$mutex = [Threading.Mutex]::new($false, 'Local\DesktopCommanderRemote-Acer')
$ownsMutex = $false
$restartCount = 0
$proxyStartAttemptAt = [datetime]::MinValue
. (Join-Path $root 'HealthPolicy.ps1')
function Write-ManagerLog([string]$message) {
    if ((Test-Path -LiteralPath $managerLog) -and (Get-Item -LiteralPath $managerLog).Length -gt 2MB) {
        Move-Item -LiteralPath $managerLog -Destination (Join-Path $logRoot ('supervisor-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff') + '.log'))
    }
    Add-Content -LiteralPath $managerLog -Value ((Get-Date -Format o) + ' ' + $message) -Encoding UTF8
}
function Write-State([string]$phase, [int]$remotePid = 0) {
    [pscustomobject]@{phase=$phase;supervisorPid=$PID;remotePid=$remotePid;restartCount=$restartCount;updatedAt=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}
function Check-RemoteHealth([int]$remoteProcessId) {
    $health=$null
    try { $health=Get-Content -LiteralPath (Join-Path $root 'connection-health.json') -Raw | ConvertFrom-Json } catch {}
    $decision=Get-RdcHealthDecision -Health $health -RemoteProcessId $remoteProcessId
    Write-State $decision.phase $remoteProcessId
    if ($decision.restart) {
        $owned=Get-CimInstance Win32_Process -Filter "ProcessId=$remoteProcessId" -ErrorAction SilentlyContinue
        if ($owned -and $owned.ExecutablePath -ieq $node -and $owned.CommandLine -like "*$entry*remote*") {
            Write-ManagerLog "No channel proof of life for 180 seconds and zero active calls; restarting owned remote PID $remoteProcessId. No tool replay."
            Stop-Process -Id $remoteProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}
function Remove-OwnedOrphans([int]$formerParentId) {
    Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.ParentProcessId -eq $formerParentId -and $_.ExecutablePath -ieq $node -and $_.CommandLine -like "*$entry*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
try {
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { exit 0 }
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    foreach ($required in @($node,$entry,$network,(Join-Path $root 'probe.cjs'))) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Missing dedicated runtime file: $required" }
    }
    $env:HTTP_PROXY = $null
    $env:HTTPS_PROXY = $null
    $env:ALL_PROXY = $null
    $env:NO_PROXY = 'localhost,127.0.0.1,::1'
    $env:NODE_USE_ENV_PROXY = $null
    $env:NODE_OPTIONS = '--require "' + $network.Replace('\','/') + '"'
    $env:DC_HEALTH_PATH = Join-Path $root 'connection-health.json'
    # Reuse/refresh saved login silently. A daemon must never open a browser.
    $env:DC_NONINTERACTIVE = '1'
    $env:PATH = (Split-Path -Parent $node) + ';' + $env:PATH
    Write-ManagerLog 'Supervisor started; waits for local SOCKS proxy and backend before launching.'
    $backoff = 10
    $waitingLogged = $false
    while ($true) {
        $existing = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.ExecutablePath -ieq $node -and $_.CommandLine -like "*$entry*" -and $_.CommandLine -match '\sremote(?:\s|$)' })
        if ($existing.Count -gt 0) {
            Write-ManagerLog "Existing remote instance detected, waiting for PID $($existing[0].ProcessId)."
            Write-State 'existing-instance' $existing[0].ProcessId
            $adopted = Get-Process -Id $existing[0].ProcessId -ErrorAction SilentlyContinue
            if ($adopted) {
                $null = $adopted.Handle
                while (-not $adopted.WaitForExit(15000)) {
                    Check-RemoteHealth $existing[0].ProcessId
                }
                Remove-OwnedOrphans $existing[0].ProcessId
                $restartCount++
            }
            continue
        }
        $proxyReady = $false
        $probeSocket = [Net.Sockets.TcpClient]::new()
        try { $connection = $probeSocket.ConnectAsync('127.0.0.1',21001); $proxyReady = $connection.Wait(2000) -and $probeSocket.Connected } catch {} finally { $probeSocket.Dispose() }
        if (-not $proxyReady) {
            Write-State 'waiting-for-proxy'
            # 仅唤醒独立网络守护，避免与其同时启动第二个星环实例。
            if (((Get-Date)-$proxyStartAttemptAt).TotalSeconds -ge 3600 -and -not (Test-Path -LiteralPath (Join-Path $root 'STAR-WATCH-STOP'))) {
                $proxyStartAttemptAt=Get-Date
                $networkTask=Get-ScheduledTask -TaskName 'Xinghuan Network Recovery - Acer' -ErrorAction SilentlyContinue
                if ($networkTask -and $networkTask.Settings.Enabled) {
                    Start-ScheduledTask -TaskName $networkTask.TaskName
                    Write-ManagerLog 'Requested independent Xinghuan recovery check; no duplicate proxy launch.'
                }
            }
            if (-not $waitingLogged) { Write-ManagerLog 'Waiting for Xinghuan SOCKS5 on 127.0.0.1:21001.'; $waitingLogged = $true }
            Start-Sleep -Seconds 15
            continue
        }
        $waitingLogged = $false
        Write-State 'checking-network'
        $preflight = Start-Process -FilePath $node -ArgumentList ('"' + (Join-Path $root 'probe.cjs') + '"') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'preflight.stdout.log') -RedirectStandardError (Join-Path $logRoot 'preflight.stderr.log') -PassThru
        # Pin the process handle before exit; Windows PowerShell can otherwise
        # lose ExitCode for short-lived Start-Process children.
        $null = $preflight.Handle
        if (-not $preflight.WaitForExit(70000)) {
            # Only this owned probe is stopped; a hung preflight must not stall recovery.
            Stop-Process -Id $preflight.Id -Force -ErrorAction SilentlyContinue
            Write-State 'preflight-timeout'
            Write-ManagerLog 'Backend preflight exceeded 70 seconds; retry in 30 seconds.'
            Start-Sleep -Seconds 30
            continue
        }
        $probeExitCode = $preflight.ExitCode
        if ($null -eq $probeExitCode -or $probeExitCode -ne 0) {
            Write-State 'waiting-for-network'
            Write-ManagerLog "Backend preflight failed (exit $probeExitCode); retry in 30 seconds without touching saved login."
            Start-Sleep -Seconds 30
            continue
        }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmssfff'
        $stdout = Join-Path $logRoot "$stamp.stdout.log"
        $stderr = Join-Path $logRoot "$stamp.stderr.log"
        $startedAt = Get-Date
        $remote = Start-Process -FilePath $node -ArgumentList @('--require',('"'+$network+'"'),('"' + $entry + '"'),'remote') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $null = $remote.Handle
        Write-State 'running' $remote.Id
        Write-ManagerLog "Remote started, PID $($remote.Id), output $stamp.stdout.log."
        while (-not $remote.WaitForExit(15000)) {
            Check-RemoteHealth $remote.Id
        }
        Write-ManagerLog "Remote exited, PID $($remote.Id), exit code $($remote.ExitCode)."
        # Only terminate orphaned local MCP children of our just-exited remote.
        Remove-OwnedOrphans $remote.Id
        $restartCount++
        if (((Get-Date) - $startedAt).TotalSeconds -ge 120) { $backoff = 10 }
        Write-State 'restarting'
        Start-Sleep -Seconds $backoff
        $backoff = [Math]::Min(120, $backoff * 2)
    }
} catch {
    try { Write-ManagerLog ('Supervisor error: ' + $_.Exception.Message + ' at ' + $_.ScriptStackTrace) } catch {}
    exit 1
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
