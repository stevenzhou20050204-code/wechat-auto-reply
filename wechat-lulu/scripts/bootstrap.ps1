param([string]$InstallRoot='', [switch]$Plan, [switch]$PrepareOnly)
$ErrorActionPreference='Stop'
$skill=Split-Path -Parent $PSScriptRoot
if(-not $InstallRoot){$InstallRoot=Join-Path $env:LOCALAPPDATA 'WechatLulu'}
$InstallRoot=[IO.Path]::GetFullPath($InstallRoot)
if([Environment]::Is64BitOperatingSystem -ne $true){throw '只支持64位Windows'}
$protected=@([IO.Path]::GetFullPath($env:USERPROFILE),[IO.Path]::GetPathRoot($InstallRoot),[IO.Path]::GetFullPath($skill))
foreach($path in $protected){
 if($InstallRoot -ieq $path -or $path.StartsWith($InstallRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or ($path -ieq $skill -and $InstallRoot.StartsWith($path.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase))){throw '安装目标不能覆盖用户目录、磁盘根目录或skill'}
}
$cursor=$InstallRoot
while($cursor){
 if(Test-Path -LiteralPath $cursor){$item=Get-Item -LiteralPath $cursor;if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw '安装路径不能包含目录联接'}}
 $parent=Split-Path -Parent $cursor;if($parent -eq $cursor){break};$cursor=$parent
}
$marker=Join-Path $InstallRoot '.lulu-bootstrap-owner.json'
if((Test-Path -LiteralPath $InstallRoot) -and @(Get-ChildItem -LiteralPath $InstallRoot -Force).Count -gt 0 -and -not (Test-Path -LiteralPath $marker)){throw '安装目录已有其他文件，拒绝覆盖'}
if(-not $Plan){
 $pauses=@((Join-Path $InstallRoot 'project/.wechat_codex/STOP'),(Join-Path $InstallRoot 'project/.wechat_codex/UI-PAUSE'),(Join-Path $InstallRoot 'rdc/STAR-WATCH-STOP'))
 if(@($pauses | Where-Object {Test-Path -LiteralPath $_}).Count){[pscustomobject]@{status='paused';changed=$false}|ConvertTo-Json;exit 0}
 $other=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in 'python.exe','pythonw.exe' -and $_.CommandLine -like '*run_two_contacts.py*' -and $_.CommandLine -notlike ('*'+$InstallRoot+'*')})
 if($other.Count){[pscustomobject]@{status='existing_deployment';changed=$false;reason='保留另一目录正在运行的监听'}|ConvertTo-Json;exit 0}
 if(-not (Test-Path -LiteralPath $InstallRoot)){New-Item -ItemType Directory -Path $InstallRoot -Force|Out-Null}
 if(-not (Test-Path -LiteralPath $marker)){[ordered]@{owner='wechat-lulu-bootstrap-v2';root=$InstallRoot}|ConvertTo-Json|Set-Content -LiteralPath $marker -Encoding UTF8}
}
$python=$null
$candidates=@()
if($env:LOCALAPPDATA){$candidates+=Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'}
$candidates+=Join-Path $InstallRoot 'tools/python/python.exe'
$candidates+=@(Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
foreach($p in $candidates){
    if(Test-Path -LiteralPath $p){
        $version=& $p -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null
        if($LASTEXITCODE -eq 0 -and $version -eq '3.12'){$python=$p;break}
    }
}
if(-not $python -and $Plan){
    [ordered]@{mode='plan';installRoot=$InstallRoot;python='Python 3.12 per-user';next='automatically run scripts/bootstrap.py install';modelInvoked=$false;messagesSent=0}|ConvertTo-Json
    exit 0
}
if(-not $python){
    $tools=Join-Path $InstallRoot 'tools'
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    $installer=Join-Path $tools 'python-3.12.10-amd64.exe'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer
    $signature=Get-AuthenticodeSignature -LiteralPath $installer
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation'){throw 'Python安装程序签名核验失败'}
    $target=Join-Path $tools 'python'
    $args=@('/quiet','InstallAllUsers=0',('TargetDir="'+$target+'"'),'Include_pip=1','Include_launcher=0','PrependPath=0','Include_test=0','AssociateFiles=0','Shortcuts=0')
    $p=Start-Process -FilePath $installer -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
    if($p.ExitCode -notin 0,3010){throw "Python安装未完成，退出码 $($p.ExitCode)"}
    $python=Join-Path $target 'python.exe'
}
$args=@('-X','utf8','-B',(Join-Path $PSScriptRoot 'bootstrap.py'),'install','--root',$InstallRoot)
if($Plan){$args+='--plan'}
if($PrepareOnly){$args+='--prepare-only'}
& $python @args
exit $LASTEXITCODE
