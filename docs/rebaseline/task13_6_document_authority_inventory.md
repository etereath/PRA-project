# Task 13.6 文档与证据权威盘点

状态：`POST-CLOSURE / G1 INPUT`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41

## 1. 目的

本文件回答：

> 当前仓库中的不同文档、Issue、报告、代码和证据分别能证明什么？哪些是现役输入，哪些只能作为历史或实现证据？Business Decision Closure 后，主要冲突已经如何收口？

本盘点采用“目录默认角色 + 高影响文件逐项审计 + 冲突状态”，避免把数百份历史材料逐篇改写，也避免“最新文件自动覆盖一切”。

## 2. 权威角色

### 2.1 Temporary Instruction

当前根级 `AGENTS.md` 只在 Task 13.6 期间控制 Codex 工作方式，不是长期 PRA 业务权威。13.6-3 必须退役并由正式版替换。

### 2.2 Business Baseline Candidate

- `docs/rebaseline/task13_6_business_decision_closure.md`：owner Business Decision Closure 裁决记录；
- `docs/rebaseline/task13_6_business_baseline_draft.md`：已经吸收 OD-01～OD-06 的 G1 业务基线候选。

G1 PASS 前仍不得授权 13.7 编码。

### 2.3 Current Implementation Evidence

指定 Git SHA 的生产代码、正式入口和真实调用链。

它回答“当前系统实际上做什么”，不能反向决定“业务应该怎样运行”。如果代码与 G1 业务语义冲突，登记 Implementation Gap，交 13.6-2 / 13.7。

### 2.4 Validation Evidence

测试、CI、真实 READ_ONLY、受控 COMMIT、故障恢复和长期运行记录。

证据只在其绑定 SHA、数据、平台、工作树和验收范围内成立；自动测试、CI、真实 READ_ONLY、单次受控 COMMIT 与长期生产可用不能互相替代。

### 2.5 Historical / Candidate Design

旧业务规范、历史 Issue、任务计划、未合并 PR、旧 AGENTS 和历史 AI 建议。

这些材料可以提供背景、成功资产、失败根因和平台事实，但不自动限制当前业务基线。

## 3. 目录默认角色

| 路径 | 默认角色 | 处理规则 |
| --- | --- | --- |
| `docs/archive/**` | Historical Evidence / Archived Context | 原样保留，不作为现役合同 |
| `docs/plans/task13_5_*` | Historical / Candidate Design | 保留历史决策和阶段边界；旧 must/冻结不继续生效 |
| `docs/reports/task13_5_*` | Historical Validation Evidence | 只证明对应 SHA/工作树/验收范围 |
| `docs/evidence/**` | Validation Evidence | 原则上不修改，通过上层索引解释适用范围 |
| Task 12 / Task 13 final handoff、evidence | Historical Implementation + Validation Evidence | 作为执行资产复用输入，不定义新业务语义 |
| `docs/prototypes/**` | Historical UI / Design Evidence | 仅用于理解历史交互与信息架构 |
| 根级入口与 `docs/*.md` | 逐项审计 | 文件位置不能自动赋予 Canonical 权威 |

## 4. 高影响文件逐项审计

### 4.1 `AGENTS.md`

角色：**Temporary Instruction**。

13.6-0 已原样归档 Task 13.5 旧版，并启用临时 13.6 指令。13.6-3 必须根据新 Canonical 文档生成正式长期版。

### 4.2 `docs/archive/AGENTS_task13_5_pre_rebaseline_20260905.md`

角色：**Historical Evidence**。

用途只包括证明 Task 13.5 结束时 Codex 实际收到的项目指令。保持原样，不重新激活其中的 Issue #20 / 13.5 权威声明。

### 4.3 `README.md`

角色：**Current Entry / Needs Rewrite**。

问题包括旧页面体系、旧库存入口和已经过期的“当前未做”说明。13.6-3 应重写为短入口；当前不能作为业务合同。

### 4.4 `docs/index.md`

角色：**Current Navigation / Mixed Authority**。

当前实现、历史计划、候选合同和验收报告仍混在同一导航层。13.6-3 应收口为“Canonical / Current Implementation & Operations / Historical Archive”三层导航。

### 4.5 `docs/project_current_status.md`

角色：**Current Implementation Narrative + Historical Timeline**。

PR #40 已正确冻结 Task 13.5 `STOPPED / SUPERSEDED`，但正文过长且混合历史实现、当前状态和未来方向。13.6-3 应缩为当前能力、已知差距和当前阶段短状态页。

### 4.6 `docs/business_decision_spec.md`

角色：**Historical Business Design / Partial Valid Input**。

仍有价值：预测性销售背景、品种+等级经营粒度、产能/冷库等辅助业务输入。

