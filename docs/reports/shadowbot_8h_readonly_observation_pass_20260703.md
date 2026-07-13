# ShadowBot 8 小时 READ_ONLY 连续运行验收报告

## 1. 验收结论

2026-07-03 第三轮 8 小时观察判定为 `PASS`。

本轮在代码、Worker 配置、队列目录和运行数据库保持不变的条件下完成。Worker 按 `max_hours=8` 自动停止，三个 READ_ONLY 检查点全部自动投递、执行、导入、归档并通过正式验收器；期间没有人工补导、服务重启、文件搬运或平台写操作。

本结论证明当前文件队列 Worker、Result Importer、Queue Watchdog、动态商品元素定位、共享证据和 READ_ONLY 长时间运行链路满足本阶段验收标准。它不等同于无人值守 COMMIT 的生产承诺。

## 2. 运行配置

| 项目 | 值 |
| --- | --- |
| 队列目录 | `D:\PRA_Runtime\shadowbot_queue` |
| 运行数据库 | `data\runtime\shadowbot_acceptance_8h_run2_20260703.sqlite3` |
| Worker | `LAPTOP-O9O76RQV` |
| Worker 上限 | 8 小时 / 50 项 |
| 执行模式 | `READ_ONLY` |
| 平台 | 蚂蚁花团供应商微信小程序 |
| 商品 | C级艾莎 |
| 队列服务启动 | 2026-07-03 12:23:57+08:00 |
| Worker 自动停止 | 2026-07-03 20:24:02+08:00 |

Worker 最终 heartbeat 为 `STOPPED`、`processed=3`。按自动停止时间与 8 小时上限反推，Worker 连续运行窗口约为 12:24:02 至 20:24:02。

## 3. 检查点结果

| 检查点 | 时间 | 结果 | 实际价格 | 证据 SHA-256 |
| --- | --- | --- | --- | --- |
| T0 | 12:25 | `READ_COMPLETED / ok=true` | `18.30` | `eb8a5dc3e4990b267f56e31289d3516bd758f9f47d452d3560407455edebdfbb` |
| T1 | 13:31 | heartbeat `RUNNING / ok=true` | - | 写失败 0、线程重启 0 |
| T4 | 16:33 | `READ_COMPLETED / ok=true` | `12.00` | `b916a410a89d9b7efb7205f94e96b8e783d83514c3d2cf4d9d35e15fcb1cc63d` |
| T6 | 18:40 | heartbeat `RUNNING / ok=true` | - | 写失败 0、线程重启 0 |
| T7:45 | 20:12 | `READ_COMPLETED / ok=true` | `11.00` | `2db941ba196d56521c937833d0312403933cdd6dfc6528950ba85cbd12f30939` |
| T8 | 20:24 | heartbeat `STOPPED / ok=true` | - | `processed=3` |

三次正式验收均满足：

- `failed_checks=[]`
- `status=READ_COMPLETED`
- `run_success_flag=true`
- `business_operation_completed=false`
- `side_effect_state=NOT_STARTED`
- 请求、结果、instruction 和 checksum 关联一致
- 共享证据存在且存储哈希一致
- execution log 已写入
- attempt 未进入 quarantine

## 4. 最终队列与健康状态

- `inbox/working/results` 全部为空。
- quarantine 中仅有两条本轮开始前已存在的历史诊断文件，本观察窗口内无新增。
- `heartbeat_write_failures=0`。
- `heartbeat_consecutive_failures=0`。
- `heartbeat_thread_restarts=0`。
- `orphan_temporary_files=[]`。
- `control/heartbeat_errors.jsonl` 不存在，说明本轮没有 heartbeat 写错误事件。
- PRA 队列服务从 12:23:57 持续运行至验收时仍存活，并始终使用 run2 数据库。

## 5. 价格变化说明

观察期间读取价格从 `18.30` 变为 `12.00`，最终为 `11.00`。READ_ONLY 请求不使用 `expected_old_price` 作为失败门槛，也不修改平台数据；三次结果均准确记录当时页面值并保存独立证据。因此该变化记录为平台业务数据漂移，不属于 RPA 稳定性故障。

任何后续 COMMIT 仍必须使用临近审批和执行时重新读取的 `expected_old_price`，不能沿用本报告中的任一历史价格。

## 6. 验收边界与后续工作

本轮完成了 READ_ONLY 文件队列 8 小时连续运行门槛。仍未完成的高风险验证包括：

1. 提交意图后收到 `stop.signal` 的真实商品样本。
2. COMMIT 提交后进程异常的真实 UNKNOWN→RECONCILE 全链路样本。
3. 更长周期的告警、磁盘清理、证据保留和服务账号运维验证。

在这些项目完成前，继续保持人工审批、单商品串行、旧价强校验、提交后只读对账和禁止自动重试 COMMIT 的边界。

## 7. 后续同步（2026-07-11）

本节第 6 节是 2026-07-03 验收结束时的历史快照。后续进展如下：

- 提交意图后收到 `stop.signal` 的真实商品样本已于 2026-07-06 完成，COMMIT 未在副作用区退出并最终 `SUCCESS/VERIFIED`。
- 真实商品 COMMIT 后 UNKNOWN→RECONCILE 全链路已于 2026-07-09 完成，自动对账最终 `VERIFIED`。
- 仍未完成的项目仅保留更长周期的告警、磁盘清理、证据保留和服务账号运维验证。

当前状态以 [../project_current_status.md](../project_current_status.md) 为准。
