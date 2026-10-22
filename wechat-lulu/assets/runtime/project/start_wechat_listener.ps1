param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ListenerArgs)
$ErrorActionPreference='Stop'
$controller='C:\Users\YOUR_USER\Documents\Codex\2026-10-02\referenced-chatgpt-conversation-this-is-an\outputs\wechat-prototype\WechatAutoReplyControl.ps1'
if(-not $ListenerArgs.Count){& $controller -Action Start}
elseif($ListenerArgs.Count -eq 1 -and $ListenerArgs[0] -eq '--stop'){& $controller -Action Stop}
elseif($ListenerArgs.Count -eq 1 -and $ListenerArgs[0] -eq '--status'){& $controller -Action Status}
else{throw '支持 wxlisten、wxlisten --stop、wxlisten --status'}
