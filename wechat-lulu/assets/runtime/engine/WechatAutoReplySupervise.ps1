$ErrorActionPreference='Stop'
$project='D:\wechatauto-replica'
$state=Join-Path $project '.wechat_codex'
$python=Join-Path $project '.venv\Scripts\python.exe'
$entry=Join-Path $project 'run_two_contacts.py'
$mutex=[Threading.Mutex]::new($false,'Local\WechatTwoContactAutoReply-Acer')
$owns=$false
try {
    try{$owns=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owns=$true}
    if(-not $owns){exit 0}
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $env:PYTHONUTF8='1'
    while(-not (Test-Path -LiteralPath (Join-Path $state 'STOP'))) {
        $existing=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'python.exe' -and $_.CommandLine -like "*$entry*"})
        if($existing.Count -gt 0){Start-Sleep -Seconds 10;continue}
        $stamp=Get-Date -Format 'yyyyMMdd-HHmmssfff'
        $arguments=@('-u',('"'+$entry+'"'),'--engine-root',('"'+$PSScriptRoot+'"'))
        $process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state "listener-$stamp.log") -RedirectStandardError (Join-Path $state "listener-$stamp.err") -PassThru
        $null=$process.Handle
        $process.Id | Set-Content -LiteralPath (Join-Path $state 'running-launcher.pid') -Encoding ASCII
        Add-Content -LiteralPath (Join-Path $state 'supervisor.log') -Value ((Get-Date -Format o)+" listener started PID $($process.Id)") -Encoding UTF8
        while(-not $process.WaitForExit(10000)) {
            if(Test-Path -LiteralPath (Join-Path $state 'STOP')){break}
        }
        if(Test-Path -LiteralPath (Join-Path $state 'STOP')){break}
        Add-Content -LiteralPath (Join-Path $state 'supervisor.log') -Value ((Get-Date -Format o)+" listener exited code $($process.ExitCode); retry in 20 seconds") -Encoding UTF8
        Start-Sleep -Seconds 20
    }
}finally{
    if($owns){$mutex.ReleaseMutex()}
    $mutex.Dispose()
}
