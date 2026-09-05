# Task 13.6 系统架构与实现差距基线候选

状态：`DRAFT / G2 CANDIDATE / NOT YET VALIDATED`

更新时间：2026-09-06

父任务：GitHub Issue #41

业务输入：`docs/rebaseline/task13_6_business_baseline_draft.md`

G1 结论：`docs/reports/task13_6_1_g1_business_baseline_review_20260906.md`

当前实现审计基线：`main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`

> 本文件严格区分“当前实现证据”和“目标职责”。它是 G2 Review 候选，不表示目标能力已经上线，不修改 G1 业务语义，也不在 G2 `PASS` 前授权 Task 13.7 编码。

## 1. 架构结论摘要

PRA 不需要重写已经通过验收的平台执行链。目标架构应保留现有 v4 / v5、operation / attempt、写锁、Queue / Worker / Importer / Watchdog 和 UNKNOWN → 唯一 RECONCILE；新增工作集中在四个真实缺口：

1. 缺少 Supply、Carryover、Current Sales Commitment、Daily Sales Closing 和 one-shot Intent 的现役权威表达；
2. 当前 20:00 第二日界、Settlement / Summary 生命周期和自动库存销售扣减与 G1 冲突；
3. 人工最终确认到既有 v4 / v5 账本之间缺少持久化、可重启的推进 owner；
4. Observation Health 与 Task 14-B 仍缺少窄而明确的 provider / capability / controlled interface。

目标系统继续由人类管理者担任 Sales Controller。新增 Coordinator 只推进已经由人明确确认的执行，不扫描全部 `pending`、不生成销售决策，也不把 Agent 变成自动销售控制器。

## 2. 约束与非目标

### 2.1 必须保留的安全边界

- 平台写只能经过 Runtime Task、确定性校验、授权、v4 / v5、写锁、Queue、Worker、回读和 Importer；
- 任何 `UNKNOWN` 都不能猜测成功或失败，只能进入唯一 RECONCILE；
- 写前必须动态唯一定位、读取旧值并校验；写后必须独立回读；
- 外部人工平台修改是一等事实，会使旧观察、预览、授权或 Intent 失效；
- 平台页面、选择器、登录和动作差异继续封装在 adapter / executor；
- 当前 `automatic_emergency_offline=false`，Observation S4 不构成自动下架授权；
- Order / Closing 不保存客户姓名、电话、地址、聊天内容或平台订单 ID；
- 未来多平台通过 capability/profile 扩展，不提前建设分布式协调系统。

### 2.2 本基线不决定的事项

- 第二平台适配；
- AI 自动定价、自动审批或自动 Sales Controller；
- Agent 直接执行销售动作；
- Closing 的人工数字改写；
- 以历史数据重算并覆盖旧 Runtime 数据；
- 为了术语整齐而立即物理删除所有 legacy 列和历史表。

## 3. 当前实现证据

以下结论只绑定 `main@8a6e792ca6b0cd13caa20a464d21270ba4f0af6e`。

### 3.1 日期与 Automation

当前 `app/services/operational_time.py`：

- `OperationalTimePolicy` 同时保存 18:00 `platform_cutoff_local_time` 和 20:00 `seller_cutoff_local_time`；
- 校验强制 `peak_start < platform_cutoff < seller_cutoff`；
- `OperationalTimeContext` 同时计算 `platform_trade_date` 和 `seller_operation_date`。

当前 `app/services/automation.py` 默认启用：

- `ONLINE_PULSE` 每 10 分钟；
- `FULL_MARKET_SCAN` 每小时；
- 18:00 前后扫描；
- 20:00 `PLATFORM_TRADE_DAY_SETTLEMENT`；
- 20:05 `SALES_PLAN_INPUT_BUILD`；
- 20:10 `DAILY_TASK_GENERATION`。

因此，当前实现仍主动执行被 G1 supersede 的第二日界和旧 Settlement 正常链。

### 3.2 Listing / Product / Order 观察

当前已经具备：

- Listing / Product READ_ONLY 观察、范围完整性、尾部验证、hash 和 Importer；
- 动态索引列表读取、滚动到底、回顶和页面恢复；
- `ORDER_SCAN` 子运行、目标日期绑定、OPEN / CLOSED 状态、日期一致性、完整/部分/空页证据；
- Order Item 的品种/商品、等级、`order_qty`、`order_transaction_amount`、`order_created_at`；
- `occurrence_no` 用于同一订单指纹在同一批次内的多重集合序号。

当前没有 `CurrentTradeDaySalesObservation` 合同或持久化。当前也没有 `purchase_sequence`；更关键的是，v14 迁移与 Schema 检查显式把它列为“retired provisional column”。这说明 13.7 必须以新业务语义重新引入，而不能只把旧临时列恢复。

### 3.3 QUICK-derived 销售估计

`app/services/sales_estimate.py` 和 `sales_estimate_segments` 已经具备重要安全资产：

- 相邻 Exposure 观察差量；
- 15 / 25 分钟质量阈值；
- PRA 写、外部人工修改、SET_ONLINE reset、RECONCILE 等调整来源；
- `TARGET_INVENTORY` 只表示意图，不自动当作已发生调整；
- 调整 coverage、hash、去重和无法解释时的低质量/不可用结果；
- 跨交易日、映射变化、观察不完整、写入未决等禁止条件。

它是 Current Sales Commitment 的 fallback provider 候选，不是统一 Commitment 本身。

### 3.4 Settlement / Summary / Inventory

