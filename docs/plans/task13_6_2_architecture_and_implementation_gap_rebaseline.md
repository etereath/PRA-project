# Task 13.6-2：系统架构与实现差距重基线

状态：`DRAFT / G2 INPUT / NO PRODUCTION CODE`

基线：`main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`

父任务：GitHub Issue #41

前置：Task 13.6-1 / G1 Business Baseline = `PASS`

## 1. 任务定位

Task 13.6-2 不再重新讨论“业务应该怎样运行”，而是回答：

> 在 G1 已冻结的业务基线下，当前 `main` 到底已经实现了什么、真实责任链在哪里断开、哪些现有资产可以原样复用、哪些必须调整、哪些能力确实缺失，以及 Task 13.7 应从哪条最薄的真实业务纵切开始。

本阶段属于 Architecture / Implementation Gap Review，不是生产功能开发。

## 2. 证据边界

本阶段只使用：

- `main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e` 的生产代码、测试与当前仓库文档；
- G1 已通过的业务基线和 Decision Closure；
- 已经位于 `main` 的历史验证报告，仅用于解释当前资产来源与已知失败模式。

不得：

- 将未合并分支或其他 PR 当作当前实现；
- 因为历史计划中存在类名或表名就假定当前代码已经实现；
- 用测试通过代替真实平台运营事实；
- 用当前代码反向修改 G1 业务语义。

如果代码与 G1 冲突，登记为 `Implementation Gap / Adapt / Retire Candidate`，不静默改变业务基线。

## 3. 本阶段交付物

### 3.1 当前实现责任图

文件：`docs/rebaseline/task13_6_current_implementation_map.md`

按真实业务旅程追踪：

```text
Web / Automation entry
→ Application Service
→ Runtime persistence / authorization
→ Queue handoff
→ Worker
→ Result Importer / Watchdog / Reconcile
→ Runtime terminal state / readback
```

并分别审计：

- observation；
- Current Sales Commitment；
- Daily Sales Closing；
- Supply；
- real inventory；
- Execution lifecycle；
- Review/notification；
- Observation Health / Incident；
- 18:00 / 20:00 时间语义。

### 3.2 目标职责与实现 Gap 矩阵

文件：`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`

必须区分：

- `REUSE`：业务职责与当前实现方向一致，原则上原样复用；
- `ADAPT`：资产有价值，但职责或业务语义需要调整；
- `MISSING`：G1 要求存在，但当前 `main` 没有实现；
- `RETIRE CANDIDATE`：当前仍在运行/存在，但已被 G1 业务语义取代；
- `DEFER`：当前阶段不需要实现。

矩阵不提前冻结数据库表数、类名或模块名。

### 3.3 Task 13.7 handoff 候选

不单独制造大型施工计划；在目标职责文档中给出：

- 13.7 Stage Goal；
- 第一条薄纵切；
- 后续扩展顺序；
- 每个纵切的业务能力和必须覆盖的失败/恢复路径。

最终施工细化留给 13.7 kickoff。

## 4. G1 已冻结、13.6-2 不得重开的业务前提

### 4.1 销售日界

- 当前蚂蚁平台 18:00 `platform_trade_date` 是唯一销售业务日界；
- 20:00 只作为 Carryover / Forecast / Strategy planning checkpoint；
- `seller_operation_date` 的 20:00 换日语义属于当前实现遗留，需要审计迁移/兼容，不是新业务要求。

### 4.2 Supply

```text
PRODUCTION_FORECAST
→ HARVEST_ESTIMATE
→ PACKAGED_ACTUAL
```

为同一 `production_date` 的覆盖关系；`CARRYOVER_CONFIRMED` 是独立事实轴。

### 4.3 Current Sales Commitment

- 冻结期：`CurrentTradeDaySalesObservation` 是当前交易日直接销售 provider；
- rollover 后：Full Scan 中 current-trade-day Order Observation 为直接 provider；
- Light Scan 始终提供平台状态和 QUICK-derived 辅助；
- provider 接管当前 Commitment，不与旧 provider/估算相加。

### 4.4 Daily Sales Closing

