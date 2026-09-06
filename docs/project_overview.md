# PRA 产品定位与路线图

角色：Canonical / Project Charter and Roadmap。阶段状态只在[当前状态](project_current_status.md)维护。

## 产品目标

PRA 为鲜切花预测性销售提供持续观察、人工经营决策、可靠执行和恢复闭环。产品成功意味着管理者能够理解经营事实的时间、来源、粒度和不确定性，提出明确决定，并知道操作结果、异常责任和下一步。

当前 Controller 是通过 Operations Web 工作的人类。未来销售 Agent 替换决策来源，继续经过相同业务校验、授权和执行基础设施。自动销售 Agent 缺席不构成当前缺陷；不提前建设复杂策略系统。

业务定义集中在[业务合同](business_contract.md)；[当前实现](rebaseline/task13_6_current_implementation_map.md)与[目标职责](rebaseline/task13_6_target_responsibility_and_gap_matrix.md)分别维护，不能把目标图当成当前部署图。

## 路线与出口

| 阶段 | 交付能力与出口 |
|---|---|
| Task 13.5 | STOPPED / SUPERSEDED；保留成熟执行资产及历史证据，不继续 7G |
| Task 13.6 | G1 业务基线、G2 架构交接、13.6-3 正式入口与 AGENTS、独立 cold-start、负责人最终确认；Overall PASS 后才进入 13.7 |
| Task 13.7 | 人工销售控制闭环。先做 1 SKU 一次 UPDATE_PRICE 的正常/恢复旅程，再按证据扩展；细化范围见目标职责，不以此表批准新 Schema |
| Task 14-A | Integrated Acceptance & Freeze：多品种多动作、授权、阻塞/重启、UNKNOWN/RECONCILE、Provider rollover、发布与版本冻结 |
| Task 14-B | Agent Intervention / Ops Agent：先诊断、状态解释和受控风险中性工具；不做自动销售 Controller，不承担确定性恢复 owner |

14-A/14-B 可在接口稳定后并行，真实接入仍需相应验收和授权。第二平台接入前完成组合层设计与隔离验收；当前不实现复杂 Exposure Allocator，不因多平台自动引入分布式架构。

## 开发策略

以完整业务旅程决定组件职责，用同一经营案例贯穿文档与验收。先复用既有能力，未证明必要不新增持久结构。历史方案可依新事实修订，已撤回意见不再作为缺陷输入；按[审核治理](pra_review_risk_and_complexity_governance.md)显式报告边界冲突。

Farm Assistant、农资库存、IoT 和员工任务管理是外部协作背景；不把其状态机、Schema 或自然语言主界面直接移植为 PRA 当前需求。
