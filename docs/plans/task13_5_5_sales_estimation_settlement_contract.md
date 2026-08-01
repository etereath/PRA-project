# 任务 13.5-5：销售估算、日结与计划输入合同

- 状态：评审整改已实施，待 PR 复审
- 冻结日期：2026-08-01
- Review Profile：`R4`
- 真实平台写操作：否
- 涉及平台：平台无关核心；首个验收平台为蚂蚁花团供应商
- 涉及账号：当前每个平台只支持一个已授权账号；本阶段不增加多账号维度
- 权威：GitHub Issue #20 最新正文、用户 2026-08-01 补充门禁、13.5-1 与 Runtime
  Schema v14、13.5-4 订单观察合同

## 0.1 2026-08-01 销售计划输入范围澄清

本阶段的核心仍是单平台销售过程对接，不建设完整预测系统。当前必须保证能够把平台
销售过程中已经采集的订单、价格、库存、上下架和质量事实形成可追溯汇总；以下内容只
记录后续接口和来源规划，不进入本阶段实现：

- 采摘量、实际等级分布、包装、冷库流水、外部补采等线下生产经营数据；
- 佣金和退款；平台不提供的曝光、浏览、访客和转化率不建立虚构字段；
- 由模型联网取得的节假日、天气和活动等外部权威标签；
- 今日花价指数、平台价格指数、特定品种外部价格/销量等市场指数。

平台页面可见的“第 N 次购买”可以作为非 PII 的复购序号来源，但 13.5-4 冻结后的
Worker、订单合同和 v14 正式表当前没有采集或保存该字段。在完成受控页面定位、合同
修订、最小 Schema 评审和 READ_ONLY 回归前，任何汇总不得声称已经提供复购订单占比。
这是一项明确的平台采集缺口，不得从订单指纹、订单数量或重复快照猜测。

计划输入直接复用唯一业务日期 `seller_operation_date`。卖家作业日 D 定义为：

```text
D-1 20:00（含） → D 20:00（不含）
```

计划投影字段 `plan_for_seller_operation_date` 只是明确说明“计划用于哪个
`seller_operation_date`”，其值必须等于 Automation Run 已冻结的
`seller_operation_date`，不是第三套业务日期或新的数据库主键。
`platform_trade_date` 继续用于平台订单结算、来源追溯和 FINAL，不作为计划特征的
分组主键。一个 PRA 交易日可能引用两个平台交易日的原始事实，必须按逐项时间或已保存
的 `seller_operation_date` 在 20:00 边界重新归属，不能直接把单个平台日结当作 PRA
交易日样本。

## 1. 范围、事故模型和复杂度预算

本阶段连接商品观察、订单观察、销售估算、交易日日结和 Automation Run，改变经营事实
的选择与汇总关系，因此按 R4 在编码前冻结合同。它只计算、导入和汇总只读经营事实，
不调用 ShadowBot 平台写动作，不新增平台合同版本。

当前部署假设为单机 SQLite、单 Automation Service、每个平台一个账号和既有单 UI
Worker；估算与日结按 `platform_name` 隔离。若要支持同平台多账号，必须先补充账号身份
合同并单独评审，不能让两个账号共享同一汇总系列。

最坏事故是：把人工或系统库存调整误算为销售、把同一销量在订单和库存下降中重复累计、
把 `OPEN` 或部分订单事实提前标为 `FINAL`。所有失败默认保留原始观察并停止提升质量或
日结状态，人工恢复成本为中等。

本 PR 原始复杂度预算固定为：

```text
新增数据库表：0
新增数据库字段：0
新增全局锁或租约：0
新增平台合同版本：0
新增真实平台动作：0
新增结算状态：0
新增恢复路径：0
```

允许新增纯计算服务、现有 Repository 的查询/写入方法、结算应用服务和 Automation
Handler。若实现发现必须持久化独立数值置信度、取消事实或库存调整事实，必须先证明
现有 v14 的 `quality_level`、输入 manifest 和来源引用无法表达，再单独回到设计评审；
不得在编码 PR 中顺手扩 Schema。

