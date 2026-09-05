# Task 13.6 PRA 业务基线草案

状态：`DRAFT / G1 INPUT / NOT CANONICAL`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41

> 本文件用于 Business Decision Closure 与 G1 Review。  
> 在 G1 `PASS` 前，不得把本文中的候选接口、术语或职责直接作为 Task 13.7 编码授权。

## 1. PRA 当前产品定位

PRA 是面向鲜切花预测性销售的经营观察与受控执行系统。

它服务的不是传统“商品已经全部生产并入库 → 按库存上架”的普通现货电商流程，而是：

1. 在实际采收和包装完全确定前，根据预测与现场逐步更新的供给信息开始销售；
2. 在交易过程中持续观察平台成交和平台状态；
3. 由管理者根据供给、成交、时间、市场和风险作出价格、Exposure 与上下架判断；
4. PRA 将明确的人工作业意图安全地送入既有执行链；
5. 执行结果、异常与平台实际状态重新进入经营视图；
6. 保存足够的历史观察、人工决定与执行结果，为未来 Agent 决策提供可信训练/回溯材料。

当前阶段的销售 Controller 是 **人类管理者**。

当前系统不以“还没有自动销售 Agent”为缺陷。未来 Agent 若接入，优先替换/辅助“决策来源”，不能绕过业务校验、授权、执行、回读和恢复基础设施。

## 2. 当前主经营闭环

```text
生产/结转信息 + 平台观察
            ↓
可信、带时间和来源的经营状态
            ↓
Operations Web
            ↓
人工管理者判断
价格 / Exposure / 上下架
            ↓
结构化业务意图 / 任务 / 授权
            ↓
受控平台执行
            ↓
平台回读 / 结果 / 异常 / 人工复核
            ↓
重新进入经营状态
```

13.6-1 只冻结这条业务责任方向，不预先要求 Intent、Task、Attempt 分别对应独立数据库表或独立状态机。

## 3. 核心数量概念

### 3.1 Daily Supply：当日生产供给逐步收敛

同一生产日的当日供给有三个阶段：

```text
PRODUCTION_FORECAST
        ↓ superseded by
HARVEST_ESTIMATE
        ↓ superseded by
PACKAGED_ACTUAL
```

含义：

- `PRODUCTION_FORECAST`：在采收前对目标生产日可产数量的预测；
- `HARVEST_ESTIMATE`：实际采收进行或完成后，对采收数量的更接近实况估计；
- `PACKAGED_ACTUAL`：完成包装后得到的当天可交付实际数量。

规则：

- 三者描述**同一生产日、同一供给轴**；
- 它们是覆盖/收敛关系，不相加；
- 同一阶段允许存在修订历史，但当前经营值使用该阶段最新有效版本；
- 阶段优先级高于单纯更新时间：一个更晚写入的 Forecast 不能覆盖已经存在的 Packaged Actual；
- 具体持久化形式留给 13.6-2/13.7，不在 13.6-1 决定表结构。

### 3.2 Carryover：上一周期剩余的独立确认事实

`CARRYOVER_CONFIRMED` 表示进入新经营周期时，被人工或可信事实确认仍可用于后续销售/履约的上一周期剩余量。

规则：

- Carryover 与当日 Daily Supply 是不同轴；
- `PACKAGED_ACTUAL(D)` 不能自动等于 `CARRYOVER_CONFIRMED(D+1)`；
- 上一日包装总量中可能已经发生销售、发货、损耗、调整或人工处置；
- 新周期的 Carryover 必须是新的剩余事实。

Carryover 与 Current Sales Commitment 的最终扣减口径见 `OD-01`。

### 3.3 Platform Exposure：平台销售暴露量

平台“目标库存/可购数量”在 PRA 业务中解释为 **Sales Exposure**：

> PRA 在该平台愿意向市场暴露的可购买额度。

它不是：

