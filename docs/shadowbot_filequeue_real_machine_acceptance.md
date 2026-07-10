# ShadowBot 文件队列实机验收流程

## 1. 目标与边界

本文定义 ShadowBot 文件队列从 PRA 投递、影刀 Worker 执行、Result Importer 导入、Queue Watchdog 监测到证据归档的实机验收流程。

验收按风险递增分为：

1. 空队列常驻与安全停止。
2. `READ_ONLY` 真实读价。
3. `FILL_PREVIEW` 填写、回读、截图、取消，再以 `READ_ONLY` 证明平台价格未改变。
4. 单商品受控 `COMMIT`。
5. 提交结果未知时自动创建唯一 `RECONCILE`，禁止自动重试 `COMMIT`。
6. 连续运行与停止行为观察。

前一阶段未通过时不得进入后一阶段。`COMMIT` 必须在操作时再次确认商品、旧价和目标价。

## 2. 验收工具

- `scripts/prepare_shadowbot_e2e_chain.py`：创建独立 task、真实批准记录、operation 和 attempt，并按 `--execution-mode` 投递请求。
- `scripts/run_shadowbot_queue_services.py`：运行独立 Result Importer 和 Queue Watchdog。
- `scripts/verify_shadowbot_filequeue_acceptance.py`：校验数据库、请求/结果 checksum、字段绑定、phase、共享证据哈希、执行日志和队列残留。
- `scripts/prepare_shadowbot_commit_acceptance.py`：只允许基于一条新鲜且全项通过的 READ_ONLY attempt 准备受控 COMMIT，并强制精确确认文本。
- `scripts/run_shadowbot_filequeue_recovery_acceptance.py`：在隔离队列中演练 UNKNOWN、Watchdog 恢复、Importer 导入和确定性 RECONCILE。
- `scripts/repair_shadowbot_expired_attempt.py`：只用于迁移修复前已隔离、且 checksum 与数据库原始 hash 一致的过期 attempt。
- `scripts/sync_shadowbot_test2.py`：备份并同步影刀 `test2` 规范代码。

实机验收建议使用独立数据库：

```text
data/runtime/shadowbot_acceptance.sqlite3
```

## 3. 运行前检查

1. 微信桌面小程序窗口已登录，标题为 `蚂蚁花团供应商`。
2. 目标商品处于上架状态。
3. `SHADOWBOT_QUEUE_DIR` 与影刀 `shadowbot_worker_config.json` 指向同一目录。
4. 共享证据目录可写，并能从 PRA 进程读取。
5. `inbox/working/results` 没有不明遗留文件，`control/stop.signal` 不存在。
6. 同步影刀代码后关闭并重新打开 `test2`，避免使用内存中的旧代码。
7. 影刀应用列表中先选中 `test2`，再点击顶部“运行”或行内“运行应用”。未选中应用时不得根据固定坐标点击。

## 4. 标准运行顺序

生产候选运行时，PRA 队列服务应先于 Worker 常驻启动：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\local_env.ps1
python scripts\run_shadowbot_queue_services.py `
  --runtime-db data\runtime\shadowbot_acceptance.sqlite3
```

若只做单次人工验收，可在 Worker 写出 result 后运行一次：

```powershell
python scripts\run_shadowbot_queue_services.py `
  --runtime-db data\runtime\shadowbot_acceptance.sqlite3 `
  --once
```

只要 `working` 尚未被 Importer 归档，Worker 就不会领取下一项，也不会因 `stop.signal` 静默丢弃当前 attempt。因此人工验收的停止顺序必须是：

1. 等待 `results/<attempt>.result.json`。
2. 运行 Importer 完成归档。
3. 创建 `control/stop.signal`。
4. 等待 `heartbeat.json` 变为 `STOPPED`。
5. 删除 `stop.signal`，避免下次启动立即退出。

## 5. 无副作用验收

### 5.1 READ_ONLY

投递时使用独立且唯一的 task、operation 和 attempt ID：