当前明确不实现 Incident 状态机、S0–S4 告警、自动紧急下架、Web 销售分析、第二平台、
高级预测模型、生产/包装/冷库 ERP 或任何订单及库存平台写操作。

若要把“第 N 次购买”纳入当前平台采集，必须作为经评审的最小合同修订单独说明其
Adapter 定位、整数解析、指纹影响、迁移和真实页面验收；不能借计划输入汇总绕过
13.5-4 的不可变原始事实边界。

## 2. 复用矩阵

| 能力 | 处理方式 | 既有权威 |
| --- | --- | --- |
| 双时间轴、18:00/20:00 归属 | 原样复用 | `OperationalTimeService` |
| 商品库存观察、完整性、映射版本 | 原样复用 | `product_observation_batches/items` |
| 订单原始事实、`OPEN/CLOSED`、多重集合 | 原样复用 | 13.5-4 `order_observation_batches/items` |
| 六级质量和四级日结状态 | 原样复用 | v14 枚举、CHECK 和触发器 |
| 日结系列、版本、supersedes、输入 manifest | 原样复用 | `TradeDaySummaryService`、`OperationalSummaryRepository` |
| Automation Run、租约、事件、幂等补跑 | 参数化复用 | 13.5-3 Automation Service |
| 已执行库存写及读回证据 | 只读复用 | tasks、operation/attempt、listing action item |
| 销量区间与取消推导 | 确需新增 | 纯计算服务；不建立平行账本 |
| 20:00 结算与计划输入编排 | 确需新增 | 复用现有 summary 和 Automation 接口 |

本阶段不修改 ShadowBot Worker、文件队列、Importer、Watchdog、写锁或唯一
`UNKNOWN → RECONCILE` 控制流。

## 3. v14 表达结论

`sales_estimate_segments` 已直接保存：

```text
inventory_before
inventory_after
known_inventory_adjustment
known_adjustment_source_refs_json
estimated_sold_qty
estimation_eligible
estimation_reason
quality_level
mapping_version
supporting_observation_ids_json
algorithm_version
```

合同中的逻辑字段 `confidence` 不新增同义列。其唯一持久化表达为：

```text
HIGH   <-> SCAN_ESTIMATED_HIGH
MEDIUM <-> SCAN_ESTIMATED_MEDIUM
LOW    <-> SCAN_ESTIMATED_LOW
```

应用模型可以提供只读 `confidence` 属性，但数据库和输入 manifest 以
`quality_level` 为权威。`platform_trade_day_summaries`、版本、事件和输入表已经能表达
20:00 首次日结、后续状态推进、迟到数据新版本和 FINAL 不可变性，因此当前不扩 Schema。

## 4. 估算区间身份和公式

一个估算区间只对应一个：

```text
platform_name
+ internal_sku
+ platform_trade_date
+ inventory_before_observation_id
+ inventory_after_observation_id
+ algorithm_version
+ evidence_manifest
```

`evidence_manifest` 规范化包含调整来源引用及未决写、冲突观察、关键扫描失败、重叠和
完整扫描替代标志。`estimate_segment_id` 由上述身份确定性生成；同一观察区间事后补充
人工证明时创建可并存的新不可变 segment，不原地更新旧 segment。选数时同一算法、同一
观察区间只采用 `created_at` 最新的证据版本，旧版本只保留审计用途；相同证据精确重放
仍生成相同 ID。前后观察按 `observed_at` 严格递增，
使用规范时间线中的相邻合格观察；不得同时按 `ONLINE_PULSE` 和 `FULL_MARKET_SCAN` 建立
重叠区间并重复累计。同一时刻同一 SKU 出现相互冲突的库存观察时，该区间不合格。

`known_inventory_adjustment` 是区间 `(interval_started_at, interval_ended_at]` 内，已经由
可审计来源证明的“非销售净库存减少量”，使用有符号整数：

- 正数：非销售原因使库存减少；
- 负数：补货、重设或修正使库存增加；
- 0：没有已确认的非销售数量变化。

