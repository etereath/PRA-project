# 任务12审查问题接续修复报告

> 本文接续《任务12最终交接报告》，不覆盖原文，也不修改任务12状态。任务状态继续等待审查方在代码、自动化测试和新增实机证据全部核验后决定。

## 1. 修复范围

本轮以下载的《任务12审查问题修复对接文档》及已经确认的修正意见为依据，保留 `contract_version=4` 和最后一次成功的单商品提交动作链，不重写页面点击、价格填写和回读逻辑。修复集中在批次安全边界：

- manifest 升级为 `shadowbot-commit-manifest-1.2`；
- request 升级为 `shadowbot-commit-batch-request-1.2`；
- result 升级为 `shadowbot-commit-batch-result-1.1`；
- phase 增加 `shadowbot-commit-batch-phase-1.0`；
- runtime schema 从 v11 增量升级为 v12。

旧 v4 文件继续作为历史归档证据读取；新正式任务只生成新合同，不继续发布 1.1/1.0 旧格式。

## 2. 逐商品身份和锁

新合同明确区分四类身份：

| 字段 | 生成依据 | 用途 |
| --- | --- | --- |
| `item_id` | `batch_id + source_task_id` | 批次内逐项证据绑定 |
| `operation_id` | `source_task_id + item_payload_sha256` | 同一业务操作跨发布重试保持稳定 |
| `item_execution_attempt_id` | 批次 `execution_attempt_id + item_id` | 区分每次实际执行尝试 |
| `page_identity_key` | 平台 + 规范商品名 + 等级 | 当前页面动态唯一定位 |
| `write_identity_key` | 平台 + `internal_sku` | 写操作并发互斥 |

批次顶层 `operation_id` 仅保留兼容意义，不再承担逐商品幂等或锁安全。v12 新增 `shadowbot_write_locks`；同一写入身份存在 `ACTIVE` 或 `UNKNOWN` 锁时，不能发布第二个 COMMIT。

## 3. 发布边界

发布前在一个 SQLite 事务内完成：

1. 再次校验明确传入的 Task Center `task_id` 列表仍为有效 `pending update_price`；
2. 核对平台、SKU、旧价、目标价和过期时间；
3. 创建或复用逐商品 operation；
4. 创建新的逐商品 execution attempt；
5. 获取逐商品写锁；
6. 将任务和批次切换到发布中状态。

文件队列明确未发布时，逐商品 attempt 记为 `START_FAILED/NOT_STARTED`，释放写锁并把任务恢复为 `pending`。发布边界不确定时，批次、任务、逐项 attempt 和写锁统一进入 UNKNOWN/人工复核边界，不允许重新 COMMIT。

## 4. Worker 和 phase

Worker 仍复用已验收的单商品 COMMIT 动作函数，只在其外层维护批次预扫描、页面顺序计划和逐项结果：

- 预先初始化与请求等长的逐商品结果骨架；
- 通用异常不再返回缺失 `items` 的结果；
- 结果超过 4 MiB 时只压缩非关键内容，仍保留全部逐商品身份、状态、价格和时间；
- 每个 item 记录 `preflight_observed_at`、`submit_intent_at`、`submit_clicked_at` 和 `readback_observed_at`；
- phase 带有当前 `item_phase`，Watchdog 可以恢复已完成项和当前风险项；
- 仅记录提交意图但确认按钮尚未点击时，只有“弹窗仍打开且成功取消”可以证明 `NOT_APPLIED`；无法取得该证明时进入 UNKNOWN。

页面快照不再用批次 `ended_at` 冒充观察时间。预扫描商品使用真实 `captured_at`，提交后回读商品使用各自 `readback_observed_at`。

## 5. 原子导入和技术回执

旧实现先写 `listing_status`，再导入批次结果；若后续逐项校验失败，会留下部分页面状态。本轮已改为：

```text
校验请求和结果文件
→ 只读规范化完整 ImportPlan
→ 严格校验 bool、规范价格、带时区时间、逐项身份、状态和计数
→ 一个 SQLite 事务写入全部业务与技术投影
→ 写入 shadowbot_commit_result_receipts
→ 事务提交
→ 写归档 import ACK 并移动请求/结果/phase
```

`shadowbot_commit_result_receipts` 是结果已被数据库接受的事实来源。ACK 写入失败时，回执标记为 `FAILED`，原结果文件保留在 `results`，下一轮只重试 ACK/归档投影；不会再次执行任务状态或平台状态写入。

