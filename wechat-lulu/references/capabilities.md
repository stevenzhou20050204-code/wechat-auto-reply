# 功能索引

优先读取 live profile 指向的源码和状态；快照用于复用与审查。不要把历史验收等同于当前可用。

| 功能 | 主要代码入口 | 关键状态或约束 |
|---|---|---|
| 私聊监听、账号核对、动态通讯录 | run_two_contacts.py、wechat_listener.py、stable_db.py、wechatauto/db.py | scope=all_private_contacts，用户名绑定 |
| 标签、昵称与固定账号排除 | lulu_policy.py、lulu_runtime.py、passive_sender.py | 生成前、发送前、最终点击前复核 |
| 同人合并、全局 FIFO、联系人隔离 | lulu_runtime.py、reply_queue_journal.py | 单并发，reply-queue.json 原子持久化 |
| 额度不足与重启接续 | codex_bridge.py、lulu_runtime.py | 保留批次、指纹；按恢复时间冷却，不发保底 |
| 原生模型、按需联网与时间 | lulu_bridge.py、codex_bridge.py、clock_rules.py | gpt-6.1-sol / medium；仅真实请求 |
| 每日首次介绍 | lulu_policy.py、lulu_runtime.py | 每上海日期、真实私聊联系人一次，确认后正常交流 |
| 专属语气、本人第一人称、身份追问 | lulu_policy.py、lulu_bridge.py、lulu_runtime.py | 可信配置固定账号绑定，不跨联系人继承 |
| 政治敏感与无法识别内容静默 | topic_policy.py、lulu_policy.py | 包含续话与媒体；silent 不发介绍或解释 |
| 图片理解、视频抽样帧、媒体上下文 | media_context.py、wechatauto/media.py | 账号、联系人、媒体隔离 |
| 原生生图、催图与改图续接 | codex_bridge.py、lulu_bridge.py、lulu_runtime.py | 非介绍的正常轮次工具可用；不因普通聊天自动画图 |
| 图片发送与正文确认 | passive_sender.py、wechat_sender.py | 最终点击前复核；发送不确定不得重试 |
| OCR 标题隔离、搜索、输入、任务栏避让 | local_ui_ocr.py、local_text_bridge.py、passive_sender.py | 精确标题、随机剪贴板标记、恢复剪贴板、正文仅规范行结束符 |
| 失焦保护 | passive_sender.py | 只对从未预约点击且已核对草稿有限恢复；未知发送不可接续 |
| 预约、点击与发送进度 | send_progress.py、pending_reply_policy.py | send-requests、账本、send-resolutions 保留 |
| 更新来信取代旧失败 | pending_reply_policy.py、lulu_runtime.py | 同账号、严格更新、事件不重叠；先备份旧草稿 |
| 状态发布与缓存共享冲突隔离 | listener_status.py、stable_db.py | 专用 TransientCacheReadError 有限隔离，不吞其他权限/写入错误 |
| 重要消息转告与去重 | lulu_runtime.py、.wechat_codex/lulu/important-messages.jsonl | 按已通知 ID 摘要，不代替主人作现实承诺 |
| 启停、单实例、守护 | WechatAutoReplyControl.ps1、WechatAutoReplySupervise.ps1 | 人工暂停优先；Start 会归档 STOP，不能当自检调用 |
| 小时健康与零生图检查 | WechatLuluHealth.ps1、lulu_image_health.py | 本机结果新鲜则只读，不启动模型或实际出图 |
| RDC 进程、joined、心跳与恢复 | RdcConnectionPolicy.ps1、RDC Supervise.ps1、HealthPolicy.ps1 | 实际 RPC ping 独立验证；已有登录、非交互续期 |
| 星环网络与账号守护 | star-network-watch.py、star-account.py、StarNetworkWatchControl.ps1 | 每小时；严格进程路径；DPAPI 凭证不进入上下文 |

已部署功能基线：contactStyleRoutingVersion=3、imageGenerationRoutingVersion=3、cacheReadGuardVersion=1、statusPublisherVersion=2。更高的新版本应核对源码，不能回退到旧快照。

底层 wechatauto 含群聊、朋友圈等通用库模块，不代表这套助手获准启用它们。旧 companion、计数器、一次性恢复及实发验收脚本不作为默认工作流。