当前 `TradeDaySettlementService` 以：

```text
PROVISIONAL → OBSERVED → RECONCILED → FINAL
```

推进 `platform_trade_day_summaries`，支持 late refresh / revision、输入 manifest 和 Summary Event。

当前 20:00 Settlement Pipeline 还会：

- 选取旧日销售事实；
- 生成 Summary；
- 通过 `InventorySalesApplicationService` 和 `inventory_sales_baselines` 把 Summary 差额写为 `SALES_DEDUCTION` / `SALES_RESTORE`；
- 生成 Sales Plan Input 和管理报告；
- 让后续 Daily Task Generation 依赖该链。

这套生命周期与 G1 的 19:00 独立 Closing 冲突。尤其 `CARRYOVER_CONFIRMED` 已定义为排除上一周期 Commitment 后的剩余，再用历史 Closing 自动扣库存会产生重复扣减风险。

### 3.5 Supply / Carryover

当前存在 Product、Inventory Balance / Transaction、Harvest Forecast 等输入，但没有：

- `PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL` 的同生产日覆盖事实；
- 独立的 `CARRYOVER_CONFIRMED`；
- 把二者与 Current Sales Commitment 组合成 Current Operating State 的权威投影。

Inventory Balance 不能被重新命名成 Supply 或 Carryover；现有工作簿也不能在没有持久化来源、版本和覆盖关系时成为长期业务权威。

### 3.6 人工任务与授权

当前 Web 已把人工流程拆为：

```text
/management/tasks/preview
→ /management/tasks/create
→ /management/executions/prepare
→ /management/executions/submit
```

`ManualTaskApplicationService` 创建精确 Runtime Task，并记录 group、映射版本和观察版本。`ExecutionAuthorizationApplicationService` 能：

- 只接受精确 task IDs；
- 校验 principal capability；
- 使用 10 分钟授权有效期和 payload digest；
- 在最终提交前重新读取商品、映射、库存、Listing 和任务状态；
- 同一批只允许同平台、同动作；
- 调用 v4 改价或 v5 上下架发布。

但 preparation、消费状态和幂等映射仅在当前进程内存中。最终授权审计先写入 `task_status_history`，之后才调用发布；若进程在两者之间退出，数据库没有一个明确的“已确认、待发布”owner。现有 Queue Service 也不会消费普通 `pending` Task。

### 3.7 v4 / v5 执行与恢复

当前已持久化并经过历史实机/自动测试的资产包括：

- Commit / Listing Action Batch 与 item；
- `shadowbot_operations`；
- `shadowbot_execution_attempts`；
- side-effect checkpoint；
- write lock；
- 结果 receipt、instruction hash、request/result file hash；
- Queue inbox / working / results / archive；
- Worker 严格串行、写前旧值检查、写后回读；
- Importer 原子投影 Task / operation / attempt / lock；
- Watchdog 对 stale ready / working / phase 的恢复；
- UNKNOWN → 单一 RECONCILE；
- Review、Notification 和 Result Projection。

这些资产从 v4 / v5 账本建立后能够继续推进和恢复，目标架构不应复制第二套写执行状态机。

### 3.8 Incident / Observation Health

当前 `operational_incidents` 和 append-only event 已支持 S0～S4、去重、Review、Notification、恢复证据和 Web 查询。

但当前 severity 主要服务价格保护、库存告警、Automation / Worker 故障等既有场景，没有 Current Sales Commitment provider 的 expected cadence / capability 选择器，也没有 G1 定义的：

- S1 首次 cadence stale；
- S2 direct provider 缺失但可信 fallback 可用；
- S3 无可信校准并立即请求 Recovery Calibration；
- 实际 recovery probe 失败才 S4。

因此应复用 Incident 基础设施，不应直接复用旧价格 S4 分类器来判断 Observation Health。

### 3.9 Operations Web 与 Agent

当前 Operations Web 已有 read model、权限、CSRF、PRG、Task 创建与执行入口，但“今日已售”等页面仍读取旧 `platform_trade_day_summaries`，没有统一 Current Operating State。

当前 `app/services/ai.py` 只是 mock/null pricing suggestion provider。历史 `docs/ai_agent_integration_spec.md` 的 Query Adapter 安全思想仍有价值，但其中 Agent Task Adapter / AgentIntent 不是 Task 14-B 首版的现行授权。

## 4. 目标职责分层

目标架构只增加业务闭环缺失的层，不复制执行基础设施。

```text
Platform Capability + Supply / Carryover + Observation Facts
                              ↓
       Current Sales Commitment / Observation Health
                              ↓
                 Current Operating State
                              ↓
                    Operations Web
                              ↓
                 Human Sales Controller
                              ↓
           scoped one-shot Sales Control Intent
                              ↓
                  exact Runtime Task(s)
                              ↓
              explicit final confirmation
                              ↓
           durable Task Dispatch progression
                              ↓
          existing v4 / v5 execution ledgers
                              ↓
         Queue → Worker → readback → Importer
                              ↓
             result / review / RECONCILE
                              ↓
     Intent + Task + Operating State terminal projection
```

19:00 Daily Sales Closing 是并行历史事实链：

```text
Platform Capability / frozen order page
                     ↓
19:00 Closing control record
                     ↓
existing ORDER_SCAN mechanics + purchase_sequence
                     ↓
immutable order facts + closing generation
                     ↓
success lock OR one retry OR Closing S2 human review
```

它不参与 Current Sales Commitment provider 竞争，也不自动扣减 Carryover / Supply。

