# Task 13.6 当前实现责任图

状态：`DRAFT / G2 INPUT / CURRENT IMPLEMENTATION EVIDENCE`

实现基线：`main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`

业务基线：Task 13.6-1 G1 `PASS`

> 本文回答“当前代码实际怎样工作”。它不因为某个旧实现已经存在，就赋予该实现新的业务权威。

## 1. 当前运行组成

当前 PRA 不是一个单进程应用，而是由几类明确宿主组成：

### 1.1 Operations Web

`app/operations_web/app.py`

当前 Web 组合并调用：

- `ManualTaskApplicationService`；
- `ExecutionAuthorizationApplicationService`；
- `ReviewResolutionApplicationService`；
- `InventoryApplicationService`；
- Automation configuration / maintenance 等服务。

真实经营相关入口包括：

```text
/management/tasks/preview
/management/tasks/create
/management/executions/prepare
/management/executions/submit
/management/reviews/resolve
```

Web 是人工控制入口，但 Web request 生命周期本身不承担长期后台推进。

### 1.2 Automation Service

入口：`scripts/run_automation_service.py`

职责包括：

- 根据持久 Automation Job 生成 Run；
- lease / heartbeat / catch-up；
- 在线 Pulse、Full Market Scan 等 observation；
- 可选 Order READ_ONLY；
- 旧 `PLATFORM_TRADE_DAY_SETTLEMENT`；
- `SALES_PLAN_INPUT_BUILD`；
- Review timeout / Daily Task Generation；
- 可选 Incident monitoring / Worker recovery / backup。

当前正式组合明确不注册平台写 handler。Automation Service 的方向是 scheduler / observation / maintenance，而不是销售写执行器。

### 1.3 Queue Service

入口：`scripts/run_shadowbot_queue_services.py`

长运行 cycle 当前拥有：

1. 登录/验证码状态监视；
2. ShadowBot Result Importer；
3. Queue Watchdog；
4. overdue Review reminder；
5. Notification Outbox delivery/watchdog；
6. heartbeat。

当前 heartbeat 组件表没有普通 Runtime Task / execution lifecycle coordinator。

### 1.4 ShadowBot Worker

ShadowBot / 影刀负责实际 UI 读写；文件队列用于 host 与 Worker 间交接。

现有 v4/v5 写链已经形成独立的：

- operation；
- execution attempt；
- batch / item；
- side-effect checkpoint；
- write lock；
- result receipt；
- UNKNOWN / RECONCILE。

这些是当前最成熟的真实副作用执行资产。

---

## 2. 人工销售动作当前主链

### 2.1 Task 创建

当前 Web 调用 `ManualTaskApplicationService`。

`app/services/manual_task_orchestration.py` 的边界是明确的：

> 只负责 Preview 与 Runtime Task 持久化；不生成 ShadowBot request、不写 Queue、不启动 Worker。

当前路径：

```text
Human Web
→ Manual Task Preview
→ latest product / mapping / listing / inventory checks
→ Runtime Task INSERT
→ PENDING
```

Task 会记录目标价格、目标上下架/Exposure 等信息以及 origin / decision trace。

**当前 owner 断点：** Task 持久化后，没有一个后台 owner 自动负责“等待什么、什么时候再次检查、何时进入人工授权、何时终止”。

这并不表示系统应自动执行所有 PENDING Task；它表示当前对象从“任务存在”到“执行请求形成”之间的业务责任主要依赖用户再次进入 Web 操作。

### 2.2 显式人工执行授权

当前 Web 有独立的两步链：

```text
/management/executions/prepare
→ ExecutionAuthorizationApplicationService.prepare_execution()

/management/executions/submit
→ ExecutionAuthorizationApplicationService.submit_execution()
```

`prepare_execution()` 当前做：

- authenticated capability；
- exact task ids；
- 重新读取商品、Mapping、Runtime Task、平台 observation；
- 检查 pending Review / write lock / freshness；
- UPDATE_PRICE 调 v4 build/prepare；
- SET_ONLINE / SET_OFFLINE 调 v5 proposal；
- 生成 confirmation digest；
- 10 分钟 TTL。

