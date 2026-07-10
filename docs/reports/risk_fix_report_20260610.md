# 高中低风险问题修复报告

- 报告日期：2026-06-10
- 修复范围：Code Review 后按高 / 中 / 低风险顺序修复，并补充回归测试
- 当前结论：已完成修复，系统冒烟测试、全量单元测试和端到端流程测试均通过

## 1. 修复摘要

本轮重点不是新增业务能力，而是加固已经跑通的运行态闭环，避免重复生成、越权访问、状态历史不一致、通知失败被隐藏、规则冲突被静默吞掉等问题。

已确认修复：

1. 重复生成运行态任务时，不再基于未插入的重复任务创建 `review_tasks`。
2. Mobile Review 公网入口不再接受外部传入的 `runtime_db` 参数，避免通过链接切换到任意 SQLite 文件。
3. 任务状态变更与 `task_status_history` 写入改为同一 repository 写操作边界。
4. Web / Mobile 复核推动源任务状态时，复核结果与源任务状态历史写入保持同一写操作边界。
5. 自动规则评估 apply 时，通知失败会进入 summary / warning / item error，而不是被成功状态掩盖。
6. 上下架规则同优先级、同具体度且动作冲突时，不再静默随机选择。
7. `PlatformSyncEvaluator` 在指定平台时只扫描对应平台的运行态任务，不再误扫其他平台。
8. `expire-review-tasks` 的 dry-run 输出增加明确提示：不会修改复核任务或源任务状态。

## 2. 高风险修复

### 2.1 重复任务导致孤立复核

问题：

运行态任务生成时，`tasks.dedupe_key` 会阻止重复任务插入，但旧流程仍可能拿“计划生成的全部任务”去创建复核，导致重复运行后产生指向未插入任务的 `review_tasks`。

修复：

- `RuntimeTaskService` 增加 `create_tasks_returning_inserted(...)`，返回本次真正插入的任务。
- `generate_runtime_tasks_from_sources(...)` 只用本次真正插入的任务创建复核。
- 补充测试：重复生成时 `inserted_tasks_count=0`，`inserted_review_tasks_count=0`，已有复核均能找到真实 `source_task_id`。

验证：

- `tests/test_workflow.py::WorkflowTests.test_runtime_generation_does_not_create_reviews_for_duplicate_tasks`

### 2.2 Mobile Review 任意 runtime_db 风险

问题：

Mobile Review 路由曾接受 query/body 中的 `runtime_db`，公网链接场景下可能被构造为访问非预期 SQLite 文件。

修复：

- `/mobile/review/{review_task_id}` 和 `/mobile/review/{review_task_id}/resolve` 固定使用服务端 `DEFAULT_RUNTIME_DB`。
- Mobile Review 表单和成功跳转不再携带 `runtime_db`。
- 补充测试验证带攻击性 `runtime_db` 参数时不会创建或访问该数据库。

验证：

- `tests/test_web.py::WebTests.test_mobile_review_ignores_runtime_db_query_parameter`
- `tests/test_web.py::WebTests.test_mobile_review_get_records_detail_access_only`
- `tests/test_web.py::WebTests.test_mobile_review_post_resolves_and_prevents_reuse`

## 3. 中风险修复

### 3.1 任务状态与历史记录一致性

问题：

任务状态更新和状态历史写入分两步执行时，如果中间失败，可能出现任务状态已变但历史缺失。

修复：

- repository 层新增 `update_task_status_with_history(...)`。
- `RuntimeTaskService.change_status(...)` 统一调用该方法。

验证：

- 系统冒烟测试覆盖 `task_status_history` 写入。
- 全量单元测试通过。

### 3.2 复核处理与源任务状态一致性

问题：

复核结果更新和源任务状态推动分开写入时，可能出现复核已完成但源任务状态或历史未同步。

修复：

- repository 层新增 `update_review_task_with_optional_task_status(...)`。
- `ReviewTaskService.resolve_review_task(...)` 在需要推动源任务时，统一写入复核结果、源任务状态和状态历史。

