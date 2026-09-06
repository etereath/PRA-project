# Task 13.6-2 G2 Architecture / Handoff Review

日期：2026-09-06

实现证据基线：`main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`

工作分支：`chatgpt/task13-6-2-architecture-gap-rebaseline`

工作 PR：#45

G1 业务基线：`PASS`

## 1. Review 结论

```text
Current Implementation Mapping Review: PASS
Target Responsibility Review: PASS
Architecture Continuity Review: PASS
Complexity / Reuse Review: PASS
G2 Architecture / Handoff: PASS
Task 13.6-2 Stage Goal: PASS

Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
Next: Task 13.6-3
```

G2 PASS 表示：目标职责链已明确、当前实现与目标差距已分类、Task 13.7 的第一纵切和后续扩展方向具备可执行输入。

G2 PASS 不表示生产功能已经实现；Task 13.7 仍必须等待 13.6-3 完成入口收口、正式 AGENTS 切换和 cold-start 验收。

---

## 2. Review 范围

本 Review 只检查：

- 当前 `main` 的真实 wiring 是否被准确描述；
- G1 业务对象在目标架构中是否有明确责任 owner；
- 非终态对象是否有 next step / trigger / restart / terminator；
- 是否优先复用 v4/v5、Queue/Worker/Importer、Review/Outbox、Inventory、Incident 等现有资产；
- 是否存在不必要的新 daemon / MQ / 分布式机制；
- 旧 20:00 seller-day / Settlement 到新业务模型是否有可控 cutover；
- Task 13.7 是否能从一条薄纵切开始，而不是再次做大爆炸式重写。

没有重新打开 OD-01～OD-06。

---

## 3. 当前实现核对结果

### 3.1 Human Task → Execution

确认：

- `ManualTaskApplicationService` 当前明确止于 Runtime Task persistence；
- Operations Web 已有 `prepare_execution → submit_execution` 人工授权链；
- execution submit 之后已有 v4/v5 persistent operation/attempt、Queue、Worker、Importer、Watchdog、UNKNOWN/RECONCILE；
- 当前缺口不是“没有平台执行器”，而是上层业务 continuation 没有统一长期 owner。

### 3.2 Exposure 冲突

确认当前至少两处仍执行旧硬规则：

```text
SET_ONLINE.target_inventory <= DB current_qty
```

分别位于：

- Manual Task Preview；
- Execution Authorization submit 前 revalidation。

因此必须作为 13.7 `ADAPT`，不能只改 UI。

### 3.3 Observation / Closing

确认：

- current/historical Order READ_ONLY、日期选择、order_created_at、qty、amount、完整性、tail/hash 已实现；
- `CurrentTradeDaySalesObservation` 当前没有生产实现；
- `purchase_sequence` 当前没有正式持久化；
- old TradeDaySummary/Settlement 不能直接改名成为新 Closing。

### 3.4 双日界 / Settlement

确认生产代码仍使用：

```text
18:00 platform cutoff
20:00 seller cutoff
20:00 Settlement
20:05 Plan Input
20:10 Daily Task Generation
```

并保留：

```text
PROVISIONAL → OBSERVED → RECONCILED → FINAL
late-data supersedes
```

这些是当前实现事实，不再是 G1 业务权威。

### 3.5 Real Inventory

确认 DB real inventory authority、append-only transactions、balance/version/idempotency 已经成熟。

需要调整的是 old Settlement-driven sales baseline coupling，不是重做 Inventory subsystem。

### 3.6 Incident

确认 generic Incident persistence / event / recovery / Review / Outbox 可复用。

但现有 S3/S4 `emergency_protection` Review 明确绑定“改价到 / 立即下架 / 我来处理”等价格保护动作，因此 Observation Health 不能直接继承其 action authorization。

---

## 4. G2 必须携带到 13.7 的实现门禁

以下不是新的 owner 业务决策，而是为了保证目标职责真正可实现而冻结的工程门禁。

### IG-01：PENDING Task 等待授权时必须有明确 owner

Coordinator 不扫描所有 PENDING Task 自动执行，并不意味着这些 Task 无 owner。

目标：