确认缓存位于 Web 进程内：

```text
_preparations: dict
_idempotency: dict
```

因此 Web 进程重启后未提交的 confirmation 必须重新预览/确认。这作为人工确认行为本身是安全的，但它不是可用于后台长期恢复的持久生命周期。

`submit_execution()` 再次重验所有关键事实和 digest，然后才：

- 写 authorization audit；
- 调用 v4/v5 publish；
- 形成 execution attempt / run id；
- 将请求交给 Queue / Worker。

结论：**人工点击执行的正式授权入口当前已经存在。**

### 2.3 Queue / Worker / Importer

授权发布之后，当前已有较完整执行路径：

```text
persistent batch / operation / attempt
→ Queue request
→ ShadowBot Worker
→ platform UI side effect
→ result file
→ Result Importer
→ operation / attempt / Task projection
```

Queue Watchdog 负责 queue/working 超时；Importer 不擅自把 timeout 猜成结果。

UNKNOWN 通过持久 execution evidence 进入唯一 RECONCILE，而不是盲重试。

### 2.4 当前真正缺少的不是 Worker

当前执行基础已经解决了大量“如何安全点平台”的问题。

主要结构缺口是：

> 一旦一个业务执行生命周期已形成，谁在进程重启、blocker、等待 Review、Queue/Worker 故障、授权失效、UNKNOWN/RECONCILE 与最新 Intent 变化之间持续拥有“下一步”并负责最终终止？

Queue Service 已经拥有 Importer / Watchdog 等部分箭头，但没有一个统一的业务 continuation owner。

---

## 3. Runtime Task 当前语义

`RuntimeTaskService` 当前主要负责：

- 插入 Task；
- dedupe；
- Task status 合法转移；
- task status history；
- 与 Review 的部分联动。

当前主要状态：

```text
PENDING
RUNNING
SUCCESS
FAILED
MANUAL_REVIEW
SKIPPED
CANCELLED
EXPIRED
```

它是持久业务动作记录，不是调度循环。

当前 Task 同时承载部分“目标”信息和“动作”信息，但没有一个明确的独立 one-shot Intent 责任模型来处理：

- 最新目标 supersession；
- 旧执行已经跨副作用边界后，新目标如何等待旧结果再纠正；
- 外部人工修改后旧目标如何失效/重确认；
- 重启后哪个目标仍有效。

这不证明必须新建一张 Intent 表，只证明目标职责目前没有形成清晰的持久边界。

---

## 4. 当前执行持久结构可复用程度

### 4.1 v4 UPDATE_PRICE

当前已有：

- `shadowbot_operations`；
- `shadowbot_execution_attempts`；
- `shadowbot_side_effect_checkpoints`；
- `shadowbot_commit_batches` / items；
- write lock；
- result receipts；
- retry / reconcile 相关结构。

其中 `shadowbot_execution_attempts` 已记录：

- `operation_id`；
- `execution_mode`；
- `shadowbot_run_id`；
- `status`；
- `side_effect_state`；
- `instruction_hash`；
- request SHA/path；
- start/end；
- raw output。

这意味着“已经进入平台执行”的 attempt 不需要重新发明。

### 4.2 v5 SET_ONLINE / SET_OFFLINE

另有：

- shared batch registry；
- `shadowbot_listing_action_batches`；
- batch items；
- v5 action operation/attempt；
- shared write lock；
- result import / reconcile。

目标架构应先统一**职责接口**，而不是强迫 v4/v5 立刻合成一套表。

### 4.3 持久 continuation 的缺口位置

现有 operation/attempt 最强的是“执行已经准备/发布之后”。

G1 要求的生命周期还需要覆盖：

- latest Intent 是否仍有效；
- 是否等待人工授权/重新授权；
- blocker 是否已解除；
- 是否需要在旧 attempt 终止后形成 correction task；
- 重启后由谁重新发现这些非终态业务状态。

