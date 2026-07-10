# 包装产能计划输入规范

本文档说明 `/business-inputs` 中“包装产能计划”的日常录入规则。

当前阶段仍以 Excel 作为业务输入来源，包装产能计划保存回 `capacity_plans.xlsx`。SQLite 只保存运行态事实，例如任务、复核、通知和脚本运行记录。本阶段不新增 SQLite schema，不迁移 Excel 主数据。

## 1. 业务定位

包装产能计划用于维护某个业务日期的可包装能力。

系统会把产量预测中的 `predicted_harvest_qty` 汇总后，与该业务日期的 `confirmed_packing_capacity_qty` 对比：

- 预测采收量未超过确认包装能力：不生成产能复核 proposal。
- 预测采收量超过确认包装能力：生成 `capacity_warning / labor_required` 复核 proposal。

这表示“预测产量超过包装能力”的预警，不是“真实订单需求超过包装能力”的预警。当前尚无真实订单需求输入，不应新增订单表或发明订单需求字段。

## 2. Excel 字段

当前 `capacity_plans.xlsx` 字段为：

- `trade_date`：业务日期。
- `normal_packing_capacity_qty`：基础包装产能，Web 展示为“基础包装产能”，默认 `250`。
- `temp_worker_capacity_qty`：单人临时工产能，默认 `100`。
- `confirmed_temp_worker_count`：临时工人数，默认 `0`。
- `confirmed_packing_capacity_qty`：确认包装能力，可自动计算，也可人工确认。
- `allocation_rule`：产能分配规则，当前默认 `proportional_by_forecast`。
- `active`：是否启用。
- `note`：备注。

如果 `confirmed_packing_capacity_qty` 为空，系统按以下公式计算：

```text
confirmed_packing_capacity_qty =
  normal_packing_capacity_qty
  + confirmed_temp_worker_count * temp_worker_capacity_qty
```

Web 表单会默认填入自动计算结果，但允许运营人员按实际情况修改确认值。

## 3. Web 表单

`/business-inputs` 已新增“包装产能计划”页签。

页面说明：

> 这里维护每日包装能力。系统会用确认包装能力与预测采收数量对比，判断是否需要产能预警或临时工确认。保存后如需影响脚本状态和复核，请运行对应自动规则评估。

列表展示：

- 业务日期
- 基础包装产能
- 临时工人数
- 单人临时工产能
- 确认包装能力
- 是否启用
- 备注
- 编辑

## 4. 校验规则

保存包装产能计划时必须满足：

- `trade_date` 不能为空，且必须是合法日期。
- `normal_packing_capacity_qty` 必须是整数且大于或等于 `0`。
- `confirmed_temp_worker_count` 必须是整数且大于或等于 `0`。
- `temp_worker_capacity_qty` 必须是整数且大于或等于 `0`。
- `confirmed_packing_capacity_qty` 必须是整数且大于或等于 `0`。
- `active` 必须是“是 / 否”或后端认可的布尔值。
- 同一 `trade_date` 不允许存在多条启用的包装产能计划。

错误提示面向运营人员，不展示 Python traceback。

## 5. 与 CapacityRuleEvaluator 的关系

`CapacityRuleEvaluator` 会读取：

- `harvest_forecasts.xlsx`
- `capacity_plans.xlsx`

评估时按 `trade_date` 查找启用的包装产能计划，并使用 `confirmed_packing_capacity_qty` 作为最终判断口径。

如果缺少产量预测或缺少对应业务日期的启用产能计划，evaluator 会生成 skipped/warning item，不写业务任务、复核或通知，也不会导致 Web 500。

`dry-run` 只写入 `script_runs / script_run_items` 作为预览记录。

`apply` 才会通过现有 `RuntimeTaskService / ReviewTaskService / NotificationSender` 链路落成运行态任务、复核和通知。

## 6. 当前不做

- 不接真实平台。
- 不接真实 RPA。
- 不接 AI Agent 自动决策。
- 不新增订单表。
- 不新增 SQLite schema。
- 不迁移 Excel 主数据。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不删除旧 `/tables` 高级表格入口。
