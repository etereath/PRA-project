# Task 13.6 Business Decision Closure

状态：`G1 DECISION CLOSURE / OWNER DECISIONS RECORDED`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41  
工作 PR：#43

> 本文件记录 Task 13.6-1 一次性 Business Decision Closure 的 owner 裁决。  
> 它用于更新业务基线并进入 G1 Review，不直接授权 Task 13.7 编码。

## 1. 已关闭的决策项

### OD-01 — CLOSED：Supply / Carryover / Commitment

- `CARRYOVER_CONFIRMED`：进入新销售周期时，已经确认未被上一周期销售承诺占用、仍可继续销售/履约的剩余量。
- `PACKAGED_ACTUAL`：目标生产日新包装出的生产总量事实。
- `Current Sales Commitment`：目标平台当前交易日累计已形成的销售承诺，不等同于平台 Exposure。
- 同一生产日 `PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL` 为覆盖关系，不相加。
- 当前经营压力可以基于 `Carryover + 当前有效 Daily Supply - 当前交易日 Commitment` 理解，但安全缓冲、损耗和履约状态不得被偷偷混入这些基础事实。

### OD-02 — CLOSED：Current Sales Commitment 的 observation provider

Current Sales Commitment 不通过把多个来源相加得到，而是选择当前可用的直接平台销售 observation provider，并以 QUICK-derived 作为辅助/fallback。

当前蚂蚁平台：

- 订单冻结期：`CurrentTradeDaySalesObservation` 是当前平台交易日的实时销售直接观察；它天然属于平台跨日后的当前交易日，不在该对象内部处理 D / D+1 映射。
- 订单页正常显示当前交易日后：Full Scan 中的 current-trade-day Order Observation 成为直接销售 observation provider。
- Light Scan 始终用于价格、Exposure、上下架及 QUICK-derived 辅助估算。
- 直接平台观察只证明其真实粒度；例如 `品种 + 等级 + 累计数量` 不得在 1:N SKU 映射时任意拆分。
- 新的 qualifying provider 接管当前 Commitment 值，不与旧估算/旧 provider 重复相加；历史 evidence 保留。

### OD-03 — CLOSED：单一 18:00 日界、Current Sales Commitment 与 Daily Sales Closing

#### 3.1 唯一销售日界

取消 20:00 `seller_operation_date` 作为独立业务换日语义。销售业务统一以平台 `platform_trade_date` 为核心日界；当前蚂蚁平台 18:00 切换到新的平台交易日。

20:00 左右只保留为一个经营计划 checkpoint，不再定义第二个销售日界。

#### 3.2 Current Sales Commitment 与 Daily Sales Closing 分离

- `Current Sales Commitment`：动态盘中状态，回答当前平台交易日截至现在已经形成多少销售承诺。
- `Daily Sales Closing`：上一已结束平台交易日的历史日结，用于回顾、研究和后续分析。
- 两者不是同一个 Summary 生命周期，也不互相改写。

#### 3.3 19:00 Daily Sales Closing

19:00 对已经冻结的上一交易日订单页执行一轮独立 Closing Order Scan。它不参与当前交易日的实时 Commitment。

成功的 Daily Sales Closing 至少需要保存以下业务事实：

- 品种；
- 等级；
- 数量；
- 下单时间 `order_created_at`；
- 页面可见的“第 N 次购买”/复购序号 `purchase_sequence`。

页面售价属于 Closing 的重要研究指标，但不新增第二套采集/持久化事实。当前订单链已经使用页面展示单价与 `order_qty` 通过 `Decimal` 精确相乘生成 `order_transaction_amount`，因此售价按以下公式从现有事实派生：

```text
page_unit_price = order_transaction_amount / order_qty
```

`order_qty` 为正整数时，该派生值等于当前订单采集链用于计算成交金额的页面单价。Closing 展示、统计或研究需要售价时应按该公式派生；不得再增加独立售价采集字段造成同一事实的双来源和一致性风险。

此外还应保留必要的审计元数据，例如平台、目标交易日、观察批次、`observed_at`、完整性/尾部验证和内容 hash；但不要求采集买家 PII 或平台订单 ID。

#### 3.4 Closing 失败与重试

Closing 是低风险历史事实采集，失败不应驱动与实时销售观察相同的高等级故障升级。

```text
19:00 Closing attempt #1
    ├─ SUCCESS → 写入 Daily Sales Closing 并锁定自动采集
    └─ FAILED  → 生成故障报告 + 自动重试一次

Closing attempt #2
    ├─ SUCCESS → 写入 Daily Sales Closing 并锁定自动采集
    └─ FAILED  → 升级为 Closing S2 + 呼叫人工复核
```

规则：

- 单纯 Closing 失败最高只到 `S2`，不继续自动升级 S3/S4。
- 第二次失败后停止自动重试，交由人工处理。
- 若同一底层故障同时影响 Current Sales Commitment 的实时 observation provider，则实时 observation health 可以独立按其自身规则进入 S3/S4；不能因为 Closing 风险低而压低实时观察故障等级。

#### 3.5 Closing 成功后的不可重扫规则

Closing 一旦通过完整性、日期和尾部验证并确认成功：

- 项目自动链不得再为同一平台/交易日发起新的 Closing 扫描；
- 后续发现问题、平台数据异常或历史事实需要更正时，必须由管理员从维护入口显式发起维护；
- 自动任务不得通过普通 retry、补跑、late-data refresh 或版本化 settlement 机制静默重开该日 Closing；
- 管理员维护必须留下原因、操作者和修改/维护记录。具体维护入口是否允许受控重读或只允许人工修正，由 13.6-2 在最小实现设计中决定，不改变“必须由管理员显式发起”的业务边界。