13.7 应先尝试复用现有 Task / operation / attempt，再决定是否只增加一个极小 continuation/dispatch 层；不能预设三套新状态机。

---

## 5. Exposure 与真实库存：当前实现冲突

G1 已冻结：Platform target inventory 是 Sales Exposure，不是 real inventory reservation。

当前代码仍有两个直接硬阻断。

### 5.1 Manual Task Preview

`ManualTaskApplicationService` 当前对 SET_ONLINE：

```text
if target_inventory > balance.current_qty:
    blocker = “平台目标库存不能超过数据库库存。”
```

### 5.2 Execution Authorization

`ExecutionAuthorizationApplicationService._revalidate()` 当前再次执行：

```text
if target_inventory > balance.current_qty:
    reject
```

因此仅修改 UI 不够；preview 与 submit 前的 deterministic gate 都需要按 G1 语义调整。

需要保留的是：

- Exposure 必须非负；
- 最新平台状态和 Mapping 必须可信；
- 写前/写后验证；
- 权限、write lock、UNKNOWN 安全；
- 真实 supply / commitment 风险可提示或按策略要求确认。

需要退役的是“Exposure <= DB current_qty”这一传统 reservation 假设。

---

## 6. Observation 当前实现

### 6.1 商品 / listing observation

已有稳定资产：

- product/listing read contract；
- price / platform inventory / online status；
- immutable observation evidence；
- Mapping；
- source attempt / observed_at；
- Light/Online Pulse / Full Market Scan 的调度基础。

这些适合继续作为：

- 当前平台事实；
- QUICK-derived 销量估算输入；
- execution precondition / write readback。

### 6.2 Order Observation

已有：

- 蚂蚁订单页 READ_ONLY adapter；
- current / historical target date；
- OPEN / CLOSED；
- `order_created_at`；
- platform product / grade；
- `order_qty`；
- `order_transaction_amount`；
- page/load/scroll/end-marker completeness；
- immutable batch / item；
- mapping；
- exact target date binding。

这些是 19:00 Closing 和 rollover 后 Current Sales Commitment 的核心可复用资产。

### 6.3 `purchase_sequence`

当前没有正式采集/持久化。

`occurrence_no` 只表示同一 observation batch 内相同指纹真实重复行的多重集合序号，不能替代买家页面“第 N 次购买”。

### 6.4 `CurrentTradeDaySalesObservation`

当前 `main` 中没有对应生产实现；现有命中仅来自 G1 文档。

因此冻结期 Current Sales Commitment 的直接 provider 是明确 `MISSING`。

---

## 7. Current Sales Commitment 当前状态

当前系统已有可贡献的部件：

- Order Observation；
- product/listing observation；
- `SalesEstimateService`；
- PRA 已知 inventory/exposure adjustment evidence；
- `SalesFactSelectionService`；
-旧 Trade Day Summary / sales baseline。

但没有一个当前业务对象明确回答：

> 当前平台、当前 trade date、当前 scope 截至现在的累计销售承诺是多少，当前 provider 是谁，evidence granularity 是什么，observed_at 多新鲜？

现有 Settlement Summary 是旧“日结/结算”对象，不能直接改名当成 Current Sales Commitment。

当前 order import 还有一个重要旧耦合：

```text
Order import
→ refresh_after_order_import(old Settlement)
→ apply_current_sku_summaries(old Inventory Sales Baseline)
```

新架构必须拆开：

```text
observation import
→ Current Sales Commitment projection
```

与：

```text
Daily Sales Closing
```

以及真实库存流水的业务触发条件。

---

## 8. 旧 18:00 / 20:00 双时间轴仍在生产代码

`OperationalTimePolicy` 当前同时定义：

```text
platform_cutoff_local_time = 18:00
seller_cutoff_local_time   = 20:00
```

`OperationalTimeContext` 同时生成：

- `platform_trade_date`；
- `seller_operation_date`；
- `seller_phase`。

当前 Automation Job 也仍从 seller cutoff 派生：

- 20:00 Settlement；
- 20:05 Plan Input；
- 20:10 Daily Task Generation。

