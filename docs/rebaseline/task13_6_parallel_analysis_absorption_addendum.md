# Task 13.6-2 平行架构分析吸收补充

状态：`G2 SUPPLEMENT / PARALLEL ANALYSIS ABSORBED`

日期：2026-09-06

主架构基线：

- `docs/rebaseline/task13_6_current_implementation_map.md`
- `docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`
- `docs/reports/task13_6_2_g2_architecture_handoff_review_20260906.md`

平行分析来源：PR #44，head `d5bed5c6e6a2d7d0117a7e5ffc983a9cf3df72c4`

工作主线：PR #45

> 本文只吸收已经由当前 `main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e` 或 G1/G2 现有契约独立验证的高价值发现。PR #44 不因此成为第二套 Canonical 架构基线；其未采纳设计继续保持候选/历史输入角色。

## 1. 吸收原则

本轮采用以下规则：

1. 业务语义仍以 G1 PASS 为最高现役业务输入；
2. 当前实现事实必须能在 `main@8a6e792...` 上独立复核；
3. 只吸收能提高责任连续性、迁移安全或未来 Agent 边界清晰度的内容；
4. 不因为平行分析提出了新表名/类名，就提前冻结物理 Schema；
5. 不改变 G2 已通过的第一实施原则：Task 13.7 先证明一条真实人工销售纵切可以端到端收口，再扩展其余业务能力；
6. 不新增 daemon、MQ、平行 v4/v5 状态机或自动 Sales Controller。

---

## 2. 吸收项 PA-01：Final-confirmation crash window

### 2.1 当前事实

当前 `ExecutionAuthorizationApplicationService` 的短时 preparation、消费状态与幂等映射保存在 Web 进程内存。

在最终 `submit_execution()` 中，当前顺序包含：

```text
revalidate
→ record authorization audit
→ publish via existing v4/v5
```

因此存在一个必须在 13.7 明确处理的恢复窗口：

```text
Human 已完成最终确认
→ authorization audit 已写入
→ 进程在 v4/v5 durable execution ledger / Queue publish 被可靠建立前退出
```

此时不能只依赖“用户再点一次”或 Web 内存判断，也不能盲目重复发布。

### 2.2 吸收后的责任门禁 IG-08

**IG-08：最终确认之后、平台发布之前必须存在可证明的 durable handoff。**

13.7 可以采用不同物理实现，但必须满足：

- 最终确认完成后，系统能从持久事实判断该确认是否已经进入可恢复 execution continuation；
- 若现有 v4/v5 batch / operation / attempt 已成功建立，重启后只跟踪现有 ledger，不再次发布；
- 若尚未建立 execution ledger，则必须能从持久确认/continuation 事实安全恢复或明确要求人工重新确认；
- 不允许出现只有一条 authorization audit、但系统无法判断“应继续发布还是应重新确认”的永久悬空状态；
- publish uncertainty 必须先查现有 batch、operation、attempt、Queue/receipt，再决定下一步；不得直接重发副作用。

### 2.3 明确不冻结

本补充**不规定**必须创建 `task_dispatch_attempt` 表，也不冻结：

```text
CONFIRMED → VALIDATING → PUBLISHING → ...
```

这套独立 Dispatch 状态机。

13.7 仍先审计能否通过现有 Task history、v4/v5 batch、operation、attempt、receipt 与极小 continuation metadata 表达该 durable handoff；只有不足时才新增最小结构。

---

## 3. 吸收项 PA-02：`purchase_sequence` 的 retired provisional 历史

### 3.1 当前事实

当前 Runtime Schema 迁移/检查逻辑中，`purchase_sequence` 出现在 order item 的 retired provisional columns 集合中。

因此“页面第 N 次购买”并不是一个从未出现过的名字；但旧 provisional 设计已经退出当前正式订单合同。

### 3.2 对 13.7 的含义

`purchase_sequence` 需要**按新的 Daily Sales Closing 业务合同重新引入**，不能把历史 provisional column 简单恢复为现役字段。

必须重新确认：

- 蚂蚁页面真实元素定位；
- 整数解析与空值语义；
- request/result contract version；
- Order Observation Item 持久化；
- evidence/hash/row identity 中是否参与稳定身份；
- 与 `occurrence_no` 的严格区分；
- READ_ONLY 实机验收与重放。

若真实页面不能稳定读取，结果必须是 capability gap，不得从排序、时间或买家信息推断。

页面售价仍不新增独立采集项：

```text
page_unit_price = order_transaction_amount / order_qty
```

---

## 4. 吸收项 PA-03：Platform Capability Profile 责任扩展

G2 原有“Platform Capability”职责进一步明确为一个**逻辑、版本化 capability/profile**，至少能回答：