- 实物库存余额；
- 订单 reservation；
- 各平台之间的硬分仓库存；
- 已经形成的销售承诺。

因此：

- 单个平台 Exposure 可以高于当前实物供给；
- 多个平台 Exposure 之和也可以高于当前实物供给；
- 不能仅凭 Exposure 总和大于供给判定已经超卖；
- Exposure 的增加不能增加 PRA 的真实供给；
- Exposure 的减少也不能自动撤销已经发生的成交承诺。

超售/短缺风险需要结合 Supply、Current Sales Commitment、时间、成交速度、安全缓冲和人工经营判断，而不是通过简单的“平台额度 <= DB 库存”硬规则解决。

### 3.4 Current Sales Commitment：盘中当前销售承诺

Current Sales Commitment 回答：

> 截至当前时刻，在某个平台、目标交易日和业务粒度上，已经形成了多少需要 PRA 承担的销售承诺？证据是什么？证据有多新鲜？

它是盘中经营状态，不等于每日经营日结，也不等于平台 Exposure。

候选证据来源包括：

1. `ORDER_OBSERVED`：订单级观察；
2. `CURRENT_DAY_SALES_AGGREGATE_OBSERVED`：平台直接提供的当日累计成交数量观察；
3. `QUICK_SCAN_DERIVED`：从平台列表变化并结合已知调整证据推导的估计；
4. stale / unavailable last-known state。

最终来源接管、回补和避免重复计数的规则见 `OD-02`。

## 4. 业务粒度

PRA 同时存在多个粒度：

- `internal_sku`；
- 品种；
- 等级；
- 品种 + 等级；
- 平台商品身份。

当前已确认：

- 生产预测和平台过渡成交窗口至少存在“品种 + 等级”粒度；
- 平台页面不天然拥有 PRA `internal_sku`；
- 同一“品种 + 等级”若唯一映射到一个内部 SKU，可以安全投影到 SKU；
- 若映射到多个 SKU，不得把聚合成交数量按比例、平均或任意方式拆分到 SKU；
- 不能为了满足现有表结构而伪造更细粒度事实。

因此，事实的 `evidence_granularity` 与事实可信度必须分开理解：

- 一个直接平台观察可以是高可信事实；
- 但如果它只证明品种 + 等级，就不能声称它直接证明每个 SKU 的销量。

## 5. 时间与日期轴

### 5.1 技术事件时间

每个观察、人工决定、执行和结果都应保留明确时间，例如：

- `observed_at`；
- `created_at`；
- `executed_at`；
- `recorded_at`。

技术时间不等同于业务日期归属。

### 5.2 Platform Trade Date

`platform_trade_date` 是特定平台的交易归属日。

当前蚂蚁花团已确认使用 18:00 截单并切换到下一平台交易日。

规则：

- 18:00 是当前平台能力/业务规则，不应自动写成所有未来平台的全球固定 cutoff；
- 后续平台应通过平台 capability/profile 说明自己的 cutoff；
- 不同平台可以拥有不同 operational trade date。

### 5.3 Seller Operation Date

`seller_operation_date` 是 PRA/卖家的经营作业周期。

当前使用 20:00 切换作业日。

它用于组织：

- 新周期经营准备；
- 供给更新；
- 收尾/复盘；
- 人工操作界面上下文。

它不是平台订单页显示日期。

### 5.4 Production Date

`production_date` 表示 Daily Supply 所针对的生产日。

Forecast、Harvest Estimate、Packaged Actual 必须绑定相同明确生产日后才构成同一条 supersession 链。

### 5.5 Order Page Visible Trade Date

`order_page_visible_trade_date` 表示平台订单页面**当前实际显示的交易日**。

这是平台 UI 的直接观察事实，不能只根据当前时钟推断。

平台 cutoff 后可以出现：

```text
operational platform_trade_date = 新交易日
order_page_visible_trade_date   = 刚结束的旧交易日
```

