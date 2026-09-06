# 自动规则脚本开发规范

本文档用于指导后续由 Codex 或开发人员新增 evaluator。目标是避免把自动判断写成零散脚本，确保所有规则评估都能被记录、复核、通知和测试。

## 1. 开发前必须阅读

新增 evaluator 前必须先阅读：

- [project_current_status.md](project_current_status.md)
- [business_rule_evaluation_framework.md](business_rule_evaluation_framework.md)
- [sqlite_runtime_persistence_plan.md](sqlite_runtime_persistence_plan.md)
- [business_decision_spec.md](business_decision_spec.md)
- [runtime_environment_variables.md](runtime_environment_variables.md)

如果涉及 Web 展示，还应阅读：

- [web_frontend_refresh_plan.md](web_frontend_refresh_plan.md)
- [web_localization_display_spec.md](web_localization_display_spec.md)

## 2. 必须使用 evaluator / proposal / runner 结构

新增自动规则必须拆成：

- Evaluator：只负责读取 context 并生成 proposal。
- Proposal：表达候选任务、复核、通知、警告、跳过或错误。
- Runner：负责 `dry-run / apply`、运行记录和通过现有 service 落库。

不得把业务判断、数据库写入和通知发送混在一个脚本里。

## 3. 禁止事项

新增 evaluator 禁止：

- 直接写 SQLite 业务表。
- 直接插入 `tasks / review_tasks / notification_logs`。
- 直接发送飞书。
- 绕过 `RuntimeTaskService`。
- 绕过 `ReviewTaskService`。
- 绕过 `NotificationSender`。
- 直接操作真实销售平台或真实 RPA。
- 将 Mock 平台测试状态当作真实平台状态写回业务输入。
- 打印 secret、raw token、完整 webhook、完整 mobile review URL 或 `token=`。
- 暴露 Python traceback 到 Web。
- 在没有文档和测试的情况下改变任务状态流转。

## 4. dry-run 要求

所有 evaluator 必须支持 `dry-run`。

`dry-run` 可以写：

- `script_runs`
- `script_run_items`

`dry-run` 不得写：

- `tasks`
- `review_tasks`
- `notification_logs`

默认运行模式必须是 `dry-run`。

## 5. apply 要求

`apply` 只能由命令行显式触发。

`apply` 必须通过现有 service：

- 创建或去重 runtime task：`RuntimeTaskService`
- 创建 review_task：`ReviewTaskService`
- 触发通知：`NotificationSender` 相关链路

Web 第一版只读展示脚本状态，不提供 `apply` 按钮。

## 6. dedupe_key 要求

每条 proposal 必须有稳定 `dedupe_key`。

建议 `dedupe_key` 至少包含：

- evaluator_id
- trade_date
- scope_type
- scope_key
- action_type 或 review_type

重复 apply 时，如果发现相同 `dedupe_key` 已经生成过业务结果，应跳过，不得重复生成。

跳过时必须记录：

- `item_status=skipped`
- `skip_reason`
- 如可获取，记录 `existing_task_id` 或 `existing_review_task_id`

## 7. decision_trace 要求

每条 proposal 必须写 `decision_trace`。

建议包含：

- 命中的业务输入。
- 触发规则。
- 计算过程。
- 是否需要人工复核。
- 是否写入业务任务。
- 如果跳过，说明跳过原因。

## 8. 错误处理

Evaluator 失败时：

- Runner 应记录 failed run。
- `script_run_items` 或 `script_runs.error_message` 应保存可读错误摘要。
- Web 展示错误摘要，不展示 traceback。

缺少业务输入时，不建议直接崩溃。可以生成 `SkippedProposal` 或 `WarningProposal`，说明缺少哪些输入。

## 9. 测试要求

新增 evaluator 至少覆盖：

- `dry-run` 不写业务表。
- `apply` 才写业务表。
- 重复 apply 不重复生成任务或复核。
- proposal 记录到 `script_run_items`。
- 错误或缺失输入不会导致 Web 500。
- Web `/tasks?task_tab=automation` 不展示敏感信息。
- `python scripts/run_system_smoke_tests.py` 通过。
- `python -m unittest discover -s tests` 通过。

## 10. 新 evaluator 接入流程

建议顺序：

1. 在文档中写清规则语义和边界。
2. 新增 evaluator，确保只返回 proposals。
3. 补充 proposal 的 payload 与 decision_trace。
4. 在 runner 注册 evaluator。
5. 增加 CLI `--list` 可见性。
6. 增加 dry-run 和 apply 测试。
7. 增加 Web 脚本状态展示回归测试。
8. 运行冒烟测试和完整单元测试。

如果 evaluator 涉及平台状态同步，例如 `PlatformSyncEvaluator`，必须额外确认：

- 平台实际状态来源是否为 Mock 平台测试库或经过批准的真实平台适配器。
- 不得把平台库存反向覆盖为 PRA 公共库存。
- 不得自动修复价格或上下架差异；第一版应生成 review proposal。
- 不得绕过 Mock/真实执行器边界直接修改平台状态。

## 11. 当前已接入 evaluator

当前第一版已接入：

- `capacity_warning`
- `listing_rules`
- `cold_storage`
- `platform_sync`

语义：

预测产量超过确认包装能力的预警。

注意：它读取 `harvest_forecasts.xlsx` 与 `capacity_plans.xlsx`，并以 `confirmed_packing_capacity_qty` 作为最终判断口径。它不是订单需求超过包装能力的预警。当前尚无真实订单需求输入，不应为此发明订单表或订单需求字段。

`listing_rules` 语义：

根据商品资料和上下架规则生成需要人工确认的上下架建议。当前第一版只在建议下架时生成 `manual_review` proposal，不直接生成可执行平台动作。

`cold_storage` 语义：

根据 `cold_storage_status.xlsx` 中对应业务日期的启用记录，比较预计占用量、冷库总容量和剩余容量阈值。预计占用量超过总容量时生成 critical 冷库超容复核 proposal；剩余容量低于或等于预警阈值时生成 warning 冷库容量预警 proposal。当前第一版只生成复核建议，不直接阻断任务或执行平台动作。

`platform_sync` 语义：

根据 Mock 平台测试库中的实际状态，对比 PRA 运行态任务中的期望价格和上下架状态。发现价格不一致、上下架不一致、平台库存为 0 或平台商品缺失时生成同步复核 proposal。当前第一版不自动修复平台状态，不回写 PRA 公共库存，也不接真实平台。