全部资格条件满足后才计算：

```text
estimated_sold_qty = max(
  inventory_before
  - inventory_after
  - known_inventory_adjustment,
  0
)
```

若调整后的余量为负，说明调整证据与观察无法闭合，区间不合格；不得依赖 `max(..., 0)`
把矛盾隐藏为零销量。`estimation_eligible=false` 时
`estimated_sold_qty=NULL`，不能写 0。

## 5. 估算资格、原因和置信度

### 5.1 最低资格

只有以下条件全部成立，`estimation_eligible` 才能为 true：

1. 前后观察属于同一平台、内部 SKU 和平台交易日，时间严格递增且未跨 18:00；
2. 两个来源批次均已接受，范围和结束标记满足各自扫描合同；
3. 两项库存均可读且非负，商品在整个区间持续在线；
4. 两端映射均为 `VERIFIED`，`internal_sku` 和 `mapping_version` 完全一致；
5. 区间内没有失败或不完整的关键扫描，没有相互冲突的同刻观察；
6. 所有 PRA 库存写、人工平台修改、上架重设、target inventory 和对账修正均已搜索；
7. 每个实际影响库存的调整都能确定发生时间、有符号数量和不可变来源引用；
8. 不存在活动 `UNKNOWN / PARTIALLY_APPLIED / NEEDS_RECONCILIATION` 库存写结果；
9. 调整覆盖已被明确证明，不能用“数据库里没有记录”推断“没有人工操作”；
10. 区间不与另一个被采用的销售估算区间重叠。

当前平台无法提供人工库存操作审计时，只有具备受控运营声明或人工确认引用的区间才能
证明调整覆盖；否则即使库存下降，也使用 `UNKNOWN_INVENTORY_DECREASE` 并保持不合格。
库存不变同样不能在覆盖未知时伪造成零销售。

### 5.2 原因码和确定性优先级

`estimation_reason` 保存一个主要原因码。多个问题同时存在时按以下顺序选择第一个，
保证重放稳定：

```text
INVALID_INTERVAL
PLATFORM_OR_SKU_MISMATCH
TRADE_DAY_MISMATCH
CROSSED_PLATFORM_CUTOFF
OBSERVATION_INCOMPLETE
INVENTORY_UNREADABLE
MAPPING_NOT_VERIFIED
MAPPING_VERSION_CHANGED
NOT_CONTINUOUSLY_ONLINE
CONFLICTING_OBSERVATION
UNRESOLVED_INVENTORY_WRITE
TARGET_INVENTORY_NOT_VERIFIED
MANUAL_CHANGE_UNQUANTIFIED
ADJUSTMENT_COVERAGE_UNPROVEN
UNKNOWN_INVENTORY_INCREASE
UNKNOWN_INVENTORY_DECREASE
ADJUSTMENT_DOES_NOT_RECONCILE
SCAN_GAP_EXCEEDED
OVERLAPPING_INTERVAL
ELIGIBLE_NO_ADJUSTMENT
ELIGIBLE_KNOWN_ADJUSTMENT
```

### 5.3 置信度

- `HIGH`：全部资格条件成立；相邻观察符合 10 分钟扫描节奏（默认不超过 15 分钟），
  中间没有 missed/failed 关键窗口，调整覆盖完整。
- `MEDIUM`：全部资格条件仍成立，但间隔大于 15 分钟且不超过 25 分钟，或一个窗口被
  有完整商品观察的合并大扫描替代。数量可以保存，计划输入只能标记降权，13.5-5 不
  引入数值权重模型。
- `LOW`：资格、覆盖或扫描连续性不足。为避免把方向性判断包装成精确整数，首版固定
  `estimation_eligible=false`、`estimated_sold_qty=NULL`，只保留原因和支撑观察。

阈值属于 `algorithm_version`；改变阈值必须创建新算法版本并重算新 segment，不能修改
旧 segment。

## 6. 已知库存调整来源

`known_adjustment_source_refs_json` 是按 `source_ref_id` 排序的对象数组，每项至少包含：