- platform；
- timezone；
- `platform_trade_date` cutoff；
- 订单页 rollover 行为；
- 是否支持显式历史日期读取；
- `CurrentTradeDaySalesObservation` 是否存在、何时适用；
- direct/fallback provider 的 expected cadence/capability；
- Closing 相对 trade cutoff 的计划偏移；
- effective range / profile version；
- 证据来源。

当前 `OperationalTimePolicy` 的 timezone、version/effective range、18:00 cutoff 机制可以参数化复用。

20:00 planning checkpoint 只能作为普通计划时间，不再属于 business-date capability。

### 4.1 实现边界

本轮只冻结逻辑责任，不规定：

- 必须新增 `platform_capability_profiles` 表；
- 必须把所有页面 selector 配置化；
- 第二平台现在就实现。

平台 UI 细节继续封装在 adapter/executor；共享 domain 只读取 capability outcome。

---

## 5. 吸收项 PA-04：Legacy cutover / rollback checklist

G2 的 IG-04/IG-05 继续是 authority 原则；本补充把迁移顺序写得更具体。

### 5.1 推荐 cutover 顺序

```text
1. 新事实/投影以 shadow 或 read-only 方式建立
2. 对比新旧输出与 evidence，不让新路径写旧 authority 所拥有的经营结果
3. 通过新 provider/Closing/Supply/Intent 的各自验收
4. 明确关闭 old Settlement-driven business authority / 相关 Job 写路径
5. 再启用新 Current Sales Commitment / Closing / planning authority
6. Operations Web 切换读取 Current Operating State
7. 验证 restart / blocked / UNKNOWN / RECONCILE / external manual edit
8. 完成 acceptance 后再考虑物理删除 legacy 字段或表
```

### 5.2 rollback 原则

回滚优先只改变：

- application read path；
- job enable/disable；
- authority selector/config。

不得通过回滚删除：

- 已采集的新 immutable observation；
- 新 Closing evidence；
- 新 Intent/Task/execution history；
- 旧历史 Summary / transaction evidence。

### 5.3 禁止状态

任何阶段都不得出现：

```text
old Settlement sales authority = ON
AND
new Commitment/Closing/Supply path = 第二个可写 sales authority
```

也不得让旧 Settlement inventory deduction 与新 accounting path 同时解释同一销售责任。

---

## 6. 吸收项 PA-05：Operations Web 当前仍读旧 Summary

### 6.1 当前事实

当前 `OperationsQueryService.today()` 会读取：

```text
OperationalSummaryRepository
→ current PLATFORM summaries for current platform_trade_date
```

并据此计算：

- `今日已售`；
- 成交金额；
- 成交均价。

所以即使新的 Current Sales Commitment/Observation 已经实现，如果 Web 不切读，Human Sales Controller 仍可能继续看到旧 Summary 作为“当前销售事实”。

### 6.2 新实现 Gap

增加明确 Gap：

> **Current Operating State / Current Sales Commitment 成为 authority 后，Operations Web 的 Today/Quality/相关当前销售读模型必须同步切换；旧 Summary 只保留历史/legacy 展示。**

该切换必须与 PA-04 authority gate 同步，不能早于新 current-state authority 的验收。

---

## 7. 吸收项 PA-06：Task 14-B typed Agent boundary

Task 14-B 首版继续不是 Agent Sales Controller。

本补充把 G2 的未来 Agent 边界细化为两个 logical facade；这只是 handoff contract，不是 13.7 第一纵切的生产依赖。

### 7.1 `OpsQueryFacade`

Agent 只读查询至少可以覆盖：

- Current Operating State；
- Current Sales Commitment quantity/provider/source refs/freshness/health；
- Supply / Carryover provenance；
- Intent / Task / execution continuation / operation / attempt；
- Incident / Review / Automation / Closing 状态。

响应必须携带足够 provenance，例如：

- authority role；
- scope / granularity；
- observed/updated time；
- quality / health；
- source/evidence refs。

Agent 不直接读业务 SQLite 表、HTML、Queue JSON 或影刀日志作为正式 Query API。

### 7.2 risk-neutral `Controlled Tool Facade`

首版可考虑的受控工具仅限风险中性、确定性、可审计操作，例如：

- 请求 READ_ONLY Recovery Calibration；
- 查询并解释 blocker；
- 给 Incident 附加结构化诊断 evidence；
- 查询 tool receipt / run result。

每次调用仍必须绑定：

- 当前 authenticated principal/capability；
- idempotency key；
- 参数 allowlist；
- append-only audit/receipt。

### 7.3 首版明确禁止

Agent 不得：

