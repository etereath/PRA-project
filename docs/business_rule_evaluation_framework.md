# 自动规则评估框架说明

本文档说明当前项目新增的轻量自动规则评估框架。它用于把包装产能、冷库、上下架等自动判断收口为统一流程：读取 Excel 业务输入，生成 proposals，记录脚本运行结果，并在 `apply` 模式下通过现有运行态服务落成任务、复核和通知。

当前框架不是调度系统，不直接操作真实平台，不接真实 RPA，也不接 AI Agent 自动决策。

## 1. 核心定位

自动规则评估框架负责：

- 读取 Excel 业务输入。
- 构建规则评估上下文。
- 调用 evaluator 生成 proposals。
- 在 `dry-run` 模式下记录预览。
- 在 `apply` 模式下通过现有服务生成运行态任务、复核和通知。
- 记录每次脚本运行和每条 proposal 的处理结果。

自动规则评估框架不负责：

- 不直接写业务 `tasks / review_tasks / notification_logs` 表。
- 不直接发送飞书或其他通知。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不迁移 Excel 主数据。
- 不接真实销售平台或真实 RPA。

## 2. SQLite 运行态表

runtime schema 已升级到 v3，新增：

- `script_runs`
- `script_run_items`

这两张表属于运行态事实记录，不是 Excel 主数据迁移。

### script_runs

用于记录一次 evaluator 运行。

关键字段：

- `script_run_id`：可展示、可复制的脚本运行 ID，用于 Web 详情查询和 `script_run_items` 关联。
- `evaluator_id`：规则评估器 ID。
- `evaluator_name`：规则评估器中文名称。
- `description`：脚本说明。
- `run_mode`：`dry-run / apply`。
- `run_status`：`running / success / failed`。
- `trade_date`：交易日。
- `started_at / finished_at`：开始和结束时间。
- `summary_json`：运行摘要。
- `error_message`：错误摘要。
- `created_by`：触发人或脚本来源。

### script_run_items

用于记录本次运行产生的每条 proposal。

关键字段：

- `item_id`
- `script_run_id`
- `proposal_type`
- `dedupe_key`
- `severity`
- `item_status`
- `message`
- `payload_json`
- `decision_trace_json`
- `related_task_id`
- `related_review_task_id`
- `related_notification_id`
- `error_message`
- `created_at`

`item_status=skipped` 时，必须在 `message`、`payload_json` 或 `decision_trace_json` 中记录可读跳过原因。若因为已有业务结果而跳过，应尽量记录 `existing_task_id` 或 `existing_review_task_id`。

## 3. dry-run 与 apply

### dry-run

`dry-run` 是默认模式。

允许写入：

- `script_runs`
- `script_run_items`

禁止写入：

- `tasks`
- `review_tasks`
- `notification_logs`

Web 页面必须清楚显示 `run_mode=dry-run`，避免运营人员误以为已经生成了业务任务。

### apply

`apply` 只有命令行显式传入 `--apply` 才会执行。

`apply` 允许通过现有服务写入：

- `tasks`
- `review_tasks`
- `notification_logs`

写入规则：

- Runtime task 必须通过 `RuntimeTaskService`。
- Review task 必须通过 `ReviewTaskService`。
- Notification 必须通过现有 review notification 链路和 `NotificationSender`。

Web 第一版只读展示脚本运行记录，不提供 `apply` 按钮。若后续增加 Web 运行按钮，第一版最多允许 `dry-run`。

## 4. 幂等与 dedupe_key

每条 proposal 必须有 `dedupe_key`。

`apply` 前必须检查该 `dedupe_key` 是否已生成过对应业务结果。

当前规则：

- 已存在未终态 source task 时，不重复生成。
- 已存在 pending review_task 时，不重复生成。
- 重复命中时，`script_run_item.item_status=skipped`。
- 跳过原因写入 `decision_trace_json.skip_reason=dedupe_key_already_applied` 或等价结构。

这可以避免重复运行脚本后生成重复的 `capacity_warning / labor_required` 复核任务。

## 5. Evaluator 接口

Evaluator 只负责判断，不负责写库。

建议接口：

```python
evaluate(context) -> list[Proposal]
```

Evaluator 元信息包括：

- `evaluator_id`
- `evaluator_name`
- `description`

当前已实现第一版：

- `CapacityRuleEvaluator`
- `ListingRuleEvaluator`
- `ColdStorageEvaluator`
- `PlatformSyncEvaluator`

## 6. Proposal 类型

Proposal 至少包含：

- `proposal_type`
- `dedupe_key`
- `severity`
- `message`
- `payload`
- `decision_trace`

当前支持：

- `RuntimeTaskProposal`
- `ReviewTaskProposal`
- `NotificationProposal`
- `WarningProposal`
- `SkippedProposal`
- `ErrorProposal`

Proposal 只是候选结果，不等于已经创建业务任务。

## 7. CapacityRuleEvaluator

第一版 CapacityRuleEvaluator 的业务语义是：

预测产量超过确认包装能力的预警。

它使用：

- `harvest_forecasts.predicted_harvest_qty`
- `capacity_plans.xlsx` 中对应 `trade_date` 的启用计划
- `capacity_plans.confirmed_packing_capacity_qty`

如果 `confirmed_packing_capacity_qty` 为空，系统按“基础包装产能 + 临时工人数 × 单人临时工产能”计算。Web 表单会默认填入计算值，也允许运营人员人工确认修改。