## 5. 全链责任模型

| 阶段 | 目标持久状态 | 当前 owner | 推进 trigger | 重启恢复点 | 终止条件 |
| --- | --- | --- | --- | --- | --- |
| 观察事实 | Observation Batch / Item、Supply / Carryover Fact | 对应 Importer 或人工录入服务 | 定时 READ_ONLY、明确人工保存 | 未完成批次、输入 hash、Importer 幂等键 | Accepted / Rejected / Needs Review |
| 当前承诺 | Commitment Snapshot + provider refs | Commitment Projection Service | 新 observation、provider cadence、capability 变化 | 最新 raw facts + 上次 projection hash | 新快照取代旧快照；原快照不可改 |
| 当前经营态 | Read projection | Operations Query Service | 查询时或事实变更后重算 | 权威 facts + projection refs | 无独立业务终态 |
| 人工意图 | Sales Control Intent | 人类 Sales Controller | 明确创建/确认/取消/替代 | intent scope、有效期、事实绑定 | Completed / Superseded / Expired / Cancelled / Review Required |
| Runtime Task | 现有 `tasks` | 未最终确认前仍由人负责 | 创建、复核、最终确认 | Task + status history | success / failed / skipped / cancelled / expired，或 manual review |
| Dispatch | Durable Task Dispatch | 确定性 Coordinator | 最终确认后的持久记录 | dispatch state、digest、principal、blocker、next attempt | 已进入 v4/v5、确认前取消、或人工阻塞 |
| 平台执行 | 现有 v4 / v5 ledgers | 现有 pipeline / Queue / Worker | Coordinator 发布或既有恢复触发 | batch / operation / attempt / lock / phase / receipt | Verified / Failed / Unknown / Review |
| 结果与恢复 | Importer / Watchdog / Review / Reconcile | 现有 deterministic services | 结果文件、stale 检查、人工复核 | result identity、hash、checkpoint、incident | Task 与 operation 可证明终态 |
| Intent 收口 | Intent projection | Intent Projection Service | Task / operation / review 状态变化 | 全部关联 Task / dispatch / operation | 完成、明确取消/替代，或人工接管 |
| Daily Closing | Closing Control + immutable generation | Closing Automation；失败后人工 | 19:00、唯一自动重试、管理员维护 | closing key、attempts、selected batch、success lock | Success locked 或 Closing S2 |

关键边界：

- `pending` Task 没有被最终确认时，owner 是人，不是 Coordinator；
- Coordinator 只查询 durable confirmed dispatch，不查询“所有 pending”；
- v4 / v5 operation 建立后，副作用真值归既有执行账本；Dispatch 只做上层进度投影；
- Agent 不成为任何销售动作非终态的 owner。

## 6. 目标业务事实与投影

### 6.1 Platform Capability Profile

目标逻辑实体至少包含：

- platform；
- timezone；
- `trade_day_cutoff_local_time`；
- 订单页 rollover / 可选历史日期能力；
- Current Sales Commitment provider 适用时段与 expected cadence；
- Closing 相对 trade cutoff 的调度偏移；
- profile version、effective range 和证据引用。

当前 `OperationalTimePolicy` 的版本选择、effective range 和 timezone 可参数化复用，但 `seller_cutoff_local_time` 不再拥有目标业务语义。20:00 planning checkpoint 应作为普通 planning job schedule，不应继续参与业务日期分类。

### 6.2 Daily Supply Facts

建议新增一个最小 append-only 逻辑集合 `daily_supply_facts`：

- `production_date`；
- scope type / key；
- stage：`PRODUCTION_FORECAST`、`HARVEST_ESTIMATE`、`PACKAGED_ACTUAL`；
- quantity、unit；
- revision / supersedes reference；
- source type / ref / evidence hash；
- actor、observed/recorded time；
- mapping/granularity metadata。

当前有效值由确定性投影选择：最高阶段优先；同阶段取最新有效 revision。任何阶段之间不相加。1:N 映射不能把品种+等级数量猜分给多个 SKU。

### 6.3 Carryover Facts

建议新增独立 append-only 逻辑集合 `carryover_facts`：

- 适用 `platform_trade_date`；
- scope type / key；
- `CARRYOVER_CONFIRMED` quantity；
- source / evidence / actor；
- revision / supersedes reference；
- confirmed_at。

Carryover 不从 Closing、Packaged Actual 或 Inventory Balance 自动推导。人工修订保留历史，只由最新有效 revision 进入经营态。

### 6.4 Current Sales Commitment Providers

三类 provider 的职责固定为：

| Provider | 适用条件 | 粒度 | 角色 |
| --- | --- | --- | --- |
| `CurrentTradeDaySalesObservation` | 冻结期且该页面能力可用 | 页面真实粒度，当前预期品种+等级 | direct |
| current-trade-day Order Observation | 订单页已 rollover 且目标日期可验证 | 订单事实可支持的真实粒度 | direct |
| QUICK-derived Sales Estimate | Light Scan 连续、调整 coverage 完整、质量门禁通过 | 可唯一映射时到 SKU，否则保留原粒度 | fallback |

建议新增不可变 `current_trade_day_sales_observations` 保存新的 direct 原始事实；现有 Order Observation 和 Sales Estimate Segment 原样/参数化复用。

建议新增轻量 `current_sales_commitment_snapshots` 保存每次 provider 选择结果：

- platform / trade date / scope；
- commitment quantity；
- selected provider；
- provider source refs 和 manifest hash；
- granularity、quality、observed_at、freshness deadline；
- capability/profile version；
- projection algorithm version。