G1 已取消 20:00 第二业务日界，因此：

- `platform_trade_date` / policy version / timezone machinery 高价值可复用；
- `seller_operation_date` 作为新业务日界应退役；
- 是否保留字段做兼容/历史证据，属于迁移设计，不需要立即删 Schema；
- 20:00 可以继续是配置化 planning checkpoint，但不能通过“seller date”决定业务归属。

---

## 9. 旧 Settlement / Summary 当前仍是完整生产路径

当前仍存在：

```text
PLATFORM_TRADE_DAY_SETTLEMENT
→ TradeDaySettlementService
→ PROVISIONAL
→ OBSERVED
→ RECONCILED
→ FINAL
→ late-data version / supersedes
```

`SettlementPipeline` 还会：

- 读写 Trade Day Summary；
- 应用 Inventory Sales Baseline；
- 生成 Sales Plan Input；
- 以 `seller_operation_date` 组织下一步；
- 输出 management report / snapshot。

这套设施不能作为新业务整体保留，因为 G1 已拆成：

```text
Current Sales Commitment（动态）
+
Daily Sales Closing（19:00 独立、成功后自动锁定）
```

但以下底层资产仍有复用价值：

- order completeness / closed snapshot；
- immutable input references；
- content hash / manifest；
- scope projection 的部分代码；
- readback/audit receipt；
- management report 的部分聚合算法。

