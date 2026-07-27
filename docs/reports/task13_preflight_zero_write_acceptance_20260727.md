# 任务13批次预检不一致与整批零写验收报告

## 结论

本轮实机验收通过。

一个两商品 `SET_ONLINE` 批次在首次平台写入前完成全目标预扫描。其中艾莎 A级符合合同，艾莎 E级已经在线且页面价格、库存与合同目标不一致。系统因此阻断整个批次，没有保存任何商品资料，也没有点击上架按钮或最终确认按钮。

本报告只记录验收事实，不修改任务13总状态。

## 运行身份

| 项目 | 值 |
|---|---|
| 批次 ID | `BATCH-T13-PREFLIGHT-ZERO-WRITE-20260727-01` |
| 运行 ID | `ATTEMPT-591f3a642e2b43f9` |
| Result ID | `RESULT-bb11f27201710cbcc5824bef` |
| 正常商品 operation | `OP-eab422965e44c059359a0cb2` |
| 不一致商品 operation | `OP-9799858044b090074a889d8a` |

## 逐商品结果

| SKU | 页面预检 | 合同目标 | 结果 | 资料保存 | 上架/最终确认 |
|---|---|---|---|---:|---:|
| `AISHA-A-70-Z` | 待上架，18.00，库存28 | 上架，18.00，库存28 | `NOT_ATTEMPTED` | 0 | 0 |
| `AISHA-E-45-Z` | 已上架，7.50，库存2 | 上架，7.00，库存1 | `NOT_APPLIED / LISTING_DATA_MISMATCH` | 0 | 0 |

批次结果为 `FAILED`，但这是安全门禁的预期结果，不表示发生了平台写入失败。结果同时满足：

- `business_operation_completed=false`
- `side_effect_state=NOT_APPLIED`
- `attempted_count=0`
- `failed_count=1`
- `not_attempted_count=1`
- `unknown_count=0`
- `partial_effect_count=0`

计数恒等式为：

`verified_count(0) + failed_count(1) + unknown_count(0) + partial_effect_count(0) + not_attempted_count(1) = batch_target_count(2)`

## 数据库回读

Importer 导入后：

- 批次：`FAILED`
- 正常商品任务：恢复为 `pending`
- 正常商品 attempt：`NOT_ATTEMPTED / NOT_STARTED`
- 不一致商品任务：`failed`
- 不一致商品 operation：`FAILED / NOT_APPLIED`
- 不一致商品 attempt：`FAILED / NOT_APPLIED`
- 两件商品写锁：均为 `RELEASED`
- open Review：0

数据库逐商品账本中的 `detail_save_clicked_at` 和 `action_clicked_at` 均为空。

## 人工运营边界

平台继续由人工正常运营，因此两个独立运行之间出现商品状态、价格或库存变化属于允许的外部事实变化。本轮艾莎 E级与原开发预期不同，不应被解释为自动化故障。

正确处理是：

1. 旧快照和未发布旧提案失效；
2. COMMIT 仍执行当次全目标预扫描；
3. 不一致时在首次写入前整批零写停止；
4. 只有点击最终确认后无法回读时才进入 `UNKNOWN` 并保留写锁。

本轮后续完整扫描 `ATTEMPT-T13-POST-PREFLIGHT-RESCAN-20260727-01` 已确认当前平台事实，并取代旧清理提案的状态基线。

## 证据

- [证据校验报告](../evidence/task13/PREFLIGHT-ZERO-WRITE-AISHA-A-E-20260727/validation_report.json)
- [证据清单](../evidence/task13/PREFLIGHT-ZERO-WRITE-AISHA-A-E-20260727/evidence_manifest.json)
- [数据库回读](../evidence/task13/PREFLIGHT-ZERO-WRITE-AISHA-A-E-20260727/database_backread.sanitized.json)
- [后续完整状态扫描](../evidence/task13/ATTEMPT-T13-POST-PREFLIGHT-RESCAN-20260727-01/validation_report.json)
- [Task13 证据索引](../evidence/task13/index.md)

CI 使用 `scripts/verify_task13_preflight_zero_write_evidence.py` 独立复算 v5 合同、request/result/phase/receipt/ACK 绑定、全批次门禁、零点击、数据库账本、写锁释放和脱敏边界。
