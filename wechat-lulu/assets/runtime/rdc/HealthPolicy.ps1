function Convert-RdcTimestamp($Value) {
    # PowerShell 7 may turn ISO JSON strings into DateTime automatically;
    # PowerShell 5 leaves them as strings. Preserve UTC in either case.
    if ($Value -is [datetimeoffset]) { return $Value }
    if ($Value -is [datetime]) { return [datetimeoffset]$Value }
    return [datetimeoffset]::Parse([string]$Value,[Globalization.CultureInfo]::InvariantCulture)
}
function Get-RdcHealthDecision {
    param($Health,[int]$RemoteProcessId,[datetimeoffset]$Now=[datetimeoffset]::Now)
    $decision=[pscustomobject]@{phase='health-unavailable';restart=$false}
    if ($null -eq $Health -or $Health.pid -ne $RemoteProcessId) { return $decision }
    try { $age=($Now-(Convert-RdcTimestamp $Health.updatedAt)).TotalSeconds } catch { return $decision }
    if ($age -lt -5 -or $age -gt 45) { $decision.phase='health-report-stale'; return $decision }
    if ($Health.authenticationStatus -eq 'owner-required') { $decision.phase='authentication-required'; return $decision }
    if ($Health.authenticationStatus -eq 'restore-unavailable' -or $Health.authenticationLost) {
        $decision.phase='session-recovery-pending'
        # 临时网络故障耗尽通道恢复后，以已保存的登录重新启动；不进入设备认证。
        if ($Health.notJoinedForMs -ge 180000 -and $null -ne $Health.activeCalls -and $Health.activeCalls -eq 0 -and -not $Health.reconnecting) { $decision.restart=$true }
        return $decision
    }
    if ($Health.shuttingDown) { $decision.phase='shutting-down'; return $decision }
    $pongStale=($null -ne $Health.lastPongAgeMs -and $Health.lastPongAgeMs -ge 180000)
    $notJoined=($Health.channelState -ne 'joined' -and $Health.notJoinedForMs -ge 180000)
    if ($pongStale -or $notJoined) {
        $decision.phase='connection-stalled'
        # Never interrupt a dispatched tool, restart on unknown activity, or
        # restart while a channel rebuild is still within its bounded deadline.
        if ($null -ne $Health.activeCalls -and $Health.activeCalls -eq 0 -and -not $Health.reconnecting) { $decision.restart=$true }
    } elseif ($Health.channelState -eq 'joined' -and $null -ne $Health.lastPongAgeMs -and $Health.lastPongAgeMs -lt 75000) {
        $decision.phase='connected'
    } elseif ($Health.startupStage -and $Health.startupStage -ne 'ready') {
        $decision.phase='starting-'+$Health.startupStage
    } else { $decision.phase='reconnecting' }
    return $decision
}