Snapshot 不是第二份销售明细。它只固定“当时选了哪个 provider、为什么、用哪些事实”，使 Web、Intent digest、重启和审计能够读取同一结论。

Provider 规则：

- qualifying direct provider 接管，不与旧 provider 或 QUICK 相加；
- direct provider 失效但可信 QUICK 可用时是 S2；
- 无可信 provider 时不输出伪精确 Commitment；
- PRA 自身 Exposure 调整必须先由现有 adjustment evidence 从 QUICK 差量剥离；
- provider 改变生成新 Snapshot，旧 Snapshot 不覆盖。

### 6.5 Current Operating State

Current Operating State 是 read projection，不是新的权威总账：

```text
Carryover current fact
+ Daily Supply current fact
- Current Sales Commitment current snapshot
= Operating pressure
```

同时返回：

- price、Exposure、listing；
- observation health；
- 每个数字的 scope / granularity / source refs / freshness；
- 未映射、1:N、不完整或冲突原因。

Operating pressure 是经营判断输入，不是库存预留、不自动生成平台写，也不强制 Exposure 小于该值。

## 7. Sales Control Intent、Task 与 Dispatch

### 7.1 Sales Control Intent

现有 Task 的 `origin_ref_id` 和 `decision_trace` 无法独立表达 scope、维度、有效期、supersession 和 completion。目标需要一个 first-class、durable、one-shot `sales_control_intent` 逻辑实体，至少保存：

- intent id、actor/source；
- platform、scope 和受影响维度（price / exposure / listing）；
- 目标值或目标动作；
- based-on operating-state / observation refs 和 digest；
- created / expires / completion condition；
- supersedes / superseded-by；
- current status；
- 关联 Task IDs。

Intent 在人工确认创建任务时持久化，不把仅浏览的 preview 当作有效 Intent。

### 7.2 Supersession

- 旧动作尚未跨越副作用边界：在一个事务内把旧 Intent 标为 superseded，并把尚未执行的旧 Task 取消；
- 旧 Dispatch 已确认但尚未建立 v4 / v5 operation：只有能证明未发布/未开始副作用时才可取消；
- `QUEUED / RUNNING / RESULT_PENDING / UNKNOWN / RECONCILE`：不能删除或假取消，旧链先收口；新 Intent 可记录为等待旧链结果；
- 外部人工平台修改：写入 adjustment / observation evidence，使旧事实 digest 失效；过时 Intent 不自动把平台改回。

### 7.3 Durable Task Dispatch

建议新增一个窄的 `task_dispatch_attempt` 逻辑实体，保存：

- dispatch id、batch group、精确 task IDs manifest/hash；
- intent id；
- action/platform；
- principal subject、capability、confirmed_at、authorization expiry；
- payload / facts digest；
- state、blocker、next retry time、last error；
- 已建立的 v4/v5 batch/attempt reference；
- idempotency key hash。

最小状态语义：

```text
CONFIRMED
→ VALIDATING
→ PUBLISHING
→ EXECUTION_LEDGER_CREATED
→ RESULT_PENDING
→ TERMINAL

可分支：
CONFIRMED / VALIDATING → BLOCKED_FOR_HUMAN
PUBLISHING → RECOVERY_CHECK
RESULT_PENDING → RECONCILE_REQUIRED
```

不要求复制 v4 / v5 item 状态。`EXECUTION_LEDGER_CREATED` 之后，Dispatch 从现有 batch / operation / attempt 投影进度。

### 7.4 Coordinator 最小职责

建议新增 `TaskExecutionCoordinator`，作为现有 Queue Service 的一个确定性组件：

1. 只领取 durable `CONFIRMED` / 明确可恢复 Dispatch；
2. 重新校验 Task 状态、Intent 有效性、事实 digest、mapping、old state 和权限证据；
3. 通过现有 v4 / v5 prepare/publish 建立执行账本；
4. 对发布不确定先检查既有 batch、attempt 和 Queue 文件，绝不盲目重发；
5. 把既有 execution ledger 结果投影回 Dispatch / Intent；
6. 遇到需要新人工决定的 blocker 时停止并创建/关联 Review 或 Incident。

它不得：

- 扫描所有 `pending` Task；
- 生成、调整或审批价格/Exposure/listing 决策；
- 绕过 Web 最终确认；
- 绕过 v4 / v5；
- 让 Agent 成为授权 principal；
- 对 UNKNOWN 猜测结果。

最终确认 endpoint 应先持久化 Dispatch，再触发同进程快速推进或等待 Queue Service 拉取。这样即使确认响应、进程或发布调用中断，系统仍有明确恢复点。

## 8. Daily Sales Closing

### 8.1 目标控制记录

建议新增 `daily_sales_closing` 控制实体，以 `(platform, platform_trade_date, generation)` 标识不可变 Closing generation：

- `generation=0` 是普通自动链；
- 自动链最多两个 Automation Run attempt；
- 保存目标日期、capability version、attempt count、selected order batch、result manifest、状态和 success lock；
- 成功 generation 不再被普通自动链修改或重扫；
- 管理员维护创建新的 generation，并引用 superseded generation，不覆盖原记录。

Automation Run / Event 继续作为每次 attempt 与故障的通用账本；Closing 控制实体负责跨重启的业务唯一性和成功锁。

### 8.2 调度与失败路径

当前蚂蚁平台默认：trade cutoff 18:00，Closing offset +60 分钟，即 19:00。偏移来自 capability/profile，不作为全球常量。

