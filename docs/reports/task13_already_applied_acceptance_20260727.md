# 任务13 ALREADY_APPLIED 幂等验收报告

## 结论

本轮实机验收通过。

目标商品 `AISHA-B-60-Z`（艾莎 B级）在执行前已经处于下架状态。系统接收一条真实 `set_offline` COMMIT 后，独立扫描“上架中”页面，确认目标不存在，返回 `ALREADY_APPLIED`。整个批次没有执行资料保存、下架按钮点击或最终确认点击，也没有创建人工 Review。

本报告只记录验收事实，不修改任务13总状态。

## 运行身份

| 项目 | 值 |
|---|---|
| 批次 ID | `BATCH-T13-ALREADY-APPLIED-20260727-01` |
| 源任务 ID | `cd0546933d96` |
| 运行 ID | `ATTEMPT-f2a0b9089ed84b7b` |
| 逐商品 attempt | `ATTEMPT-6879d487c2143fecb6650575` |
| operation ID | `OP-f832a7e45a9e092f5dac8078` |
| result ID | `RESULT-7bfd59f8addfe713af030cf1` |

## 逐商品结果

| SKU | 动作 | 页面结论 | 业务结果 | 资料保存 | 下架/最终确认 |
|---|---|---|---|---:|---:|
| `AISHA-B-60-Z` | `set_offline` | 完整扫描后不在“上架中” | `ALREADY_APPLIED` | 0 | 0 |

原始结果同时满足：

- `business_operation_completed=false`
- `side_effect_state=NOT_STARTED`
- `detail_effect_state=NOT_APPLIED`
- `listing_effect_state=NOT_APPLIED`
- `detail_save_clicked=false`
- `detail_save_clicked_at=null`
- `action_confirm_clicked=false`
- `action_clicked_at=null`
- `attempted_count=0`
- `already_applied_count=1`
- `verified_count=1`

## 数据库回读

Importer 导入后：

- 源任务：`success`
- 批次：`VERIFIED`
- operation：`VERIFIED`
- attempt：`VERIFIED / NOT_APPLIED`
- 写锁：`RELEASED`
- open Review：0

数据库最终 operation 使用统一成功投影 `VERIFIED`；逐 attempt 的 `raw_output_json` 继续保留原始业务结果 `ALREADY_APPLIED`，因此不会丢失“未执行写操作”的事实。

批次计数满足：

`verified_count(1) + failed_count(0) + unknown_count(0) + partial_effect_count(0) + not_attempted_count(0) = batch_target_count(1)`

## 耗时

本轮 Worker 总耗时约 `73.419 秒`：

- 窗口准备：`1.837 秒`
- 登录检查：`0.471 秒`
- 商品页刷新及空列表就绪判断：`71.056 秒`
- 上架中目标扫描：`0.040 秒`

实际商品判断只用了 40 毫秒，主要时间消耗在空“上架中”列表的刷新就绪等待。该性能问题不影响 0 点击幂等结论，但可以作为后续优化项。

## 证据

- [证据校验报告](../evidence/task13/ALREADY-APPLIED-AISHA-B-60-Z-20260727/validation_report.json)
- [证据清单](../evidence/task13/ALREADY-APPLIED-AISHA-B-60-Z-20260727/evidence_manifest.json)
- [数据库回读](../evidence/task13/ALREADY-APPLIED-AISHA-B-60-Z-20260727/database_backread.sanitized.json)
- [Task13 证据索引](../evidence/task13/index.md)

CI 使用 `scripts/verify_task13_already_applied_evidence.py` 复算合同与哈希绑定、receipt/ACK、0 写点击、最终数据库账本、写锁释放和脱敏边界。

## 后续事项

下一组优先验收项为：

1. 整批预检异常时 0 次资料保存、0 次最终确认。
2. UNKNOWN→RECONCILE→`NOT_APPLIED`。
3. UPDATE_PRICE 与上下架动作共享写锁冲突。
4. phase/result 恢复路径和 Web 投影审查。
