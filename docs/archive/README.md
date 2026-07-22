# 文档归档

本目录保存已经被当前实现替代、但仍具有决策和故障追溯价值的历史文档。

归档文档不得作为当前合同、运行步骤或任务状态的依据。当前事实来源依次为：

1. [项目当前状态总览](../project_current_status.md)
2. [任务12最终交接报告](../reports/task12_final_handoff_20260723.md)
3. [PRA 平台价格快照与 ShadowBot 改价对接说明](../shadowbot_listing_status_integration.md)
4. [ShadowBot 常驻文件队列运行手册](../shadowbot_file_queue_operations.md)

## `shadowbot_pre_task12/`

以下文档归档于 2026-07-23：

- `shadowbot_wechat_exploration_status_and_plan.md`：任务12之前的元素探索和后续计划。
- `shadowbot_wechat_price_update_development_spec.md`：单商品垂直切片开发规范。
- `shadowbot_filequeue_real_machine_acceptance.md`：2026-07-01 单商品文件队列分阶段验收流程。
- `task12_handoff_and_implementation_plan.md`：任务12初始交接与实施计划；当前实现已由最终交接报告取代。

它们包含仍可复用的安全思想，如副作用边界、UNKNOWN→RECONCILE、证据哈希和失败分类；但其中的单商品合同、强制 READ_ONLY/FILL_PREVIEW 前置和旧运行步骤已经过期。
