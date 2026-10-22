param([ValidateSet('Start','Stop','Status')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$taskName = 'Desktop Commander Remote - Acer'
if ($Action -eq 'Start') {
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output 'Automatic Desktop Commander service enabled and started.'
} elseif ($Action -eq 'Stop') {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
    Stop-ScheduledTask -TaskName $taskName
    $entry = Join-Path $PSScriptRoot 'app\node_modules\@wonderwhy-er\desktop-commander\dist\index.js'
    Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like "*$entry*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Output 'Automatic Desktop Commander service disabled and stopped. Saved login retained.'
} else {
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
    $state = Join-Path $PSScriptRoot 'status.json'
    if (Test-Path -LiteralPath $state) { Get-Content -LiteralPath $state }
}
