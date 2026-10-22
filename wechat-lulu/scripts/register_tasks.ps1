param([Parameter(Mandatory=$true)][string]$ProfilePath,[switch]$Plan)
$ErrorActionPreference='Stop'
$p=Get-Content -LiteralPath $ProfilePath -Raw -Encoding UTF8|ConvertFrom-Json
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$definitions=@(
 @{role='setup';schedule='logon'},
 @{role='listener';schedule='logon'},
 @{role='rdc';schedule='logon'},
 @{role='health';schedule='day-hours'},
 @{role='network';schedule='all-hours'}
)
if($Plan){$definitions|ConvertTo-Json;exit 0}
foreach($definition in $definitions){
 $role=$definition.role;$name=$p.task_names.$role
 $arguments='-X utf8 -B "'+(Join-Path $p.install_root 'service_host.py')+'" --profile "'+$ProfilePath+'" --service '+$role
 $action=New-ScheduledTaskAction -Execute $p.pythonw -Argument $arguments -WorkingDirectory $p.install_root
 $existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
 if($existing){
  if($existing.Actions.Count -ne 1 -or $existing.Actions[0].Execute -ine $p.pythonw -or $existing.Actions[0].Arguments -ne $arguments){throw ('任务身份冲突，保留原任务：'+$name)}
  continue
 }
 $triggers=@()
 if($definition.schedule -eq 'logon'){$triggers+=New-ScheduledTaskTrigger -AtLogOn -User $user}
 elseif($definition.schedule -eq 'day-hours'){foreach($h in 8..22){$triggers+=New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($h))}}
 else{foreach($h in 0..23){$triggers+=New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($h))}}
 $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
 if($definition.schedule -eq 'logon'){
  $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
 }else{
  $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
 }
 Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description ('WechatLulu managed '+$role+'; profile '+$ProfilePath) | Out-Null
}
[ordered]@{registered=$true;user=$user;runLevel='Limited';hiddenPython=$p.pythonw}|ConvertTo-Json -Compress