## 6. UNKNOWN 和 RECONCILE

Worker 和 Watchdog 只产生完整 UNKNOWN 结果，不自行创建对账任务。Result Importer 在数据库接受 UNKNOWN 后，仅调用 `ShadowBotExecutor.ensure_reconcile_attempt(...)`。该方法使用源逐商品 attempt 生成确定性 RECONCILE ID，保证只读对账唯一。

UNKNOWN 写锁保持为 `UNKNOWN`，阻止相同 SKU 再次 COMMIT。RECONCILE 得到 `VERIFIED` 或 `NOT_APPLIED` 后，由 Executor 释放该写锁。

## 7. 自动化验证

当前已经完成：

- v12 新库初始化和结构健康检查通过，迁移记录连续为 `1..12`；
- COMMIT 管线、Worker 编排、运行态迁移和历史 operation/recovery 定向回归通过；
- 新增“明确未发布补偿”和“发布边界不确定隔离”测试；
- 新增字符串 `"false"` 不能冒充布尔值的严格导入测试；
- 系统冒烟测试 `16` 项通过、`0` 项失败；
- 从当前源码新构建 wheel，在仓库外隔离安装、初始化 v12 和 health 检查通过；
- Windows wheel 安装显式包含 `tzdata`，不再依赖系统时区数据库；
- wheel 验证器从唯一版本常量计算迁移范围，不再写死旧版本。

在实机验收后补充了终态结果幂等重投影：若结果身份和文件哈希已经被数据库接受，但任务或商品状态投影曾中途失败，同一结果再次导入时只修复 operation、任务、写锁和 `listing_status`，不重新发布队列请求，也不重复写执行日志。对应故障恢复测试已加入 `tests/test_shadowbot_queue.py`。

最终源码回归结果：

- 全量 pytest：`547 passed, 3 skipped, 97 subtests passed`，用时 `114.89s`；
- 系统冒烟测试：`16` 项通过、`0` 项失败；
- 从最终源码重新构建 wheel 成功；
- wheel 在仓库外隔离安装后，依赖安装、核心导入、路径隔离、CLI、v12 初始化和 health 检查全部通过。

## 8. 修复版实机验收

修复版要求的两类真实证据均已取得。原始运行归档保存在
`D:\PRA_Runtime\shadowbot_queue\archive`；经过脚本脱敏、重新绑定 request SHA
并通过独立校验器复算的副本已提交到
[`docs/evidence/task12`](../evidence/task12/index.md)。CI 同时执行
`scripts/verify_task12_sanitized_evidence.py`，不再依赖人工抄写本机绝对路径和哈希。

### 8.1 正常四商品 COMMIT

| 字段 | 结果 |
| --- | --- |
| 批次 | `BATCH-T12-REMEDIATION-COMMIT-20260723-01` |
| Run ID | `ATTEMPT-52c584afca044d79` |
| 批次结果 | `VERIFIED` |
| 计数 | `total=4, attempted=4, verified=4, failed=0, unknown=0, not_applied=0, not_attempted=0` |
| 结果 SHA-256 | `4a7d2df8bbc9c98553844b538f648d41ff56fe79ce922ea7b47cb2f60969b826` |

逐商品独立回读：

| 顺序 | 任务 ID | SKU | 商品 | 旧价 → 目标价 | 实际回读 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `fbf5602937a5` | `CAPPUCCINO-E-45-Z` | 卡布奇诺 E级 | 12.50 → 12.60 | 12.60 | `VERIFIED` |
| 2 | `d2e183fba9a7` | `AISHA-C-55-Z` | 艾莎 C级 | 7.80 → 7.90 | 7.90 | `VERIFIED` |
| 3 | `d8255d95290d` | `AISHA-D-50-Z` | 艾莎 D级 | 9.00 → 9.10 | 9.10 | `VERIFIED` |
| 4 | `44832c9ddcb3` | `AISHA-B-60-Z` | 艾莎 B级 | 10.00 → 10.10 | 10.10 | `VERIFIED` |

SQLite 回读显示四个任务均为 `success`，批次回执已接受，逐商品写锁均已释放。

### 8.2 受控 UNKNOWN → 唯一 RECONCILE

