# Task 13.6 PRA 业务基线候选

状态：`POST-CLOSURE / G1 CANDIDATE / NOT YET CANONICAL`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41  
Owner 裁决记录：`docs/rebaseline/task13_6_business_decision_closure.md`

> 本文件已经吸收 Task 13.6-1 Business Decision Closure 的 OD-01～OD-06 裁决。  
> 在 G1 `PASS` 前，它仍不是 Task 13.7 的编码授权；G1 通过后，13.6-2 应以本业务基线回答“当前实现如何支持、缺什么、谁负责”，不得静默重新定义经营语义。

## 1. PRA 当前产品定位

PRA 是面向鲜切花预测性销售的经营观察与受控执行系统。

它服务的不是传统“全部生产并入库后再销售”的普通现货电商，而是：

1. 在实际采收和包装完全确定前，根据预测与结转事实开始销售；
2. 在交易过程中持续观察平台成交、价格、Exposure 与上下架状态；
3. 当前由人类管理者根据供给、成交、时间、市场和风险作出经营判断；
4. PRA 将明确的人工作业意图送入确定性校验、授权和受控执行链；
5. 执行结果、异常、人工平台修改和重新观察回到经营状态；
6. 保存足够的历史观察、人工决定和执行结果，为未来 Agent 决策与研究提供可信材料。

当前 Sales Controller 是 **人类管理者**。未来 Agent 优先替换或辅助“决策来源”，不能绕过确定性业务校验、授权、执行、回读与恢复基础设施。

## 2. 当前经营主闭环

```text
Supply / Carryover + Platform Observations
                    ↓
       Current Operating State
                    ↓
             Operations Web
                    ↓
          Human Sales Controller
     Price / Exposure / Listing Decision
                    ↓
        scoped one-shot Intent / Task
                    ↓
 deterministic validation / authorization
                    ↓
         controlled execution chain
                    ↓
     result / readback / reconcile / review
                    ↓
          updated operating state
```

13.6-1 只冻结职责和业务语义，不要求 Intent、Task、Attempt 分别对应独立数据库表或独立状态机。

## 3. 唯一销售日界与日期语义

### 3.1 Platform Trade Date

销售业务以 `platform_trade_date` 为核心日界。当前蚂蚁花团平台在 **18:00** 切换到新的平台交易日。

18:00 是当前平台已确认的 capability / 业务规则，不是所有未来平台的全球固定 cutoff。未来平台必须通过各自 capability/profile 定义自己的交易日界。

### 3.2 20:00 不再是第二个销售日界

旧实现中的 20:00 `seller_operation_date` 换日语义已被 owner 取消。

20:00 左右只保留为 **planning checkpoint**：录入上一周期结余、当前交易日对应生产日的预测供给，并据此制定或修订当前 active trade day 的正式销售策略。

旧 `seller_operation_date` 字段、20:00 Settlement 调度和相关兼容影响属于 13.6-2 的实现审计，不再拥有现行业务权威。

### 3.3 Production Date

`production_date` 表示 Daily Supply 所针对的生产日。Forecast、Harvest Estimate、Packaged Actual 必须绑定相同生产日后才构成同一条覆盖链。

### 3.4 Order Page Visible Trade Date

`order_page_visible_trade_date` 仅表示订单页面当前实际显示的交易日，是平台 UI 观察事实。

在当前蚂蚁平台 18:00 跨日后的冻结期，可以同时成立：

```text
platform_trade_date              = 新交易日
CurrentTradeDaySalesObservation  = 新交易日实时销售
order_page_visible_trade_date    = 刚结束并冻结的上一交易日
```

这不是日期冲突。`order_page_visible_trade_date` 主要服务订单页能力选择、Closing 目标日期验证和 rollover 检测，不应重新演变为第二套业务日历。

## 4. Supply：生产供给逐步收敛

同一 `production_date` 的 Daily Supply 有三个阶段：

```text
PRODUCTION_FORECAST
        ↓ superseded by
HARVEST_ESTIMATE
        ↓ superseded by
PACKAGED_ACTUAL
```

- `PRODUCTION_FORECAST`：采收前对目标生产日可产数量的预测；
- `HARVEST_ESTIMATE`：采收进行或完成后得到的更接近实况的数量估计；
- `PACKAGED_ACTUAL`：包装完成后得到的该生产日新包装生产总量事实。

规则：

