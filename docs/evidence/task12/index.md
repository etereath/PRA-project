# 任务12脱敏实机证据

> 本索引由 `scripts/export_task12_sanitized_evidence.py` 自动生成；CI 使用 `scripts/verify_task12_sanitized_evidence.py` 复算合同、哈希绑定、计数恒等式和 UNKNOWN→RECONCILE 关系。

| 运行 ID | 批次 | 状态 | 项目计数 | 对账 |
|---|---|---|---:|---|
| [ATTEMPT-52c584afca044d79](ATTEMPT-52c584afca044d79/validation_report.json) | `BATCH-T12-REMEDIATION-COMMIT-20260723-01` | `VERIFIED` | 4 | — |
| [ATTEMPT-0f30900b398045cc](ATTEMPT-0f30900b398045cc/validation_report.json) | `BATCH-T12-CONTROLLED-UNKNOWN-20260723-02` | `UNKNOWN` | 1 | `RECONCILE-046a063ae885fcb4f352` → `VERIFIED` |

脱敏只替换本机/共享路径和 Worker 设备标识；商品身份、任务 ID、价格、时间、manifest、instruction hash、逐项状态与执行序号均保留。
