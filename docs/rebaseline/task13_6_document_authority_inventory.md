# Task 13.6 文档与证据权威盘点

状态：`DRAFT / G1 INPUT`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41

## 1. 目的

本文件不负责定义最终业务架构，而是回答：

> 当前仓库中的不同文档、Issue、报告和证据分别能证明什么？哪些仍会误导后续 AI？哪些需要保留但降为历史身份？

本盘点采用“目录默认角色 + 高影响文件逐项审计”，避免把数百份历史材料逐篇改写成新的文档负担。

## 2. 权威角色

### 2.1 Temporary Instruction

仅在 Task 13.6 期间控制 Codex 工作方式，不是长期 PRA 业务权威。

当前文件：根级 `AGENTS.md`。

### 2.2 Canonical Candidate

Task 13.6 新生成、等待 G1/G2/G3 验收的业务或架构说明。

在通过对应 Stage Gate 前只能标记为 `DRAFT / INPUT`，不得授权 13.7 编码。

### 2.3 Current Implementation Evidence

固定 Git SHA 的生产代码、正式入口和真实调用关系。

它可以否定“某功能已经实现”的错误声明，但不能反向决定业务应该怎样运行。

### 2.4 Validation Evidence

测试、CI、真实 READ_ONLY、受控 COMMIT、故障恢复和长期运行记录。

证据只能在其绑定的 SHA、数据、平台、工作树和验收范围内成立。

### 2.5 Historical / Candidate Design

旧业务规范、历史 Issue、任务计划、未合并 PR 和旧 AI 建议。

这些材料可以贡献背景、成功资产和失败教训，但不自动限制 Task 13.6。

## 3. 目录默认角色

| 路径 | 默认角色 | 处理规则 |
| --- | --- | --- |
| `docs/archive/**` | Historical Evidence / Archived Context | 原样保留；不作为现役合同 |
| `docs/plans/task13_5_*` | Historical / Candidate Design | 保留历史决策与阶段边界；旧 must/冻结只对当时任务有效 |
| `docs/reports/task13_5_*` | Historical Validation Evidence | 只证明对应 SHA/工作树/验收范围；不直接定义新业务 |
| `docs/evidence/**` | Validation Evidence | 原则上不改内容；通过上层索引解释适用范围 |
| Task 12 / Task 13 final handoff、evidence | Historical Implementation + Validation Evidence | 作为执行资产复用审计输入，不定义 13.6 业务口径 |
| `docs/prototypes/**` | Historical UI / Design Evidence | 仅用于理解当时信息架构和交互意图 |
| 根级入口与 `docs/*.md` | 逐项审计 | 不能仅按文件位置自动视为 Canonical |

## 4. 高影响文件逐项审计

### 4.1 `AGENTS.md`

当前角色：**Temporary Instruction**。

Task 13.6-0 已完成三段式生命周期：

- Task 13.5 原始 `AGENTS.md` 原样归档；
- 当前根级 `AGENTS.md` 只服务 Task 13.6；
- 13.6-3 根据新 Canonical 文档重新生成正式长期版。

结论：当前文件可以约束 13.6 工作方法，但不得被引用为最终业务定义。

### 4.2 `docs/archive/AGENTS_task13_5_pre_rebaseline_20260905.md`

当前角色：**Historical Evidence**。

用途：证明 Task 13.5 结束时 Codex 实际收到的仓库级指令。

处理：保持原样，不修正文案，不重新激活其中的 13.5/Issue #20 权威声明。

### 4.3 `README.md`

当前角色：**Current Entry / Needs Rewrite**。

主要问题：

- 仍以早期“运行态运营后台”阶段描述为主；
- 仍列出旧的 Dashboard / Tasks / Reviews / Notifications / Business Inputs / System 页面体系；
- 仍说明商品资料与库存录入可保存 `products.xlsx.current_stock`，与后续 DB inventory authority 方向不一致；
- “当前未做”中仍把手机 review、真实通知等已完成能力描述为未完成，部分内容已明显落后。

结论：不能作为 13.6 业务权威。13.6-3 应重写为短入口。

### 4.4 `docs/index.md`

