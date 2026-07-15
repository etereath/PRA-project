# 文档索引

本文档是当前项目文档入口。项目说明文本以中文为主，英文键名和代码标识保持原样。

## 当前状态与入口

- [project_current_status.md](project_current_status.md)：当前项目定位、已完成能力、主控流程、安全边界和下一步优先级。
- [../README.md](../README.md)：快速启动、环境变量、cpolar / Mobile Review、飞书测试通知和测试命令。
- [../项目注意事项.md](../项目注意事项.md)：项目级注意事项。

## 业务与决策规则

- [business_decision_spec.md](business_decision_spec.md)：鲜切花预测性销售业务决策规则。
- [project_overview.md](project_overview.md)：项目背景和早期架构说明。当前真实进度以 `project_current_status.md` 为准。
- [ai_agent_integration_spec.md](ai_agent_integration_spec.md)：AI Agent 接入治理规范。当前不接 AI Agent 自动决策。

## 运行态与 SQLite

- [sqlite_runtime_persistence_plan.md](sqlite_runtime_persistence_plan.md)：SQLite 运行态持久化设计与落地进度。
- [runtime_environment_variables.md](runtime_environment_variables.md)：本地运行、飞书、Mobile Review 所需环境变量。
- [core_wheel_shadowbot_deployment.md](core_wheel_shadowbot_deployment.md)：核心 wheel 构建/审计/隔离安装，以及 ShadowBot 独立部署边界。
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
- `/system`：配置检查与飞书测试通知。

## 影刀与真实平台接入实验

- [shadowbot_wechat_exploration_status_and_plan.md](shadowbot_wechat_exploration_status_and_plan.md)：影刀控制桌面微信小程序的探索状态、元素策略和后续计划。
- [shadowbot_wechat_price_update_development_spec.md](shadowbot_wechat_price_update_development_spec.md)：影刀微信小程序商品改价流程开发规范。
- [shadowbot_file_queue_operations.md](shadowbot_file_queue_operations.md)：无 OpenAPI 常驻文件队列、Worker、Result Importer、Queue Watchdog 和恢复操作手册。
- [reports/shadowbot_fault_injection_20260625.md](reports/shadowbot_fault_injection_20260625.md)：2026-06-25 核心故障注入结果报告。

## 运维与验收

- [运行与排错手册.md](运行与排错手册.md)：运行与常见排错。
- [阶段验收清单.md](阶段验收清单.md)：阶段验收检查项。
- [shadowbot_filequeue_real_machine_acceptance.md](shadowbot_filequeue_real_machine_acceptance.md)：ShadowBot 文件队列分阶段实机验收、校验器和当前现场记录。
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

## 当前下一步

Code Review 后高中低风险问题已完成修复，系统冒烟测试、全量单元测试和端到端流程测试均已通过。

当前真实平台 / RPA 状态：

- 已完成影刀控制桌面微信小程序的真实平台 UI 自动化实验。
- 已完成 `READ_ONLY/FILL_PREVIEW/COMMIT/RECONCILE` 垂直切片、安全边界验证和核心故障注入。
- 已完成 `ShadowBotExecutor`、常驻文件队列 runner、Result Importer、Queue Watchdog、自动只读对账和 Web 队列状态入口。
- 已完成 8 小时 READ_ONLY 常驻 Worker 连续运行验收；长期证据归档和运营告警闭环仍待完成。
- 已完成提交意图后 `stop.signal` 实机验收，以及真实商品 `COMMIT -> UNKNOWN -> 自动 RECONCILE -> VERIFIED` 验收。
- 暂不承诺无人值守生产改价。

下一步优先级：

1. 持续运行系统冒烟测试脚本，保持主控流程测试基线稳定。
2. 在已完成 8 小时观察、提交意图后停止和 UNKNOWN 自动对账验收的基础上，补充长期告警、磁盘清理、证据保留和服务账号运维样本。
3. 为元素版本漂移和白屏建立可重复的专用测试夹具；登录、网络和证据上传失败的实机故障注入已经完成。
4. 进入下一轮功能开发前，先做小范围设计审查，避免绕过运行态 service 边界。
5. 继续打磨业务输入、Web 可用性和运行态排障体验。

当前不优先做：

- 不承诺无人值守生产改价。
- 不扩大到多平台、多 SKU 并发执行。
- 不接 AI Agent 自动决策。
- 不迁移 Excel 主数据。
- 不新增完整权限系统。