已 supersede：

- 前一日 23:00 / 15:30 / 17:00 的旧交易窗口；
- reservation 风格 `actual_stock + predicted_harvest - reserved_qty` 公式；
- 与当前真实 RPA / Mobile Review / 通知能力不一致的旧阶段描述。

### 4.7 `docs/ai_agent_integration_spec.md`

角色：**Historical Accepted Design / Needs Architecture Rebaseline**。

继续有效的安全思想：Agent 不直接拼 Queue JSON、直连平台或绕过确定性执行基础设施；不因 Agent 概念提前增加大量专属 Schema。

已 supersede：旧文件“Task 14 不承担 Agent”的任务归属。当前 G1 已冻结 Task 14-A / 14-B 双工作线。

`AgentIntent / Agent Task Adapter` 的具体形式仍是历史 Accepted Design，13.6-2 重新审查。

### 4.8 `docs/business_rule_evaluation_framework.md`

角色：**Current Feature / Implementation Documentation**。

Evaluator / Proposal / dry-run / apply 是现有功能，不是 PRA 总体业务权威。Proposal 不自动等价于未来 Sales Control Intent。

### 4.9 `docs/sqlite_runtime_persistence_plan.md`

角色：**Current Implementation Documentation / Mixed Historical Progress**。

可以证明 Runtime SQLite、tasks/review/outbox/operation/attempt 等结构当前已经存在，但文中的阶段性“下一步”与 schema 叙事不能决定新业务语义。13.6-2 以当前代码和 schema 实际状态为准。

### 4.10 `docs/product_inventory_input_spec.md`

角色：**Current Feature / Transition History**。

其中 DB inventory authority、不可变 transaction 和工作簿切换历史是实现输入；但历史 `SET_ONLINE.target_inventory <= inventory` 方向已被 G1 的 Exposure 语义 supersede。代码中的相关上限约束属于 Implementation Gap。

### 4.11 `docs/capacity_plan_input_spec.md`、`docs/cold_storage_input_spec.md` 等

角色：**Feature Input Contract / Lower Authority**。

继续描述当前 Excel 输入与 evaluator 使用方式，但不决定 Supply、Current Sales Commitment 或实时销售 Controller。

### 4.12 `docs/pra_review_risk_and_complexity_governance.md`

角色：**Governance Input / Draft**。

继续有效的原则包括风险投入匹配、人工平台操作是一等场景、多平台不等于分布式系统、复杂度必须由真实需求证明。13.6-3 再与新治理规范、正式 AGENTS 收口。

### 4.13 Task 12 / Task 13 handoff 与 evidence

角色：**Historical Implementation + Validation Evidence**。

重点证明：

- v4/v5 平台写执行资产；
- 动态定位、旧值校验、严格串行和回读；
- operation/attempt、write lock、UNKNOWN → 唯一 RECONCILE；
- Queue / Worker / Importer / Watchdog。

不能证明新的 Supply / Commitment / Closing / Intent 已经实现，也不能证明普通 Runtime Task 当前一定端到端推进到终态。

### 4.14 Task 13.5 plans / reports

角色分别为 **Historical / Candidate Design** 与 **Historical Validation Evidence**。

可以提取平台发现、已验证安全边界、当时设计理由和失败根因；不得直接恢复旧阶段顺序或旧业务合同。

### 4.15 PR #39（未合并）

角色：**Historical Failure Evidence + Candidate Architecture**。

其 7F failure analysis 对“组件正确但责任链断裂”的根因仍有价值；7G Coordinator plan 只是候选设计，不能恢复为当前施工授权。

## 5. 主要冲突主题与当前状态

### C-01：项目入口仍混合多个历史阶段

状态：`OPEN / 13.6-3 DOCUMENT ENTRY GAP`

README、index、Current Status 和若干 feature docs 仍会给新读者不同“当前项目”答案。13.6-3 统一入口，不在 13.6-1 批量改历史文档。

### C-02：旧交易时间与双日界

状态：`RESOLVED BUSINESS / IMPLEMENTATION GAP FOR 13.6-2`

历史上存在：

- 23:00 / 15:30 / 17:00 旧业务窗口；
- Task 13.5 的 18:00 platform day + 20:00 seller day 双日界。

当前 G1 业务方向已经收口为：

- 当前蚂蚁平台 18:00 `platform_trade_date` 是唯一销售日界；
- 20:00 只是 planning checkpoint；
- `seller_operation_date` 作为第二业务日界已被 supersede；
- 未来平台 cutoff 是各自 Platform Capability。

当前代码仍保留双日界，交 13.6-2 做迁移/兼容审计。

### C-03：真实库存与 Platform Exposure 被旧合同错误绑定

状态：`RESOLVED BUSINESS / IMPLEMENTATION GAP`

