# RDC自检只读本机状态；远程RPC的pong由每小时Codex自检单独核对。
function Convert-RdcCheckTime($Value){
    if($Value -is [datetimeoffset]){return $Value}
    if($Value -is [datetime]){return [datetimeoffset]$Value}
    return [datetimeoffset]::Parse([string]$Value,[Globalization.CultureInfo]::InvariantCulture)
}
function Get-LuluRdcConnection {
    param($Health,$Manager,$Processes,$Task,[datetimeoffset]$Now=[datetimeoffset]::UtcNow)
    $root='C:\Users\YOUR_USER\Documents\Codex\DesktopCommanderRemote'
    $entry=Join-Path $root 'app\node_modules\@wonderwhy-er\desktop-commander\dist\index.js'
    $node=Join-Path $root 'runtime\node.exe'
    $issues=[Collections.Generic.List[string]]::new()
    $result=[ordered]@{connected=$false;healthFresh=$false;managerFresh=$false;processRunning=$false;supervisorRunning=$false;taskEnabled=$null;authenticationRequired=$false;recoveryPending=$false;channelState=$null;pongAgeMs=$null;activeCalls=$null;rpcChecked=$false;issues=@()}
    if($null -eq $Task){$issues.Add('无法读取RDC守护任务')}
    else{$result.taskEnabled=$Task.Settings.Enabled;if(-not $Task.Settings.Enabled){$issues.Add('RDC守护任务已暂停，保持人工暂停状态')}}
    if($null -eq $Health){$issues.Add('RDC连接状态文件缺失')}
    else{
        try{
            $age=($Now-(Convert-RdcCheckTime $Health.updatedAt)).TotalSeconds
            $result.healthFresh=($age -ge -5 -and $age -le 45)
            if(-not $result.healthFresh){$issues.Add('RDC连接状态已过期')}
            $owned=@($Processes | Where-Object {$_.ProcessId -eq $Health.pid -and $_.ExecutablePath -ieq $node -and $_.CommandLine -like "*$entry*" -and $_.CommandLine -match '\sremote(?:\s|$)'})
            $result.processRunning=($owned.Count -eq 1)
            if(-not $result.processRunning){$issues.Add('RDC远端进程未运行或与状态PID不匹配')}
            $result.channelState=$Health.channelState;$result.activeCalls=$Health.activeCalls
            if($result.healthFresh -and $result.processRunning){
                $result.authenticationRequired=($Health.authRecoveryVersion -eq 1 -and $Health.authenticationStatus -eq 'owner-required')
                $result.recoveryPending=($Health.authenticationLost -eq $true -or $Health.authenticationStatus -eq 'restore-unavailable') -and -not $result.authenticationRequired
                if($result.authenticationRequired){$issues.Add('RDC明确需要本人授权；不会打开登录界面')}
                elseif($result.recoveryPending){$issues.Add('RDC会话恢复暂不可用；保留凭证后台重连')}
                if($null -ne $Health.lastPongAgeMs){$result.pongAgeMs=[Math]::Max(0,[double]$Health.lastPongAgeMs)+[Math]::Max(0,$age*1000)}
                if($Health.channelState -ne 'joined' -or $null -eq $result.pongAgeMs -or $result.pongAgeMs -gt 75000){$issues.Add('RDC缺少75秒内的服务器心跳确认')}
                if($Health.shuttingDown){$issues.Add('RDC正在关闭')}
            }
        }catch{$issues.Add('RDC连接状态格式无效')}
    }
    if($null -eq $Manager){$issues.Add('RDC守护状态文件缺失')}
    else{
        try{
            $managerAge=($Now-(Convert-RdcCheckTime $Manager.updatedAt)).TotalSeconds
            $result.managerFresh=($managerAge -ge -5 -and $managerAge -le 60)
            $supervisors=@($Processes | Where-Object {$_.ProcessId -eq $Manager.supervisorPid -and $_.CommandLine -like "*$(Join-Path $root 'Supervise.ps1')*"})
            $result.supervisorRunning=($supervisors.Count -eq 1)
            if(-not $result.managerFresh -or -not $result.supervisorRunning){$issues.Add('RDC守护未运行或状态已过期')}
            if($Manager.phase -in @('waiting-for-network','preflight-timeout','waiting-for-proxy')){$result.recoveryPending=$true;$issues.Add('RDC等待网络或代理恢复；不是凭证撤销')}
            if($result.healthFresh -and $result.processRunning -and $Manager.remotePid -ne $Health.pid){$issues.Add('RDC守护记录的远端PID不匹配')}
        }catch{$issues.Add('RDC守护状态格式无效')}
    }
    $result.connected=($issues.Count -eq 0)
    $result.issues=@($issues)
    return [pscustomobject]$result
}
