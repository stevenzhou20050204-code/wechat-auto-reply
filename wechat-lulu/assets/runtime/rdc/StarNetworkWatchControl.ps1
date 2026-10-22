param([ValidateSet('Start','Stop','Status')][string]$Action='Status')
$ErrorActionPreference='Stop'
$taskName='Xinghuan Network Recovery - Acer'
$stop=Join-Path $PSScriptRoot 'STAR-WATCH-STOP'
if($Action -eq 'Stop'){
    [IO.File]::WriteAllText($stop,(Get-Date -Format o),[Text.UTF8Encoding]::new($false))
    Disable-ScheduledTask -TaskName $taskName | Out-Null
    Write-Output '网络自愈已停止；不会重启或点击星环。'
}elseif($Action -eq 'Start'){
    if(Test-Path -LiteralPath $stop){Move-Item -LiteralPath $stop -Destination ($stop+'.'+(Get-Date -Format 'yyyyMMdd-HHmmssfff')+'.previous')}
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output '网络自愈已启用；正常连接时不操作星环。'
}else{
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
    $health=Join-Path $PSScriptRoot 'star-network-health.json'
    if(Test-Path -LiteralPath $health){Get-Content -LiteralPath $health -Raw}
}
