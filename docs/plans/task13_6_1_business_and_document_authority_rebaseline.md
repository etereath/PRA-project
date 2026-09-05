# Task 13.6-1：业务与文档权威重基线

更新时间：2026-09-06  
状态：Draft / In Progress  
父任务：GitHub Issue #41  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
前置：Task 13.6-0 Stage Goal = `PASS`

## 1. 任务定位

13.6-1 负责把 PRA 的历史材料重新整理成一套可供项目 owner 审核的业务基线输入。

本阶段不负责生产功能开发，也不负责把候选架构写死为实现方案。核心问题是：

> 在不把历史对话、旧 Issue、旧计划或当前代码自动视为永久业务权威的前提下，重新说明 PRA 到底服务什么经营流程、关键数量和日期分别代表什么、哪些材料仍可信、哪些冲突必须由 owner 一次性裁决。

13.6-1 的最终输出是 **G1 Business Baseline Review 的输入**，不是 Task 13.6 总体 PASS。

## 2. 本阶段四个交付物

### 2.1 文档与证据权威盘点

文件：`docs/rebaseline/task13_6_document_authority_inventory.md`

目标：

- 给高影响入口、业务规范、历史计划、实施报告和证据目录分配明确角色；
- 记录它们可以证明什么、不能证明什么；
- 找出当前仍会误导人类或 AI 的冲突；
- 避免通过“把旧文件全部归档”或“最新文件覆盖一切”这种粗粒度规则丢失有效工程证据。

### 2.2 业务基线草案

文件：`docs/rebaseline/task13_6_business_baseline_draft.md`

目标：

- 用经营语言重建当前业务主链；
- 明确人工 Controller、供给、成交承诺、Exposure、平台观察、时间轴、日结和执行关系；
- 已确认规则直接写入草案；
- 尚未裁决的细节引用 Open Decision Register，不在正文中偷偷拍板。

该文件在 G1 通过前始终是 `DRAFT / G1 INPUT`，不得作为 13.7 的编码授权。

### 2.3 Open Decision Register

文件：`docs/rebaseline/task13_6_open_decision_register.md`

目标：

- 收集会改变经营语义、数据口径或后续架构的真正待决问题；
- 不把普通实现细节交给项目 owner 重复决定；
- 在业务主链初稿完成后一次性进行 Business Decision Closure；
- G1 通过后原则上不再零散重新打开核心业务定义。

### 2.4 G1 Review 输入清单

在本阶段收尾时形成，不提前伪造 PASS。

至少需要：

- 业务基线草案；
- Open Decision Register 的 owner 裁决结果；
- 主要历史冲突与 supersession 清单；
- 固定经营情景推演；
- 明确仍需 13.6-2 处理的实现/架构差距。

## 3. 信息来源与权威规则

### 3.1 Business Fact

项目 owner 对真实经营、平台行为、人工流程、日期和数量口径的直接说明。

它定义“业务实际怎样运行”，但仍需在文档中写清适用平台、日期和前提，避免把单平台事实误写成全局规则。

### 3.2 Accepted Design

项目 owner 曾明确采纳的设计。

它是强输入，但不是不可重新审查的永久合同。如果存在更简单、更可靠、更符合当前经营目标的方案，可以在 Open Decision Register 中提出替代设计。

### 3.3 Current Implementation Evidence

指定 Git SHA 的生产代码、正式入口和真实调用关系。

它回答“系统当前实际上做了什么”，不能反向决定业务应该怎样运行。

### 3.4 Validation Evidence

测试、CI、READ_ONLY、受控 COMMIT、恢复演练和长期运行记录。

不同证据等级必须分开，不得用自动测试替代真实平台验收，也不得用一次受控实机成功推导生产常驻能力。

### 3.5 Historical / Candidate Design

历史 Issue、计划、旧业务规范、旧 AGENTS、未合并 PR 和历史 AI 建议。

保留其时间、SHA、工作树和上下文，但不自动约束新业务基线。

## 4. 本阶段不做

13.6-1 不得：

