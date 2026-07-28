# 文档索引

本文档是当前项目文档入口。项目说明文本以中文为主，英文键名和代码标识保持原样。

运行态业务数据以 SQLite 为中心，当前结构版本为 v13。v9 使用“平台 + 品种 + 等级”作为 `listing_status` 业务身份，v10 将任务旧价结构化，v11 增加单次请求的 ShadowBot 多商品 COMMIT 批次账本，v12 增加逐商品操作/尝试身份、活动写锁、观察时间和技术回执，v13 增加公共批次注册表、通用上下架 operation、两页快照、页面异常和 v5 动作账本。Excel 继续承担商品、规则等主数据输入，不是运行态任务数据库。

## 当前状态与入口

- [project_current_status.md](project_current_status.md)：当前项目定位、已完成能力、主控流程、安全边界和下一步优先级。
- [reports/task12_final_handoff_20260723.md](reports/task12_final_handoff_20260723.md)：任务12当前实现、正式合同、实机证据、已知限制和审查步骤的最终交接入口。
- [reports/task13_final_handoff_20260727.md](reports/task13_final_handoff_20260727.md)：任务13当前实现、四维状态模型、v5上下架合同、实机证据、运行边界和审查步骤的最终交接入口。
- [reports/task12_review_remediation_20260723.md](reports/task12_review_remediation_20260723.md)：针对任务12审查问题的接续修复记录；以该文档说明 v12 合同、原子导入和待补实机证据，不覆盖原交接报告。
- [evidence/task12/index.md](evidence/task12/index.md)：任务12正常 COMMIT 与 UNKNOWN→RECONCILE 的 PR 内脱敏原始证据及自动复算入口。
- [evidence/task13/index.md](evidence/task13/index.md)：任务13 独立 SYNC_STATUS、单/多商品上下架、批次预检零写、严格串行 UNKNOWN 和唯一 RECONCILE 的脱敏证据及 CI 复算入口。
- [reports/task12_development_report_20260723.md](reports/task12_development_report_20260723.md)：任务12开发成果、问题回溯、修复方法和防复发规则。
- [reports/task12_evidence_index_20260723.md](reports/task12_evidence_index_20260723.md)：任务12代表性实机 Run ID、结果 SHA-256、归档位置和审查核对项。
- [task12_reusable_assets.md](task12_reusable_assets.md)：后续任务可直接复用的代码、合同、测试、运行流程和安全门禁。
- [shadowbot_task12_task13_reusable_lessons.md](shadowbot_task12_task13_reusable_lessons.md)：任务12与任务13的标准扩展路径、重复失败根因、防复发门禁和任务14前置清单。
- [plans/task13_ui_discovery_report.md](plans/task13_ui_discovery_report.md)：任务13 T13-0 已接受的无副作用页面结构、选择器规律和重复身份风险。
- [plans/task13_t13_1_contract_freeze.md](plans/task13_t13_1_contract_freeze.md)：任务13 T13-1 四维状态模型、v5 合同、action gate 和两阶段副作用冻结。
- [../任务13_单平台商品上下架与状态对账闭环_交接与实施计划.md](../任务13_单平台商品上下架与状态对账闭环_交接与实施计划.md)：任务13原始交接与实施计划；用于追溯阶段和完成定义，不覆盖最终交接事实。
- [../任务13_单平台商品上下架与状态对账闭环_交接与实施计划_修改意见.md](../任务13_单平台商品上下架与状态对账闭环_交接与实施计划_修改意见.md)：任务13流程、点击边界和数据库合同的接续修改意见。
- [../任务13_商品状态定义修改反馈.md](../任务13_商品状态定义修改反馈.md)：四维状态、位置快照、Review、UNKNOWN 和 v13 迁移语义的详细冻结反馈。
- [reports/task13_t13_2_runtime_schema_v13.md](reports/task13_t13_2_runtime_schema_v13.md)：任务13 T13-2 Runtime Schema v13、无损迁移、结构健康检查和 v4 回归结果。
- [reports/task13_t13_3_independent_sync_status.md](reports/task13_t13_3_independent_sync_status.md)：任务13 T13-3 独立两页 SYNC_STATUS、原子快照投影、异常 Review/Outbox、失败快照和人工报告。
- [reports/task13_unknown_reconcile_acceptance_20260727.md](reports/task13_unknown_reconcile_acceptance_20260727.md)：任务13 最终确认点击后的受控 UNKNOWN、唯一自动只读 RECONCILE、数据库归并、写锁释放和可复算证据。
- [reports/task13_unknown_not_applied_acceptance_20260727.md](reports/task13_unknown_not_applied_acceptance_20260727.md)：任务13 受控 UNKNOWN 后经授权外部恢复、唯一只读 RECONCILE 得出 `NOT_APPLIED`、任务失败和写锁释放的可复算证据。
- [reports/task13_already_applied_acceptance_20260727.md](reports/task13_already_applied_acceptance_20260727.md)：任务13 已满足目标状态时的 `ALREADY_APPLIED`、0 写点击、账本归并和可复算证据。
- [reports/task13_multi_success_acceptance_20260726.md](reports/task13_multi_success_acceptance_20260726.md)：任务13 两商品正常上架与下架的逐商品结果、数据库账本和 CI 复算入口。
- [reports/task13_serial_unknown_acceptance_20260726.md](reports/task13_serial_unknown_acceptance_20260726.md)：任务13 首项成功、次项 UNKNOWN、后续停止及唯一 RECONCILE 的严格串行证据。
- [reports/task13_preflight_zero_write_acceptance_20260727.md](reports/task13_preflight_zero_write_acceptance_20260727.md)：任务13 全批次前置不一致时 0 次资料保存和 0 次最终确认的验收证据。
- [reports/task13_write_lock_and_recovery_acceptance_20260727.md](reports/task13_write_lock_and_recovery_acceptance_20260727.md)：任务13 跨动作共享写锁和 phase/result 中断恢复矩阵。
- [reports/task13_web_projection_acceptance_20260727.md](reports/task13_web_projection_acceptance_20260727.md)：任务13 v5 批次、operation、attempt、UNKNOWN 和 RECONCILE 的只读运营投影。
- [reports/task13_acceptance_status_20260727.md](reports/task13_acceptance_status_20260727.md)：任务13当前验收覆盖矩阵和剩余项；不代替审查方修改任务状态。
- [../README.md](../README.md)：快速启动、环境变量、cpolar / Mobile Review、飞书测试通知和测试命令。
- [../项目注意事项.md](../项目注意事项.md)：项目级注意事项。