- 三者是同一生产日、同一供给轴；
- 是覆盖/收敛关系，不相加；
- 当前有效值优先级为 `PACKAGED_ACTUAL > HARVEST_ESTIMATE > PRODUCTION_FORECAST`；
- 更晚写入的低阶段数据不能覆盖已存在的高阶段事实；
- 同一阶段的修订历史可以保留，但当前经营值只使用最新有效版本。

## 5. Carryover：上一周期未被旧承诺占用的剩余

`CARRYOVER_CONFIRMED` 定义为：

> 进入新平台交易日后，经人工或可信事实确认，**未被上一周期销售承诺占用**、仍可继续用于销售/履约的上一周期剩余量。

规则：

- Carryover 与当日 Daily Supply 是不同轴；
- `PACKAGED_ACTUAL(D)` 不会自动成为 `CARRYOVER_CONFIRMED(D+1)`；
- 销售、交付、损耗、人工处置等会改变下一周期可确认结余；
- 新周期 Carryover 必须是新的剩余事实。

## 6. Current Sales Commitment：盘中当前销售承诺

`Current Sales Commitment` 回答：

> 当前平台交易日截至现在，目标业务粒度已经形成多少销售承诺？当前值来自什么直接平台观察或辅助估算？证据有多新鲜？

它是动态盘中状态，不等于平台 Exposure，也不等于历史 `Daily Sales Closing`。

### 6.1 当前蚂蚁平台的直接 observation provider

**订单冻结期：**

`CurrentTradeDaySalesObservation` 是平台跨日后的当前交易日实时销售直接观察。它天然属于当前平台交易日，不在对象内部处理 D / D+1 映射。

当前已确认其业务粒度至少包括：

- 品种；
- 等级；
- 当前交易日累计销售数量。

它不需要伪造成订单明细，也不得制造不存在的订单 ID、金额或下单时间。

**订单页已经 rollover 到当前交易日后：**

Full Scan 中的 current-trade-day Order Observation 成为直接销售 observation provider。

### 6.2 Light Scan 与 QUICK-derived

Light Scan 全时段持续服务：

- 当前价格；
- Exposure；
- 上下架状态；
- 商品状态变化；
- 必要时的 QUICK-derived 辅助估算。

Current Sales Commitment 不通过把多个来源相加得到。当前 qualifying direct provider 接管当前 Commitment 值；QUICK-derived 只作辅助/fallback，旧来源保留为 evidence，不重复累计。

### 6.3 粒度与可信度分离

一个直接平台观察可以很可信，但如果它只证明“品种 + 等级”，就不能声称它直接证明每个 SKU 的销量。

同一“品种 + 等级”若唯一映射到一个内部 SKU，可以安全投影；若 1:N 映射到多个 SKU，不得按比例、平均或任意规则拆分。

## 7. Platform Exposure：销售暴露，不是实物 reservation

平台“目标库存/可购数量”在 PRA 中解释为 **Sales Exposure**：PRA 愿意向该平台市场暴露的可购买额度。

它不是：

- 实物库存余额；
- 订单 reservation；
- 平台硬分仓库存；
- 已经形成的销售承诺。

因此：

- 单个平台 Exposure 可以高于当前实物供给；
- 多个平台 Exposure 之和也可以高于实物供给；
- Exposure 总和大于供给不能单独证明已经超卖；
- Exposure 增减不能创造供给或撤销已经发生的成交承诺。

短缺风险应结合 Supply、Current Sales Commitment、成交速度、时间、安全缓冲和人工判断，而不是用 `target_inventory <= real_inventory` 一类硬规则替代经营判断。

## 8. PRA 自身 Exposure 调整必须成为 evidence

仅靠平台可购数量变化推导销量存在一个关键风险：PRA 自己也会改变 Exposure。

因此成功的 PRA Exposure 调整必须可审计。概念上：

```text
observed platform quantity change
- known PRA exposure adjustment
- other proven non-sales changes
= only then may contribute to QUICK sales estimation
```

PRA 自己的调整、已确认人工平台修改或其他已知非销售变化不得直接被解释为销量。无法解释的外部变化应保留为未知/待校准，而不是为了连续曲线猜测销量。

## 9. Current Sales Commitment 与 Supply 的经营关系

基础经营压力可以用下式理解：

```text
CARRYOVER_CONFIRMED
+ current effective Daily Supply
- current platform-trade-day Current Sales Commitment
```

这里：

- Carryover 已经排除了上一周期销售承诺占用；
- Daily Supply 是目标生产日当前最高有效阶段；
- Commitment 表示当前平台交易日累计已形成的销售承诺；
- 安全缓冲、损耗和具体履约风险不偷偷混入这些基础事实，可在经营视图中作为独立风险输入。