```powershell
python scripts\prepare_shadowbot_e2e_chain.py `
  --runtime-db data\runtime\shadowbot_acceptance.sqlite3 `
  --platform "蚂蚁花团供应商" `
  --sku "SKU-AISHA-C" `
  --platform-sku "SKU-AISHA-C" `
  --product-name "艾莎" `
  --grade "C级" `
  --expected-old-price "9.80" `
  --target-price "10.30" `
  --execution-mode READ_ONLY `
  --task-id "<TASK-ID>" `
  --approval-id "<REVIEW-ID>" `
  --operation-id "<OPERATION-ID>" `
  --execution-attempt-id "<ATTEMPT-ID>" `
  --start
```

通过条件：`READ_COMPLETED`、`run_success_flag=true`、`business_operation_completed=false`、`side_effect_state=NOT_STARTED`，并有可访问且 hash 一致的共享证据。

### 5.2 FILL_PREVIEW 与后置读价

先用最新 `READ_ONLY` 的实际价作为 `expected_old_price`，目标价使用明确的测试值。通过条件：

- `PREVIEW_COMPLETED`。
- `input_price_readback == target_price`。
- `side_effect_state=NOT_STARTED`。
- 共享证据完整。
- 紧接着的新 `READ_ONLY` 仍读到预览前旧价。

不得把 FILL_PREVIEW 的页面草稿复用于 COMMIT。

## 6. 验收校验器

Importer 完成归档后运行：

```powershell
python scripts\verify_shadowbot_filequeue_acceptance.py `
  --runtime-db data\runtime\shadowbot_acceptance.sqlite3 `
  --queue-dir D:\PRA_Runtime\shadowbot_queue `
  --execution-attempt-id "<ATTEMPT-ID>" `
  --execution-mode READ_ONLY `
  --report docs\reports\shadowbot_filequeue_acceptance_<name>.json
```

只有报告 `ok=true` 且 `failed_checks=[]` 才算通过。校验器会验证：

- attempt 已结束且三类 hash/队列路径已记录。
- archive 中恰有一组 request、result、phase 和 checksum。
- request、result、数据库之间的 task/operation/attempt/mode/instruction hash 绑定一致。
- mode 对应的状态、技术成功、业务完成和副作用状态一致。
- FILL_PREVIEW 输入回读等于请求目标价；COMMIT 实际价等于请求目标价。
- 共享证据存在，上传状态、`hash_verified` 和磁盘 SHA-256 一致。
- 当前队列无该 attempt 残留，未进入 quarantine，执行日志已写入。

## 7. COMMIT 与 RECONCILE 门槛

进入 COMMIT 前必须重新执行 READ_ONLY，确认最新旧价，并由 PRA 重新生成批准载荷。COMMIT 验收必须满足：

- `SUCCESS` 或 `ALREADY_APPLIED`。
- `business_operation_completed=true`。
- `side_effect_state=VERIFIED`。
- `actual_price == target_price`。
- 提交前和提交后证据均成功上传并通过 hash 校验。

先运行不带 `--start` 的只读预检：

```powershell
python scripts\prepare_shadowbot_commit_acceptance.py `
  --runtime-db data\runtime\shadowbot_acceptance.sqlite3 `
  --queue-dir D:\PRA_Runtime\shadowbot_queue `
  --source-read-attempt-id "<READ-ATTEMPT-ID>" `
  --target-price "10.30" `
  --confirmed-by "<OPERATOR>"
```

预检要求来源 READ_ONLY 在默认 10 分钟内完成、验收报告全项通过、共享证据可读、队列为空且没有 `stop.signal`。输出的 `required_confirmation_text` 必须由操作员核对。

真正投递时才增加 `--start` 和逐字一致的 `--confirmation-text`：

```powershell
python scripts\prepare_shadowbot_commit_acceptance.py `
  --runtime-db data\runtime\shadowbot_acceptance.sqlite3 `
  --queue-dir D:\PRA_Runtime\shadowbot_queue `
  --source-read-attempt-id "<READ-ATTEMPT-ID>" `
  --target-price "10.30" `
  --confirmed-by "<OPERATOR>" `
  --confirmation-text "COMMIT C级艾莎 9.80 -> 10.30" `
  --start