```json
{
  "adjustment_id": "稳定的同一物理调整身份",
  "source_type": "来源类型",
  "source_ref_id": "既有不可变账本或确认记录 ID",
  "adjustment_qty": 0,
  "occurred_at": "带时区时间",
  "evidence_sha256": "sha256:..."
}
```

同一物理调整可能同时由 `SET_ONLINE_INVENTORY_RESET` 和 `TARGET_INVENTORY` 支撑，必须
共用 `adjustment_id`，数量只计一次。来源类型至少区分：

| `source_type` | 语义和资格规则 |
| --- | --- |
| `PRA_INVENTORY_WRITE` | 只有 operation 已验证且前后读回闭合时可计入 |
| `MANUAL_PLATFORM_MODIFICATION` | 必须有人工确认、时间、前后值和数量；否则区间不合格 |
| `SET_ONLINE_INVENTORY_RESET` | 跨越上架重设的区间不合格；后续区间从已验证读回重新起算 |
| `TARGET_INVENTORY` | 仅是意图，单独出现绝不能作为已发生调整；必须绑定已验证 action/readback |
| `RECONCILIATION_CORRECTION` | 只修正解释时数量可为 0；证明真实调整时必须链接原操作和读回 |
| `ADJUSTMENT_COVERAGE_ATTESTATION` | 证明区间人工外部操作覆盖，数量固定为 0 |
| `UNKNOWN_INVENTORY_INCREASE` | 仅记录不合格原因，不进入 known adjustment 求和 |
| `UNKNOWN_INVENTORY_DECREASE` | 仅记录不合格原因，不得默认解释为销售 |

引用只允许指向现有 task、operation/attempt、listing action item、Automation Run/Event、
Review/人工确认或不可变观察。没有可审计引用的说明不算已知调整。

## 7. 订单事实与扫描估算的权威关系

同一平台交易日和汇总范围的选择顺序固定为：

```text
完整 CLOSED 订单事实
> 部分订单事实
> 合格扫描估算
> UNAVAILABLE
```

1. 最新的完整 `CLOSED` 订单批次是正式销量、订单数和成交金额的权威来源。
2. 批次选择按 `scan_completed_at` 最新优先，再以 batch ID 稳定决胜；不同订单批次是
   重复快照，不能跨批次累加。
3. `ORDER_COMPLETE` 按汇总范围评价。平台总量不因 SKU 映射缺口丢失，但 SKU/品种等
   依赖映射的范围必须要求相关行 `VERIFIED` 且映射版本一致。
4. 存在 `ORDER_COMPLETE` 时，扫描估算只作为对账输入保留，绝不加入订单销量。
5. 只有部分订单事实时，汇总保持 `ORDER_OBSERVED / ORDER_PARTIAL`；扫描估算继续作为
   独立输入和差异证据，不补齐、不择一拼接，也不形成“部分订单 + 估算”的混合总量。
6. 没有可接受订单事实时，只有 HIGH/MEDIUM 合格 segment 才能形成
   `SCAN_ESTIMATED` 的 PROVISIONAL 数量；LOW 或不合格区间不计入。
7. 扫描估算首版只估数量，不从商品标价伪造订单成交金额；
   `transaction_amount_total` 在扫描来源下保持 NULL。

`source_proportions_json` 只描述覆盖和对账占比，不承载第二套销量，也不能用来把两个
来源相加。

## 8. 取消数量推导

取消是两个不可变订单快照之间的纯派生比较，不向订单观察表伪造取消行，也不新增取消
表。比较结果由两个订单批次输入、确定性算法版本和输入 hash 重算。

只有以下条件全部满足时才产生确定的 `cancelled_qty`：