该表达是经营语义，不预先决定数据库账本或物理库存实现方式。

## 10. 18:00、19:00、20:00 的跨日经营流程

### 10.1 18:00 — 唯一销售日换日

当前蚂蚁平台切换到新的 `platform_trade_date`，新交易日 Current Sales Commitment 开始建立。

未来 Sales Controller / Agent 需要支持一轮初步跨日商品调整，例如前一交易日为了清库存大幅降价后，在新交易日重新评估价格、Exposure 与上下架状态。

这一项目前只写入运营策略要求，不由当前 13.6/13.7 自动销售决策实现。

### 10.2 19:00 — 上一交易日 Daily Sales Closing

19:00 对已经冻结的上一交易日订单页执行一轮独立 Closing Order Scan。

它只负责历史日结，不参与当前交易日 Current Sales Commitment。

### 10.3 20:00 左右 — 当前交易日 planning checkpoint

录入或确认：

- 上一周期 `CARRYOVER_CONFIRMED`；
- 当前交易日对应生产日的 `PRODUCTION_FORECAST`；
- 截至此时的 Current Sales Commitment；

并据此制定/修订当前 active trade day 的正式销售策略。

20:00 是经营计划更新时间，不是业务换日。

## 11. Daily Sales Closing：独立历史日结

`Daily Sales Closing` 回答：

> 刚结束的平台交易日，冻结后的订单历史事实是什么？

它与 Current Sales Commitment 完全分离，不共享一个 Summary 生命周期，也不被普通盘中观察持续改写。

### 11.1 Closing 业务事实

成功 Closing 至少保留：

- 品种；
- 等级；
- `order_qty`；
- `order_transaction_amount`；
- 下单时间 `order_created_at`；
- 页面“第 N 次购买”对应的复购序号 `purchase_sequence`。

必要审计元数据包括：

- 平台；
- 目标 `platform_trade_date`；
- observation batch；
- `observed_at`；
- 日期选择验证；
- 范围完整性；
- 尾部/空页验证；
- 内容 hash。

不要求采集买家 PII 或平台订单 ID。

### 11.2 页面售价不新增第二套事实

当前订单读取链已经使用页面展示单价与 `order_qty` 通过 `Decimal` 精确计算：

```text
order_transaction_amount = page_unit_price × order_qty
```

因此 Closing 展示或研究所需页面售价通过现有事实稳定派生：

```text
page_unit_price = order_transaction_amount / order_qty
```

`order_qty` 为正整数时，该派生值就是当前采集链使用的页面单价。不得再新增独立售价采集/持久化字段制造双来源漂移。

如果未来平台金额语义改变，例如引入无法由页面单价和数量解释的订单级折扣，再重新验证此派生关系。

### 11.3 `purchase_sequence` 是当前明确实现缺口

`order_created_at` 已在现有 Adapter、Importer 和订单事实中持久化。

平台页面可见“第 N 次购买”，但当前 Worker、Importer 和正式订单事实没有持久化 `purchase_sequence`。

现有 `occurrence_no` 只是同一 observation batch 内相同订单指纹真实重复行的多重集合序号，不是买家的第 N 次购买，不能替代 `purchase_sequence` 或推导复购率。

## 12. Closing 失败、重试与维护边界

Closing 是低风险历史采集，失败链固定为：

```text
19:00 Closing attempt #1
    ├─ SUCCESS → write Closing → lock automatic collection for that platform/date
    └─ FAILED  → fault report + one automatic retry

Closing attempt #2
    ├─ SUCCESS → write Closing → lock automatic collection
    └─ FAILED  → Closing S2 + human review → stop automatic retry
```

规则：

- 单纯 Closing 故障最高只到 `S2`，不继续自动升级 S3/S4；
- 第二次失败后停止自动重试并交人工；
- Closing 一旦成功并通过日期、范围、尾部等完整性验证，项目自动链不得再为同一平台/交易日发起 Closing 重扫；
- 后续历史问题或数据修正必须由管理员从维护入口显式发起，并留下原因、操作者和维护记录；
- 管理员维护入口是否允许受控重读或只允许人工修正，留给 13.6-2 做最小实现设计。

如果同一底层故障同时破坏 Current Sales Commitment 的实时 observation provider，则实时 Observation Health 仍按自己的 S0–S4 规则独立评级；不能因为 Closing 风险低而压低实时风险。