旧 `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 和 automatic late-data reopen 是 `RETIRE CANDIDATE`，不是 G1 Closing 的正常生命周期。

---

## 10. Real Inventory 当前实现

当前已经建立 DB authority：

- `inventory_authority_state`；
- `inventory_balances`；
- append-only `inventory_transactions`；
- bootstrap / adjustment；
- Inventory Provider；
- Sales Baseline。

这些解决了“Excel/DB 双库存权威”的问题，原则上应保留。

但当前销售扣减由旧 Settlement Summary 驱动：

```text
eligible current SKU summary
→ compare inventory_sales_baseline
→ delta_sold
→ SALES_DEDUCTION / SALES_RESTORE
```

G1 已把 Supply / Commitment / Closing 分开，因此该扣减触发语义需要重新审计。

13.6-2 的结论不是删除 real inventory ledger，而是：

> 保留 DB 实物库存权威，解除它与旧 Settlement 生命周期的强绑定；新业务里何时形成实物库存扣减必须由明确业务事件负责，不能因为 Exposure 或普通 observation 变化自动修改实物事实。

具体触发契约留给 13.7 设计/验证，不在本阶段改代码。

---

## 11. Supply 当前实现

现有 `HarvestForecast` 可以表达：

- variety + grade；
- forecast date；
- target trade date；
- predicted harvest qty；
- bounds / confidence / source。

但 G1 Supply 需要：

```text
production_date
PRODUCTION_FORECAST
→ HARVEST_ESTIMATE
→ PACKAGED_ACTUAL
+
CARRYOVER_CONFIRMED
```

当前没有统一持久事实链表达：

- Carryover confirmed；
- Harvest Estimate 覆盖 Forecast；
- Packaged Actual 覆盖 Harvest Estimate；
- production_date 与 trade_date 的清晰分离。

现有旧 `InventoryPlan` / reservation 风格字段仍包含 `actual_stock + predicted_harvest - reserved_qty` 一类历史语义，不应直接升级为新 Supply Authority。

结论：

- `HarvestForecast` / Excel 输入可作为 `PRODUCTION_FORECAST` 的迁移输入资产；
- 新的 Supply current-state / evidence selection 为 `MISSING`；
- 不需要为三个 stage 建三个独立子系统。

---

## 12. Incident / Observation Health 当前实现

当前已有成熟的 `OperationalIncident` 基础设施：

- detect / dedupe；
- status / severity / occurrence；
- append-only event；
- recovery event；
- Review / Token / Outbox；
- human acknowledgement；
- Worker recovery。

这些是高价值可复用机制。

但当前 `IncidentReviewService` 的 S3/S4 语义是**价格/紧急保护专用**：

- Review type = `emergency_protection`；
- 只允许 S3/S4 创建；
- 用户动作包括“改价到 / 立即下架 / 我来处理”。

因此不能把它直接当成新的 provider-centric Observation Health。

目标应：

- 复用 Incident persistence / event / notification / recovery infrastructure；
- Observation Health 自己定义 provider freshness / fallback / S3 recovery / S4 confirmed failure；
- Closing S2 使用独立低风险 incident/review 语义；
- Observation S4 不能借用 extreme-price S4 的自动下架授权。

---

## 13. Review 与通知

当前已有：

- `review_tasks`；
- Mobile Review；
- Review Token；
- Outbox；
- retry / UNKNOWN delivery；
- reminder；
- decision-first Incident Review；
- Review resolution service。

目标架构原则上继续复用这些通道。

不要为：

- Closing S2；
- execution re-confirm；
- Observation recovery escalation；

分别建设第二/第三套通知状态机。

差异应通过 review type / payload / business command 区分。

---

## 14. 当前责任链总表

| 业务对象/阶段 | 当前 owner | 当前能完成什么 | 当前断点 |
| --- | --- | --- | --- |
| Manual preview | Web + ManualTaskService | 范围、Mapping、平台状态、库存、价格检查 | 含旧 Exposure<=库存规则 |
| Runtime Task PENDING | Runtime DB | 持久动作与状态 | 没有长期 next-step owner |
| Execution prepare | Web + ExecutionAuthorization | 最新事实重验、confirmation | confirmation 仅进程内，重启需重新确认 |
| Execution submit | Web + v4/v5 publisher | 权限审计、形成 execution attempt、Queue publish | 之后业务 continuation 分散 |
| Queue pending/working | Queue/Worker/Watchdog | 串行执行、超时检测 | 各组件正确但无统一 business continuation owner |
| Result | Importer | 验证、投影 Task/operation、archive | 特殊结果/blocked 后续由不同服务处理 |
| UNKNOWN | operation/attempt + RECONCILE | fail-closed、唯一 reconcile | 最新 Intent 对齐需要上层责任 |
| Light listing observation | Automation/ShadowBot read | price/exposure/status | 尚未投影统一 Commitment |
| Current order observation | Full Scan / ORDER_SCAN | current/historical order evidence | rollover freeze provider 缺失 |
| CurrentTradeDaySalesObservation | 无 | — | MISSING |
| Daily Sales Closing | 旧 Settlement 不等价 | 旧 Summary 可结算/版本化 | 新 19:00 Closing contract MISSING |
| Supply | Excel HarvestForecast 等 | Forecast 输入 | three-stage + Carryover authority MISSING |
| Real Inventory | Inventory DB authority | balance + append-only transactions | 销售扣减仍绑旧 Settlement |
| Observation Health | Incident/Worker health 的多个组件 | incident/recovery 基础 | provider-centric S0-S4 MISSING |

---

## 15. 当前实现审计结论

当前项目并不是“需要重写”。

大量复杂且高风险的底层能力已经存在并可复用：

```text
Authentication / capability
Review / Token / Outbox
Automation schedule / lease
Product + Order READ_ONLY
Mapping
v4/v5 deterministic write contracts
operation / attempt
write lock
Queue / Worker / Importer / Watchdog
UNKNOWN / RECONCILE
DB real inventory authority
Incident event / recovery infrastructure
```

真正需要 Task 13.7 解决的是上层职责重接线：

1. 新业务 Intent 与现有 Task 的关系；
2. 已形成执行生命周期之后的持续 owner；
3. Exposure 与 real inventory 解耦；
4. Current Sales Commitment；
5. 冻结期 CurrentTradeDaySalesObservation；
6. 19:00 Daily Sales Closing；
7. Supply three-stage + Carryover；
8. provider-centric Observation Health；
9. 退役/隔离旧 20:00 seller-day / Settlement 语义；
10. 解除普通 Order Import → old Settlement → inventory deduction 的隐式耦合。

这些属于“重接业务责任”，不是重新发明 RPA 执行平台。