- 修改 `app/`、`shadowbot/`、生产 `scripts/` 或 Runtime Schema；
- 修改真实 Runtime DB、Queue、Worker、Automation 配置或平台状态；
- 执行真实平台写入；
- 实现 Current Sales Commitment、Supply 新结构、Intent、Dispatch Attempt、Coordinator、Observation Health 或 Agent；
- 为了让文档“看起来一致”而修改历史 evidence、哈希绑定文件或归档 AGENTS；
- 把旧 13.5 计划整体改名继续使用；
- 在发现一个冲突时立刻把局部方案写成最终 Canonical。

生产代码中发现的业务冲突只登记为 `Implementation Gap / Task 13.7 Input`。

## 5. 盘点策略

为了避免数百份历史文件逐篇改写，采用“目录默认角色 + 高影响文档逐项审计”。

默认规则：

- `docs/archive/**`：Historical Evidence / Archived Context；
- `docs/plans/task13_5_*`：Historical / Candidate Design；
- `docs/reports/task13_5_*`：Historical Validation Evidence，只在其明确 SHA、工作树和验收范围内有效；
- `docs/evidence/**`：Validation Evidence，原则上保持原样；
- Task 12 / Task 13 final handoff 与对应 evidence：已验证执行资产的历史实现/验收证据，不直接定义新业务语义；
- 根级 `README.md`、`AGENTS.md`、`docs/index.md`、`docs/project_current_status.md`、业务规范和治理文档：逐项审计。

文件是否属于某个目录不能覆盖内容事实；如有重要例外，必须在权威盘点中单列。

## 6. 业务主链初稿的固定范围

13.6-1 至少必须解释：

1. 当前销售决策由谁负责；
2. 一个生产周期中预测、采摘估计、包装实数和结转库存怎样变化；
3. 平台 Exposure 与实物供给为什么不是 reservation 关系；
4. 盘中 Current Sales Commitment 的作用与证据来源；
5. 平台交易日、卖家作业日、生产日、订单页展示日和观察时间的区别；
6. 订单页尚未跨日时，旧日关闭证据与新日盘中成交如何同时存在；
7. PRA 自己的 Exposure 调整怎样避免被误认为销量；
8. 每日指定日结与盘中实时状态分别负责什么；
9. 人工新决定、外部人工平台修改和已经跨越副作用边界的旧执行怎样共存；
10. 未来 Agent 与当前人工 Controller 的边界。

## 7. Business Decision Closure 执行时点

执行顺序固定为：

```text
文档/证据盘点
→ 业务主链初稿
→ Open Decision Register 完整化
→ 一次性 Business Decision Closure
→ 更新业务基线草案
→ G1 Business Baseline Review
→ 通过后进入 13.6-2
```

不要在盘点阶段遇到一个问题就逐项临时冻结。

只有会改变经营语义、风险承担、数量口径或后续系统职责的选项才交给项目 owner。普通字段命名、Repository 选择、表数量等实现问题留给 13.6-2 / 13.7。

## 8. G1 通过后的 reopen 规则

G1 通过后，13.6-2 应回答“现有系统如何支持这些业务、缺什么、谁负责”，而不是继续重新定义怎样经营。

只有出现以下情况才允许重新打开业务基线：

- 新的真实平台/经营事实推翻既有前提；
- 项目 owner 主动改变经营要求；
- 发现 G1 内部逻辑矛盾导致核心旅程无法成立。

重新打开时必须显式记录：

`BUSINESS BASELINE REOPENED`

不得在架构文档里静默改变业务语义。

## 9. 13.6-1 完成条件

本阶段只有在以下条件满足时才可进入 G1：

- [ ] 高影响文档与目录默认角色已完成盘点；
- [ ] 主要现行/历史冲突已登记；
- [ ] 业务主链草案覆盖本计划第 6 节的十个主题；
- [ ] 所有未决核心问题集中在 Open Decision Register；
- [ ] 未决问题没有被 Codex/ChatGPT 在正文中静默拍板；
- [ ] 没有生产代码、Schema、真实数据、运行配置或平台副作用；
- [ ] 可用固定业务情景对业务基线进行整体推演；
- [ ] G1 前仍明确标记为 `DRAFT / NOT YET VALIDATED`。

## 10. 阶段状态

当前：

```text
Task 13.6-1: IN PROGRESS
G1 Business Baseline: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
