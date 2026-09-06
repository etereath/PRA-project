# PRA 文档索引与权威角色

本页是文档身份和阅读顺序的主入口；阶段进度只在[当前状态](project_current_status.md)维护。

## Canonical 与当前实现

| 问题 | 主文档/证据 | 权威边界 |
|---|---|---|
| 产品为什么存在、路线是什么 | [产品与路线图](project_overview.md) | 当前目标和长期方向 |
| 业务应该怎样运行 | [业务合同](business_contract.md) | G1 及用户最新明确裁决；实现偏差不能反向改业务 |
| 当前源码实际做什么 | [实现责任图](rebaseline/task13_6_current_implementation_map.md)及指定 SHA 源码 | Current Implementation，不能当未来产品规范 |
| 各段由谁负责、13.7 补什么 | [目标职责/gap/IG-01～11](rebaseline/task13_6_target_responsibility_and_gap_matrix.md) | 已采纳架构，不是生产完成证明 |
| 如何开发、审查、控制复杂度 | [治理 v2.0](pra_review_risk_and_complexity_governance.md)与[AGENTS](../AGENTS.md) | 仓库工作规则；常规选择由执行者判断，业务冲突显式报告 |
| 当前阶段通过了吗 | [当前状态](project_current_status.md)与绑定版本的验收记录 | CI、READ_ONLY、COMMIT、部署、长期运行分别证明 |

每个主题只在主文档维护一次。README/AGENTS/Status 是短入口；历史材料不能通过标题中的“冻结”“必须”重新成为现役合同。用户最新明确指令优先于仓库旧规则，但不使未实现能力自动成为事实。

## Current Implementation & Operations

这些是特定运行能力说明，使用前核对对应代码/版本；不覆盖上述业务定义。

- [核心 wheel/ShadowBot 部署](core_wheel_shadowbot_deployment.md)
- [环境变量](runtime_environment_variables.md)、[Core CI](core_ci.md)
- [Queue/Worker 运维](shadowbot_file_queue_operations.md)、[上下架集成](shadowbot_listing_status_integration.md)
- [DB 库存输入及迁移背景](product_inventory_input_spec.md)、[SQLite 并发](sqlite_concurrency.md)
- [通知 Outbox](notification_outbox.md)、[Mobile Review token](mobile_review_token_spec.md)
- [业务规则评估框架](business_rule_evaluation_framework.md)：现有 evaluator 功能，不能自动升级为销售 Controller
- [产能输入](capacity_plan_input_spec.md)、[冷库输入](cold_storage_input_spec.md)：辅助输入说明，不定义新 Supply authority

## 当前任务与验证证据

- [13.6-3 计划](plans/task13_6_3_canonical_entrypoint_convergence.md)
- [G1 Gate](reports/task13_6_1_g1_business_baseline_review_20260906.md)
- [G2 Gate](reports/task13_6_2_g2_architecture_handoff_review_20260906.md)、[增量 G2 Gate](reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)
- [文档迁移与来源映射](rebaseline/task13_6_document_authority_inventory.md)

## Historical / Superseded / Draft

| 材料 | 角色和用途 |
|---|---|
| `docs/plans/task13_5_*`、Issue #20、PR #39 | Historical/Candidate；13.5 STOPPED，7G 不继续；未合并 PR 不是 main 事实 |
| `docs/reports/task13_5_*`、`docs/evidence/**`、Task12/13 evidence | 历史验证；只在绑定 SHA/工作树/平台/scope 成立，原始/hash-bound 文件不改写 |
| G1 Closure、Open Decision Register、旧 G1 候选入口 | 决策历史；OD-01～OD-06 已关闭，现役规则见业务合同 |
| [平行吸收补充](rebaseline/task13_6_parallel_analysis_absorption_addendum.md)、PR #44 | G2 采纳历史与 donor；有效 delta 已合入目标职责；未采纳表/状态机不生效 |
| [旧业务规范](business_decision_spec.md)、[旧 Agent 入口](ai_agent_integration_spec.md)、`doc/project_overview.md` | 历史/转向入口，不再定义当前业务或任务归属 |
| [Archive](archive/README.md) | 原样归档证据，包括旧 AGENTS；不自动继承其指令 |

其他 `docs/*.md` 默认是特定 feature/implementation 或历史说明，不因路径浅而自动成为 Canonical。高影响旧入口的处置见迁移表；需要追溯原导航可读取[收口前完整索引](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/index.md)。不要求历史旧词零命中，不批量改写历史报告。