```text
attempt #1 failed
→ 持久 fault evidence
→ 同一 closing generation 自动重试一次

attempt #2 failed
→ closing status = S2_REVIEW_REQUIRED
→ Operational Incident / Review
→ 停止自动重试
```

Closing 自身最高 S2。若实时 provider 同时失效，由独立 Observation Health 处理 S3 / S4。

### 8.3 管理员维护

Task 13.7 的最小维护能力选择为 **受控重新读取**：

- 仅 `SYSTEM_ADMIN`；
- 必填 platform、trade date、reason 和 idempotency key；
- 显示原 success generation、来源 batch 和 hash；
- 新建 maintenance generation，重新走 READ_ONLY 日期/范围/尾部/空页验证；
- 新 generation 成功后才成为 effective Closing；原 generation 永久可追溯；
- 不提供手工输入销量/金额或直接改旧行。

人工数字修正需要独立 provenance、双人复核和冲突策略，超出 13.7 最小实现。

### 8.4 `purchase_sequence`

`purchase_sequence` 必须作为页面展示事实重新引入：

- 在 Mayi adapter 中增加独立 selector / parser；
- Queue request/result 与 Order Observation contract 版本升级；
- Order Item 新增非 PII 字段；
- 纳入 row evidence / identity hash 时必须明确是否参与稳定身份；
- `occurrence_no` 继续表示重复指纹序号，不能替代 `purchase_sequence`；
- 页面售价仍由 amount / qty 派生，不新增第二价格采集字段。

若页面字段在受控 READ_ONLY 中不可稳定读取，应报告 capability gap，不得从订单排序或时间猜测。

## 9. Observation Health

### 9.1 实现位置

目标新增窄的 `ObservationHealthService`，输入为：

- Platform Capability Profile；
- 最新 direct/fallback provider facts；
- Automation Run、Queue/Worker health 和 recovery evidence；
- 当前 UI channel 合法占用状态。

输出为带 evidence 的 health decision。持久化复用现有 `operational_incidents` 和 append-only events：S0 不创建 Incident；S1～S4 以 provider scope 的稳定 dedupe key 创建/更新。初期不新增第二套 Health truth table。

### 9.2 状态语义

| 状态 | 判定 | 自动动作 | 销售动作影响 |
| --- | --- | --- | --- |
| S0 | 当前适用 provider 在 cadence 内且证据可用 | 无 | 正常确定性校验 |
| S1 | 首次 cadence stale，但尚未失去可信校准 | 记录/等待下一 cadence | Web 警示 |
| S2 | direct 不可用，但可信 fallback 可用 | 保留 fallback refs | 风险增加动作需额外人工确认 |
| S3 | 无可信 provider / 校准 | 幂等请求 Recovery Calibration | 风险增加动作阻止；风险降低动作仍需明确人工与新鲜旧值 |
| S4 | Recovery probe 实际证明平台级或链路级失败 | Incident / 通知 / 人工恢复 | 不自动下架，不自动猜测状态 |

合法 UI 排队或恢复处理中保持 `S3 / RECOVERING`，不因等待时间升级 S4。单 SKU 映射/字段失败不自动变成平台 S4。

### 9.3 Recovery Calibration

Recovery Calibration 应复用 Automation Run、UI channel、READ_ONLY adapter、Worker Recovery 和 Incident Event：

- stable idempotency key；
- 明确 target provider / scope / capability version；
- 执行前检查是否已有 queued/running probe；
- 成功产生新 observation 并回评 Health；
- 失败必须保存 actual probe evidence 才能进入 S4。

## 10. 过时 Exposure 规则

当前生产硬限制仅发现于：

- `app/services/manual_task_orchestration.py`；
- `app/services/execution_authorization.py`。

两处都以 DB `current_qty` 阻止 `SET_ONLINE.target_inventory > current_qty`。Schema 本身只要求非负。

Task 13.7 应：

- 删除这两个硬上限和对应旧测试预期；
- 保留非负、整数、mapping、old status、事实新鲜度、Intent scope 和权限校验；
- 在 Current Operating State / preview 中展示 Supply、Carryover、Commitment、Operating pressure 与 Exposure 差异；
- 把风险作为可解释 warning / 额外确认输入，不重新引入 reservation 风格硬限制。

## 11. Task 14-B 最小 Agent Interface

Task 14-B 首版只建设 Ops Agent，不建设 Agent Sales Controller。

### 11.1 Query Facade

Agent 只读通过 typed `OpsQueryFacade`：

- Current Operating State；
- Current Sales Commitment provider、source refs、freshness 和 Health；
- Supply / Carryover provenance；
- Intent / Task / Dispatch / operation / attempt 进度；
- Incident、Review、Automation 和 Closing 状态。

每个响应必须包含 authority role、observed/updated time、quality/health、scope/granularity 和证据引用。Agent 不直接读业务 SQLite 表、HTML、Queue JSON 或影刀日志。

### 11.2 Controlled Tool Facade

首版只开放风险中性、可审计工具：

- 请求 READ_ONLY Recovery Calibration；
- 查询并解释 blocker；
- 给 Incident 附加结构化诊断 evidence；
- 查询工具调用 receipt。

每次调用使用当前用户 principal、capability、idempotency key、参数 allowlist 和 append-only audit。Agent 不得：

- 创建或审批 Sales Control Intent；
- 提交、调整或执行销售 Task；
- 发起 Closing 管理员维护；
- 直接发布 Queue / v4 / v5；
- 修改权限、capability 或 emergency flag。

