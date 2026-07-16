# 通知 Outbox 与投递语义

任务 9 将运行态通知升级为 Runtime Schema v6。旧 `notification_logs` 保留用于历史展示和兼容读取；发送主路径以 `notification_outbox` 和 `notification_delivery_attempts` 为权威。

## 核心边界

- 业务状态与通知意图必须在同一个 SQLite 事务中提交；业务事务不执行网络发送。
- `notification_key` 由事件类型、业务实体、事件版本、渠道和收件人组成，重复入队返回已有记录。
- Worker 领取使用 `BEGIN IMMEDIATE`、owner token、递增 `lease_version` 和过期时间；续租与写回都必须通过 owner/version fencing。
- 网络调用前先写入 `STARTED` attempt 和请求指纹。明确未发送的临时失败进入有限 `RETRY_WAIT`；永久失败进入 `FAILED`。
- 发送结果不确定、发送后崩溃或 lease 在 `SENDING` 阶段过期时进入 `UNKNOWN_DELIVERY`，普通 Worker 不会自动重发。
- 验证码人工介入通知使用最高优先级、2 至 10 分钟 deadline 和最多 3 次尝试；payload 不得保存 token、密码、Cookie、完整请求头或 webhook URL。

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