- 19:00 独立扫描已冻结的上一交易日订单页；
- 成功后自动链不得再次重扫同平台/交易日；
- 第一次失败允许一次自动重试；第二次失败 Closing S2 + 人工；
- 后续维护必须管理员显式发起；
- `purchase_sequence` 是当前最小采集缺口；
- 页面单价由 `order_transaction_amount / order_qty` 派生，不增加第二采集源。

### 4.5 Exposure

平台 target inventory 是 Sales Exposure，不是 real inventory reservation。

### 4.6 Intent / execution

人工 Sales Control Intent 是有范围、有有效期、有完成条件的 one-shot business intent。

- 尚未跨副作用边界的旧目标可被新 Intent supersede；
- 已 QUEUED/RUNNING/UNKNOWN 的旧执行必须先收口真实结果，再判断是否需要纠正；
- 外部人工平台修改不能被过时 Intent 自动改回。

### 4.7 Observation Health

S0～S4 是 realtime observation provider 健康语义；Closing 自身失败最高 S2。Observation S4 不继承价格极端风险的自动下架授权。

## 5. 审计方法：先看箭头，不只看方框

每个非终态业务对象都必须回答：

1. 当前 owner 是谁；
2. 当前阶段是什么；
3. 下一步是什么；
4. 什么事件/条件触发下一步；
5. 失败如何处理；
6. 进程重启后如何恢复；
7. 谁负责让它最终终止。

重点路径：

```text
Human decision
→ one-shot Intent
→ Runtime Task
→ deterministic validation / authorization
→ persistent execution lifecycle
→ Queue
→ Worker
→ Importer / Watchdog / RECONCILE
→ terminal
→ new observation
```

以及：

```text
Scheduled Observation
→ provider-specific read
→ immutable evidence
→ current operating projection
→ health / recovery
```

## 6. 架构复杂度约束

### 6.1 不增加第三个后台守护进程

当前已经存在：

- Automation Service：计划窗口、读观察、维护类 handler；
- Queue Service：Importer、Watchdog、登录监视、Review reminder、Notification Outbox；
- ShadowBot Worker：平台 UI 执行端。

如果 execution lifecycle 需要长期 owner，优先将其作为 Queue Service 的一个 composition component，而不是再建新 daemon。

### 6.2 Coordinator 不拥有经营决策

目标执行协调职责不得：

- 扫描所有 `pending Runtime Task` 并自行授权执行；
- 决定哪个价格更好；
- 绕过人工/确定性授权；
- 在 UNKNOWN 时猜测副作用。

它只负责已经形成的持久执行生命周期在 blocker、Queue、Worker、Importer、RECONCILE 和重启之间持续有 owner。

### 6.3 不强制“一概三张新表”

逻辑上需要区分 Intent / Task / Execution lifecycle，但 13.6-2 不要求它们各自对应一张新表。

13.7 kickoff 应先审计现有：

- `tasks`；
- `shadowbot_operations`；
- `shadowbot_execution_attempts`；
- v4/v5 batch ledger；
- Task history / Automation events；

再决定最小持久化扩展。

## 7. 本阶段明确不做

- 不改 `app/`、`shadowbot/`、生产 scripts；
- 不改 Runtime Schema；
- 不改真实 Runtime DB；
- 不写真实 Queue request；
- 不操作平台；
- 不实现 CurrentTradeDaySalesObservation；
- 不实现 Closing；
- 不实现 Coordinator；
- 不实现 Supply 新模型；
- 不实现 Agent；
- 不提前删除旧 Settlement / seller-day 代码。

## 8. G2 Review 入口

13.6-2 可以进入 G2 Architecture / Handoff Review 的最低条件：

- [ ] 当前实现图以 `main@8a6e792...` 的真实 wiring 为依据；
- [ ] 每条核心责任链都标出当前 owner 与责任断点；
- [ ] G1 业务对象映射到 `REUSE / ADAPT / MISSING / RETIRE / DEFER`；
- [ ] target architecture 没有 ownerless handoff；
- [ ] 不以“安全停止”冒充“业务完成”；
- [ ] 不用新 daemon、MQ、分布式锁解决当前单机可处理的问题；
- [ ] Task 13.7 第一薄纵切能产生真实用户能力；
- [ ] Task 13.7 仍未开始生产编码。

当前状态：

```text
Task 13.6-2: IN PROGRESS
G2 Architecture / Handoff: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