## 13. Observation Health

Observation Health 关注当前经营判断所依赖的实时观察链是否足够新鲜、完整、可校准。

- **S0**：当前 calibration provider 正常；
- **S1**：首次超出 provider expected cadence，进入 stale warning；
- **S2**：主校准连续缺失，但仍存在可信 fallback；风险增加操作需要额外确认；
- **S3**：已无足够可信实时校准，立即请求 Recovery Calibration；已排队或因合法 UI 占用等待时保持 `S3/RECOVERING`；
- **S4**：主动 Recovery Calibration 已确认平台级/链路级失败后立即进入，不额外靠时间等待升级。

关键规则：

- freshness 优先使用 provider expected cadence + capability，不冻结所有平台统一分钟常数；
- 单 SKU 故障不自动升级为平台 S4；
- 排队、人工暂停 Automation、合法 UI lease 占用不等于已确认平台失败；
- S4 表示实时观察链故障，不自动等价于全平台自动下架授权；
- 风险降低动作仍可保留人工执行入口。

## 14. 当前人工 Sales Control Intent

当前人工阶段的 Sales Control Intent 是：

> 有范围、有有效期、有完成条件的 **one-shot business intent**，不是永久 standing policy。

规则：

- 新 Intent 只 supersede 明确涉及的业务维度；
- 尚未跨越副作用边界的旧动作可以安全 supersede；
- 已经进入 Queue / RUNNING / RESULT_PENDING / UNKNOWN / RECONCILE 等可能产生副作用的旧动作不得删除或假装取消；必须先完成、回读或 reconcile；
- 外部人工直接修改平台属于正常经营场景；旧 Intent 默认不得自动把平台改回，应失效或进入重新确认；
- 未来 Agent 若需要持续维持某个目标，应使用独立、版本化策略，而不是把人工 Intent 变成无限纠正循环。

13.6-1 不决定 Intent 是否需要独立表。

## 15. Runtime Task 与执行过程

需要区分至少三种责任：

1. **Business Intent**：当前希望发生什么经营改变；
2. **Runtime Task**：为实现该目标需要执行什么业务动作；
3. **Execution progression**：这次动作已经执行到哪里、是否产生副作用、为什么停住、下一步是什么。

已有 v4/v5、Queue、Worker、Importer、write lock、operation/attempt 和 UNKNOWN → 唯一 RECONCILE 等执行资产仍是重要复用候选。

但组件存在不等于端到端业务动作一定会推进到终态。每个非终态业务动作都必须能回答：当前 owner、当前阶段、阻塞原因、下一步、重启后如何恢复、如何终止。具体由哪个服务和持久结构承担，归 13.6-2。

## 16. 人工外部平台修改是一等场景

员工或负责人可能直接在平台 App / 小程序修改：

- 价格；
- Exposure；
- 上下架状态。

PRA 不能假设自己是唯一写入者。

因此：

- 当前平台事实以重新观察为准；
- PRA 历史意图和执行记录保留为审计事实；
- 外部人工修改后，不允许过时 Intent 无条件自动覆盖；
- 真实写继续坚持写前读取、预期旧状态比较、执行、写后回读。

## 17. 多平台方向

- Supply 是农场/商品层共享经营事实，不复制成“每个平台各自一份真实库存”；
- Current Sales Commitment 按平台观察，再在日期、范围、单位和去重条件明确时用于全局经营判断；
- Exposure 是平台特定经营状态，不能把平台 Exposure 直接相加当成销量；
- cutoff、订单页 rollover、CurrentTradeDaySalesObservation 等 provider、写动作能力都属于 Platform Capability；
- 当前不建设复杂 Exposure Allocator、跨平台原子事务或分布式消息系统；
- 第二平台正式接入前需要独立跨平台架构 gate。

## 18. Agent 与 Task 14

Task 14 采用两条并行工作线：

### 14-A Integrated Acceptance & Freeze

负责：

- 多品种、多动作；
- 阻塞恢复；
- UNKNOWN / RECONCILE；
- provider rollover；
- 正式授权；
- 版本冻结与运维门禁。

### 14-B Agent Intervention / Ops Agent

首版负责：

- 诊断；
- 运行状态解释；
- Incident / Observation Health 辅助；
- 受控工具调用。

首版不直接成为自动销售 Controller。

两条线可以并行实现，但真实接入前必须经过共同 integration gate。Agent 不得绕过确定性校验、授权、执行、回读和恢复基础设施；确定性 recovery 不能依赖 Agent 在线。

