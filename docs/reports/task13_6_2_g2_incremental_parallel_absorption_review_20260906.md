# Task 13.6-2 增量 G2：平行方案吸收 Review

日期：2026-09-06

主工作 PR：#45

主工作基线：`main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`

原 G2 Review：`docs/reports/task13_6_2_g2_architecture_handoff_review_20260906.md`

平行分析 donor：PR #44，head `d5bed5c6e6a2d7d0117a7e5ffc983a9cf3df72c4`

吸收补充：`docs/rebaseline/task13_6_parallel_analysis_absorption_addendum.md`

## 1. 增量 Review 结论

```text
Parallel Analysis Fact Verification: PASS
Business Baseline Reopen: NO
Architecture Route Change: NO
Complexity / Anti-overengineering Review: PASS
Responsibility Continuity Delta Review: PASS
Cutover / Recovery Delta Review: PASS
Agent Boundary Delta Review: PASS

P1 Incremental Blocker: 0
P2 Incremental Blocker: 0

Incremental G2 Parallel Absorption: PASS
Original G2 Architecture / Handoff: REMAINS PASS
Task 13.6-2 Stage Goal: REMAINS PASS

Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
Next: merge 13.6-2, then Task 13.6-3
```

本轮没有重新打开 OD-01～OD-06，也没有把 PR #44 升格为第二套 Canonical 架构基线。

---

## 2. Review 方法

本轮只审查平行分析带来的 delta：

1. donor 中的实现事实是否能被当前 `main` 独立证明；
2. 被吸收内容是否提高责任连续性或迁移安全；
3. 是否与 G1/G2 已冻结业务边界冲突；
4. 是否把逻辑职责过早固化成多张表/多套状态机；
5. 是否改变 Task 13.7“先端到端纵切、后扩能力”的实施策略；
6. 是否给 Agent 或 Observation Health 扩大了销售动作权限。

未重新审查已通过的 v4/v5、Queue/Worker/Importer、UNKNOWN/RECONCILE 基础安全合同。

---

## 3. 吸收项逐项 Review

### PA-01 Final-confirmation crash window — PASS / HIGH VALUE

当前实现证据支持：

- Execution preparation / consumption state 是进程内短时状态；
- submit 路径会先完成最新事实校验和 authorization audit，然后调用既有 v4/v5 publish；
- 因而 final confirmation 与 durable execution ledger 建立之间存在需要明确恢复语义的窗口。

吸收后的 IG-08 正确，因为它冻结的是**责任结果**：

> Human final confirmation 之后必须存在可恢复 durable handoff，或系统能明确安全要求重新确认。

它没有冻结 `task_dispatch_attempt` 或一套新的 Dispatch 状态机，因此不与 IG-06 冲突。

结论：`PASS`。

### PA-02 `purchase_sequence` retired provisional history — PASS

当前 Runtime Schema 代码可独立证明 `purchase_sequence` 曾位于 retired provisional order columns。

这说明未来实现必须：

- 重新按 Closing 业务合同引入；
- 重做 READ_ONLY 页面定位与 contract/version；
- 不把历史 provisional 设计直接恢复成 authority；
- 不与 `occurrence_no` 混用。

该事实强化了原 IG-07，不改变 G1。

结论：`PASS`。

### PA-03 Platform Capability logical contract — PASS

新增 logical profile 覆盖：

- timezone；
- trade cutoff；
- order rollover/history selection；
- freeze-period direct provider capability；
- expected cadence；
- Closing offset；
- effective version/range。

这与原 G2“平台差异留在 capability/adapter 层”一致，并可复用现有 Operational Time Policy 的版本/effective range。

补充明确没有冻结新表或第二平台实现，所以复杂度可控。

结论：`PASS`。

### PA-04 Cutover / rollback checklist — PASS

新增顺序把原 IG-04/05 从原则变成可实施迁移：

```text
shadow/read-only
→ compare/evidence
→ validate new path
→ old authority OFF
→ new authority ON
→ Web read cutover
→ restart/UNKNOWN/external-edit acceptance
```

rollback 只切 read path/job/authority selector，不删除历史/新 evidence。

这降低了双 authority 和 double count 风险，没有要求大爆炸式迁移。

结论：`PASS`。

### PA-05 Operations Web current-sales old Summary gap — PASS / IMPORTANT

当前 `OperationsQueryService.today()` 独立复核确认：

- 读取 `OperationalSummaryRepository` 的 current PLATFORM summary；
- 据此计算“今日已售 / 成交金额 / 成交均价”。

因此新 Commitment 实现后，如果不切 Web read model，Human Sales Controller 仍会看到旧 Summary 作为 current sales fact。

新增 IG-09 合理：Web read cutover 必须与 authority gate 同步。

结论：`PASS`。

### PA-06 Task 14-B typed facade — PASS AS HANDOFF ONLY

`OpsQueryFacade + risk-neutral Controlled Tool Facade` 符合已冻结 Agent 边界：

- Agent 读取 supported query；
- 只调用确定性、风险中性、审计型工具；
- 不创建/批准销售 Intent；
- 不提交销售 Task；
- 不发 Queue/v4/v5；
- 不发起 Closing 管理维护；
- 不拥有 deterministic recovery。

新增 IG-11 明确：该 facade 不是 13.7 deterministic lifecycle 的依赖。

结论：`PASS`。

---

## 4. 明确不吸收项 Review

