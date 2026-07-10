# 冷库状态输入表单化规则

本文档说明 `/business-inputs` 中“冷库状态”页签的当前实现规则。本文档以当前代码进度为准，用于后续维护和 Code Review。

## 1. 定位

冷库状态用于记录某个业务日期的全场共享冷库容量、当前占用、预计入库、预计出库和预警阈值。

系统会用这些数据判断是否需要生成冷库预警或人工复核。它不是订单需求表，也不代表真实平台销售数据。

当前仍然：

- 保存回 `cold_storage_status.xlsx`。
- 不迁移 Excel 主数据。
- 不新增 SQLite schema。
- 不接真实平台。
- 不接真实 RPA。
- 不接 AI Agent 自动决策。

## 2. Excel 字段

当前 `cold_storage_status.xlsx` 字段为：

- `trade_date`：业务日期。
- `total_capacity_qty`：冷库总容量，默认 500。
- `current_occupied_qty`：当前占用量。
- `expected_inbound_qty`：预计入库量。
- `expected_outbound_qty`：预计出库量。
- `warning_threshold_qty`：预警阈值，默认 50。
- `projected_occupied_qty`：预计占用量。
- `remaining_capacity_qty`：剩余容量。
- `active`：是否启用。
- `note`：备注。

旧字段 `cold_storage_total_capacity_qty / cold_storage_current_qty` 仍可被读取为兼容输入，但保存时会写入当前新字段结构。

## 3. 计算规则

默认计算：

```text
projected_occupied_qty =
  current_occupied_qty + expected_inbound_qty - expected_outbound_qty

remaining_capacity_qty =
  total_capacity_qty - projected_occupied_qty
```

页面会自动填入预计占用量和剩余容量，但允许运营人员人工确认修改。

## 4. Web 表单

`/business-inputs` 已新增“冷库状态”页签。

页面展示：

- 业务日期
- 冷库总容量
- 当前占用量
- 预计入库量
- 预计出库量
- 预计占用量
- 剩余容量
- 预警阈值
- 是否启用
- 备注
- 编辑入口

页面说明：

“这里维护每日冷库占用情况。系统会用预计占用量和剩余容量判断是否需要冷库预警或人工复核。保存后如需影响脚本状态和复核，请运行对应自动规则评估。”

旧 `/tables` 仍保留为高级兼容入口。

## 5. 校验规则

保存冷库状态时：

- `trade_date` 不能为空，且必须是合法日期。
- `total_capacity_qty` 必须是整数且大于 0。
- `current_occupied_qty` 必须是整数且大于或等于 0。
- `expected_inbound_qty` 必须是整数且大于或等于 0。
- `expected_outbound_qty` 必须是整数且大于或等于 0。
- `warning_threshold_qty` 必须是整数且大于或等于 0。
- `projected_occupied_qty` 为空时按公式计算；不为空时必须是整数且大于或等于 0。
- `remaining_capacity_qty` 为空时按公式计算；不为空时必须是整数。
- `active` 必须是“是/否”或后端认可的布尔值。
- 同一 `trade_date` 不允许存在多条启用的冷库状态。

错误提示面向运营人员，不展示 Python traceback。

## 6. ColdStorageEvaluator 衔接

`ColdStorageEvaluator` 的业务语义是：

使用 `cold_storage_status.xlsx` 中对应业务日期的启用记录，判断预计冷库占用是否超过容量或接近容量。

判断口径：

- 如果 `projected_occupied_qty > total_capacity_qty`，生成 critical 冷库超容复核 proposal。
- 如果 `remaining_capacity_qty <= warning_threshold_qty`，生成 warning 冷库容量预警 proposal。
- 否则生成 skipped 预览项，不写业务任务。

`apply` 时，proposal 会先生成非平台 source task，再通过 `ReviewTaskService.create_from_tasks()` 落成 pending review_task，并触发通知日志。

`dry-run` 只写 `script_runs / script_run_items`，不会写 `tasks / review_tasks / notification_logs`。

重复 `apply` 会基于 `dedupe_key` 跳过，不重复生成同一冷库预警复核。

## 7. 保持边界

本阶段不做：

- 不新增冷库流水系统。
- 不新增订单需求表。
- 不直接阻断任务生成。
- 不直接发送飞书。
- 不直接写 `review_tasks`。
- 不接真实平台或真实 RPA。
- 不迁移 Excel 主数据到 SQLite。
