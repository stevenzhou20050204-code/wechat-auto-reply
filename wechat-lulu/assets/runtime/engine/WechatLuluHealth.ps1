param([switch]$NoRepair)
# 本地检查不调用模型、不读取聊天正文、不进行微信输入。
# 夜间不进行自检，延迟触发也必须遵守上海时间窗口。
$chinaNow=[DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8))
if($chinaNow.Hour -lt 8 -or $chinaNow.Hour -gt 22 -or ($chinaNow.Hour -eq 22 -and $chinaNow.Minute -gt 0)){exit 0}
$ErrorActionPreference='Stop'
function Convert-LuluTimestamp($value){
    if($value -is [datetimeoffset]){return $value}
    if($value -is [datetime]){return [datetimeoffset]$value}
    return [datetimeoffset]::Parse([string]$value,[Globalization.CultureInfo]::InvariantCulture)
}
. (Join-Path $PSScriptRoot 'RdcConnectionPolicy.ps1')
$project='D:\wechatauto-replica'
$state=Join-Path $project '.wechat_codex'
$taskName='WeChat Auto Reply - Acer'
$healthPath=Join-Path $state 'lulu-health.json'
$issues=[Collections.Generic.List[string]]::new()
$repairs=[Collections.Generic.List[string]]::new()
$now=[DateTimeOffset]::UtcNow
$result=[ordered]@{checkedAt=$now.ToString('o');mode='unknown';issues=@();repairs=@();modelInvoked=$false;testMessagesSent=0}
try {
    $stopExists=Test-Path -LiteralPath (Join-Path $state 'STOP')
    $task=Get-ScheduledTask -TaskName $taskName
    $config=Get-Content -LiteralPath (Join-Path $project 'wechat_autoreply.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $processes=@(Get-CimInstance Win32_Process)
    $entry=Join-Path $project 'run_two_contacts.py'
    $launchers=@($processes | Where-Object {$_.Name -eq 'python.exe' -and $_.CommandLine -like "*$entry*"})
    # Windows虚拟环境会用一个python启动另一个python；按叶进程计数。
    $parentIds=[Collections.Generic.HashSet[uint32]]::new()
    foreach($launcher in $launchers){[void]$parentIds.Add([uint32]$launcher.ParentProcessId)}
    $workers=@($launchers | Where-Object {-not $parentIds.Contains([uint32]$_.ProcessId)})
    $weixin=@($processes | Where-Object {$_.Name -eq 'Weixin.exe'})
    $status=$null
    $statusPath=Join-Path $state 'listener-status.json'
    if(Test-Path -LiteralPath $statusPath){$status=Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json}
    $result.stopExists=$stopExists;$result.listenerCount=$workers.Count;$result.wechatRunning=($weixin.Count -gt 0);$result.taskEnabled=$task.Settings.Enabled
    $result.configVersion=$config.version;$result.listenerMode=$status.mode
    $result.replyModel=$status.model;$result.reasoningEffort=$status.reasoningEffort
    $result.contactCount=$status.contactCount;$result.contactExclusions=$status.contactExclusions
    $result.contentPolicy=$status.contentPolicy
    $result.pendingReplyPolicy=$status.pendingReplyPolicy
    $result.interaction=$status.interaction
    $result.uiPauseExists=Test-Path -LiteralPath (Join-Path $state 'UI-PAUSE')
    $rdcHealthPath='C:/Users/YOUR_USER/Documents/Codex/DesktopCommanderRemote/connection-health.json'
    $result.rdcAuthenticationRequired=$false
    $result.rdcRecoveryPending=$false
    if(Test-Path -LiteralPath $rdcHealthPath){
        $rdcHealth=Get-Content -LiteralPath $rdcHealthPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $rdcAge=($now-(Convert-LuluTimestamp $rdcHealth.updatedAt)).TotalSeconds
        $rdcLive=@($processes | Where-Object {$_.ProcessId -eq $rdcHealth.pid -and $_.Name -eq 'node.exe'})
        $result.rdcHealthFresh=($rdcAge -ge -5 -and $rdcAge -le 45 -and $rdcLive.Count -eq 1)
        if($result.rdcHealthFresh){
            # A lost session or old startup marker is not proof of revocation.
            $result.rdcAuthenticationRequired=($rdcHealth.authRecoveryVersion -eq 1 -and $rdcHealth.authenticationStatus -eq 'owner-required')
            $result.rdcRecoveryPending=($rdcHealth.authenticationLost -eq $true -and -not $result.rdcAuthenticationRequired) -or $rdcHealth.authenticationStatus -eq 'restore-unavailable'
            $result.rdcChannelState=$rdcHealth.channelState
        }
    }
    $rdcManagerPath='C:/Users/YOUR_USER/Documents/Codex/DesktopCommanderRemote/status.json'
    if(Test-Path -LiteralPath $rdcManagerPath){
        $rdcManager=Get-Content -LiteralPath $rdcManagerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $result.rdcManagerPhase=$rdcManager.phase
        if($rdcManager.phase -in @('waiting-for-network','preflight-timeout','waiting-for-proxy')){$result.rdcRecoveryPending=$true}
    }
    $rdcTask=Get-ScheduledTask -TaskName 'Desktop Commander Remote - Acer' -ErrorAction SilentlyContinue
    $result.rdc=Get-LuluRdcConnection -Health $rdcHealth -Manager $rdcManager -Processes $processes -Task $rdcTask -Now $now
    $result.rdcConnected=$result.rdc.connected
    $result.rdcAuthenticationRequired=$result.rdc.authenticationRequired
    $result.rdcRecoveryPending=$result.rdc.recoveryPending
    $result.rdcHealthFresh=$result.rdc.healthFresh
    $result.rdcChannelState=$result.rdc.channelState
    if($stopExists -or -not $task.Settings.Enabled){
        $result.mode='paused'
    }else{
        if($config.version -ne 2 -or $config.scope -ne 'all_private_contacts' -or $config.assistant_name -ne '鹭鹭'){$issues.Add('鹭鹭活动配置不完整')}
        if($status -and ($status.model -ne 'gpt-6.1-sol' -or $status.reasoningEffort -ne 'medium')){$issues.Add('监听没有应用指定的6.1 sol中等推理设置')}
        if($config.contact_exclusions.labels -notcontains '排除标签示例' -or $config.contact_exclusions.nicknames -notcontains '排除昵称示例' -or $config.contact_exclusions.contact_ids -notcontains 'wxid_CONFIGURE_CONTACT_3'){$issues.Add('活动配置缺少排除标签示例或排除昵称示例排除规则')}
        if($status.contactExclusions.version -ne 1 -or $status.contactExclusions.labels -notcontains '排除标签示例' -or $status.contactExclusions.nicknames -notcontains '排除昵称示例' -or $status.contactExclusions.boundContactCount -lt 1){$issues.Add('监听尚未应用新的联系人排除规则')}
        if($config.content_policy.sensitive_politics -ne 'silent' -or $status.contentPolicy.sensitivePolitics -ne 'silent'){$issues.Add('政治敏感话题静默规则未生效')}
        $result.replyQueue=$status.replyQueue
        # 主人已重新启用联系人甲；这里只核验，不据旧规则恢复排除。
        if($config.contact_exclusions.contact_ids -contains 'wxid_CONFIGURE_CONTACT_4'){$issues.Add('活动配置仍排除已恢复的联系人甲固定账号')}
        if($status.mode -eq 'lulu-assistant' -and $status.replyQueue.persisted -ne $true){$issues.Add('监听尚未启用持久回复队列')}
        if($config.pending_reply_policy -ne 'latest_new_message' -or $status.pendingReplyPolicy -ne 'latest_new_message'){$issues.Add('新来信取代失败旧回复规则未生效')}
        # 仅查生图路由、工具开关、文件权限及原生功能元数据；不创建模型轮次或图片。
        $imageChecker=Join-Path $project 'lulu_image_health.py'
        if($status.imageGenerationRoutingVersion -ne 3){$issues.Add('监听尚未加载连续对话生图工具修复')}
        $imageHealthPath=Join-Path $state 'image-generation-health.json'
        if(Test-Path -LiteralPath $imageChecker){
            $imagePython=Join-Path $project '.venv/Scripts/python.exe'
            $imageProbeOutput=& $imagePython -u $imageChecker --engine-root $PSScriptRoot 2>&1
            if(Test-Path -LiteralPath $imageHealthPath){
                $imageHealth=Get-Content -LiteralPath $imageHealthPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $result.imageGeneration=$imageHealth
                if(-not $imageHealth.ok){$issues.Add('生图链路就绪检查未通过：'+($imageHealth.issues -join '；'))}
                if($imageHealth.modelInvoked -or $imageHealth.generatedImageCount -ne 0){$issues.Add('生图自检违反零生成约束')}
                $imageAge=($now-(Convert-LuluTimestamp $imageHealth.checkedAt)).TotalSeconds
                if($imageAge -gt 120){$issues.Add('生图自检结果未更新')}
            }else{$issues.Add('生图自检未生成状态文件')}
        }else{$issues.Add('缺少生图链路就绪检查程序')}
        if($status.replyQueue.quotaPaused -eq $true){$issues.Add('模型额度冷却中，待回复消息已持久保存，将在quotaResumeAt后自动接续')}

        if($config.reply_order -eq 'global_fifo' -and ($status.replyQueue.mode -ne 'global_fifo' -or $status.replyQueue.maxConcurrentReplies -ne 1)){$issues.Add('全局顺序回复队列没有生效')}
        if($status.replyQueue.active -gt 1){$issues.Add('顺序回复模式检测到多个同时处理的任务')}
        if($result.uiPauseExists -and $status.interaction.uiPaused -ne $true){$issues.Add('界面暂停开关存在，但监听尚未应用后台模式')}
        foreach($rdcIssue in $result.rdc.issues){$issues.Add($rdcIssue)}
        if($weixin.Count -eq 0){$issues.Add('微信进程未运行，需要检查登录状态')}
        if($workers.Count -eq 0){
            $issues.Add('监听进程未运行')
            if(-not $NoRepair -and $weixin.Count -gt 0 -and $config.version -eq 2){
                Start-ScheduledTask -TaskName $taskName
                $repairs.Add('已请求守护任务启动；保留所有账本及预约')
            }
        }elseif($workers.Count -gt 1){$issues.Add('发现多个监听进程，需要检查单实例锁')}
        if($status -and $status.updatedAt){
            $age=[Math]::Max(0,$now.ToUnixTimeMilliseconds()/1000-[double]$status.updatedAt)
            $result.statusAgeSeconds=[Math]::Round($age,1)
            if($age -gt 300){$issues.Add('监听状态超过5分钟未更新；不强杀未知发送中的进程')}
            if($status.lastError){
                $result.lastError=$status.lastError
                if($now.ToUnixTimeMilliseconds()/1000-[double]$status.lastError.at -lt 3600){$issues.Add('最近一小时出现回复故障，详情见lastError；不重试未知发送')}
            }
            $result.greetingReservations=$status.greetingReservations
            if($status.greetingReservations -and $status.greetingReservations.stale -gt 0){$issues.Add('存在超过5分钟未确认的每日介绍预约，禁止当作健康或自动重试')}
        }else{$issues.Add('监听状态文件尚未生成')}
        $journalRoot=Join-Path $state 'send-journals'
        $unknownSends=@(if(Test-Path -LiteralPath $journalRoot){Get-ChildItem -LiteralPath $journalRoot -Filter '*.json' | ForEach-Object {$journal=Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json;if($journal.phase -in 'send_reserved','clicked'){$journal.contact_hash}}})
        $result.unknownSendCount=$unknownSends.Count
        if($unknownSends.Count -gt 0){$issues.Add('存在点击结果未知的发送，保留预约并通知主人')}
        $socket=[Net.Sockets.TcpClient]::new()
        try{
            $connection=$socket.BeginConnect('127.0.0.1',21000,$null,$null)
            $connected=$connection.AsyncWaitHandle.WaitOne(1500)
            if($connected){$socket.EndConnect($connection)}
            $result.proxyAvailable=($connected -and $socket.Connected)
            if(-not $result.proxyAvailable){$issues.Add('本机模型代理端口21000不可用')}
        }catch{$result.proxyAvailable=$false;$issues.Add('本机模型代理端口21000不可用')}finally{$socket.Dispose()}
        # 星环由独立本机任务每小时检查；这里只读取结果，绝不额外唤醒账号或网络操作。
        $starRoot='C:\Users\YOUR_USER\Documents\Codex\DesktopCommanderRemote'
        $starHealthPath=Join-Path $starRoot 'star-network-health.json'
        $result.starWatchStopped=Test-Path -LiteralPath (Join-Path $starRoot 'STAR-WATCH-STOP')
        if(Test-Path -LiteralPath $starHealthPath){
            $starHealth=Get-Content -LiteralPath $starHealthPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $result.xinghuan=$starHealth
            if(-not $result.starWatchStopped){
                $starAge=($now-(Convert-LuluTimestamp $starHealth.updatedAt)).TotalSeconds
                if($starAge -gt 4500){$issues.Add('星环每小时检查结果超过75分钟未更新')}
                if($starHealth.intervalSeconds -ne 3600){$issues.Add('星环每小时检查频率未生效')}
                if(-not $starHealth.networkHealthy){$issues.Add('星环代理或RDC后端连接未确认正常')}
                if($starHealth.status -in 'needs_attention','account_needs_attention','owner_required','login_unconfirmed'){$issues.Add('星环本轮检查或账号恢复需要处理，查看星环状态文件')}
                if($starHealth.account.status -ne 'matched' -or $starHealth.account.observed -ne 'CONFIGURE_TARGET_ACCOUNT'){$issues.Add('星环登录账号未核对为指定账号；查看账号恢复状态')}
            }
        }else{$issues.Add('缺少星环每小时检查结果')}
        $result.mode=if($issues.Count -eq 0){'healthy'}else{'degraded'}
    }
}catch{$issues.Add($_.Exception.Message);$result.mode='error'}
$result.issues=@($issues);$result.repairs=@($repairs)
New-Item -ItemType Directory -Path $state -Force | Out-Null
$encoded=$result | ConvertTo-Json -Depth 8
$temporary=$healthPath+'.tmp'
[IO.File]::WriteAllText($temporary,$encoded,[Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination $healthPath -Force
Write-Output $encoded