验证：

- Web Session 复核、Mobile Review、系统冒烟测试均覆盖源任务推动和历史记录。

### 3.3 通知失败被隐藏

问题：

自动规则评估 apply 后，即使 review 初始通知失败，脚本运行 summary 仍可能表现为成功，运营人员不易发现通知异常。

修复：

- `ReviewTaskService.create_from_tasks(...)` 会收集通知创建或发送失败。
- `BusinessRuleRunner` 在 apply summary / warning / script_run_item 中暴露 `notification_errors`。

验证：

- `tests/test_business_rule_evaluation.py::BusinessRuleEvaluationTests.test_apply_exposes_notification_failures_in_summary`

### 3.4 上下架规则冲突被静默处理

问题：

同一候选商品命中多条同优先级、同具体度但动作不同的上下架规则时，旧逻辑可能静默选择某一条。

修复：

- `ListingService` 增加同 rank 冲突检测。
- 冲突时抛出可读校验错误，不生成不确定任务。

验证：

- `tests/test_listing_and_tasks.py::ListingAndTaskTests.test_listing_same_rank_conflict_is_not_silently_swallowed`

## 4. 低风险修复

### 4.1 PlatformSyncEvaluator 平台范围

问题：

指定默认平台时，sync evaluator 可能扫描到其他平台任务，导致跨平台误判。

修复：

- `PlatformSyncEvaluator` 只处理 `task.platform_name == context.platform_name` 的任务。
- Mock 平台状态读取也按当前平台过滤。

验证：

- `tests/test_mock_platform_sync_lab.py::MockPlatformSyncLabTests.test_platform_sync_default_platform_does_not_scan_other_platform_tasks`

### 4.2 expire-review-tasks dry-run 文案

问题：

dry-run 虽然不会写库，但 CLI 输出不够醒目，容易被误认为已执行过期处理。

修复：

- `expire-review-tasks` dry-run 输出增加：
  `dry-run preview only: no review task or source task status was changed.`

验证：

- `tests/test_cli.py::CliTests.test_expire_review_tasks_cli_supports_dry_run_and_apply`

### 4.3 Mobile token 重复提交保护

现状确认：

- GET 详情页只更新 `last_used_at`。
- POST resolve 成功后写入 `used_at`。
- `used_at` 非空、复核非 pending、Web 已处理后，手机端再次提交均会失败。

验证：

- Mobile Review 相关单元测试与系统冒烟测试均覆盖。

## 5. 验证结果

已执行：

```powershell
python scripts/run_system_smoke_tests.py
python -m unittest discover -s tests
python scripts/run_e2e_flow_tests.py --notification-mode env
```

结果：

- 系统冒烟测试：15 项通过，0 项失败。
- 全量单元测试：153 项通过，0 项失败。
- 端到端流程测试：6 个场景通过，0 个失败。

最新端到端报告：

- [e2e_flow_report_20260610_055957.md](e2e_flow_report_20260610_055957.md)

## 6. 文档同步检查

本轮检查了 `docs/` 中与运行态、Mobile Review、规则评估、Mock 平台、系统检查相关的文档。

已同步：

- 修正 `docs/web_frontend_refresh_plan.md` 中 `/system` schema 检查要求，从 `latest_schema_version >= 2` 更新为当前 `latest_schema_version >= 3`。
- 补充 `script_runs / script_run_items` 计数说明。
- 更新文档索引，加入本修复报告。
- 更新当前状态文档，说明 Code Review 后风险修复已完成。

未发现必须立即修改的其他文档项。

## 7. 后续建议

1. 后续每次进入真实平台 / RPA / 新 evaluator 开发前，先运行系统冒烟测试和全量单元测试。
2. 涉及运行态写操作时，继续保持 service 层负责业务约束，repository 层负责原子读写。
3. 涉及公网入口时，避免接受用户传入的本地路径、secret、token 或数据库路径。
4. 涉及通知链路时，失败必须进入 summary / log / warning，不应被正常成功状态掩盖。