```text
Task PENDING / MANUAL_EXECUTION_AUTHORIZATION_REQUIRED
owner = Human + Operations Web workflow
next trigger = explicit prepare/submit
terminator = submit / supersede / cancel / expire
```

短时 `ExecutionPreparation` confirmation 属于可失效授权令牌：

- 可以保留进程内；
- Web 重启后失效并要求重新确认；
- 不把它当成后台必须恢复的持久 business lifecycle object。

### IG-02：已有开放 Task 不得阻止记录新的人工决定

当前 Manual Task Preview 会因同 SKU+平台已有 `pending/running/manual_review` Task 而直接 blocker。

G1 后目标必须变成：

```text
new Human Decision
→ always record valid new Intent first
```

然后：

- 旧 Task 尚未跨 side-effect boundary：supersede/cancel old action，生成新 Task；
- 旧 Task 已进入 durable execution / RUNNING / UNKNOWN：不删除；新 Intent 成为 latest valid target，等待旧执行收口；
- 收口后按最新 observation 对比 latest Intent，需要时生成 correction Task；
- correction Task 仍走正常授权。

因此“已有开放 Task”以后只能是 execution scheduling/supersession 条件，不能继续作为拒绝新业务决定的理由。

### IG-03：Queue Service coordinator 必须错误隔离

优先将 coordinator 加入 `run_shadowbot_queue_services.py`，不新建 daemon。

但必须保证：

- 单个 continuation/attempt 错误 → 记录事件并继续其他对象；
- coordinator component 自身可恢复错误 → 不杀死 Importer/Watchdog/Review/Notification；
- 只有 DB/schema/process 等宿主级不可继续故障才允许服务整体 FAILED。

当前 Queue Service 已对多数组件使用独立 try/except；新增 coordinator 必须沿用该隔离模式，不扩大宿主故障面。

### IG-04：新旧 sales authority 不得同时生效

Task 13.7 可以按 slice 逐步开发：

- Current Sales Commitment；
- Closing；
- Supply；
- Observation Health。

但在 cutover 前，新路径若与 old Settlement/Inventory coupling 有重叠，只能：

- shadow / read-only；或
- 明确限定不写旧 authority 所拥有的业务结果。

切换时必须有一个明确 authority gate：

```text
old Settlement-driven business authority OFF
→ new Commitment / Closing / planning authority ON
```

禁止 old Summary 和 new Commitment 同时作为销售扣减/计划生成的业务权威。

### IG-05：Real Inventory sales application 必须有唯一明确契约

保留 DB Inventory ledger。

13.7 在解除 old Settlement coupling 前必须明确：

- 哪个业务事实/事件允许更新 real inventory sales balance；
- Current Sales Commitment、Carryover、Daily Supply 如何避免同一销售责任重复扣减；
- late observation / provider calibration 是否只调整 operating projection，还是触发 inventory accounting correction；
- rollback/cutover 后不能重新启用过时 Summary 作为第二库存权威。

至少用跨日数值样例证明 no double count，再切换真实 inventory authority 接线。

### IG-06：Intent / continuation 先审计复用，再新增 Schema

逻辑上必须有可恢复的 one-shot Intent 和 persistent execution continuation。

物理实现必须先审计：

- `tasks` / origin / history；
- v4/v5 batch；
- `shadowbot_operations`；
- `shadowbot_execution_attempts`；
- Task history / Automation events。

只有这些无法表达必要 responsibility 时才新增最小表。

不得机械形成：

```text
Intent table + Task table + Dispatch table + Coordinator table
```

四套重叠状态机。

### IG-07：新平台 READ_ONLY provider 先验收再进入 authority selector

`CurrentTradeDaySalesObservation` 和 `purchase_sequence` 都属于新采集能力。

必须先完成：

- 真实页面 READ_ONLY 定位；
- 数据类型/粒度；
- 失败/空值语义；
- immutable evidence；
- 重放；
- 真实页面回归；

通过后才能成为 Current Sales Commitment / Closing 的正式 provider。

---

## 5. 固定情景 Review

### G2-A：人工改价正常成功 — PASS

责任链：

```text
Human
→ Intent owner: Business Application
→ Task owner: Human/Web until authorization
→ Authorization owner: ExecutionAuthorization
→ durable operation/attempt owner: Queue Service lifecycle
→ Worker
→ Importer/Watchdog
→ terminal
→ platform readback/observation
```