这在平台 rollover 期间可以是正常状态，而不是自动判定系统故障。

## 6. 平台 rollover 期间的观察模式

当前平台事实表明：部分平台在订单页正式切换到新交易日前，会提供一个当日成交聚合窗口。

该窗口至少提供：

- 品种；
- 等级；
- 累计成交数量。

不提供或未确认提供：

- 订单 ID；
- 订单行 ID；
- 买家；
- 单笔金额；
- 单笔时间；
- 支付/发货状态。

因此它应被理解为：

`Current Trade Day Sales Aggregate Observation`

而不是伪造为 `OrderObservation`。

### 6.1 rollover 期间的双源并存

在正常 transition 中：

- 旧订单页可以继续为刚结束交易日提供 closing/settlement 证据；
- 新日聚合窗口可以为新交易日提供盘中 Current Sales Commitment 校准；
- 两者服务不同 target trade date，不互相污染。

### 6.2 订单页跨日后

当订单页实际切换到新交易日：

- 正常订单观察重新成为可用校准来源；
- 最后一次聚合观察与首个订单级/完整观察之间需要 reconciliation；
- 历史证据均保留，不能删除其中一个来制造“连续数据”。

具体接管规则见 `OD-02`。

## 7. QUICK 推导与 PRA 自身调整

仅用平台可购数量变化推导销量存在一个关键风险：PRA 自己也会改 Exposure。

因此所有成功的 PRA Exposure 调整都必须能够产生可审计的 **Adjustment Evidence**。

概念规则：

```text
观察到的平台数量变化
- PRA 已知 Exposure 调整影响
- 其他已证明的非销售变化
= 才可能用于 QUICK 销量推导
```

不能把：

- PRA 自己上调 Exposure；
- PRA 自己下调 Exposure；
- 已确认的人工平台修改；

直接当成成交。

对无法解释的外部变化，应保留为未知/待校准状态，而不是为了连续曲线而猜测销量。

## 8. 人工管理者是当前销售 Controller

当前 Operations Web 的核心价值不是展示所有内部表，而是帮助管理者回答：

- 目前能卖多少、证据是什么；
- 已经卖出/承诺多少、证据有多新鲜；
- 各平台现在暴露多少；
- 当前价格、在线状态和销售速度怎样；
- 现在最合理的操作是什么；
- 一个已提交操作是否真的完成、阻塞、失败或需要人工处理。

当前阶段：

```text
Observation / Supply
→ Web
→ Human Decision
→ Controlled Execution
```

未来 Agent：

```text
Observation / Supply
→ Human and/or Agent Decision Source
→ same controlled business/execution boundary
```

Agent 不能成为修复当前任务生命周期缺口的前置依赖。

## 9. 人工外部平台修改是一等经营场景

员工、负责人或其他授权人员可能直接在平台 App / 小程序中修改：

- 价格；
- Exposure；
- 上下架状态。

PRA 不能假设自己是平台唯一写入者。

因此：

- 平台当前实际状态以重新观察为准；
- PRA 的历史意图和执行记录仍保留为审计事实；
- 如果平台被外部人工改成不同状态，旧 PRA 决定不能在没有重新确认当前有效目标的情况下自动覆盖人工操作；
- 真实写继续坚持写前读取、比较预期旧状态、执行、写后回读。

Intent 的有效期与 supersession 范围见 `OD-05`。

## 10. 业务决定、Runtime Task 与执行过程

当前需要区分三种职责，即使最终不一定对应三套表：

### 10.1 当前业务目标

回答：

> 现在希望平台处于什么经营状态？

例如价格、Exposure、上下架状态。

### 10.2 业务 Task

回答：

> 为了实现这个目标，需要执行什么明确动作？

### 10.3 执行过程

回答：

> 这次动作实际走到哪里、是否已经产生副作用、为什么停住、下一步是什么？

关键业务规则：