### NA-01 固定新表集合 — REJECT AS G2 FREEZE

逻辑职责存在，不代表必须一一对应新表。

保持 IG-06：先审计现有 Task/history/batch/operation/attempt/receipt，再决定最小 Schema。

结论：`REJECT AS FROZEN DESIGN / KEEP AS 13.7 CANDIDATE`。

### NA-02 独立 Dispatch 状态机 — REJECT AS G2 FREEZE

现有 v4/v5 已有 batch/operation/attempt/side-effect/UNKNOWN/RECONCILE。再冻结一套完整 Dispatch 状态会增加投影一致性风险。

只保留 durable handoff / owner 责任。

结论：`REJECT AS FROZEN DESIGN`。

### NA-03 Commitment 必须持久 Snapshot — REJECT AS G2 FREEZE

需要的是 provenance、restart correctness 和 selector 可审计，不是特定表形。

结论：`KEEP IMPLEMENTATION CHOICE OPEN`。

### NA-04 Closing maintenance 只能 reread generation — REJECT AS SOLE CONTRACT

受控重扫是优先 candidate，但 owner 只冻结“管理员显式维护 + actor/reason/provenance”。

结论：`KEEP IMPLEMENTATION CHOICE OPEN`。

### NA-05 blanket retire 所有 sales-driven inventory deduction — REJECT AS PREMATURE

可冻结：

- Closing success 本身不扣库存；
- old Settlement coupling 退出新 authority；
- real Inventory ledger 保留。

不可在 G2 冻结：最终在哪个 physical/accounting event 更新 real inventory。

结论：`ACCOUNTING CONTRACT REMAINS 13.7 GATE`。

### NA-06 横向 Package A→G 作为 13.7 主顺序 — REJECT

保持原 G2 第一薄纵切：

```text
1 SKU + Human UPDATE_PRICE
→ Intent responsibility
→ Task
→ Human Authorization
→ durable handoff
→ existing v4
→ Queue / Worker / Importer
→ terminal / readback
```

必须覆盖 final-confirmation crash 或 restart/blocked recovery。

结论：`ORIGINAL G2 VERTICAL-SLICE ORDER RETAINED`。

---

## 5. 增量责任连续性情景

### ΔG2-01 Final confirmation 后 Web 立即崩溃 — PASS WITH IG-08

目标要求：

- 若 v4/v5 durable ledger 已存在：只跟踪，不重复 publish；
- 若 durable ledger 未建立：从持久 confirmation/continuation 恢复，或安全要求人工重新确认；
- 不允许 audit-only ambiguity；
- publish uncertainty 先检查既有 ledger/Queue/receipt。

无必要新增第二执行状态机。

### ΔG2-02 新 Commitment 已 shadow，但 Web 仍显示旧 Summary — PASS WITH IG-09

目标要求：

- shadow 阶段 Web current authority 不提前切换；
- authority gate 成功后 Web Today/Quality 同时切到 Current Operating State / Commitment；
- legacy Summary 只能作为历史/legacy 数据展示。

### ΔG2-03 Platform rollover/cadence 改版 — PASS WITH IG-10

目标 capability/profile 允许版本/effective range 演进；共享 domain 不硬编码蚂蚁页面细节。

### ΔG2-04 Agent 请求恢复扫描 — PASS WITH IG-11

Agent 只能通过受控 tool 请求 READ_ONLY Recovery Calibration；实际调度、权限、idempotency、run evidence 和结果仍由 deterministic system owner 管理。

---

## 6. Complexity Review Delta

```text
New daemon introduced: NO
New MQ/event bus introduced: NO
Second execution state machine frozen: NO
Fixed table count frozen: NO
Autonomous Agent authority introduced: NO
Closing maintenance implementation shape frozen prematurely: NO
Inventory accounting event frozen without evidence: NO
```

平行分析被用于增加证据和恢复边界，而不是扩张架构层级。

结论：`PASS`。

---

## 7. 修订后的 13.7 实现门禁

原 IG-01～IG-07 保持有效，新增：

- **IG-08 Final-confirmation durable handoff**：最终确认后不得存在无法恢复下一步的 audit-only ambiguity；
- **IG-09 Current-state Web cutover**：新 current authority 生效时 Web current-sales read model 同 gate 切换；
- **IG-10 Versioned Platform Capability**：时间/provider/cadence/Closing offset 可版本化，但保持物理实现最小；
- **IG-11 Agent facade is not lifecycle owner**：Task 14-B 只能读取/调用已存在的 deterministic 接口。

这些门禁都不要求 Task 13.7 在首个 PR 一次实现全部业务能力；它们只约束相关 slice 到来时必须满足的架构边界。

---

## 8. 增量 G2 最终判定

```text
Original G2: PASS
Parallel donor reviewed: PR #44 @ d5bed5c6
Accepted high-value deltas: 6
Rejected/deferred over-specific designs: 6
Business Baseline Reopen: NO
Architecture Route Change: NO
P1: 0
P2: 0

Incremental G2 Parallel Absorption: PASS
Task 13.6-2 Stage Goal: PASS
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
Next: Task 13.6-3 after merge
```

13.6-3 生成正式 Canonical 入口和最终 `AGENTS.md` 时，应读取原 G1/G2 报告以及本吸收补充；不得重新把 PR #44 的未采纳表结构/状态机候选写成永久项目规则。