当前不是“真实订单需求超过包装能力”的预警。在没有真实订单需求输入前，不新增订单表，也不发明订单需求字段。

当预测产量超过确认包装能力时，可能生成：

- `capacity_warning`
- `labor_required`

这些 proposal 在 `apply` 后会先生成非平台 source task，再经由 `ReviewTaskService.create_from_tasks()` 落成 pending review_task，并触发通知日志。

## 8. ColdStorageEvaluator

第一版 ColdStorageEvaluator 的业务语义是：

预计冷库占用超过容量或剩余容量低于阈值的预警。

它使用：

- `cold_storage_status.xlsx` 中对应 `trade_date` 的启用记录
- `cold_storage_status.projected_occupied_qty`
- `cold_storage_status.remaining_capacity_qty`
- `cold_storage_status.total_capacity_qty`
- `cold_storage_status.warning_threshold_qty`

判断规则：

- `projected_occupied_qty > total_capacity_qty`：生成 critical 冷库超容复核 proposal。
- `remaining_capacity_qty <= warning_threshold_qty`：生成 warning 冷库容量预警 proposal。
- 其他情况：生成 skipped 预览项，不写业务任务。

ColdStorageEvaluator 不直接写 `review_tasks`，不直接发送飞书。`apply` 时会先生成非平台 source task，再通过 `ReviewTaskService.create_from_tasks()` 落成 pending review_task，并触发通知日志。

缺少 `cold_storage_status.xlsx` 或缺少对应业务日期启用记录时，会生成 skipped/warning item，不导致 Web 500。

## 9. CLI

新增脚本：

```powershell
python scripts/evaluate_business_rules.py --list
python scripts/evaluate_business_rules.py --evaluator capacity_warning --trade-date 2026-05-05 --dry-run
python scripts/evaluate_business_rules.py --evaluator capacity_warning --trade-date 2026-05-05 --apply
python scripts/evaluate_business_rules.py --evaluator listing_rules --trade-date 2026-05-05 --dry-run --platform 蚂蚁
python scripts/evaluate_business_rules.py --evaluator cold_storage --trade-date 2026-05-05 --dry-run
python scripts/evaluate_business_rules.py --evaluator platform_sync --trade-date 2026-05-05 --dry-run --mock-platform-db data/runtime/mock_platform.sqlite3
```

默认是 `dry-run`。没有 `--apply` 不得写入业务 `tasks / review_tasks / notification_logs`。

## 10. Web 展示

任务中心 `/tasks` 增加二级分页：

- `任务状态`
- `脚本状态`

`脚本状态` 对应：

```text
/tasks?task_tab=automation
```

第一版只读展示：

- 脚本运行 ID
- 脚本名称
- 脚本说明
- 最近运行时间
- 运行状态
- 运行模式
- 生成任务数
- 生成复核数
- 生成通知数
- 错误摘要
- 查看详情

不提供 `apply` 按钮。

## 11. PlatformSyncEvaluator

PlatformSyncEvaluator 用于 Mock 平台同步实验室。它读取 Mock 平台状态，并对比 PRA 运行态任务中的期望价格、期望上下架状态与模拟平台实际状态。

业务语义：

- 这是本地测试台同步检查，不是真实平台同步。
- 它不自动修复平台差异。
- 它不会把平台库存写回 PRA 公共库存。
- 它只生成 review proposal，后续仍由 `ReviewTaskService / NotificationSender` 链路处理。

当前差异类型：

- `price_mismatch`：PRA 目标价与 Mock 平台实际价格不一致。
- `listing_status_mismatch`：PRA 目标上下架状态与 Mock 平台实际状态不一致。
- `stock_mismatch`：Mock 平台库存为 0 或负数。
- `platform_sync_warning`：平台商品缺失或其他同步警告。

`apply` 时，runner 会先生成 source task，再经由 `ReviewTaskService.create_from_tasks()` 生成 pending review_task，并触发通知日志。重复 apply 会基于 `dedupe_key` 跳过，不重复生成同一同步复核。

## 12. 安全边界

脚本运行记录、Web 页面和 CLI 输出不得展示：

- secret
- raw token
- `token=`
- 完整 webhook
- 完整 mobile review URL
- 平台账号、密码、token

错误信息应是可读摘要，不向 Web 暴露 Python traceback。

## 13. 后续扩展

后续可在该框架下继续接入：

- 上下架规则 evaluator
- 冷库压力 evaluator
- 更完整的包装产能 evaluator
- 交易窗口约束 evaluator

当前不直接引入 Celery / Prefect。若后续需要定时运行，可考虑 APScheduler；APScheduler 只负责调度，业务规则仍在本项目 evaluator 框架中实现。

## 12. ListingRuleEvaluator

当前已接入保守版 `ListingRuleEvaluator`。

语义：

- 读取 `products.xlsx` 和 `listing_rules.xlsx`。
- 根据品种、等级、平台、库存阈值和上下架策略评估商品。
- 当规则建议下架时，生成 `manual_review` review proposal。

边界：

- 不直接生成真实平台动作。
- 不直接写 `review_tasks`。
- 不直接发送飞书。
- `apply` 时仍通过 source task + `ReviewTaskService.create_from_tasks()` 链路生成复核和通知。
- 重复 apply 基于 `dedupe_key` 跳过，不重复生成同一复核。