- 新人工决定应能 supersede 尚未执行的旧决定；
- 已经进入 Queue、RUNNING、RESULT_PENDING、UNKNOWN/RECONCILE 等可能产生副作用的旧动作，不能删除记录或直接宣称取消；
- 必须先收口实际结果，再判断是否需要根据最新目标执行纠正动作；
- Web 请求生命周期不能成为长期执行责任主体。

具体 Intent 有效期、替代范围和外部人工修改后的行为见 `OD-05`。

## 11. 当前执行基础与业务生命周期

已验证执行资产包括 v4/v5、Queue、Worker、Importer、write lock、operation/attempt 和 UNKNOWN → 唯一 RECONCILE 等。

但这些资产的存在不等于：

> 任意一个 Runtime Task 从创建后一定会被持续推进到明确终态。

历史 7F 故障分析已暴露：组件各自正确并不能证明跨组件业务旅程完整。

13.6-1 只冻结业务要求：

> 每个已被接受的非终态业务动作必须能够回答当前 owner、当前阶段、阻塞原因、下一步和最终终止条件。

由哪个服务/持久结构承担该责任，留给 13.6-2。

## 12. Observation Health

Observation Health 关注的是：

> 当前用于经营判断的观察链是否足够新鲜、完整、可校准？

当前方向：

- S0：正常；
- S1：开始陈旧/需要关注；
- S2：降级；
- S3：当前观察风险已经高到需要主动 Recovery Calibration；
- S4：主动恢复已经确认平台级/观察链不可用。

关键规则：

- S3 不只是继续等待时间，应主动触发适合当前平台观察模式的 recovery calibration；
- recovery calibration 在平台/链路层面确认失败后，立即进入 S4；
- “扫描正在排队”“Automation 被人为停用”“单 SKU 局部异常”不能自动等价于平台级 S4；
- rollover transition 中若聚合观察来源健康，可以维持正常观察能力；订单页尚未换日本身不是故障；
- S4 表示观察链故障，不自动等价于“允许全平台自动下架”。

具体 freshness/capability 阈值和动作限制见 `OD-04`。

## 13. 每日经营日结

项目 owner 当前要求：

> 每日指定 19:00 完整扫描形成/更新一次经营日结；普通盘中 QUICK、订单观察或其他扫描不应持续改写这份固定经营日报。

本文先冻结以下最小语义：

- 盘中 Current Sales Commitment 与每日经营日结是不同用途；
- 日结记录应明确 target trade date、生成时间、输入来源和状态；
- 19:10 等后续普通盘中观察可以更新实时经营状态，但不能顺手改写“19:00 指定经营日结”；
- 日结失败、补跑、迟到订单、历史纠正的最终行为尚未裁决。

现有 `PlatformTradeDaySummary` / Settlement 的 PROVISIONAL / OBSERVED / RECONCILED / FINAL / supersedes 设施如何复用，见 `OD-03`。

## 14. 多平台方向

当前仅有一个真实执行平台完成较深实机验证，但多平台仍是项目方向。

13.6-1 先冻结以下原则：

- Supply 是农场/商品层共享经营事实，不因平台复制成“每个平台各自一份真实库存”；
- Current Sales Commitment 按平台观察，再在满足日期、范围、单位和去重条件后用于全局经营判断；
- Exposure 是平台特定经营状态，不能把各平台 Exposure 简单相加当成已发生销量；
- 平台 cutoff、订单页 rollover、可用观察来源、写动作能力属于 Platform Capability；
- 当前不建设复杂 Exposure Allocator、跨平台原子事务或分布式消息系统。

第二平台正式接入前需要单独的跨平台架构 gate。

## 15. Agent 与 Task 14

当前业务基线方向：