历史 Agent Task Adapter / AgentIntent 只保留为未来候选，在新的 Sales Controller 授权任务中重新评审。

## 12. 复用 / 调整 / 新增 / 退役矩阵

| 能力/资产 | 当前证据 | 分类 | 目标处理 |
| --- | --- | --- | --- |
| Platform timezone / effective policy version | Operational Time Policy / Registry | 参数化复用 | 演进为 Capability Profile；销售日期只读 trade cutoff |
| 20:00 seller day classification | `seller_cutoff_local_time` / `seller_operation_date` | 退出现役 | legacy 兼容字段保留，目标服务不得作为业务日读取 |
| 20:00 Settlement 调度 | 默认 Automation Job | 退出现役 | 切换前禁用；由 19:00 Closing 和普通 planning checkpoint 取代 |
| ONLINE_PULSE / FULL_MARKET_SCAN scheduler | Automation Scheduler / Run / lease | 参数化复用 | 保留 cadence，扩展 provider/health input |
| Product / Listing READ_ONLY | adapter、Importer、scope/end/hash | 原样复用 | 继续提供 price / exposure / listing 事实 |
| 动态列表 materialization | END/HOME/尾部/回顶 | 原样复用 | Order / 新页面只做 selector 与字段参数化 |
| Order Scan 父子约束 | FULL_MARKET_SCAN → ORDER_SCAN | 参数化复用 | 增加 Closing parent/mode，普通 child-only 规则继续 |
| Order item facts | qty/amount/created_at/grade | 参数化复用 | 加 `purchase_sequence`，保持无 PII |
| `occurrence_no` | 批次内多重集合序号 | 原样复用 | 不改名、不充当 purchase sequence |
| QUICK Sales Estimate | segments、quality、adjustment coverage | 参数化复用 | 作为 Commitment fallback，不作为统一真值 |
| Inventory adjustment evidence | PRA/manual/reset/reconcile refs | 原样复用 | Commitment fallback 先扣除已证实 Exposure 调整 |
| CurrentTradeDaySalesObservation | 当前不存在 | 确需新增 | adapter contract、raw fact、Importer、测试 |
| Commitment provider arbitration | 当前不存在 | 确需新增 | versioned deterministic projection + snapshot |
| Platform Trade Day Summary lifecycle | PROVISIONAL→OBSERVED→RECONCILED→FINAL | 退出现役 | 历史只读；不再作为 Current Commitment 或 Closing 状态机 |
| Summary input manifest / aggregation helpers | Summary Repository / Settlement | 参数化/抽取复用 | 只抽取 hash、选择和聚合助手到新 Closing；不复制旧生命周期 |
| Settlement late refresh / reopen | TradeDaySettlementService | 退出现役 | successful Closing 不自动重开 |
| Settlement → inventory deduction | InventorySalesApplicationService | 退出现役 | 禁止 Closing 自动扣 Carryover / Supply / Inventory |
| Inventory balance / transaction | Runtime v17 | 原样复用 | 保留物理/人工库存职责，不冒充 Supply / Carryover |
| Daily Supply fact | 当前不存在 | 确需新增 | 三阶段 append-only + current selection |
| Carryover fact | 当前不存在 | 确需新增 | 独立 append-only + revision |
| Current Operating State | 当前页面分散拼装 | 确需新增投影 | Query Service 组合，不建重复总账 |
| Manual task preview/create | Web Application Service | 参数化复用 | 创建 Intent + exact Tasks；preview 仍不持久化 |
| Execution prepare/submit digest | Authorization Service | 参数化复用 | preparation 可临时；最终确认必须先持久化 Dispatch |
| 内存 preparation/consumption map | 当前进程内 dict | 退出现役责任 | 不能作为最终确认后的恢复真值 |
| Runtime `tasks` / status history | SQLite Runtime | 原样复用 | 继续作为执行任务与审计，不承载全部 Intent 语义 |
| one-shot Sales Control Intent | 当前不存在 | 确需新增 | first-class scoped durable entity |
| Durable Dispatch | 当前不存在 | 确需新增 | 只桥接最终确认到 v4/v5 ledger |
| v4 / v5 batch/operation/attempt | Commit / Listing Action pipelines | 原样复用 | 不新建平行执行状态机 |
| write lock / side-effect checkpoint | v4 / v5 | 原样复用 | 完整保留 |
| Queue / Worker / Importer / Watchdog | Queue Service | 原样复用 | Coordinator 作为窄组件接入现有服务宿主 |
| UNKNOWN → unique RECONCILE | v4 / v5 / Watchdog | 原样复用 | 所有新上层状态只引用，不重写 |
| Review / Notification | Runtime services | 参数化复用 | 承接 Dispatch blocker、Closing S2、Observation Health |
| Operational Incident / Event | v15 | 参数化复用 | 新增 provider scope/category；旧 price severity 逻辑不复用 |
| Daily Closing control/success lock | 当前不存在 | 确需新增 | generation + attempts + admin maintenance |
| 现有 rerun 入口 | Automation Configuration | 参数化复用 | 升级为 SYSTEM_ADMIN + reason + Closing generation；旧 rerun allowlist 退役 |
| Operations Web read/auth/CSRF/PRG | Operations Web | 原样/参数化复用 | 页面改读 Current Operating State 和新应用服务 |
| 旧 Summary “今日已售”读模型 | Operations Query | 退出现役 | 改读 Current Sales Commitment Snapshot |
| mock/null AI pricing provider | `app/services/ai.py` | 保留现状 | 不当作 Ops Agent runtime |
| 历史 Agent Query Adapter 思想 | Agent spec | 参数化复用 | 收窄为 typed OpsQueryFacade |
| 历史 Agent Task Adapter / AgentIntent | Agent spec | 推迟/退出现役目标 | Task 14-B 首版不提供销售写 |
| automatic emergency offline | disabled flag / fence | 原样保留禁用 | S4 不自动启用或授权下架 |