- 创建/批准 Sales Control Intent；
- 提交或调整销售 Runtime Task；
- 代替 Human final confirmation；
- 发起 Closing 管理员维护；
- 直接写 Queue / v4 / v5；
- 修改权限、capability、emergency flag；
- 直接拥有 deterministic recovery lifecycle。

---

## 8. 明确不吸收 / 降级为 13.7 候选的设计

以下来自平行分析的设计有参考价值，但不会作为 G2 已冻结结论写入主架构：

### NA-01 固定新增多张业务表

不冻结：

- `daily_supply_facts`；
- `carryover_facts`；
- `current_trade_day_sales_observations`；
- `current_sales_commitment_snapshots`；
- `sales_control_intent`；
- `task_dispatch_attempt`；
- `daily_sales_closing`。

这些名称可作为实现候选，但 13.7 必须先做现有 Schema/ledger 复用审计。

### NA-02 固定 Dispatch 状态机

不冻结平行的 Dispatch 状态序列。目标只冻结：

> 最终确认以后必须有 durable owner/continuation，v4/v5 ledger 建立后不复制底层执行状态机。

### NA-03 Commitment 一定持久 Snapshot

Current Sales Commitment 可以持久 snapshot，也可以由 immutable raw facts + deterministic selector 重建。必须持久的是可追踪 provenance/restart correctness，不是某张特定 snapshot 表。

### NA-04 Closing 管理员维护只能“重新扫描 generation”

已冻结业务要求只有：

- 成功后普通自动链不重扫；
- 后续由管理员显式维护；
- 保留 actor/reason/audit/provenance。

受控重新读取是优先 candidate，但不是唯一被允许的未来维护形状。

### NA-05 blanket retire 所有 sales-driven inventory deduction

已冻结：

- Closing success 本身不能自动成为库存扣减理由；
- 旧 Settlement-driven inventory coupling 必须退出当前业务 authority；
- real Inventory ledger 保留。

尚未冻结：

- 最终采用哪个 physical/accounting event 更新 real inventory sales balance。

该 accounting contract 仍由 13.7 在 no-double-count 数值验收前明确。

### NA-06 以 Capability→Supply→Commitment→Closing→Intent/Dispatch 的横向 Package 顺序开工

不采纳为 13.7 主顺序。

G2 继续要求第一刀先完成：

```text
1 SKU + Human UPDATE_PRICE
→ one-shot Intent responsibility
→ Task
→ Human Authorization
→ durable handoff
→ existing v4
→ Queue / Worker / Importer
→ terminal / readback
```

并覆盖 final-confirmation crash 或 restart/blocked recovery。

原因：先证明“箭头”连续，再逐步增加业务事实能力，避免重复 Task 13.5 的组件正确但整体无人推进问题。

---

## 9. 新增/强化的 13.7 实现门禁

在原 IG-01～IG-07 基础上增加：

### IG-08：Final-confirmation durable handoff

Human final confirmation 后，在任何可能丢失 Web 进程的点，都必须能从持久事实恢复下一步或安全要求重新确认；不得形成 audit-only ambiguous state。

### IG-09：Current-state Web cutover

Current Sales Commitment / Current Operating State 成为 authority 时，Operations Web 当前销售读模型必须同 gate 切换；旧 Summary 不得继续作为 Human Controller 的 current sales source。

### IG-10：Capability 版本化但物理实现保持最小

trade cutoff、rollover/provider capability、expected cadence、Closing offset 必须可版本化/审计；不得因此提前建设第二平台、分布式配置中心或大规模 selector DSL。

### IG-11：Task 14-B facade 不是 13.7 deterministic lifecycle 依赖

13.7 必须先独立完成 deterministic observation/execution/recovery；Task 14-B 只能读取和调用已存在的受控接口，不能成为其 owner 或补丁层。

---

## 10. 对 G2 结论的影响

本补充没有重新打开 G1，也没有改变 G2 的核心架构路线。

吸收后仍保持：

```text
Human = current Sales Controller
Automation Service = scheduled READ_ONLY / Closing / recovery host
Execution Authorization = deterministic human gate
Queue Service = preferred persistent execution lifecycle host
Worker / Importer / Watchdog / UNKNOWN→RECONCILE = REUSE
Current Sales Commitment != Daily Sales Closing != Supply != Real Inventory != Exposure
Observation severity != action authorization
No new daemon / MQ / autonomous Sales Agent
```

变化仅是：

- final-confirmation crash 成为显式必测恢复窗口；
- `purchase_sequence` 的历史语义被准确记录；
- Platform Capability logical contract 更完整；
- cutover/rollback 更可执行；
- Web current-sales read gap 被明确登记；
- Task 14-B handoff contract 更清晰。

增量 Gate 结论记录在：

`docs/reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md`
