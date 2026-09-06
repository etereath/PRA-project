# 文档归档

本目录保存已经被当前实现替代、但仍具有决策和故障追溯价值的历史文档。

归档文档不得作为当前合同、运行步骤或任务状态的依据。现役文档按[文档索引](../index.md)的角色读取，阶段见[当前状态](../project_current_status.md)。Task12/13 报告只证明各自绑定版本和验收范围。

## AGENTS 原样归档

- [13.5 结束前原版](AGENTS_task13_5_pre_rebaseline_20260905.md)：13.6-0 已归档。
- [13.6 临时版](AGENTS_task13_6_temporary.md)：13.6-3 原样归档自 main `08041bfe25a7f31f032564a2abca35e5eb5f5330`；Git blob `3042a7555ee0ece0e65d09ef5290de44e870b632`，没有插入历史标注或重排正文。

当前指令只使用[根级正式 AGENTS](../../AGENTS.md)。以上文件中的临时任务顺序及历史权限不继续生效。

## `shadowbot_pre_task12/`

以下文档归档于 2026-07-23：

- `shadowbot_wechat_exploration_status_and_plan.md`：任务12之前的元素探索和后续计划。
- `shadowbot_wechat_price_update_development_spec.md`：单商品垂直切片开发规范。
- `shadowbot_filequeue_real_machine_acceptance.md`：2026-07-01 单商品文件队列分阶段验收流程。
- `task12_handoff_and_implementation_plan.md`：任务12初始交接与实施计划；当前实现已由最终交接报告取代。

它们包含仍可复用的安全思想，如副作用边界、UNKNOWN→RECONCILE、证据哈希和失败分类；但其中的单商品合同、强制 READ_ONLY/FILL_PREVIEW 前置和旧运行步骤已经过期。