当前角色：**Current Navigation / Mixed Authority**。

优点：覆盖大量历史计划、报告、证据和运维入口。

问题：

- 当前实现、历史计划、候选合同与验收报告在同一列表中并列；
- 阅读顺序需要依赖读者自己判断文档时代；
- 仍存在旧页面、旧阶段和旧“当前权威”语义。

结论：13.6-3 应改为“当前 Canonical 入口 + 当前实现/运维 + 历史档案”三层导航，不删除历史材料。

### 4.5 `docs/project_current_status.md`

当前角色：**Current Implementation Narrative + Historical Timeline**。

优点：PR #40 已明确 Task 13.5 `STOPPED / SUPERSEDED`，是当前阶段状态的重要入口。

问题：

- 正文长期累积 Task 13.5 各阶段实现、测试与实机验收细节；
- 当前实现、历史实现、当时计划和未来方向混合在一个长文档；
- 部分早期说明在后文已被更新，但仍需要读者自行判断先后。

结论：当前可以证明项目历史和阶段转折，但不应承担新业务 Contract。13.6-3 应收口为“当前实现能力、已知差距、当前阶段”短状态页，详细历史保留在 reports。

### 4.6 `docs/business_decision_spec.md`

当前角色：**Historical Business Design / Partial Valid Input**。

仍有价值的部分：

- PRA 不是传统“先入库再销售”的普通现货电商；
- 鲜切花业务应支持提前预测、提前销售、实际采收后履约的预测性销售逻辑；
- 预测粒度以“品种 + 等级”为重要经营粒度的方向；
- 产能、冷库、等级兼容等业务约束可以作为未来输入。

明确过时或需要重新裁决的部分：

- `trade_open_at = 前一日 23:00`、`15:30` 清库存、`17:00` 关闭交易的旧窗口；
- `actual_stock + predicted_harvest - reserved_qty` 一类 reservation 风格公式；
- “当前不接真实 RPA / 手机 review / 真实通知”的旧阶段说明；
- 将 `reserved_qty` 作为核心可承诺量扣减基础的传统库存思路，与当前 Exposure / Commitment 重基线不一致。

结论：不整份删除，也不继续作为现役 Business Spec。有效业务背景迁移到新业务基线，其余标记 Historical。

### 4.7 `docs/ai_agent_integration_spec.md`

当前角色：**Historical Accepted Design / Needs Rebaseline**。

仍有价值的安全边界：

- Agent 不应直接拼 Queue JSON、直接操作平台或绕过确定性执行基础设施；
- Agent 建议与真实平台副作用应分层；
- 不应因 Agent 概念提前增加大量专属 Schema。

需要重新裁决的部分：

- 文件写明“Task 14 只做综合验收，也不承担 Agent 实现”；当前 owner 已决定在 Task 14 增加 Agent Intervention / Ops Agent 并行工作线；
- 强制 `AgentIntent → Agent Task Adapter` 的具体形式仍属于历史 Accepted Design，应在 13.6-2 根据最终职责模型重新确认；
- 未来 Agent 对真实平台写是否永远必须逐项人工 Review，不能在本阶段永久冻结，应保留未来独立授权策略空间。

结论：安全思想可复用，任务归属与具体接口降为待重基线设计。

### 4.8 `docs/business_rule_evaluation_framework.md`

当前角色：**Current Feature / Implementation Documentation**。

它描述轻量 evaluator/proposal/dry-run/apply 框架，是当前代码功能说明，不是 PRA 的总体业务权威。

需要注意：

- Proposal 是当前规则框架内部候选结果，不应自动与未来 Sales Control Intent 合并或等价；
- 包装产能、冷库等 evaluator 可作为未来经营辅助输入，但当前人工销售主闭环不依赖它们完成；
- 文档中的 Runtime schema 版本说明可能落后，需要在 13.6-3/运维文档收口时校正。

结论：保留为功能级实现文档，不提升为 Canonical Business Contract。

### 4.9 `docs/capacity_plan_input_spec.md`、`docs/cold_storage_input_spec.md` 及类似输入规范

当前角色：**Feature Input Contract / Lower Authority**。