1. 前后批次属于同一平台和同一平台交易日；
2. 两个批次在目标范围均完整，日期、加载、滚动、解析和尾部验证全部成功；
3. 两个批次是同一交易日按 `scan_completed_at` 排序的相邻完整快照；
4. 比较没有跨越 18:00 换日，且不是新交易日页面替换旧交易日页面；
5. 目标范围不存在阻断比较的映射失败或映射版本漂移；
6. 某 `order_identity_fingerprint` 的前一多重集合数量大于后一多重集合数量；
7. 先按 `(order_qty, order_transaction_amount)` 对同身份实例做精确多重集合抵消；
8. 后一集合的所有实例均能在前一集合中精确匹配，剩余前一实例的 `order_qty` 因而
   唯一可确定。

满足条件时：

```text
cancelled_order_count = 剩余前一实例数
cancelled_qty = sum(剩余前一实例.order_qty)
```

若后一集合含无法精确匹配的新内容，不能判断是订单内容变化还是哪个实例消失，结果为
`CANCELLATION_AMBIGUOUS`，`cancelled_qty=NULL`。取消量只用于对账解释；正式订单销量
直接取所选最新完整 CLOSED 批次的 `order_qty` 合计，不再减一次取消量。

## 9. 20:00 日结与 FINAL 门禁

20:00 作业结算刚结束的平台交易日，目标日期由 `OperationalTimeService` 计算，不由
调用方手填。首次执行只允许创建或幂等修订 `PROVISIONAL`，即使当时已经有完整 CLOSED
订单批次，也不能在同一步直接创建 `OBSERVED / RECONCILED / FINAL`。

来源选择规则为：完整订单、部分订单、合格扫描估算、UNAVAILABLE。无事实时数量、订单
数和成交金额均为 NULL，不得伪造 0。后续只允许既有单向转换：

```text
PROVISIONAL → OBSERVED → RECONCILED → FINAL
```

`PROVISIONAL → OBSERVED` 必须绑定至少一个已接受订单批次；只有扫描估算时保持
PROVISIONAL。`OBSERVED → RECONCILED` 必须完成订单/估算比较，所有差异均为确定取消、
已验证调整、明确接受偏差或已有人工决定；未分类差异保持 OBSERVED。本阶段不创建或
推进 Incident 状态机。

`RECONCILED → FINAL` 必须在同一 `BEGIN IMMEDIATE` 事务内重新验证：

1. 当前版本确为 `RECONCILED` 且 `is_current=1`；
2. 权威订单输入为目标交易日的 `CLOSED` 完整批次；
3. 正式汇总范围质量为 `ORDER_COMPLETE`；
4. `sold_qty`、`order_count`、`transaction_amount_total` 可从绑定订单输入精确复算；
5. 取消比较如存在，结果确定且未被重复扣减；
6. 映射版本、订单输入 hash、估算 segment hash、算法版本和时间策略版本完整；
7. 订单与估算差异已经分类，没有 `UNCLASSIFIED_DIFFERENCE`；
8. 没有现存、未解决且 `blocks_finalization=1` 的 Incident；本阶段只读取该门禁；
9. 使用显式 finalization policy 版本，事件
   `trigger_type=FINALIZATION_POLICY`、`trigger_ref_id=<policy_version>`；
10. summary 更新、当前输入 manifest 和状态事件在同一事务提交。

`ORDER_PARTIAL`、`OPEN`、SCAN_ESTIMATED、UNAVAILABLE、时间到达 20:00 或一次 Automation
Run 成功，均不能单独进入 FINAL。FINAL 后迟到订单或人工修正继续复用现有
supersedes 版本链，新版本从 OBSERVED 开始。

历史订单回补必须在一个 `BEGIN IMMEDIATE` 中冻结 current summaries、最新订单、估算和
当前 SKU 维度，并按新输入重新计算全部应有范围。既有非 FINAL 范围刷新同一版本，既有
FINAL 范围创建 current OBSERVED supersedes，新出现的品种、等级、SKU 和时间桶复用
PROVISIONAL→OBSERVED 状态机创建；范围、输入和事件必须同时提交或全部回滚。相同订单
batch 和 manifest 重放不得再创建版本。

## 10. Repository、应用服务和 Automation 边界

编码阶段只允许增加以下职责：

