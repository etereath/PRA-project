# ShadowBot 文件队列实机验收报告

## 1. 验收结论

2026-07-01 已建立并跑通 ShadowBot 文件队列实机验收流程，结论为：

- 文件队列闭环验收流程：`PASS`。
- 单商品受控改价链路：`PASS`。
- 无人值守生产认证：`NOT YET APPROVED`。

本结论表示 PRA 可以按标准流程准备请求、由影刀 Worker 执行、由 Result Importer 导入、由 Queue Watchdog 恢复、由自动校验器核对证据；不表示已经完成 8 小时连续运行和所有真实故障长期样本。

> 后续同步（2026-07-11）：8 小时 READ_ONLY 连续运行、提交意图后停止、真实商品 UNKNOWN→自动 RECONCILE、登录失效、网络异常以及证据不可写/hash 不一致均已完成验收。本文其余内容保留为 2026-07-01 时点报告，当前状态以 [../project_current_status.md](../project_current_status.md) 为准。

## 2. 实机环境

- 平台：蚂蚁花团供应商微信桌面小程序。
- 影刀应用：`test2`。
- 队列：`D:\PRA_Runtime\shadowbot_queue`。
- 证据共享：`\\LAPTOP-O9O76RQV\pra-evidence`。
- 验收数据库：`data/runtime/shadowbot_acceptance.sqlite3`。
- 验收商品：`C级 艾莎`。

## 3. 已通过链路

| 验收项 | Attempt | 结果 |
| --- | --- | --- |
| 空队列启动/停止 | 无业务 attempt | 心跳 `RUNNING -> STOPPED`，无任务被领取 |
| READ_ONLY | `ATTEMPT-ACCEPT-READ-20260701-002` | `READ_COMPLETED`，旧价 `9.80` |
| FILL_PREVIEW | `ATTEMPT-ACCEPT-PREVIEW-20260701-001` | 回读 `10.30`，取消后无副作用 |
| 预览后 READ_ONLY | `ATTEMPT-ACCEPT-POSTPREVIEW-20260701-001` | 列表仍为 `9.80` |
| 受控 COMMIT | `ATTEMPT-ACCEPT-COMMIT-94538902e3dd` | `SUCCESS/VERIFIED`，`9.80 -> 10.30` |
| COMMIT 后 READ_ONLY | `ATTEMPT-ACCEPT-POSTCOMMIT-20260701-001` | 独立读价仍为 `10.30` |
| 副作用前停止 | `ATTEMPT-ACCEPT-STOP-PRESUBMIT-20260701-002` | `FAILED/WORKER_STOP_REQUESTED/NOT_STARTED`，Worker 正常停止 |
| UNKNOWN→RECONCILE | `ATTEMPT-RECOVERY-UNKNOWN` | 唯一确定性 RECONCILE，最终 `NOT_APPLIED`，无队列残留 |
| 过期请求 | `ATTEMPT-ACCEPT-STOP-PRESUBMIT-20260701-001` | `FAILED/REQUEST_EXPIRED/NOT_STARTED`，历史悬挂已修复归档 |

## 4. 自动校验结果

- COMMIT：43 项检查通过。
- post-COMMIT READ_ONLY：37 项检查通过。
- PRE_SUBMIT_STOP：31 项检查通过。
- UNKNOWN→RECONCILE：10 项恢复检查通过。
- 最终 ShadowBot 针对性回归：46 项测试通过。
- 中文 Markdown、JSON 报告均通过 UTF-8 回读检查，不含替换字符。

自动校验覆盖：

- request/result checksum。
- task、operation、attempt、mode、instruction hash 绑定。
- `approved_payload_hash`、`instruction_hash`、`request_file_sha256` 审计字段。
- phase 和副作用状态。
- Result Importer 归档和 Queue Watchdog 恢复。
- Worker/Importer/Watchdog 与 Executor 的职责边界。
- 共享证据存在性、上传状态和磁盘 SHA-256。
- 执行日志、数据库终态、quarantine 和活动队列残留。

## 5. 本轮发现并修复

1. 影刀包环境无法使用 Worker 顶层模块导入：改为包内相对导入，并保留仓库测试回退。
2. CLI 的 `--runner-type filequeue` 仍走旧 filedrop 默认目录：改为真实 `ShadowBotFileQueueRunner` 和环境队列目录。
3. 准备脚本硬编码 COMMIT：新增显式四模式支持。
4. COMMIT 缺少新鲜读价强制门：新增 `prepare_shadowbot_commit_acceptance.py` 和精确确认文本。
5. 缺少统一验收校验器：新增 `verify_shadowbot_filequeue_acceptance.py` 和 `PRE_SUBMIT_STOP` profile。
6. 发布后过期请求只进 quarantine，数据库 attempt 会悬挂：可信过期请求现写可导入 `REQUEST_EXPIRED` 结果；历史样本已修复。
7. 缺少真实文件级 UNKNOWN 恢复验收：新增隔离恢复演练器并完成双 attempt 归档验证。

## 6. 仍需生产观察

- 8 小时连续 Worker/Importer/Watchdog 运行观察。
- 微信、影刀或小程序升级后的重复 READ_ONLY/FILL_PREVIEW 回归。
- 提交意图后的真实 stop.signal 不再主动制造第二次商品变更；当前由代码不检查停止信号、真实 COMMIT phase、UNKNOWN 恢复和 RECONCILE 验收共同证明安全状态机。
- 跨机器 UNC 队列启用前的服务账号和共享 ACL 验证。
- 告警、证据保留周期、磁盘容量和长期 quarantine 运维。

在以上观察完成前，系统继续保持单平台、单窗口、单商品串行和人工批准，不承诺无人值守生产。
