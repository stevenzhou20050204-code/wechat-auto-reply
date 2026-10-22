# wechat-auto-reply

wechat auto reply by using codex

## 新电脑自动部署

将 `wechat-lulu/` 安装为 Codex skill，然后说：

> 使用微信鹭鹭 skill 配置这台新电脑，自动安装环境并启动监听、RDC 和网络守护。

Skill 会直接执行安装入口，自动处理 Python、Node、Codex CLI、业务/OCR依赖、微信和星环软件、路径、端口、账号绑定、用户登录时的隐藏启动任务及小时自检。用户无需逐项手装环境或改盘符。

微信和星环由本人登录。优先复用本机已有 Codex 登录。RDC 官方要求新设备首次确认授权，安装器会打开流程并在完成后自动接续；无法复制旧电脑凭证或绕过服务验证。Windows 如果强制本人授权，也必须正常完成。

默认部署目录为当前用户的 `%LOCALAPPDATA%/WechatLulu`。本机已有其他目录监听时保留原部署，重复执行不清空指纹、账本、水位、队列、草稿、预约和人工暂停。

## 自动运维

- 私聊动态名单、每日介绍、联系人独立语气与本人视角功能
- 同人消息合并、全局 FIFO 单并发、额度不足持久等待
- 按需联网、时间、图片理解、视频抽帧与原生生图续接
- 精确目标与正文校验、发送预约、未知点击保护及更新来信取代旧失败
- 重要消息去重、人工暂停保护与零生成小时健康检查
- RDC 自动启动、已有登录静默续期、进程/通道监测与有界恢复
- 星环实际路径与端口自动识别，每小时三轮确认后按保护规则恢复

## 自动安装入口与诊断

Skill 会替用户运行，不要求用户手动敲这些命令：

```powershell
powershell.exe -NoProfile -File wechat-lulu/scripts/bootstrap.ps1
python wechat-lulu/scripts/bootstrap.py doctor
python wechat-lulu/scripts/bootstrap.py resume
```

离线预览使用 `bootstrap.ps1 -Plan`。环境准备验证使用 `bootstrap.py install --prepare-only`，此模式不安装微信/星环、不创建任务、不登录或发送。

安装阶段、登录等待、启动中和就绪状态分别记录。就绪要求监听状态新鲜及 RDC 当前进程、joined 和服务器 pong；完整 RPC 需要实际远程工具 pong，不能以本地健康文件替代。

## 公开包与验证边界

公开包不包含私人联系人绑定、实际账号、聊天记录、数据库、账本、水位、预约、登录凭证、密码、密钥或 DPAPI 密文。星环软件从用户指定链接下载并核验签名，不将安装器上传至仓库。

Windows CI 分别检查隔离回归和全新依赖目录的安装。不会调用回复模型、生图、发送微信或绕过账号验证。CI 通过不能等同于已在真实新电脑完成微信登录、设备授权与发送验收。

第三方源码及 RDC 恢复补丁分别保留原许可。详细行为见 [SKILL.md](wechat-lulu/SKILL.md) 和 [自动部署文档](wechat-lulu/references/deployment.md)。