这些文件可以继续描述当前 Excel 输入格式和 evaluator 使用方式，但：

- 不决定供给事实模型；
- 不决定盘中 Sales Commitment；
- 不要求 13.7 为未进入主闭环的容量/冷库功能扩建系统。

处理：保留实现价值，后续根据新业务基线更新“属于主闭环还是辅助输入”的定位。

### 4.10 `docs/pra_review_risk_and_complexity_governance.md`

当前角色：**Governance Input / Draft**。

仍然重要的原则：

- 风险投入与任务风险等级匹配；
- 多平台兼容不等于分布式高可用；
- 人工平台操作是一等场景；
- 新增复杂度必须证明必要性；
- 优先运行约束、人工恢复、复用现有机制，再考虑新全局状态。

问题：文件自身状态仍为评审草案，且其中部分“当前/近期目标”基于较早阶段。

结论：治理原则继续作为 13.6 的强输入，但最终长期治理规范和正式 AGENTS 在 13.6-3 一并收口。

### 4.11 Task 12 / Task 13 final handoff 与 evidence

当前角色：**Historical Implementation + Validation Evidence**。

它们证明的重点是：

- v4/v5 平台写执行资产；
- 动态定位、旧值校验、严格串行、回读确认；
- operation/attempt、write lock、UNKNOWN → 唯一 RECONCILE；
- Queue / Worker / Importer / Watchdog 等执行基础。

它们不能证明：

- 当前 Runtime Task 生命周期已经端到端闭环；
- 当前新业务 Supply / Commitment / Exposure 语义已经实现；
- 未来 Coordinator / Intent 已经存在。

处理：13.6-2 做字段级与调用链复用审计时引用，不在 13.6-1 改写。

### 4.12 Task 13.5 plans

当前角色：**Historical / Candidate Design**。

全部默认失去现役施工授权。可以提取：

- 真实平台能力发现；
- 已经被验证的安全边界；
- 当时阶段为什么作出某种设计；
- 后续失败如何暴露该设计的不足。

不能直接提取为：

- 当前必须继续的阶段顺序；
- 当前永久不可改变的业务定义；
- 13.7 的现成施工计划。

### 4.13 Task 13.5 reports

当前角色：**Historical Validation Evidence**。

报告中“已完成”只对其绑定的代码、测试和实机范围有效。尤其必须区分：

- 代码实现通过；
- 自动测试通过；
- CI 通过；
- 真实 READ_ONLY；
- 单次明确授权的真实 COMMIT；
- 长期生产运行。

不能把较低等级证据提升为更高等级。

### 4.14 PR #39（未合并）

当前角色：**Historical Failure Evidence + Candidate Architecture**。

重要资产：

- `task13_5_7f_automation_queue_failure_analysis_20260831.md` 记录了 Runtime Task、授权、Review、Queue/Worker/Importer 之间缺少端到端推进责任的真实失败模式。

限制：

- 报告针对特定 7F 工作树与审查快照；
- PR #39 本身未合并；
- 7G Coordinator plan 是候选设计，不是当前架构合同；
- 其 AGENTS/Current Status 改动不得恢复。

处理：13.6-2 使用根因证据，不恢复 7G 阶段计划。

## 5. 当前主要冲突主题

### C-01：项目入口仍描述多个历史阶段

涉及：README、docs/index、Current Status、旧 feature docs。

影响：新 AI 可能从不同入口得到不同“当前能力”。

处理：13.6-3 收口入口；13.6-1 先建立新的业务基线候选。

### C-02：交易时间存在旧 23:00/15:30/17:00 与现 18:00/20:00 两套语义

旧业务规范仍保留早期交易窗口；Task 13.5 后续已实现 18:00 平台交易日、20:00 卖家作业日。

当前 13.6 方向：

- 当前蚂蚁花团平台 18:00 交易日切换作为已观察平台能力输入；
- 20:00 卖家作业日作为 PRA 当前运营周期输入；
- 未来平台自己的 cutoff/rollover 作为平台 capability，不把蚂蚁花团 18:00 永久写成所有平台全局规则。

### C-03：真实库存与平台目标库存被旧合同错误绑定

