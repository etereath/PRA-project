# 任务13 Web 与运营投影验收报告

## 结论

T13-7 的任务详情只读投影已完成。

任务中心继续展示任务本身的动作类型、目标和状态；选中一个已进入 v5 上下架流水线的
任务后，新增“上下架运行投影”面板，直接从 Runtime Schema v13 的批次、逐商品项、
operation 和 attempt 表读取事实。

本改动只增加运营可见性，不增加自动审批、自动调度、COMMIT 发布或绕过 Review 的入口。

## 展示字段

| 计划字段 | 当前投影 |
|---|---|
| 动作类型 | `action_type` |
| 预期旧状态 | `expected_old_status` |
| 目标状态 | `target_status` |
| 实际回读状态 | 由逐商品 `operation_result` 与旧/目标状态保守投影 |
| 批次 ID | `batch_id` |
| operation ID | `operation_id` |
| execution attempt | 同一 operation 的全部 COMMIT/RECONCILE attempt |
| UNKNOWN | 批次和 operation 状态直接展示 |
| RECONCILE 状态 | 单独列出 RECONCILE attempt ID 与状态 |
| observed_at | `readback_observed_at` |
| 错误代码 | 逐商品 `error_code` |
| 人工处理入口 | 继续使用任务关联的 Review 面板，不新增隐式写操作 |

实际状态采用保守规则：

- `VERIFIED`：显示目标状态；
- `NOT_APPLIED`：显示预期旧状态；
- `NEEDS_RECONCILIATION`：显示 `UNKNOWN`；
- 没有足够回读事实：显示 `-`。

## 验证

- `test_tasks_page_displays_task13_listing_action_projection` 验证 UNKNOWN、
  RECONCILE、批次/operation/attempt、observed_at 和错误码均能显示。
- Runtime Schema v13 和上下架流水线定向回归共 `14 passed`。
- 使用当前真实运行数据库回读既有任务，成功取得
  `BATCH-T13-PREFLIGHT-MISMATCH-SETUP-ONLINE-20260727-01` 和
  `BATCH-T13-PREFLIGHT-ZERO-WRITE-20260727-01` 的 v5 投影。

旧数据库缺少 v13 表时，页面保持兼容并隐藏该面板，不会因只读投影导致任务中心不可用。