```

脚本会把来源 READ_ONLY attempt ID 写入 task 决策轨迹和 review 审计载荷。确认文本不匹配时不创建 task、operation、attempt 或队列请求。

若点击提交后无法确认结果，只允许返回 `NEEDS_RECONCILIATION + UNKNOWN`。Importer 导入后由 `ShadowBotExecutor` 自动创建唯一 `RECONCILE`；Worker、Importer 和 Watchdog 不得创建，也不得自动重试 COMMIT。

## 8. 2026-07-01 实机记录

| 阶段 | Attempt | 结果 | 结论 |
| --- | --- | --- | --- |
| 空队列 | 无 | `RUNNING -> STOPPED` | 心跳与安全停止通过 |
| 首次 READ_ONLY | `ATTEMPT-ACCEPT-READ-20260701-001` | `FAILED/WORKER_EXECUTION_FAILED` | 影刀包环境顶层导入失败；副作用未开始，结果已归档 |
| 修复后 READ_ONLY | `ATTEMPT-ACCEPT-READ-20260701-002` | `READ_COMPLETED`，实际价 `9.80` | 35 项校验全部通过 |
| FILL_PREVIEW | `ATTEMPT-ACCEPT-PREVIEW-20260701-001` | `PREVIEW_COMPLETED`，回读 `10.30` | 增强后价格断言和基础校验全部通过 |
| 后置 READ_ONLY | `ATTEMPT-ACCEPT-POSTPREVIEW-20260701-001` | `READ_COMPLETED`，实际价仍为 `9.80` | 证明预览取消未持久化 |
| COMMIT 只读预检 | 来源为后置 READ_ONLY | `ready_to_start=false` | 通过；要求确认文本 `COMMIT C级艾莎 9.80 -> 10.30`，未投递任务 |
| 受控 COMMIT | `ATTEMPT-ACCEPT-COMMIT-94538902e3dd` | `SUCCESS/VERIFIED`，`9.80 -> 10.30` | 42 项校验全部通过，两份共享证据 hash 一致 |
| post-COMMIT READ_ONLY | `ATTEMPT-ACCEPT-POSTCOMMIT-20260701-001` | `READ_COMPLETED`，实际价 `10.30` | 独立重新进入列表后确认改价持久化 |
| 隔离恢复验收 | `ATTEMPT-RECOVERY-UNKNOWN` | `UNKNOWN -> RECONCILE -> NOT_APPLIED` | Watchdog、Importer、Executor、确定性对账和双 attempt 归档全部通过 |
| 历史过期请求 | `ATTEMPT-ACCEPT-STOP-PRESUBMIT-20260701-001` | `FAILED/REQUEST_EXPIRED/NOT_STARTED` | 发现旧 Worker 仅隔离会令数据库悬挂；修复并迁移归档 |
| 副作用前停止 | `ATTEMPT-ACCEPT-STOP-PRESUBMIT-20260701-002` | `FAILED/WORKER_STOP_REQUESTED/NOT_STARTED` | 在 `UI_STARTED` 后注入，31 项校验全部通过，Worker 导入后正常停止 |

本轮修复了 Worker 在影刀包执行环境中的模块导入方式，并确认人工单次运行必须先由 Importer 归档 working，再请求 Worker 停止。

8 小时 READ_ONLY 连续运行观察已于 2026-07-03 完成并通过，详见 [reports/shadowbot_8h_readonly_observation_pass_20260703.md](reports/shadowbot_8h_readonly_observation_pass_20260703.md)。提交意图后的 stop.signal 实机行为已于 2026-07-06 完成核心验收并归档，详见 [reports/shadowbot_post_intent_stop_acceptance_20260706.md](reports/shadowbot_post_intent_stop_acceptance_20260706.md)。2026-07-09 已在真实商品提交后主动制造 UNKNOWN，并由 Executor 自动创建唯一 RECONCILE，最终真实价格复核为 `13.00`、operation 归并 `VERIFIED`；详见 [reports/shadowbot_unknown_reconcile_attempt_20260709.md](reports/shadowbot_unknown_reconcile_attempt_20260709.md)。
