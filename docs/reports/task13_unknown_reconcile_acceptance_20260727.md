# 任务13受控 UNKNOWN→自动 RECONCILE 验收报告

## 结论

本轮受控故障验收通过。

目标商品为 `AISHA-B-60-Z`（艾莎 B级），执行动作是 `set_offline`。COMMIT 在最终确认按钮已经点击后，按测试配置主动中断回读，因此正确进入 `UNKNOWN / NEEDS_RECONCILIATION`；Result Importer 随后自动创建且仅创建一次只读 RECONCILE。RECONCILE 没有执行保存、上架、下架或最终确认点击，通过独立页面回读确认原下架动作已经生效，最终把任务、operation 和批次归并为成功，并释放写锁。

本报告只冻结已完成的实机证据，不把任务13状态改为完成。

## 运行身份

| 项目 | 值 |
|---|---|
| 批次 ID | `BATCH-T13-AUTO-RECONCILE-CONTROLLED-UNKNOWN-20260727-03` |
| 商品 SKU | `AISHA-B-60-Z` |
| operation ID | `OP-8d6fbb4aad301d6979fc864d` |
| COMMIT 运行 ID | `ATTEMPT-664d3064fd864dcc` |
| COMMIT 逐商品 attempt | `ATTEMPT-49a4e1b1e6d18b4fe9cc88ca` |
| COMMIT result ID | `RESULT-8bbd70470b553034b30c7bb9` |
| RECONCILE 运行 ID | `RECONCILE-ab020713eb4633c24441f141` |
| RECONCILE 逐商品 attempt | `ATTEMPT-452d0d19de48a2579c13a17f` |
| RECONCILE result ID | `RESULT-3e84f03182960053ceb93250` |

## 执行结果

COMMIT 前读取到该商品价格为 `10.50`、库存为 `20`。最终下架确认已经点击，点击时间为 `2026-07-26T21:06:28+00:00`。由于受控故障发生在点击之后、回读之前，系统没有猜测平台结果，而是记录：

- 批次状态：`UNKNOWN`
- 逐商品结果：`NEEDS_RECONCILIATION`
- 上下架副作用：`UNKNOWN`
- 错误码：`CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN`
- 写锁：保留，禁止重复 COMMIT

Importer 导入 UNKNOWN 结果后自动创建唯一 RECONCILE。该次对账只读取“上架中”页面，没有执行任何写点击，并在 `2026-07-26T21:07:45+00:00` 确认目标商品已经不在上架列表。最终结果为：

- 批次：`VERIFIED`
- 逐商品 operation：`VERIFIED`
- 源任务：`success`
- 写锁：`RELEASED`
- 人工 Review：由 `system:listing_reconcile` 取消
- attempt 数量：1 次 COMMIT + 1 次 RECONCILE

最终批次计数满足：

`verified_count(1) + failed_count(0) + unknown_count(0) + partial_effect_count(0) + not_attempted_count(0) = batch_target_count(1)`

## 证据与复算

完整脱敏证据位于：

- [证据校验报告](../evidence/task13/UNKNOWN-RECONCILE-AISHA-B-60-Z-20260727/validation_report.json)
- [证据清单](../evidence/task13/UNKNOWN-RECONCILE-AISHA-B-60-Z-20260727/evidence_manifest.json)
- [数据库回读](../evidence/task13/UNKNOWN-RECONCILE-AISHA-B-60-Z-20260727/database_backread.sanitized.json)
- [Task13 证据索引](../evidence/task13/index.md)

证据包保留商品身份、价格、库存、批次/operation/attempt/result ID、时间、最终确认点击边界、数据库账本和计数；仅替换本机路径与 Worker 设备标识。CI 使用 `scripts/verify_task13_unknown_reconcile_evidence.py` 离线复算合同、哈希、回执、ACK、只读对账、最终数据库状态和脱敏边界。

## 本轮同时修复的证据兼容问题

旧的单商品上下架往返证据采用早期 v5 operation ID 公式。当前运行合同已经使用带 `batch_id` 的新公式，因此旧证据在现行严格校验器下会失败。

现已增加仅由历史证据校验器显式启用的兼容路径：

- 正式运行请求仍默认只接受当前 operation ID 公式；
- 旧公式必须精确满足早期的 `source_task_id + item_payload_sha256` 计算结果；
- 只有单商品往返证据校验器传入兼容开关；
- 新 UNKNOWN→RECONCILE 证据继续使用当前严格公式。

这项修改恢复了旧证据的可复算性，没有放宽生产请求门禁。

## 尚未完成

任务13仍需继续覆盖以下验收或审查项：

1. UNKNOWN→RECONCILE→`NOT_APPLIED` 样本。
2. 最终文档、CI 和人工复审。

`ALREADY_APPLIED` 的 0 写点击实机样本已在后续批次
`BATCH-T13-ALREADY-APPLIED-20260727-01` 完成，详见
[任务13 ALREADY_APPLIED 幂等验收报告](task13_already_applied_acceptance_20260727.md)。

整批预检异常零写、多商品成功、严格串行 UNKNOWN、共享写锁、phase/result
恢复和 Web 投影也已在后续收口，统一状态见
[任务13受控实机验收覆盖状态](task13_acceptance_status_20260727.md)。