## 业务与决策规则

- [shadowbot_listing_status_integration.md](shadowbot_listing_status_integration.md)：当前任务12价格快照、正式任务字段、v4 多商品请求、页面身份、结果导入和状态回写规范。

- [business_decision_spec.md](business_decision_spec.md)：鲜切花预测性销售业务决策规则。
- [project_overview.md](project_overview.md)：项目背景和早期架构说明。当前真实进度以 `project_current_status.md` 为准。
- [ai_agent_integration_spec.md](ai_agent_integration_spec.md)：AI Agent 接入治理规范。当前不接 AI Agent 自动决策。

## 运行态与 SQLite

- [sqlite_runtime_persistence_plan.md](sqlite_runtime_persistence_plan.md)：SQLite 运行态持久化设计与落地进度。
- [runtime_environment_variables.md](runtime_environment_variables.md)：本地运行、飞书、Mobile Review 所需环境变量。
- [core_wheel_shadowbot_deployment.md](core_wheel_shadowbot_deployment.md)：核心 wheel 构建/审计/隔离安装，以及 ShadowBot 独立部署边界。
- [core_ci.md](core_ci.md)：Windows Core、最低限度 Linux Core、隔离安装、失败语义与分支保护说明。
- [system_smoke_test.md](system_smoke_test.md)：系统冒烟测试脚本说明和验收流程。
- [business_rule_evaluation_framework.md](business_rule_evaluation_framework.md)：自动规则评估框架，说明 evaluator、proposal、runner、dry-run/apply 和 script_runs。
- [business_rule_script_development_guide.md](business_rule_script_development_guide.md)：新增自动规则脚本的开发规范，约束不得绕过运行态服务和通知服务。
- [mock_platform_sync_lab.md](mock_platform_sync_lab.md)：Mock 平台同步实验室说明，覆盖模拟平台状态、Mock 执行器、PlatformSyncEvaluator 和测试边界。

## 复核、通知与手机端

- [mobile_review_token_spec.md](mobile_review_token_spec.md)：Mobile Review token 机制规划。
- [review_token_implementation_plan.md](review_token_implementation_plan.md)：review_token、Mobile Review、飞书通知落地进度。

## Web 后台

