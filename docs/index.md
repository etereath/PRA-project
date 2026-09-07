# PRA 文档索引与权威角色

本页仅维护文档路径、主题和角色，不保存项目进度、验收结论或当前待办。项目进度统一见[当前状态](project_current_status.md)。

## 项目主文档

| 主题 | 文档 | 角色 |
|---|---|---|
| 项目进度与验证状态 | [当前状态](project_current_status.md) | 唯一进度页 |
| 产品目标与路线 | [产品与路线图](project_overview.md) | 产品定义 |
| 业务定义 | [业务合同](business_contract.md) | 业务主定义 |
| 实现责任与调用关系 | [实现责任图](rebaseline/task13_6_current_implementation_map.md) | 指定 SHA 的实现事实 |
| 目标职责与缺口 | [目标职责 / gap / IG-01～11](rebaseline/task13_6_target_responsibility_and_gap_matrix.md) | 目标架构 |
| Agent 工作入口 | [AGENTS](../AGENTS.md) | 硬边界、权限与最小入口 |
| 开发范围与投入判断 | [开发范围与效率治理](pra_development_scope_and_efficiency_governance.md) | WHY / WHEN |
| 开发执行步骤 | [开发执行参考](development_workflow.md) | HOW |

## 实现与运维参考

- [核心 wheel / ShadowBot 部署](core_wheel_shadowbot_deployment.md)
- [环境变量](runtime_environment_variables.md)、[Core CI](core_ci.md)
- [Queue / Worker 运维](shadowbot_file_queue_operations.md)、[上下架集成](shadowbot_listing_status_integration.md)
- [DB 库存输入及迁移背景](product_inventory_input_spec.md)、[SQLite 并发](sqlite_concurrency.md)
- [通知 Outbox](notification_outbox.md)、[Mobile Review token](mobile_review_token_spec.md)
- [业务规则评估框架](business_rule_evaluation_framework.md)
- [产能输入](capacity_plan_input_spec.md)、[冷库输入](cold_storage_input_spec.md)

## 任务计划与证据目录

- [13.7-1 已满足目标确认与授权回执](reports/task13_7_1_p2_authorization_receipts_20260907.md)：P2 修复报告
- [13.7-1 人工终态收口](reports/task13_7_1_p1_human_resolution_20260907.md)：P1 修复报告
- [13.7-1 人工改价实现、迁移和隔离验证](reports/task13_7_1_human_update_price_20260907.md)：首版实现报告
- [13.7 首条人工改价纵向切片计划](plans/task13_7_human_update_price_vertical_slice.md)、[开发 Goal](plans/task13_7_first_slice_codex_goal.md)
- [13.6-3 计划](plans/task13_6_3_canonical_entrypoint_convergence.md)
- [13.6-3 原始问答、外部测试反馈与修订记录](reports/task13_6_3_canonical_entrypoint_and_cold_start_20260906.md)
- [G1 Gate](reports/task13_6_1_g1_business_baseline_review_20260906.md)
- [G2 Gate](reports/task13_6_2_g2_architecture_handoff_review_20260906.md)、[增量 G2 Gate](reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)
- [文档迁移与来源映射](rebaseline/task13_6_document_authority_inventory.md)

## 历史与归档目录

| 材料 | 文档角色 |
|---|---|
| `docs/plans/task13_5_*`、Issue #20、PR #39 | 历史计划与候选设计 |
| `docs/reports/task13_5_*`、`docs/evidence/**`、Task12/13 evidence | 绑定版本、环境与范围的原始验证材料 |
| G1 Closure、Open Decision Register、旧 G1 候选入口 | 决策历史 |
| [平行吸收补充](rebaseline/task13_6_parallel_analysis_absorption_addendum.md)、PR #44 | 历史分析与来源材料 |
| [旧业务规范](business_decision_spec.md)、[旧 Agent 入口](ai_agent_integration_spec.md)、`doc/project_overview.md` | 历史定义与转向入口 |
| [Archive](archive/README.md) | 原样归档证据 |

其他 `docs/*.md` 的主题与角色见相应文件和迁移表。[旧版完整索引](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/index.md)仅用于历史导航；历史材料不自动成为现役规则。