旧 `AgentIntent / Agent Task Adapter` 具体接口只作为历史 Accepted Design，归 13.6-2 重新审查。

## 19. 与旧 Settlement / Summary 的关系

现有业务语义中的以下内容已被 supersede：

- 20:00 `seller_operation_date` 作为第二销售日界；
- 20:00 `PLATFORM_TRADE_DAY_SETTLEMENT` 作为正常跨日主流程；
- `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 作为正常每日经营日结生命周期；
- successful Closing 后由普通 late-data 自动重开历史日结。

13.6-2 必须审查哪些底层能力继续复用，例如：

- 订单页 READ_ONLY 扫描；
- 日期、范围、尾部和空页验证；
- immutable observation / hash / evidence；
- sales baseline 或其他已有库存扣减设施；
- 管理报告投影；
- 历史维护审计。

不得因为旧代码已经存在，就让新的 Daily Sales Closing 再次承担 Current Sales Commitment 或第二销售日界职责。

## 20. 当前明确不冻结的实现结构

13.6-1 不决定：

- Supply 需要几张表；
- Current Sales Commitment 是否持久化还是可重建；
- Sales Control Intent 是否必须新表；
- Dispatch Attempt 是否复用既有 attempt；
- Coordinator 的类名、模块名或表结构；
- Observation Health 是否落表；
- Closing 管理员维护入口的具体 UI/Schema；
- Event bus / message queue；
- 第二平台 Exposure 分配算法。

这些问题只有在 G1 通过后才能依据当前代码做最小复用设计。

## 21. 固定业务情景

### Scenario A：Supply 收敛且不重复扣减

当前新交易日开始后确认上一周期可继续销售剩余 40；当前生产日：

```text
20:00  Forecast = 120
11:00  Harvest Estimate = 115
17:00  Packaged Actual = 113
```

Daily Supply 当前值依次为 120 → 115 → 113，不相加。

若当前交易日 Commitment 为 20，则基础经营压力依次为：

```text
40 + 120 - 20 = 140
40 + 115 - 20 = 135
40 + 113 - 20 = 133
```

上一周期承诺不会再次扣除，因为 `CARRYOVER_CONFIRMED=40` 已经定义为未被旧承诺占用的可继续销售剩余。

### Scenario B：18:00 跨日后订单页仍冻结在旧日

18:30：

```text
platform_trade_date              = D+1
CurrentTradeDaySalesObservation  = D+1 current sales
order_page_visible_trade_date    = D frozen page
```

正确理解：CurrentTradeDaySalesObservation 直接校准 D+1 Current Sales Commitment；冻结订单页 D 等待 19:00 Closing。二者不互相污染。

### Scenario C：19:00 Closing 成功

19:00 Closing Order Scan(D) 完成日期、范围和尾部验证并写入 Closing。

之后普通自动任务不得再次对 `(platform, D)` 发起 Closing 扫描。若管理员以后发现历史问题，只能从维护入口显式处理并留下审计记录。

### Scenario D：19:00 Closing 连续失败

第一次失败 → 故障报告 + 一次自动重试；第二次失败 → Closing S2 + 人工复核，并停止自动重试。

如果同时实时 provider 也失效，Current Sales Commitment 的 Observation Health 独立进入 S3/S4 判断。

### Scenario E：Exposure 调整与成交交错

PRA 将 Exposure 100 → 150，随后平台显示 142。

不能机械把 8 当成销量；必须先绑定 PRA 自身 +50 调整 evidence，再依据有效 observation contract 判断是否能形成 QUICK-derived 销量。

### Scenario F：新 Intent 替代旧执行

旧目标价格 9.5，新人工决定 10.5。

- 旧动作尚未跨越副作用边界：可以 supersede；
- 旧动作已 QUEUED / RUNNING / UNKNOWN：不能删除，先收口实际结果，再根据最新有效 Intent 判断是否需要纠正。

### Scenario G：S3 Recovery

当前实时 provider 进入 S3：

- Recovery 成功：恢复或重新评级；
- Recovery 已排队或因合法 UI 占用：保持 `S3/RECOVERING`；
- 平台级 Recovery 实际确认失败：立即 S4；
- 不因为普通等待时间直接宣称平台失败。

## 22. G1 状态

Business Decision Closure 已完成，OD-01～OD-06 均已关闭。

```text
Business Decision Closure: CLOSED
Business Baseline Candidate: READY FOR G1 RETEST
G1 Business Baseline: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