无 ownerless handoff。

### G2-B：新 Intent 到来，旧动作未发布 — PASS

新 Intent 可成为 latest；旧未跨副作用边界的 Task 可 supersede。

### G2-C：新 Intent 到来，旧动作已 RUNNING/UNKNOWN — PASS

旧 attempt 不删除；UNKNOWN 先 RECONCILE；完成后再按 latest Intent 产生 correction Task。

### G2-D：Queue Service 重启 — PASS

目标 coordinator 从 persistent operation/attempt/continuation 重新发现非终态对象，不依赖 Web 内存。

### G2-E：18:00 freeze — PASS

```text
platform_trade_date = D+1
CurrentTradeDaySalesObservation = D+1 direct provider
order_page_visible_trade_date = D
```

Current Commitment 和 previous-day Closing 互不污染。

### G2-F：19:00 Closing success — PASS

成功 Closing 形成 auto-rescan lock；普通 Full Scan/catch-up 不得重新创建 Closing。

### G2-G：Closing 两次失败 — PASS

Closing 自身最高 S2 + human；realtime Observation Health 仍独立评级。

### G2-H：Exposure > real inventory — PASS

这是合法 Intent/Task 输入；旧 reservation hard blocker 需要删除。Supply/Commitment/Exposure 可以做风险视图，但不能伪装成 reservation invariant。

### G2-I：Supply convergence — PASS

Carryover 40；Forecast 120 → Harvest 115 → Packaged 113。

Daily Supply current value依次 120/115/113，不相加；Carryover 独立。

### G2-J：Observation S4 + extreme-price S4 — PASS

Severity 可同为 S4，但 action authorization 独立；Observation failure 不获得 SYSTEM_EMERGENCY offline 权限。

### G2-K：old/new authority cutover — PASS WITH IG-04/05

新路径在正式 cutover 前保持 shadow/read-only，切换时确保 old Settlement-driven authority 不与新业务 authority 并存。

---

## 6. Complexity Review

### 不新增 daemon — PASS

- observation 继续 Automation Service；
- execution continuation 复用 Queue Service host；
- Worker 不重写。

### 不新增 MQ — PASS

当前单机 SQLite + file queue + periodic recovery 足以表达 restart correctness。

### 不重写 v4/v5 — PASS

现有 side-effect safety / UNKNOWN / RECONCILE 优先复用。

### 不提前冻结 Schema — PASS

目标文档冻结 logical responsibility，不冻结表数/类名。

### 不让 Agent 修补确定性系统缺口 — PASS

Agent 仍在 Task 14-B；13.7 的 deterministic lifecycle 不依赖 Agent。

---

## 7. Task 13.7 Handoff Review

推荐 Stage Goal：

> PRA 人工销售控制闭环重构与执行生命周期实现。

第一薄纵切：

```text
1 SKU + human UPDATE_PRICE
→ persistent one-shot Intent
→ Task
→ human authorization
→ existing v4 execution
→ Queue/Worker/Importer
→ terminal/readback
```

必须同时验证：

- PENDING Task human owner；
- restart 后 durable execution owner；
- one blocked/recovery path；
- no automatic execution of unrelated PENDING Tasks。

后续按：

```text
Exposure semantics
→ Current Sales Commitment existing order provider
→ CurrentTradeDaySalesObservation
→ Daily Sales Closing
→ Supply
→ Observation Health
→ old Settlement/seller-day cutover
```

逐步扩展。

这个顺序允许先证明责任连续性，再逐步替换业务数据模型；旧/new authority 重叠阶段必须遵守 IG-04。

---

## 8. G2 最终判定

```text
P1 Architecture Blocker: 0
P2 Architecture Blocker: 0
Business Baseline Reopen: NO
New Daemon Required: NO
New MQ Required: NO
Production Code Changed in 13.6-2: NO

G2 Architecture / Handoff: PASS
Task 13.6-2 Stage Goal: PASS
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
Next: Task 13.6-3
```

本报告作为 G2 Gate 记录，后续 13.6-3 应把 G1/G2 结果收口到 Canonical 入口与正式 `AGENTS.md` 候选中。
