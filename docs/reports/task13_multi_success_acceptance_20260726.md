# 任务13多商品正常上下架验收报告

## 结论

多商品正常上架和多商品正常下架均已通过实机验收。

两轮均以一次完整队列处理 `AISHA-E-45-Z`（艾莎 E级）和
`CAPPUCCINO-E-45-Z`（卡布奇诺 E级），按页面轨迹严格串行执行；
每轮 2 个目标全部独立回读为 `VERIFIED`，没有失败、UNKNOWN、部分完成或未尝试项。

本报告冻结既有实机事实，不改变任务13状态。

## 运行身份与计数

| 动作 | 批次 ID | 运行 ID | 目标数 | 成功数 | 其他结果 |
|---|---|---|---:|---:|---:|
| 上架 | `BATCH-T13-OPTIMIZED-SET-ONLINE-20260726-02` | `ATTEMPT-T13-OPTIMIZED-SET-ONLINE-20260726-02` | 2 | 2 | 0 |
| 下架 | `BATCH-T13-OPTIMIZED-SET-OFFLINE-20260726-02` | `ATTEMPT-T13-OPTIMIZED-SET-OFFLINE-20260726-02` | 2 | 2 | 0 |

两轮分别满足计数恒等式：

`verified_count(2) + failed_count(0) + unknown_count(0) + partial_effect_count(0) + not_attempted_count(0) = batch_target_count(2)`

## 逐商品结果

| SKU | 商品 | 上架结果 | 下架结果 |
|---|---|---|---|
| `AISHA-E-45-Z` | 艾莎 E级 | `VERIFIED`，资料保存、最终确认和独立回读完整 | `VERIFIED`，最终确认和独立回读完整 |
| `CAPPUCCINO-E-45-Z` | 卡布奇诺 E级 | `VERIFIED`，资料保存、最终确认和独立回读完整 | `VERIFIED`，最终确认和独立回读完整 |

上架队列对每件商品依次完成资料修改、保存、上架确认和回读；下架队列不修改详情资料，只执行下架确认和回读。数据库保留两批次、四个 operation、逐次 attempt 和最终释放的写锁。

## 证据与复算

- [证据校验报告](../evidence/task13/MULTI-SUCCESS-AISHA-E-CAPPUCCINO-E-20260726/validation_report.json)
- [证据清单](../evidence/task13/MULTI-SUCCESS-AISHA-E-CAPPUCCINO-E-20260726/evidence_manifest.json)
- [数据库回读](../evidence/task13/MULTI-SUCCESS-AISHA-E-CAPPUCCINO-E-20260726/database_backread.sanitized.json)
- [Task13 证据索引](../evidence/task13/index.md)

CI 使用 `scripts/verify_task13_multi_success_evidence.py` 离线复算两轮合同绑定、
严格串行轨迹、逐商品回读、数据库账本、回执/ACK 和脱敏边界。