- `SalesEstimateService`：选择相邻观察、解析已知调整、计算资格/原因/质量并生成 segment；
- `OrderCancellationService`：对相邻完整快照执行纯多重集合比较；
- 现有 Repository：查询候选观察、来源账本、segment，追加 segment，选择日结输入；
- `TradeDaySettlementService`：20:00 创建 PROVISIONAL、选择订单权威、对账和推动既有状态；
- `SettlementPipeline`：编排一次冻结输入、一次多范围计算、原子持久化、新连接回读和
  多个只读投影；它不建立平行账本或状态机；
- `SalesPlanInputService`：从同一结算 Snapshot 生成只读、确定性的计划输入 manifest；
- Automation Handler：接入现有 `PLATFORM_TRADE_DAY_SETTLEMENT` 和
  `SALES_PLAN_INPUT_BUILD`，复用既有 Run、租约、完成接口和事件。

计划输入首版只汇总平台销售过程中可获得并可追溯的事实：

- 按 `seller_operation_date` 组织的历史销量、成交金额、观察订单数、品种、等级和 SKU；
- 10/30/60 分钟或现有精度允许的时间桶、首末订单时间、订单到达间隔、峰值和销售速度；
- 计划生成前已经发生的 18:00–20:00 平台早期订单信号；该信号必须标记来源日期和
  `feature_role=PRE_PLAN_EARLY_SIGNAL`，不得计入目标 PRA 交易日实际销量标签；
- 平台观察到的价格、库存、上下架轨迹，以及可由扫描精度支持的变价次数、观察持续
  时间、缺货/补货区间和可售时长；
- 可确定取消、后续订单修订、映射质量、扫描覆盖、数据新鲜度、结算状态和输入引用；
- “第 N 次购买”仅在平台采集合同修订完成后形成复购序号和复购订单占比，修订前保持
  `UNAVAILABLE`，不得推断。

平台日结可以作为来源证据，但计划样本必须在 20:00 边界重新归属；不能用
`platform_trade_date` 与 `seller_operation_date` 组成二维预测日期。首版不预测未来
销量、不创建销售任务、不写平台。线下生产、佣金/退款、外部日历/天气和市场指数只在
文档中保留后续 Provider 规划，不进入本阶段 manifest。

20:00 日结运行执行以下唯一流水线：

```text
冻结订单/估算/维度输入
→ 一次计算全部范围
→ 同一 SQLite 事务持久化
→ 新只读连接回读
→ SettlementSnapshot
→ SalesPlanInput / ManagementReport / AuditReceipt
```

`SALES_PLAN_INPUT_BUILD` 是恢复和校验入口，只读取 20:00 结算 Run Event 中已持久化的
计划投影并复算 hash，不重新选择订单、汇总销量或创建版本。历史回补投影必须标记
`AUDIT_ONLY`，不得作为当前运营计划。

`PRE_PLAN_EARLY_SIGNAL` 的可信零值和确认总量要求最新完整、已接受 OPEN 快照在20:00
边界之后10分钟内完成，即默认 `[20:00,20:10]`；生成时刻之后的快照不进入当前投影。
20:00 前完成的快照只能作为部分领先证据，不得确认整个 `[18:00,20:00)` 窗口。
该快照之后只要出现 FAILED、UNAVAILABLE、PARTIAL、范围不完整或尾部未确认的订单扫描，
结果即为 `EVIDENCE_INSUFFICIENT`，数量与金额保持 NULL，`trusted_zero=false`。没有边界
附近快照同样属于证据不足；历史补跑只可标记 `AUDIT_ONLY`。

### 10.1 销售管理报告与技术审计分离

人工可读结算报告面向销售管理，不承担数据库审计回执职责。正文不得展示 Run、Summary、
Batch、订单指纹、manifest、SHA-256、mapping version、algorithm version、原始 JSON 或
supersedes ID；这些技术证据只保存在 Runtime DB、Automation Event 和系统维护入口。

报告默认控制在一个屏幕内，只回答：