| 字段 | 结果 |
| --- | --- |
| 批次 | `BATCH-T12-CONTROLLED-UNKNOWN-20260723-02` |
| 任务 | `a95ea3dfd690` / `AISHA-B-60-Z` |
| COMMIT Run ID | `ATTEMPT-0f30900b398045cc` |
| 稳定逐商品 attempt | `ATTEMPT-0b137ed254c9fb59f81392fd9c3adf59` |
| COMMIT 结果 | `UNKNOWN / SUBMIT_RESULT_UNKNOWN` |
| COMMIT 结果 SHA-256 | `3c28f255fd0b675337d1e20f130b4602b1f292a5adbae2c933a5ac2ad666b84a` |
| 唯一 RECONCILE | `RECONCILE-046a063ae885fcb4f352` |
| RECONCILE 结果 | `VERIFIED`，实际价格 `10.30` |
| RECONCILE 结果 SHA-256 | `50316f907638e7fa96b0a0d30852ca2b991991c8fe5b6fc099f4a12a79eb0890` |
| RECONCILE 截图 SHA-256 | `2329e5c823c4459d613d7dfd4f42ca5df5a39b9555357cdacff9b6f65d9cb141` |

本批次只执行了一次 COMMIT 和一个确定性 RECONCILE，没有追加恢复
COMMIT。最终数据库状态为：任务 `success`、operation `VERIFIED`、写锁
`RELEASED`、`listing_status.current_price=10.30`、库存 `3`、状态 `online`。
历史 COMMIT 批次和逐商品 attempt 保持 `UNKNOWN`，用于忠实保留当时无法
确认提交结果的事实。

受控故障过程中曾出现三次衔接问题：旧 Worker 错误要求空
`platform_sku`、页面 `B级` 与合同 `B` 未规范化、任务从
`manual_review` 恢复到 `success` 的状态转换缺失。三项均已修复并加入定向
测试；失败请求和结果保留在 quarantine 作为审计证据。最终恢复复用了同一个
RECONCILE ID，没有创建第二个对账或第二次写操作。

## 9. 当前交付边界

代码、自动化回归、正常 COMMIT 和受控 UNKNOWN→RECONCILE 证据均已完成。
本文仍不修改任务12状态；审查方需要核对代码、运行归档、SQLite 回读和本文
列出的哈希后，再决定是否验收任务12。

## 10. 复审第二轮接续修正

本节接续记录复审基线 `9e35c7f` 之后的修正，不覆盖上文，也不修改任务12状态。

- Worker 最外层异常改为读取并严格绑定 v4 phase；从
  `batch_result_snapshot` 和 `item_phase` 恢复逐项事实。phase 缺失、损坏或
  绑定无效时 fail-closed 为 `UNKNOWN`，不再假设 `NOT_STARTED`。
- Watchdog 不再因为 `phase=RESULT_WRITTEN` 永久跳过。结果文件不存在时，以
  SQLite `shadowbot_commit_result_receipts` 是否已接受为终止依据；无回执则
  从 request + phase 生成恢复结果。
- v4 结果隔离按 `batch_id` 冻结全部逐项 attempt、operation、任务和写锁，不再
  错用不存在的批次级 attempt。
- `VERIFIED + UNKNOWN` 的批次状态统一推导为 `UNKNOWN`。Worker、Watchdog 和
  Importer 共用 `derive_v4_batch_semantics()`；`PARTIAL` 只表示没有 UNKNOWN
  的明确成功/失败混合。
- Importer 强制要求并核对 `status`、`run_success_flag`、
  `business_operation_completed` 和 `side_effect_state`，缺失、类型错误或与
  items 推导冲突的结果一律隔离。
- v4 VERIFIED 价格状态使用逐项 `readback_observed_at`；RECONCILE 使用实际
  `readback_observed_at/observed_at`。没有可靠观察时间时不刷新
  `listing_status` 新鲜度。
- PR 内证据包含正常成功批次和 UNKNOWN→唯一 RECONCILE 批次的脱敏
  request/result/phase/manifest/receipt、执行序号和校验报告；索引与报告由脚本
  生成，CI 重新计算绑定关系。
- 最终源码全量回归：`556 passed, 3 skipped, 97 subtests passed`；系统冒烟
  `16/16`，脱敏证据独立校验通过。

任务14前的调度边界同时冻结如下：

1. 任务12生产入口只能由操作人员明确传入一个或多个 `--task-id`。
2. `pending update_price` 只表示候选任务，不能被调度器自动解释为已授权执行。
3. 统一任务审查、授权状态和无人值守自动发布延期到任务14。
4. 任务14完成并审查前，禁止接入“扫描全部 pending 后自动创建并发布 COMMIT”
   的调度器。
