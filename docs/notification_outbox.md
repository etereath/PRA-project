# 通知 Outbox 与投递语义

任务 9 将运行态通知升级为 Runtime Schema v6。旧 `notification_logs` 保留用于历史展示和兼容读取；发送主路径以 `notification_outbox` 和 `notification_delivery_attempts` 为权威。

## 核心边界

- 业务状态与通知意图必须在同一个 SQLite 事务中提交；业务事务不执行网络发送。
- `notification_key` 使用版本化 canonical JSON 的 SHA-256 表示，避免冒号、Unicode 和控制字符碰撞；重复 Review 的 canonical fingerprint 覆盖业务字段、截止时间、`review_payload`、平台、渠道与收件人，不一致会返回冲突错误。
- Worker 领取使用 `BEGIN IMMEDIATE`、锁内权威时间、owner token、递增 `lease_version` 和过期时间；续租与写回都必须通过 owner/version fencing，迟到写回不能变成 `SENT`。
- 网络调用前先写入 `STARTED` attempt 和请求指纹。明确未发送的临时失败进入有限 `RETRY_WAIT`；永久失败进入 `FAILED`。
- 发送结果不确定、发送后崩溃或 lease 在 `SENDING` 阶段过期时进入 `UNKNOWN_DELIVERY`，普通 Worker 不会自动重发。
- 验证码人工介入通知使用最高优先级、2 至 10 分钟 deadline 和最多 3 次尝试；ShadowBot 登录验证的 `ReviewTask` 与 Outbox 在同一事务创建，业务事务不调用渠道。
- `NotificationChannelRegistry` 按持久化 `channel` 绑定 `fake / scripted / feishu` 适配器；发送前强制校验适配器 channel。业务创建路径只入队，绝不自动执行 `FakeSender`；未配置渠道会以 `unconfigured` 保持 `PENDING`，生产 `mock` 与缺失配置会在健康检查中报错。
- 所有 channel 在构造 key 和落库前统一小写；Feishu 新旧适配器复用同一官方签名函数。只有显式 `code=0` 或 `StatusCode=0` 才确认成功；无确认码和已越过发送边界的 HTTP 5xx 均进入 `UNKNOWN_DELIVERY`，429 才按明确限流拒绝进入有限重试。
- Service 不允许一个时间戳跨越领取、网络调用和写回；各 Repository 事务在取得写锁后独立读取注入时钟。
- 已知通知类型采用字段白名单、类型和长度限制；provider 错误只保留安全错误码/摘要，Bearer、Cookie、Webhook URL 等值会被拒绝或脱敏。
- Review 解决、取消或过期时，同一事务将尚未进入 `SENDING` 的关联 Outbox 推进 `CANCELLED`；`SENDING` 保留不确定投递语义。
- ReviewTask、Outbox 和初始 `notification_logs` 兼容投影在同一事务创建；重复 Review 按业务 `dedupe_key` 返回已有 Outbox，并可幂等补建历史缺失的投影。
- Review 超时与安全回退使用一个 Repository 事务同时推进 ReviewTask、可选源 Task、History、`review_expired` Outbox 和兼容日志；任一写入失败会整体回滚。
- 所有领取过期、取消、完成写回和 Watchdog 状态变化会在同一事务同步旧 `notification_logs` 兼容投影。

## 测试发送器

CI 使用 `FakeSender` 和 `ScriptedSender`，不访问真实渠道。故障脚本覆盖 `before_send`、`temporary_reject`、`permanent_reject`、`timeout_after_bytes_sent`、发送确认后数据库写回前崩溃等边界。

## 验收命令

```powershell
python -m pytest -q tests -k "notification or outbox or delivery or unknown_delivery"
python -m pytest -q tests -k "lease or retry or concurrent"
python -m pytest -q tests -k "schema or migration or health"
python scripts/run_system_smoke_tests.py --temporary-db
python -m pytest -q tests
```