## 13. 兼容与切换策略

### 13.1 不改写历史

- 旧 `platform_trade_day_summaries`、events、inputs 和 inventory sales transactions 原样保留；
- legacy `seller_operation_date` 历史值不批量重算；
- 历史报告继续按其绑定 SHA 和旧业务合同解释；
- 新页面和新服务不得把旧 Summary 自动转换成新 Closing success。

### 13.2 Legacy 日期字段

Task 13.7 初期不必一次删除所有 `seller_operation_date` 列。兼容规则固定为：

- 目标领域模型、查询和调度只接受 `platform_trade_date`、`production_date`、`order_page_visible_trade_date` 和 observed timestamps；
- legacy NOT NULL 边界若仍需写值，由单一 compatibility mapper 写入，不允许业务代码直接读取；
- compatibility mapper 不使用 20:00 换日，可临时映射为同一 `platform_trade_date` 并明确标记 legacy contract version；
- 后续在调用方全部迁移后再物理删除，不把大规模列删除放到业务闭环首个 PR。

### 13.3 切换顺序

目标切换必须遵循：

1. 先增加新 Schema / service，旧路径仍可只读；
2. 建立 Capability、Supply、Carryover、Commitment 和 Closing 新事实链；
3. 完成双写/影子投影对比，但不得把旧 Summary 当成新真值；
4. 先禁用旧 20:00 Settlement / Sales Plan / Daily Task 自动链，再启用 19:00 Closing；
5. Web 改读 Current Operating State；
6. 启用 Intent / Dispatch Coordinator；
7. 完成迁移回读、restart、UNKNOWN / RECONCILE 和外部人工修改验收；
8. 明确回滚只切回应用读路径与 Job enable 状态，不删除新事实或历史证据。

任何时候都不能同时让旧 Settlement inventory deduction 与新 Carryover / Supply 主链写同一经营结果。

## 14. Implementation Gap Register

| Gap | 优先级 | 当前风险 | 13.7 完成证据 |
| --- | --- | --- | --- |
| G-01 20:00 第二日界仍参与分类和 Job | P0 | 新业务日期被旧日界污染 | 20:00 前后 platform date 不变；目标服务无 seller day 读取 |
| G-02 旧 Settlement / Summary 仍是启用自动链 | P0 | 与 19:00 Closing 冲突 | 旧 Job 禁用；历史表只读；新 Closing 唯一锁生效 |
| G-03 Settlement 自动写 SALES_DEDUCTION | P0 | Carryover/供给重复扣减 | Closing 成功不产生 inventory sales transaction |
| G-04 缺 CurrentTradeDaySalesObservation | P0 | 冻结期无 direct current provider | READ_ONLY contract + importer + raw fact +实机样本 |
| G-05 缺 `purchase_sequence` | P0 | Closing 研究字段不完整 | selector/parser/contract/schema/readback；不与 occurrence_no 混用 |
| G-06 缺 Daily Closing control/retry/admin | P0 | 重扫、无限 retry 或无维护审计 | success lock、两次上限、Closing S2、admin generation |
| G-07 缺 Commitment arbitration/snapshot | P1 | Web 和 Intent 使用不同销售事实 | provider replacement、refs/hash/freshness 一致 |
| G-08 缺 Supply / Carryover authoritative facts | P1 | 经营态继续依赖临时表或错误库存语义 | append-only revision + stage selection + provenance |
| G-09 缺 one-shot Intent | P1 | Task 无法表达 scope/expiry/supersession | Intent 状态与 Task/operation 全链验收 |
| G-10 最终确认后缺 durable Dispatch owner | P1 | 进程退出后已确认动作悬空 | confirm-before-crash、publish-uncertain、restart 场景通过 |
| G-11 Exposure 被 real inventory 硬限制 | P1 | 预测性销售合法动作被拒绝 | 两处硬限制删除并替换为 warning/confirmation |
| G-12 缺 Observation provider health | P1 | stale/缺失/真实链路失败混淆 | S0-S4 + Recovery probe 情景通过 |
| G-13 Web 仍以旧 Summary 表示今日已售 | P1 | 当前经营态展示错误 | Today/Quality 改读 Commitment/Health |
| G-14 Agent 接口未收窄 | P2 / Task 14-B input | 未来可能直连表或扩大授权 | typed query + risk-neutral controlled tools contract |

## 15. Task 13.7 实现输入

13.7 应拆成可独立审查、按依赖排序的小包；下面是职责包，不预先绑定 PR 编号。

### Package A：Capability 与旧链隔离

- 引入 target Capability Profile / compatibility mapper；
- 20:00 从日期分类中退出；
- 新增 19:00 Closing job 定义但先保持关闭；
- 准备旧 Settlement / Sales Plan / Daily Task job 的受控禁用与回滚；
- 禁止新目标代码读取 `seller_operation_date`。

### Package B：Supply / Carryover / Operating State

- Schema、Repository、Service、manual input；
- 三阶段覆盖和同阶段 revision；
- Carryover 独立；
- scope / mapping / provenance；
- Operating pressure read projection。

