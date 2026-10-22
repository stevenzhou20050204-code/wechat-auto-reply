param([ValidateSet('Start','Stop','Status')][string]$Action='Status')
$ErrorActionPreference='Stop'
$task='WeChat Auto Reply - Acer'
$project='D:\wechatauto-replica'
$state=Join-Path $project '.wechat_codex'
$stop=Join-Path $state 'STOP'
if($Action -eq 'Start') {
    if(Test-Path -LiteralPath $stop){Move-Item -LiteralPath $stop -Destination ($stop+'.'+(Get-Date -Format 'yyyyMMdd-HHmmssfff')+'.previous')}
    Enable-ScheduledTask -TaskName $task | Out-Null
    Start-ScheduledTask -TaskName $task
    $config=Get-Content -LiteralPath (Join-Path $project 'wechat_autoreply.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($config.version -eq 2 -and $config.scope -eq 'all_private_contacts') {
        Write-Output '鹭鹭：全部真实联系人私聊的后台持续监听已启动。'
    }else{
        $contacts=$config.contacts.psobject.Properties.Name
        Write-Output (($contacts -join '、')+'：后台持续监听已启动。')
    }
}elseif($Action -eq 'Stop') {
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    (Get-Date -Format o) | Set-Content -LiteralPath $stop -Encoding UTF8
    $snapshot=@(Get-CimInstance Win32_Process)
    $owned=@($snapshot | Where-Object {$_.Name -eq 'python.exe' -and $_.CommandLine -like "*$(Join-Path $project 'run_two_contacts.py')*"})
    $ownedIds=[Collections.Generic.HashSet[uint32]]::new()
    foreach($process in $owned){[void]$ownedIds.Add([uint32]$process.ProcessId)}
    do {
        $added=$false
        foreach($candidate in $snapshot) {
            if($candidate.Name -in 'python.exe','codex.exe' -and $ownedIds.Contains([uint32]$candidate.ParentProcessId)){
                if($ownedIds.Add([uint32]$candidate.ProcessId)){$added=$true}
            }
        }
    }while($added)
    Disable-ScheduledTask -TaskName $task | Out-Null
    Stop-ScheduledTask -TaskName $task
    foreach($ownedId in $ownedIds){Stop-Process -Id $ownedId -Force -ErrorAction SilentlyContinue}
    $statusPath=Join-Path $state 'listener-status.json'
    if(Test-Path -LiteralPath $statusPath){
        $status=Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $status.pid=$null;$status.mode='stopped';$status.modelStarted=$false;$status.pending=0;$status.active=0
        $status.updatedAt=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000
        [IO.File]::WriteAllText($statusPath,($status | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    }
    Write-Output '后台监听已停止；停止开关和发送去重记录已保留。'
}else{
    Get-ScheduledTask -TaskName $task | Select-Object TaskName,State
    if(Test-Path -LiteralPath (Join-Path $state 'listener-status.json')){Get-Content -LiteralPath (Join-Path $state 'listener-status.json')}
}