1. 本 PRA 交易日卖了多少：销量、订单观察数、成交金额、平均每扎成交金额；
2. 什么卖得好：销量和成交金额靠前的品种、等级，默认只显示前三项；
3. 什么时候卖得快：主要高峰时段及其销量占比；
4. 当前需要处理什么：缺货/低库存、异常取消、明显修订、数据不完整；
5. 数据能否用于经营判断：明确区分“可信零值 / 证据不足 / 无数据或不可读取”。

没有实质异常时不输出空的异常表；没有可比历史时不展示空同比；低价值的全量 SKU、
24 个空时间桶和逐项来源不进入正文。需要进一步分析时由销售分析页面展开，不把日报
写成技术日志。

完整事实确认无销售时，销量、订单观察数和成交金额可以显示可信的 0，Top 榜单和高峰
时段可以为空，并显示“当天暂无成交”。存在部分或低质量事实但不能下结论时显示
“证据不足”及简短原因；没有可接受事实或读取能力失败时才显示“无数据/不可读取”。

统一 `SettlementSnapshot` 可以同时派生两种输出，但二者不得混写：

```text
ManagementReport：简短业务结论，不含技术 ID/hash
AuditReceipt：readback_passed、计数、版本和 hash，仅供系统审计
```

## 11. 测试与开工门禁

开发中只运行估算、取消、日结、Repository、Automation Handler 及直接依赖测试。
Ready for review 前按 R4 统一运行受影响集成、完整 pytest、系统冒烟和 Linux/Windows
CI；本阶段没有新增真实页面或平台动作，不要求重复 13.5-4 实机读取。

至少覆盖：

- 正/负/零 known adjustment 的符号和公式；不合格区间保持 NULL；
- PRA 写、人工修改、上架重设、target inventory、对账修正、未知增减逐类处理；
- 没有记录不等于调整覆盖完整；UNKNOWN/RECONCILE 阻断；
- 相邻、缺失、冲突、重叠、跨 18:00、离线、映射漂移和算法版本变化；
- segment 精确重放、事后补证后的不可变新身份和当前证据版本选择；
- HIGH/MEDIUM/LOW 映射，订单和估算绝不重复累计；
- 完整 CLOSED、部分订单、仅估算和 UNAVAILABLE 四种来源选择；
- 重复订单、多重集合减少、数量唯一、内容变化歧义和取消不重复扣减；
- 20:00 只创建 PROVISIONAL，非法跳级/回退/直接 FINAL 全部拒绝；
- FINAL 的十项门禁、同事务失败回滚、输入 manifest 幂等和迟到数据 supersedes；
- FINAL 复算订单、估算 segment 集合、算法/映射版本、差值和对账决定绑定；
- 旧 FINAL 回补生成 current OBSERVED supersedes；非 FINAL 回补保持同一版本并回到/保持
  OBSERVED；相同 manifest 精确重放；
- 历史回补新建品种/等级/SKU/时间桶，多范围来自同一冻结输入且同一事务提交；任一范围
  失败时全部旧 FINAL 继续保持 current；
- PRA 交易日严格使用 20:00 边界，跨两个平台交易日的事实按逐项时间重新归属；
- 平台日结只作为来源，不被直接冒充为 PRA 交易日样本；18:00–20:00 领先信号不进入
  目标日实际销量标签；
- 价格、库存和上下架观察进入销售过程汇总；未采集购买序号时复购指标保持不可用；
- 人工报告正文不出现 ID、hash、manifest、原始 JSON 或版本链，只保留销售管理关键结论；
- 20:00 后10分钟快照新鲜度、后续失败/缺失门禁和历史 `AUDIT_ONLY`；可信零值、证据
  不足、无数据/不可读取三态互不混用；Automation Event payload 有界；
- 不创建 Incident、不注册平台写 Handler、零平台副作用；
- 平台、交易日、SKU、映射版本和输入引用隔离。

编码只能在本合同被接受且复用矩阵无新增平行实现后开始。超出复杂度预算、需要新表/
字段/状态/锁/平台动作，或需要提前实现 Incident 时，必须暂停并重新评审。
