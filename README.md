# wechat-auto-reply

wechat auto reply by using codex

## 微信鹭鹭 Skill

Windows 微信 4.x 私聊监听、自动回复和后台运维的 Codex skill，附业务源码快照。

- 私聊联系人筛选、每日介绍、联系人独立语气与本人视角配置
- 同人消息合并、全局 FIFO 单并发、额度不足时持久等待
- 原生模型、按需联网、时间、图片理解、视频抽帧与生图续接
- 精确目标与正文校验、发送预约、未知点击保护、更新来信取代旧失败
- 重要消息去重转告、人工暂停、小时健康检查、RDC 与星环守护

## 安装与离线检查

把 `wechat-lulu/` 放入 Codex 的 skills 目录，按 `wechat-lulu/SKILL.md` 使用。常驻运行仍依赖现有 Windows/Python 程序与任务。

```powershell
python wechat-lulu/scripts/lulu_skill.py verify
python wechat-lulu/scripts/lulu_skill.py validate-config --config wechat-lulu/assets/config.template.json
python wechat-lulu/scripts/lulu_skill.py stage --destination C:/new-empty-folder
```

暂存工具只复制文件，不安装、不认证、不创建任务、不启动监听。首次使用需自行配置实际路径、本人账号、联系人与机器依赖；配置模板默认为禁用。

## 分发版的边界

这是脱敏源码分发版，不含私人联系人绑定、个人路径、聊天记录、数据库、发送账本、水位、预约、登录凭证、密码、密钥或 DPAPI 密文。本机已部署版本保持原样。

文档中的特定联系人规则是配置示例，不能直接作为其他账号的授权。星环目标账号默认留空，导入账号模块会明确停止，需审查并配置后才能启用。原有运行状态不可通过本分发包恢复，也不能补发历史消息。

离线检查只验证源码完整性、语法和配置，不代表微信登录、实时 RPC、生图服务或真实发送已经验收。自检不生成图片、不发送测试、不调用回复模型。

业务源码来自原部署快照；底层 wechatauto 的许可保留在其包内 LICENSE。外部 RDC 安装、Python 环境和 Node 依赖不包含在本仓库内。