- [web_frontend_refresh_plan.md](web_frontend_refresh_plan.md)：Web 运行态运营后台刷新计划和当前进度。
- [web_localization_display_spec.md](web_localization_display_spec.md)：Web 与飞书通知的运营中文展示术语表。
- [product_inventory_input_spec.md](product_inventory_input_spec.md)：商品资料与库存补充录入规则，说明 `products.xlsx` 兼容、公共库存、SKU 生成、新增品种弹窗和旧 `/tables` 入口边界。
- [price_rule_input_spec.md](price_rule_input_spec.md)：价格规则输入表单化规则，说明 `price_rules.xlsx` 兼容、定价字段、低价边界和旧 `/tables` 入口策略。
- [listing_rule_input_spec.md](listing_rule_input_spec.md)：上下架规则输入表单化规则，说明 `listing_rules.xlsx` 新字段、三维筛选、策略枚举和 ListingRuleEvaluator 边界。
- [capacity_plan_input_spec.md](capacity_plan_input_spec.md)：包装产能计划输入表单化规则，说明 `capacity_plans.xlsx` 字段、确认包装能力和 CapacityRuleEvaluator 判断口径。
- [cold_storage_input_spec.md](cold_storage_input_spec.md)：冷库状态输入表单化规则，说明 `cold_storage_status.xlsx` 字段、预计占用/剩余容量计算和 ColdStorageEvaluator 判断口径。

当前 Web 页面结构：

- `/dashboard`：运营总览。
- `/tasks`：任务追踪。
- `/tasks?task_tab=automation`：自动规则评估脚本运行记录。
- `/tasks?task_tab=mock_platform`：Mock 平台测试台状态，只读展示本地模拟平台数据。
- `/reviews`：Web 复核主入口。
- `/notifications`：通知排障。
- `/execution-logs`：执行日志入口。
- `/business-inputs`：业务输入入口。
- `/task-generator`：校验业务输入、预览并生成待处理任务；确认后同时导出工作簿并写入任务中心。单规则模式会解析实际平台，并为同一次生成的商品任务分配共享规则组 ID、截止时间和派生组状态；每个商品任务仍保留唯一任务 ID。
- `/system`：配置检查与飞书测试通知。

## 影刀与真实平台接入

- [reports/task12_final_handoff_20260723.md](reports/task12_final_handoff_20260723.md)：任务12单平台多商品正式改价闭环的主交接文档。
- [shadowbot_listing_status_integration.md](shadowbot_listing_status_integration.md)：任务中心、SKU 映射、v4 COMMIT、完整页面快照和 SQLite 回写合同。
- [shadowbot_file_queue_operations.md](shadowbot_file_queue_operations.md)：无 OpenAPI 常驻文件队列、Worker、Result Importer、Queue Watchdog、生命周期记录和恢复操作手册。
- [shadowbot_markdown_report.md](shadowbot_markdown_report.md)：人工可读报告的结果、逐商品证据、数据库回读和计数要求。
- [reports/shadowbot_fault_injection_20260625.md](reports/shadowbot_fault_injection_20260625.md)：2026-06-25 核心故障注入结果报告。

## 运维与验收

- [运行与排错手册.md](运行与排错手册.md)：运行与常见排错。
- [阶段验收清单.md](阶段验收清单.md)：阶段验收检查项。
- [reports/task12_final_handoff_20260723.md](reports/task12_final_handoff_20260723.md)：当前多商品 COMMIT 的正式成功、安全阻断、非默认视口和性能证据。
- [reports/shadowbot_filequeue_real_machine_acceptance_20260701.md](reports/shadowbot_filequeue_real_machine_acceptance_20260701.md)：2026-07-01 文件队列实机总体验收结论、修复项和生产观察边界。
- [reports/shadowbot_8h_observation_failure_20260702.md](reports/shadowbot_8h_observation_failure_20260702.md)：首轮 8 小时观察失败的时间线、Windows heartbeat 文件占用、固定索引商品漏捕获、Importer 瞬时 I/O 误隔离及修复方案。
- [reports/shadowbot_8h_readonly_observation_pass_20260703.md](reports/shadowbot_8h_readonly_observation_pass_20260703.md)：修复后第三轮 8 小时 READ_ONLY 连续运行的检查点、证据、最终状态和通过结论。
- [reports/shadowbot_evidence_fault_injection_20260704.md](reports/shadowbot_evidence_fault_injection_20260704.md)：证据共享目录不可用、共享截图 hash 篡改、PRA 拒绝与恢复结果。
- [reports/shadowbot_ui_fault_injection_20260704.md](reports/shadowbot_ui_fault_injection_20260704.md)：登录失效与网络异常实机结果；白屏仅有分类单元测试，不宣称实机通过。
- [reports/shadowbot_post_intent_stop_acceptance_20260706.md](reports/shadowbot_post_intent_stop_acceptance_20260706.md)：COMMIT 提交意图后注入 stop.signal 的时间线、结果归档和剩余运维清理项。
- [reports/shadowbot_unknown_reconcile_attempt_20260709.md](reports/shadowbot_unknown_reconcile_attempt_20260709.md)：COMMIT 后 UNKNOWN→RECONCILE 实机记录；包含旧价变化安全中止、第二轮 UNKNOWN、自动 RECONCILE VERIFIED 与后续队列/证据配置修复。
- [reports/document_sync_audit_20260711.md](reports/document_sync_audit_20260711.md)：当前状态、开发规范和历史报告之间的进度口径同步检查。
- [reports/shadowbot_product_list_refresh_readonly_20260711.md](reports/shadowbot_product_list_refresh_readonly_20260711.md)：商品列表强制刷新路径的真实 READ_ONLY 验收、证据和队列归档结果。
- [reports/risk_fix_report_20260610.md](reports/risk_fix_report_20260610.md)：Code Review 后高中低风险问题修复报告。
- [reports/e2e_flow_report_20260610_055957.md](reports/e2e_flow_report_20260610_055957.md)：最新主控流程端到端测试报告。

