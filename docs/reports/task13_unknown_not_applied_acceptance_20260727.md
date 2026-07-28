# 任务13 UNKNOWN→RECONCILE→NOT_APPLIED 验收报告

## 结论

本轮受控实机验收通过。

目标商品是 `AISHA-B-60-Z`（艾莎 B级），动作是 `set_offline`。COMMIT
已经点击最终下架确认，随后按开发测试配置中断结果回读，因此系统正确记录
`UNKNOWN / NEEDS_RECONCILIATION`，没有猜测下架是否成功。经用户预先明确授权，
在导入 UNKNOWN 结果之前人工把该商品恢复为上架状态；Result Importer 随后导入
UNKNOWN 并自动创建唯一的只读 RECONCILE。RECONCILE 独立回读确认商品仍在
“上架中”，最终归并为 `NOT_APPLIED`，释放写锁并结束人工审查。

这里的 `NOT_APPLIED` 表示“对账时目标下架状态没有保留”。由于 UNKNOWN 与
RECONCILE 之间发生了已授权的外部人工恢复，它不能证明原始下架点击从未短暂生效。
这正是本轮要覆盖的安全语义：当最终平台事实不满足任务目标时，系统必须把任务记为
失败，而不能把曾经发生过一次点击误报为成功。

本报告冻结实机事实和可复算证据，不修改任务13的项目状态；任务状态继续等待审查方
确认。

## 运行身份

| 项目 | 值 |
|---|---|
| 批次 ID | `BATCH-T13-UNKNOWN-NOT-APPLIED-20260727-01` |
| 商品 SKU | `AISHA-B-60-Z` |
| operation ID | `OP-9963b92d45766829c2947ad5` |
| COMMIT 运行 ID | `ATTEMPT-T13-UNKNOWN-NOT-APPLIED-20260727-01` |
| COMMIT 逐商品 attempt | `ATTEMPT-c700f86bbd6df3b34e631c4b` |
| COMMIT result ID | `RESULT-38cb43eadad7a6c14b48c29c` |
| RECONCILE 运行 ID | `RECONCILE-bcd7f9f2293440cf2d38fef9` |
| RECONCILE 逐商品 attempt | `ATTEMPT-969a9b17d87e8a929f18bde6` |
| RECONCILE result ID | `RESULT-9041763b00d6b275fb77bb73` |

## 执行和人工恢复

COMMIT 前读取到：

- 商品名称与等级：艾莎 B级；
- 价格：`10.50`；
- 库存：`1`；
- 页面事实：唯一存在于“上架中”。

最终下架确认于 `2026-07-27T10:33:31+00:00` 点击。受控故障发生在点击后、
回读前，结果为：

- 批次状态：`UNKNOWN`；
- 逐商品 operation：`NEEDS_RECONCILIATION`；
- 上下架副作用：`UNKNOWN`；
- 错误码：`CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN`；
- 最终确认点击：`true`；
- 写锁：继续保留。

随后按本轮用户授权人工恢复同一商品上架。恢复时再次核对商品是
`AISHA-B-60-Z`，没有混淆为“艾莎（10枝）”；价格仍为 `10.50`，库存仍为
`1`。恢复后该商品唯一出现在“上架中”。

首次投递曾因运行态测试开关仍为关闭状态，在领取前被 Worker 以
`UNSAFE_TEST_PARAMETER_REJECTED` 隔离。该次拒绝发生在任何平台点击之前。
之后按受控测试流程临时开启故障注入、重启 Worker，并复用同一已授权批次和同一
请求哈希执行；完成后开关已恢复为 `false`，长期 Worker 已重新启动。

## 唯一 RECONCILE 结果

Importer 导入 UNKNOWN 后只创建了一次 RECONCILE。该次请求：

- 只读取页面；
- `action_confirm_clicked=false`；
- `action_clicked_at=null`；
- `detail_save_clicked=false`；
- 没有执行保存、上架、下架或最终确认。

在 `2026-07-27T10:35:35+00:00`，RECONCILE 独立回读到价格 `10.50`、
库存 `1`，且目标商品仍在“上架中”，因此最终结果为：

- 批次：`FAILED`；
- 逐商品 operation：`NOT_APPLIED`；
- 源任务：`failed`；
- 写锁：`RELEASED`；
- 人工 Review：由 `system:listing_reconcile` 自动取消；
- attempt 数量：1 次 COMMIT + 1 次 RECONCILE。

最终计数恒等式为：

`verified_count(0) + failed_count(1) + unknown_count(0) + partial_effect_count(0) + not_attempted_count(0) = batch_target_count(1)`

## 证据与复算

- [证据校验报告](../evidence/task13/UNKNOWN-NOT-APPLIED-AISHA-B-60-Z-20260727/validation_report.json)
- [证据清单](../evidence/task13/UNKNOWN-NOT-APPLIED-AISHA-B-60-Z-20260727/evidence_manifest.json)
- [数据库回读](../evidence/task13/UNKNOWN-NOT-APPLIED-AISHA-B-60-Z-20260727/database_backread.sanitized.json)
- [Task13 证据索引](../evidence/task13/index.md)

证据包保留商品身份、价格、库存、批次/operation/attempt/result ID、点击边界、
时间、数据库账本和计数恒等式，只替换本机路径和 Worker 设备标识。CI 使用
`scripts/verify_task13_unknown_not_applied_evidence.py` 离线复算合同、哈希、
回执、ACK、只读 RECONCILE、最终数据库状态、写锁释放和脱敏边界。

## 收尾状态

- 故障注入开关：`false`；
- Worker：新鲜 `RUNNING`；
- Queue Service：运行中；
- `inbox/working/results`：无活动文件；
- `stop.signal`：不存在；
- 任务13状态：未修改，等待审查。
