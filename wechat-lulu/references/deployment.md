# 新电脑自动部署

这是安装型 skill。通常只需在新电脑安装此 skill 后说“用微信鹭鹭配置这台电脑并启动”，Codex 应直接运行 scripts/bootstrap.ps1 并完成环境工作，而不是交给用户一串安装步骤。

## 自动完成的工作

1. 检测 Windows x64、已有部署和目录归属；已有其他目录监听时保留并报告。
2. 自动定位或安装 Python 3.12，创建独立虚拟环境，安装项目、OCR和媒体依赖并执行 pip check。
3. 下载 Node 22 LTS 官方分发包并核对 SHASUMS256；定位本机原生 Codex，必要时通过官方 npm 包安装，自动适配 app-server 的 proxy/stdio 启动方式。
4. 安装 @wonderwhy-er/desktop-commander 0.2.51，核对并应用已验证的静默续期/认证拒绝隔离补丁；不迁移旧设备凭证。
5. 检测微信和星环；缺少微信时用 Windows Package Manager（缺失时按微软官方方式自动恢复），缺少星环时使用受信清单中的指定安装器。检查 Authenticode 签名，不关闭证书验证。
6. 使用实际安装目录、当前 Windows 用户和独立任务名称生成 profile；自动识别星环所拥有的本地 HTTP/SOCKS 端口。
7. 注册五项当前用户任务：登录接续、监听守护、RDC守护、08:00–22:00小时健康、全天小时星环守护。使用 Interactive/Limited 与 pythonw，不要求 SYSTEM，不创建前台终端。
8. 打开微信和星环供本人登录；识别本机实际账号并原子写入配置。登录后自动接续，初始监听不回填历史。微信账号变更不覆盖原绑定。
9. RDC 首次打开官方设备授权流程，完成后自动保存于 RDC 的标准用户目录并由后台守护复用。已有会话不强制重登；待定工具不重放；后续 daemon 保持 DC_NONINTERACTIVE=1。

星环可信源由用户提供：
https://dla.fastlyss.com/xinghuan/xinghuan_82001_x64_6.0.5.exe
签名必须匹配 SSS VPN Technical Co., Ltd。已完整下载核对发布者签名，并将 SHA256 固定在清单；安装时哈希和签名同时检查；不从同名搜索结果随便换产品。

## 命令与状态

    powershell.exe -NoProfile -File scripts/bootstrap.ps1
    powershell.exe -NoProfile -File scripts/bootstrap.ps1 -InstallRoot C:/WechatLulu
    python scripts/bootstrap.py doctor
    python scripts/bootstrap.py resume

默认安装在 %LOCALAPPDATA%/WechatLulu。无需手工修改源码中的盘符或任务名。doctor 只读文件；resume 是主人要求继续配置时的接续，不清人工 STOP。

- environment_ready：依赖准备完成，PrepareOnly 模式未装应用或创建任务。
- waiting_for_login / waiting_for_wechat_login / waiting_for_star_login：等待本人登录。
- waiting_for_star_connection：正确账号已核对，等待现有严格网络守护连接。
- codex_authorization_required：未发现本机可复用的 Codex 登录；skill 需走官方登录，不能伪造会话。
- rdc_authorization_required：只有新鲜状态明确报告 owner-required 才要求本人授权；超时或临时恢复失败不作此判断。
- starting：组件正在启动，不能说成功。
- ready：监听状态新鲜、RDC joined 且75秒内服务器 pong；RPC 仍需实际远程工具验证。
- paused / needs_attention：保留原状态，按具体来源处理。

## 幂等与恢复

重复执行不清账本、队列、水位、指纹、预约、草稿或凭证；已有禁用配置和新人工暂停不被解除。任务重名但身份不同会停止，不接管他人任务。安装目录须属于本安装器，不覆盖已有普通文件夹，不接受目录联接或符号链接。

联网或包安装失败会保存步骤状态；下次直接接续，不重新创建账号或重置凭证。日志仅保存在本机安装目录；GitHub 发布只包含源代码与模板。

安装完成并不意味着第三方身份验证可省略。微信、星环由本人登录；如果 RDC 要求首次设备确认、Codex 没有已有登录，或系统要求本人授权，skill 自动准备并引导确认后继续，不能保证绕过这些服务的验证。

## 验证边界与来源

离线验证与CI验证不调用模型、不生图、不发送微信。没有真实全新电脑的登录与 RPC 证据时，不声称已在新电脑端到端验收。

- Codex 安装与本机运行：https://learn.chatgpt.com/docs/codex/cli
- Codex 登录状态：https://learn.chatgpt.com/docs/auth
- RDC 设备授权与会话保存：https://github.com/wonderwhy-er/DesktopCommanderMCP/blob/main/src/remote-device/README.md

- Windows Package Manager 自动恢复：https://learn.microsoft.com/en-us/windows/package-manager/winget/troubleshooting