## 历史归档

- [archive/README.md](archive/README.md)：归档规则和当前事实来源。
- [archive/shadowbot_pre_task12/shadowbot_wechat_exploration_status_and_plan.md](archive/shadowbot_pre_task12/shadowbot_wechat_exploration_status_and_plan.md)：任务12之前的元素探索记录。
- [archive/shadowbot_pre_task12/shadowbot_wechat_price_update_development_spec.md](archive/shadowbot_pre_task12/shadowbot_wechat_price_update_development_spec.md)：已被 v4 多商品合同替代的单商品开发规范。
- [archive/shadowbot_pre_task12/shadowbot_filequeue_real_machine_acceptance.md](archive/shadowbot_pre_task12/shadowbot_filequeue_real_machine_acceptance.md)：2026-07-01 单商品分阶段验收流程。

归档文档只用于历史追溯，不得覆盖当前状态、合同或运行手册。

## 当前下一步

Code Review 后高中低风险问题已完成修复，系统冒烟测试、全量单元测试和端到端流程测试均已通过。

当前真实平台 / RPA 状态：

- 已完成任务中心到蚂蚁花团供应商小程序的单平台、多商品、单次请求 v4 COMMIT 闭环；按页面实时位置严格串行执行，不依赖任务输入顺序。
- 已完成写操作前整页预扫描、全目标唯一匹配、全批次旧价门禁、提交后逐商品独立回读和完整页面状态回传。
- 已完成 `ShadowBotExecutor`、v4 批次账本、常驻文件队列 runner、Result Importer、Queue Watchdog、自动只读对账和 Web 队列状态入口。
- 已完成 8 小时 READ_ONLY 常驻 Worker 连续运行验收；长期证据归档和运营告警闭环仍待完成。
- 已完成提交意图后 `stop.signal` 实机验收，以及真实商品 `COMMIT -> UNKNOWN -> 自动 RECONCILE -> VERIFIED` 验收。
- 任务13已完成独立两页 SYNC_STATUS、单商品往返、正常多商品上下架、整批预检零写、严格串行 UNKNOWN、`UNKNOWN -> 唯一自动 RECONCILE -> VERIFIED`、`UNKNOWN -> 唯一自动 RECONCILE -> NOT_APPLIED`、`ALREADY_APPLIED` 0 写点击、跨动作共享写锁、phase/result 恢复、Web 运营投影、最终本地回归和交接报告；PR #19 审查修复和 Windows/Linux Core 已通过。本轮文档整理不代替审查方执行合并或任务状态变更。
- 最终暖态四商品批次 4/4 `VERIFIED`，总用时 `51.094 秒`；READ_ONLY 完整页面结束标记样本为 1 次扫描、0 次滚动、`27.445 秒`。
- 暂不承诺无人值守生产改价。

下一步优先级：

1. 任务12审查修复已通过 PR #18 合并；其 v4 改价链路继续作为任务13和后续任务的稳定基线。
2. 任务13计划中的实现、受控实机验收、最终本地回归、PR #19 审查修复和双平台 CI 均已覆盖；后续合并与任务状态由审查方处理。
3. 任务14实现统一任务审查和正式调度授权；在此之前继续保持显式 `task_id` 门禁。
4. 补充长期告警、磁盘清理、证据保留和服务账号运维样本，并分别定义冷态/暖态耗时口径。
5. 为元素版本漂移和白屏建立可重复的专用测试夹具；登录、网络和证据上传失败的实机故障注入已经完成。
6. 持续运行系统冒烟和 ShadowBot 成功基线测试，避免后续任务重写已验证 COMMIT 链路。
7. 任务14开始前按[任务12—13复用路径与失败复盘](shadowbot_task12_task13_reusable_lessons.md)建立黄金基线卡、最小差异和禁止重写清单。

当前不优先做：

- 不承诺无人值守生产改价。
- 不扩大到跨平台混合批次或多 Worker 并发执行；当前多商品仍由单 Worker 严格串行处理。
- 不接 AI Agent 自动决策。
- 不迁移 Excel 主数据。
- 不新增完整权限系统。
