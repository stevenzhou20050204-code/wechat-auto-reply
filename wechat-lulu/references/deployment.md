# 打包与迁移

本 skill 安装在 Codex skills 目录，含指令、离线管理工具和白名单业务源码快照。常驻监听仍由原来的 Python 程序与 Windows 任务运行。skill 不会自动增加模型调用；短入口和按需参考可减少重复长提示词，但真实自动回复仍产生正常模型开销。

## 包内内容

- assets/runtime/project：监听、策略、队列、媒体、发送与底层库源码。
- assets/runtime/engine：原生 CLI 定位依赖、守护、控制及健康检查源码。
- assets/runtime/rdc：连接与星环守护的自有代码；外部 RDC 安装及其 Node 依赖未内嵌。
- runtime-manifest.json：逐文件大小与 SHA-256。
- assets/config.template.json：禁用的配置模板，没有联系人/账号绑定。
- assets/local-profile.json：待配置的示例路径与任务名称，不是登录凭证。

不包含聊天数据库/聊天导出、通讯录缓存、活动/pending 个人配置、账本、水位、预约、日志、生成图片、模型会话、API 密钥、DPAPI 密文、密码、Python 环境、外部 Node 包及历史一次性恢复/实发验收脚本。上传版已经移除私人默认绑定，仍需审查并配置路径与规则，不能宣称完全通用或一键迁移。

## 离线校验

    python scripts/lulu_skill.py verify
    python scripts/lulu_skill.py validate-config --config assets/config.template.json

verify 校验清单、路径和源码语法，不执行生产模块、PowerShell 控制器、网络、UI 或模型。status 只读文件，不证明实时进程、RPC 或发送结果。

## 新目录暂存

    python scripts/lulu_skill.py stage --destination C:/new-empty-folder

目标必须是新的或空目录，不能为 profile 中的生产路径及其父/子目录。只复制 project、engine、rdc 和禁用示例；不安装、不创建任务、不认证、不启动。失败保留现场，不自动递归清理。生产原目录和原服务不动。

新机器需 Windows、兼容微信 4.x、本人微信登录、Python/依赖、原生 Codex CLI 及所需外部工具。Python 依赖以 project/pyproject.toml 与 requirements-lulu-ocr.txt 为准。路径与机器专用健康检查中的账号规则须审查重绑，已有接收者授权不能自动移植给另一个账号。

先保持 enabled=false，绑定实际 account_username、路径、标签和固定账号例外，再完成需要的本人授权。凭证由正常登录/原组件安全保存，不能打包或从日志恢复。不为安装创建测试来信、生成图或发送微信。

迁移旧实例的指纹/账本/预约/水位需要单独受控迁移；本分发包刻意不带这些私密状态。原实例不得清空或与新实例并行发送；不能以“新安装”为由补发历史。部署成功、实时 RPC 与发送验收须各自核对，不把离线打包通过说成在线验证通过。

assets/heartbeat-prompt.txt 可在主人要求精简监控时用于替换重复长说明；打包本身不修改现有 automation。