- Task 13.6 不实现 Agent；
- Task 13.7 优先完成可靠的人工销售控制闭环；
- Task 14 增加 Agent Intervention / Ops Agent 并行工作线，同时重新确认原有综合验收职责；
- 确定性 recovery、人工 Review 和真实平台执行安全不能依赖 Agent 在线；
- Agent 不能通过直连数据库、Queue 或平台绕过正式边界。

Task 14 的最终任务边界见 `OD-06`；Agent 具体接口归 13.6-2 重新审查，旧 `AgentIntent / Agent Task Adapter` 方案不是当前不可修改合同。

## 16. 本阶段明确不冻结的实现结构

13.6-1 不决定：

- Supply 需要几张表；
- Current Sales Commitment 是否单独落表还是可重建投影；
- Sales Control Intent 是否必须新表；
- Dispatch Attempt 是否复用现有 attempt 还是新增持久结构；
- Coordinator 的类名、模块名和表结构；
- Observation Health 的数据库结构；
- Event bus / message queue；
- 第二平台 Exposure 分配算法。

这些必须在业务基线通过后，基于当前代码做最小复用审计。

## 17. 固定业务情景

### Scenario A：供给逐步收敛

同一生产日：

```text
20:00  Forecast = 120
11:00  Harvest Estimate = 115
17:00  Packaged Actual = 113
```

正确理解：当前 Daily Supply 依次为 120 → 115 → 113，而不是 348。

若另有上一周期确认剩余 40，则 Carryover 是另一条事实轴；它与已发生 Commitment 的最终净额关系由 `OD-01` 裁决。

### Scenario B：平台已经进入新日，订单页仍是旧日

18:30：

```text
operational platform_trade_date = D+1
order_page_visible_trade_date   = D
current-day aggregate window    = D+1
```

正确理解：

- D 订单页可继续服务 D 的关闭/结算证据；
- D+1 聚合窗口服务 D+1 盘中 Commitment；
- 两者不是冲突数据。

### Scenario C：Exposure 调整与成交交错

PRA 将平台 Exposure 从 100 调到 150，随后观察到平台显示 142。

正确理解：不能把 `100 - 142` 或 `150 - 142` 机械当成销量。必须先识别 PRA 自己的 +50 调整 evidence，再结合观察合同判断是否可以推导成交。

### Scenario D：新决定替代旧执行

旧价格目标 9.5，新人工决定 10.5。

- 旧动作尚未跨越副作用边界：可以安全 supersede；
- 旧动作已 QUEUED/RUNNING/UNKNOWN：不能删除旧执行，先收口实际状态，再按最新有效目标决定是否纠正。

### Scenario E：S3 Recovery

当前观察进入 S3：

- Recovery Calibration 成功：根据新观察恢复或重新评级；
- 平台级 Recovery Calibration 确认失败：立即 S4；
- 扫描仅在排队或 Automation 被人工暂停：不能仅凭等待时间宣称平台链路失败。

### Scenario F：19:00 日结后又有普通观察

19:00 指定日结已经形成。19:10 出现普通盘中观察。

正确理解：实时经营状态可以更新；固定 19:00 经营日结不被普通观察自动改写。迟到数据是否形成更正版本由 `OD-03` 裁决。

## 18. 仍需 Business Decision Closure 的主题

以下问题保持 OPEN，不在本文中静默决定：

- `OD-01` Supply / Carryover / Commitment 的净额与重复扣减；
- `OD-02` Aggregate / Order / QUICK 的来源接管与 reconciliation；
- `OD-03` 19:00 经营日结与现有 Settlement/Summary 的关系；
- `OD-04` S0–S4 freshness/capability 阈值与行为；
- `OD-05` Intent supersession 的有效期、范围和外部人工修改后的行为；
- `OD-06` Task 14 原综合验收职责与 Agent 并行工作线边界。

## 19. 当前状态

```text
Business Baseline Draft: READY FOR OWNER DECISION INPUT
G1 Business Baseline: NOT YET VALIDATED
Canonical Business Contract: NOT YET ESTABLISHED
```