历史 7D 文档明确要求 `SET_ONLINE.target_inventory <= real inventory`；当前代码和测试中仍存在同类约束。

当前 owner 已重新说明：平台目标库存是 **sales exposure**，不是实物 reservation。

因此：

- 旧上限规则降为 Superseded Business Rule；
- 当前代码中的上限检查登记为 Implementation Gap；
- 13.6-1 不修改代码，13.7 根据 G1/G2 结果处理。

### C-04：供给仍以单一库存/预测字段表达，缺少逐步收敛模型

当前业务需要区分：

`PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL`

以及独立 `CARRYOVER_CONFIRMED`。

现有文档和代码没有一套统一现役合同表达这一模型。

处理：13.6-1 建立业务语义；具体持久化结构留给 13.6-2/13.7。

### C-05：Current Sales Commitment 缺少现役统一定义

现有系统有订单观察、扫描估算、销售 Summary 和库存 sales baseline，但这些对象不等同于“盘中截至当前已形成的销售承诺”。

处理：13.6-1 定义经营概念、来源和粒度原则；不提前建表。

### C-06：平台交易日与订单页显示日目前仍易混用

当前平台在 cutoff 后可能继续显示刚结束交易日，另有当前日聚合交易窗口。

处理：业务基线明确：

- `platform_trade_date`；
- `order_page_visible_trade_date`；
- `observed_at`；
- transitional aggregate observation；

是不同事实。

### C-07：每日 19:00 指定日结与现有 versioned Settlement/Summary 存在语义冲突

现有 `PlatformTradeDaySummary` / settlement 实现支持 PROVISIONAL / OBSERVED / RECONCILED / FINAL 和迟到数据 supersedes。

当前 owner 要求经营日报由每日指定 19:00 完整扫描更新一次，普通盘中扫描不得持续改写。

处理：进入 Open Decision Register；在 Business Decision Closure 一次性裁决“经营日报、迟到更正、旧 Settlement 设施”关系，不通过新名字绕开冲突。

### C-08：Agent 文档与 Task 14 新方向冲突

旧 Agent spec 明确 Task 14 不承担 Agent；当前 owner 决定 Task 14 增加 Agent Intervention / Ops Agent 并行线。

处理：任务归属必须在 13.6-1/G1 明确，具体接口在 13.6-2 重审。

### C-09：Runtime Task / Queue / Review 已存在，但业务任务端到端所有权不足

PR #39 failure analysis 是重要历史证据；当前系统不能因为 Worker/Queue 正常就宣称普通 Runtime Task 会自动推进到终态。

处理：13.6-1 只把它登记为实现事实与业务目标之间的差距；13.6-2 才决定目标职责与最小改造。

### C-10：当前人工 Controller 与历史自动规则/预测任务框架的关系需要重新定位

PRA 已有 evaluator、Automation、预测和规则生成能力，但当前阶段销售决定由人工管理者负责。

处理：

- 自动规则/预测可以作为信息、建议、确定性维护或未来策略输入；
- 不把它们自动提升为当前实时销售 Controller；
- 未来 Agent/自动策略接管必须作为独立授权阶段。

## 6. 13.6-1 后续文档处理原则

### 6.1 不批量删除旧文档

旧文档中的实现事实、平台发现和验收记录仍有审计价值。

优先：

- 改新 Canonical 入口；
- 在索引标角色；
- 归档真正失去维护价值的入口副本；
- 保留 evidence 原文。

### 6.2 不用“最新文件”覆盖所有历史

Task 13.6 新文档必须说明自身状态和通过的 Gate。只有完成对应 Review 后才升级为 Canonical。

### 6.3 不让代码反向成为业务权威

如果新业务定义与当前代码冲突：

- 记录 `Implementation Gap`；
- 保持代码不动；
- 交给 13.6-2/13.7。

### 6.4 不让历史设计自动进入 13.7

只有经过 G1 业务基线与 13.6-2 架构复用审计的内容才可成为 13.7 输入。

## 7. 当前状态

```text
Document Authority Inventory: DRAFT
Business Canonical: NOT YET ESTABLISHED
G1 Business Baseline: NOT YET VALIDATED
```