#### 3.6 18:00 / 20:00 经营动作

- 18:00：平台交易日切换；进入新交易日的 `Current Sales Commitment`；未来 Sales Controller/Agent 需要支持一轮初步跨日商品调整，例如撤销前一交易日清库存造成的极端低价。此策略要求只写入运营策略，本阶段不实现自动决策。
- 19:00：独立完成上一交易日 Daily Sales Closing。
- 20:00 左右：录入上一周期结余、估算当前交易日对应生产日的产量，并据此制定/修订当前 active trade day 的正式销售策略。20:00 是 planning checkpoint，不是换日。

#### 3.7 与旧 Settlement/Summary 的关系

现有 20:00 `PLATFORM_TRADE_DAY_SETTLEMENT`、`seller_operation_date` 和 `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 业务流程降为历史实现输入。

13.6-2 需要评估：

- 哪些底层订单扫描、完整性验证、hash/evidence、库存销售 baseline、报告投影等能力可以复用；
- 哪些旧 Settlement 状态机、20:00 日界、late-data automatic revision 应退役；
- 不得因为旧代码存在而让新的 Daily Sales Closing 重新承担实时 Commitment 或第二销售日界职责。

### OD-04 — CLOSED：Observation Health

- S0：当前 calibration provider 正常。
- S1：首次超出 provider expected cadence 的 stale warning。
- S2：主校准连续缺失，但仍存在可信 fallback；风险增加操作需要额外确认。
- S3：已无足够可信实时校准，立即请求 Recovery Calibration；已排队或因合法 UI 占用等待时保持 S3/RECOVERING，不等同于平台失败。
- S4：主动 Recovery Calibration 已确认平台级/链路级失败后立即进入；不额外靠时间等待升级。
- 单 SKU 故障不自动升级为整个平台 S4。
- S4 不等同于全平台自动下架授权；风险降低动作仍可保留人工执行入口。
- 具体 freshness 参数优先使用 provider expected cadence + capability，而不是为所有平台冻结统一分钟常数。

### OD-05 — CLOSED：Intent supersession

- 当前人工阶段的 Sales Control Intent 是有范围、有有效期、有完成条件的 one-shot business intent，不是永久 standing policy。
- 新 Intent 只 supersede 明确涉及的业务维度。
- 已跨越 Queue / side-effect boundary 的旧动作不得删除或假装取消；必须先完成、回读或 reconcile，再判断是否需要新的纠正动作。
- 外部人工直接修改平台后，旧 Intent 默认不得自动把状态改回；应失效或进入重新确认。
- 未来 Agent 如需要持续维持某目标，应使用独立、版本化策略，而不是把人工 Intent 变成无限纠正循环。

### OD-06 — CLOSED：Task 14 / Agent

Task 14 采用两条并行工作线：

- `14-A Integrated Acceptance & Freeze`：多品种、多动作、阻塞恢复、UNKNOWN/RECONCILE、rollover provider、正式授权与版本冻结等综合验收。
- `14-B Agent Intervention / Ops Agent`：首版负责诊断、运行解释、Incident/Observation Health 辅助和受控工具调用，不直接成为自动销售 Controller。

两条线可以并行实现，但真实接入必须经过共同 integration gate。Agent 不得绕过确定性业务校验、授权、执行、回读与恢复基础设施。

## 2. Current implementation gaps identified during closure

这些是当前代码事实与新业务合同之间的实现差距，不重新打开 owner 业务决策：

### 2.1 `order_created_at` 已实现

当前订单 Adapter、Importer、Runtime 表和 settlement model 已采集并持久化 `order_created_at`，且现有 TIME_BUCKET 分析也使用它。

### 2.2 `purchase_sequence` 尚未实现

平台页面可见“第 N 次购买”，历史设计曾明确把它定义为可在不保存买家 PII 的前提下采集的 `purchase_sequence`，但当前 Worker、Importer 和 Runtime v14+ 正式订单表没有持久化该字段。

现有 `occurrence_no` 不能替代 `purchase_sequence`：

- `occurrence_no` 只是在同一 observation batch 内，为相同订单指纹的真实重复行保留多重集合实例；
- 它不是买家的第 N 次购买，也不能用来推导复购率。

因此 `purchase_sequence` 是 Daily Sales Closing 的明确最小采集缺口，后续实现需要受控元素定位、解析、持久化和 READ_ONLY 回归。

### 2.3 页面售价可由现有事实稳定派生

当前订单读取链会读取页面展示单价与数量，并通过 `Decimal` 精确计算：

```text
order_transaction_amount = page_unit_price × order_qty
```

正式订单 observation 已持久化 `order_qty` 与 `order_transaction_amount`。因此 Daily Sales Closing 所需页面售价可以稳定反算：

```text
page_unit_price = order_transaction_amount / order_qty
```

这不是新的采集缺口，也不应新增独立售价持久化字段。新增第二套售价事实会带来双来源漂移风险，而没有提供额外业务信息。

如果未来平台订单金额语义发生变化，例如引入无法从页面单价与数量解释的订单级优惠、折扣或其他调整，应重新验证该派生关系；在当前蚂蚁平台已验证的订单采集合同下，优先保持现有单一事实链。

## 3. G1 入口

Business Decision Closure 完成后，13.6-1 下一步是：

1. 用本文件裁决更新业务基线候选；
2. 将旧 20:00 双日界和 Settlement 正常生命周期明确降为历史实现；
3. 进行 G1 Business Baseline Review；
4. G1 `PASS` 后才允许进入 13.6-2 系统架构与实现差距重基线。

当前状态：

```text
Business Decision Closure: CLOSED
G1 Business Baseline: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