Platform target inventory 已冻结为 Sales Exposure，不是 real inventory reservation。历史 `SET_ONLINE.target_inventory <= real inventory` 规则已 supersede；当前代码/测试中的同类限制交 13.6-2/13.7 处理。

### C-04：Supply 缺少逐步收敛模型

状态：`RESOLVED BUSINESS / IMPLEMENTATION GAP`

G1 已定义：

`PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL`

以及独立 `CARRYOVER_CONFIRMED`。具体持久化结构未冻结。

### C-05：Current Sales Commitment 缺少现役统一定义

状态：`RESOLVED BUSINESS / IMPLEMENTATION GAP`

冻结期由 `CurrentTradeDaySalesObservation` 作为直接 provider；订单页 rollover 后由 current-trade-day Order Observation 作为直接 provider；Light Scan 提供持续状态和 QUICK-derived 辅助。

### C-06：平台交易日、订单页显示日与冻结期实时销售曾被混用

状态：`RESOLVED BUSINESS / PLATFORM CAPABILITY INPUT`

当前明确区分：

- `platform_trade_date`：唯一销售业务日；
- `CurrentTradeDaySalesObservation`：冻结期当前交易日实时销售直接观察；
- `order_page_visible_trade_date`：订单页实际显示日期，只服务 capability/Closing/rollover 判断；
- `observed_at`：技术观察时间。

不再使用模糊的“transitional aggregate observation”作为主业务术语。

### C-07：19:00 日结与旧 Settlement/Summary 冲突

状态：`RESOLVED BUSINESS / ARCHITECTURE REUSE GAP`

当前业务已冻结：

- Current Sales Commitment 与 `Daily Sales Closing` 完全分离；
- 19:00 独立 Closing Order Scan 读取被冻结上一交易日；
- Closing 首次失败自动重试一次，第二次失败最高 Closing S2 并交人工；
- Closing 成功后自动链不得为同平台/交易日再次扫描；
- 后续维护只能由管理员显式发起；
- 20:00 Settlement 和 `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 不再是现行 Daily Closing 业务流程。

旧订单扫描、完整性验证、hash/evidence、sales baseline 和管理报告等底层能力如何复用，归 13.6-2。

### C-08：Agent 文档与 Task 14 新方向冲突

状态：`RESOLVED BUSINESS / ARCHITECTURE INTERFACE GAP`

Task 14 冻结为：

- 14-A Integrated Acceptance & Freeze；
- 14-B Agent Intervention / Ops Agent。

Agent 具体接口仍归 13.6-2 重审。

### C-09：Runtime Task / Queue / Review 存在但端到端推进责任不足

状态：`OPEN ARCHITECTURE GAP / 13.6-2`

这是 PR #39 failure analysis 的核心有效根因。G1 只要求每个非终态业务动作具有 owner、next step、trigger、restart behavior 和 terminator；目标架构由 13.6-2 决定。

### C-10：人工 Controller 与历史自动规则/预测框架关系

状态：`RESOLVED BUSINESS`

当前实时销售 Controller 是人类管理者。Evaluator、Automation、预测可以提供信息、建议或确定性维护能力，但不自动升级成实时 Sales Controller。未来 Agent/自动策略需要独立授权。

### C-11：Daily Sales Closing 研究字段实现差距

状态：`IMPLEMENTATION GAP / 13.6-2 INPUT`

- `order_created_at` 已实现；
- `purchase_sequence` 尚未采集/持久化，是明确最小缺口；
- `occurrence_no` 不能代替复购序号；
- 页面售价不新增独立采集字段，由现有 `order_transaction_amount / order_qty` 稳定派生。

## 6. 后续文档处理原则

### 6.1 不批量删除历史

历史文档中的实现事实、平台发现和验收记录仍有审计价值。优先通过 Canonical 入口和角色标注解决权威混乱。

### 6.2 不让代码反向成为业务权威

如果 G1 业务定义与当前代码冲突：登记 Implementation Gap，不在 13.6-1 顺手改代码。

### 6.3 不让历史设计自动进入 13.7

只有 G1 业务基线与 13.6-2 架构复用审计共同确认的内容才能成为 13.7 实现输入。

### 6.4 G1 后业务变化必须显式 reopen

G1 PASS 后，只有新的真实平台事实、owner 主动改变经营要求或发现 G1 内部逻辑矛盾时才允许标记：

`BUSINESS BASELINE REOPENED`

架构文档不得静默改变业务语义。

## 7. 当前状态

```text
Document Authority Inventory: POST-CLOSURE / READY FOR G1 RETEST
Business Decision Closure: CLOSED
Business Baseline Candidate: READY FOR G1 RETEST
G1 Business Baseline: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
