# Task 13.6-2：系统架构与实现差距重基线

更新时间：2026-09-06

状态：`IN PROGRESS / G2 CANDIDATE PREPARATION`

父任务：GitHub Issue #41

工作分支：`codex/task13-6-2-architecture-gap-rebaseline`

审计基线：`main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`

前置 Gate：Task 13.6-1 G1 Business Baseline = `PASS`

## 1. 任务定位

13.6-2 负责在已经通过 G1 的业务语义下，回答三个问题：

1. 当前系统实际具备哪些能力、由谁推进、持久化在哪里；
2. 目标业务闭环需要哪些最小职责、状态和恢复边界；
3. 哪些资产应原样复用、参数化调整、新增或退役，以及这些结论如何成为 Task 13.7 的可执行输入。

本阶段只做架构与差距重基线，不修改生产代码、Runtime Schema、Runtime DB、Queue、Worker、Automation 运行配置或真实平台状态。

## 2. 权威输入

- `docs/reports/task13_6_1_g1_business_baseline_review_20260906.md`：G1 Gate 结论；
- `docs/rebaseline/task13_6_business_baseline_draft.md`：G1 已通过的业务语义输入；
- `docs/rebaseline/task13_6_business_decision_closure.md`：OD-01～OD-06 owner 裁决；
- `docs/rebaseline/task13_6_document_authority_inventory.md`：文档与证据角色；
- `main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`：当前实现证据；
- Task 12 / Task 13 已绑定 evidence：平台写、READ_ONLY、UNKNOWN、唯一 RECONCILE、Queue / Worker / Importer / Watchdog 的历史验收输入。

当前代码只能证明“现在怎么做”，不能推翻 G1 已冻结的业务语义。若二者不一致，应登记 Implementation Gap。

## 3. 审计主链

所有目标设计都必须覆盖完整责任链：

```text
业务意图
→ 持久状态
→ 调度 / 授权
→ 执行
→ 结果
→ 恢复 / 复核
→ 终态
```

每个非终态至少要回答：

- 当前 owner 是谁；
- 下一步是什么；
- 由什么 trigger 推进；
- 进程重启后从哪里恢复；
- 什么条件终止自动推进；
- 什么事实证明已经完成或仍然不确定。

## 4. 固定审计范围

13.6-2 必须逐项收口 G1 转交的 11 个问题：

1. 18:00 / 20:00 dual-boundary 的退役与兼容；
2. 旧 Settlement / Summary 的复用与退役；
3. Current Sales Commitment 的最小持久化与投影；
4. Daily Supply / Carryover 的最小权威事实；
5. one-shot Sales Control Intent 与 Task / operation / attempt 的映射；
6. Runtime Task 端到端推进 owner / coordinator；
7. `purchase_sequence` 的 READ_ONLY 采集与持久化；
8. Daily Sales Closing 的成功锁、一次重试、S2 与管理员维护；
9. `target_inventory <= real inventory` 等过时规则；
10. Observation Health 的 provider / capability / recovery；
11. Task 14-B 首版 Ops Agent 的最小受控接口。

## 5. 工作方法

### 5.1 当前实现审计

按以下边界读取当前代码、Schema、测试与入口：

- 日期与调度：`app/services/operational_time.py`、`app/services/automation.py`、Automation Repository / Configuration；
- 观察事实：商品、Listing、Order、Sales Estimate、Mapping、Importer；
- 日结与库存：Trade Day Settlement、Summary、Sales Plan、Inventory Sales Application；
- 人工执行：Operations Web、Manual Task、Execution Authorization；
- 平台写链：v4 / v5 batch、operation、attempt、write lock、Queue、Worker、Importer、Watchdog、RECONCILE；
- 异常与恢复：Incident、Review、Notification、Worker Recovery；
- Agent：现有 AI service 与历史 Agent integration spec。

### 5.2 分类标准

每项资产只进入以下一种主分类：

| 分类 | 判定标准 |
| --- | --- |
| 原样复用 | 职责与 G1 语义一致，控制流和安全属性应整体保留 |
| 参数化/抽取复用 | 核心机制正确，但日期、provider、任务类型或页面字段需扩展 |
| 确需新增 | 当前没有可表达该业务事实或端到端责任的持久化能力 |
| 退出现役 | 当前实现承载了已被 G1 supersede 的业务语义；历史数据只读保留 |

“当前有表”不等于应复用，“新业务术语”也不自动等于需要新服务或新表。

## 6. 目标产物

### 6.1 架构基线候选

`docs/rebaseline/task13_6_system_architecture_baseline.md`

至少包含：

- 当前实现与目标职责的分离说明；
- 全链责任与恢复模型；
- 核心业务事实、投影和执行账本边界；
- 11 项差距的架构结论；
- 原样复用 / 参数化复用 / 新增 / 退役矩阵；
- 兼容与切换顺序；
- Task 13.7 的实现包、依赖、验收情景和禁止项。

### 6.2 G2 Review

13.6-2 完成后单独形成 G2 System Architecture & Gap Review。只有 Review 明确 `PASS`，Task 13.6-2 才完成；G2 `PASS` 仍不代表 Task 13.6 总体通过，也不自动授权跳过 13.6-3。

## 7. 当前阶段边界

本阶段不得：

- 修改 `app/`、`shadowbot/` 或生产 `scripts/`；
- 增加 Runtime Schema migration；
- 修改或迁移真实 Runtime DB；
- 修改 Queue、Worker、影刀应用或运行态；
- 改 Automation 实际启停状态；
- 生成或执行真实平台写；
- 把 G1 业务语义改回旧双日界、旧 Settlement 或自动 Sales Controller；
- 把目标架构候选描述成已经上线；
- 在 G2 Review 前宣称 Task 13.7 Ready。

## 8. G2 固定审查情景

G2 至少检查：

1. 18:30 新交易日 Current Commitment 与旧交易日冻结订单页并存；
2. 19:00 Closing 成功后普通自动链不重扫同日；
3. Closing 连续两次失败后进入 Closing S2 并停止自动重试；
4. 20:00 只触发 planning checkpoint，不改变销售日；
5. Supply 三阶段覆盖、Carryover 独立且不重复扣旧 Commitment；
6. PRA Exposure 调整先从 QUICK 差量中剥离；
7. 新 Intent 在副作用前后分别如何 supersede 旧 Intent；
8. Queue 发布、进程重启、结果延迟、UNKNOWN 和唯一 RECONCILE 的 owner；
9. 外部人工平台修改如何使旧事实/Intent 失效并要求重读；
10. S3 Recovery 与实际 probe 失败才 S4；
11. Ops Agent 只通过带权限、幂等与审计的 Query / Controlled Tool Facade。

## 9. 当前 Gate 状态

```text
G1 Business Baseline: PASS
Task 13.6-2 Architecture Candidate: IN PROGRESS
G2 System Architecture & Gap Review: NOT RUN
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