### Package C：Current Sales Commitment

- `CurrentTradeDaySalesObservation` adapter / contract / Importer；
- 复用 Order Observation 和 QUICK Estimate；
- provider arbitration / snapshot / freshness；
- PRA adjustment evidence；
- Web shadow read model 和对比报告。

### Package D：Daily Sales Closing

- Order reader 增加 `purchase_sequence`；
- Closing control generation、19:00 run、唯一成功锁；
- 一次 retry、Closing S2、Review / Incident；
- SYSTEM_ADMIN maintenance generation；
- 旧 Summary 与 inventory deduction 退出现役。

### Package E：Intent / Dispatch / Coordinator

- first-class one-shot Intent；
- durable final confirmation / Dispatch；
- Coordinator 接入 Queue Service；
- pre-side-effect supersession、post-side-effect收口；
- restart / publish-uncertain / result-late / UNKNOWN / unique RECONCILE；
- 删除 `target_inventory <= current_qty` 硬限制。

### Package F：Observation Health 与 Web 收口

- provider cadence / capability evaluator；
- Incident/Event 复用；
- S3 Recovery Calibration 与 actual-probe-only S4；
- Current Operating State 成为 Operations Web 当前事实入口；
- 旧 Summary 页面标记为历史或移出当前导航。

### Package G：Task 14-B 接口合同

- 只交付 OpsQueryFacade 与 risk-neutral Controlled Tool contract；
- 不实现 Agent Sales Controller；
- 与 Task 14-A 共用 integration gate 输入。

每个 Package 开工前仍须遵守项目“原样调用 → 参数化 → 抽取 → 新增”的复用门禁，并提供实际复用矩阵。

## 16. G2 验收情景

G2 Review 至少逐项确认目标职责在以下情景无 owner 断点：

### 16.1 18:30 冻结期

```text
platform_trade_date = D+1
CurrentTradeDaySalesObservation = D+1
order_page_visible_trade_date = D
```

Commitment 使用 D+1 direct provider；Closing 只读取 D；两者不相加、不互相覆盖。

### 16.2 19:00 Closing 成功

一次完整 READ_ONLY 保存 order facts、purchase sequence、hash 和 success lock。普通自动链再次到期时跳过，不重扫。

### 16.3 Closing 双失败

第一次失败保存 evidence 并只重试一次；第二次失败生成 Closing S2 / Review，停止自动重试，不升级 Observation S4。

### 16.4 20:00 Planning

Planning checkpoint 可以录入 Carryover / Supply 或形成新 Intent，但不能改变 `platform_trade_date`，也不能启动旧 Settlement lifecycle。

### 16.5 Supply convergence

Forecast 120 → Harvest 115 → Packaged 113，current Daily Supply 依次为 120 / 115 / 113；Carryover 40 独立；不相加三个阶段。

### 16.6 Exposure adjustment

PRA 把 Exposure 100 → 150，后续读到 142。QUICK 先绑定 +50 adjustment evidence；若 coverage 不完整，不输出“销售 8”的伪精确结论。

### 16.7 Intent supersession

旧价格 9.5、新价格 10.5：未发布旧动作可事务性取消；已经 queued/running/unknown 的旧动作先收口，新 Intent 等待新鲜回读后再决定。

### 16.8 Final confirmation crash

用户最终确认后进程立即退出。重启后 Coordinator 从 durable Dispatch 恢复；若 v4/v5 账本已存在则只跟踪，不重复发布。

### 16.9 Late result / UNKNOWN

迟到结果按 result identity / hash 幂等导入。UNKNOWN 只创建或复用唯一 RECONCILE；新 Dispatch 不复制 reconciliation 状态机。

### 16.10 External manual edit

外部人工修改平台 Exposure/price 后，新 observation / adjustment evidence 使旧 digest 失效；旧 Intent 不自动回写平台。

### 16.11 Observation Recovery

首次 stale 为 S1；direct 丢失但可信 QUICK 为 S2；无可信校准为 S3 并发起唯一 probe；排队保持 RECOVERING；实际 probe 失败才 S4。

### 16.12 Agent boundary

Agent 能解释 Operating State、请求 READ_ONLY recovery 并取得 receipt；不能创建 Intent、提交 Task、发起 Closing 维护或直接访问 Queue。

## 17. G2 Review 门禁

G2 `PASS` 前必须满足：

- 11 个 G1 转交问题均有明确架构结论；
- 每个非终态有 owner、trigger、restart behavior 和 terminator；
- 复用矩阵没有平行复制 v4 / v5、Queue、Review 或 Incident；
- 旧双日界、Settlement、Summary 和 inventory deduction 的退出顺序明确；
- 目标事实没有把 Supply、Carryover、Commitment、Exposure 和 Inventory 混为一体；
- Closing 与 realtime Commitment 完全分离；
- Agent 权限不超过人类当前授权；
- Task 13.7 包含可执行依赖、切换、回滚和验收输入；
- 文档 UTF-8、相对链接和仓库 diff 检查通过；
- Review 结论单独记录，不能用本文自评代替。

## 18. 当前 Gate 输出

```text
G1 Business Baseline: PASS
13.6-2 Current Implementation Audit: DRAFT COMPLETE
13.6-2 Target Responsibility Model: DRAFT COMPLETE
13.6-2 Reuse / Gap Matrix: DRAFT COMPLETE
G2 System Architecture & Gap Review: NOT RUN
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
