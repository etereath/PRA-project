# Task 13.6 Open Decision Register

状态：`OPEN / BUSINESS DECISION CLOSURE INPUT`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41

## 1. 使用规则

本表只收录会改变以下任一项的问题：

- 真实经营语义；
- 数量口径；
- 风险承担方式；
- 日常运营流程；
- 后续系统职责边界。

普通字段命名、数据库表数量、Repository 选择、类名、模块路径、缓存方式等实现细节不得占用 owner 决策预算。

执行时点：

```text
文档/证据盘点
→ 业务主链初稿
→ Open Decision Register 完整化
→ 一次性 Business Decision Closure
→ 更新业务基线
→ G1 Review
```

在 Business Decision Closure 之前，本文中的候选方案都不是 Canonical。

---

## OD-01：Supply / Carryover / Commitment 的净额与重复扣减

状态：`OPEN / G1 BLOCKER`

### 问题

进入一个新经营周期时，如何解释：

- `CARRYOVER_CONFIRMED`；
- 当日 `PRODUCTION_FORECAST / HARVEST_ESTIMATE / PACKAGED_ACTUAL`；
- 已经形成的 Current Sales Commitment；

才能避免同一销售承诺被重复扣减，又能正确反映真实可履约压力？

### 已确认约束

- 同日三个 Daily Supply 阶段是 supersession，不相加；
- `PACKAGED_ACTUAL(D)` 不能直接复制成 `CARRYOVER_CONFIRMED(D+1)`；
- Carryover 是进入新周期时独立确认的实际剩余事实；
- Current Sales Commitment 是盘中已形成的销售责任；
- Exposure 不是 reservation，不能作为实物扣减基础；
- 不应为了现有 DB balance 结构而强行选择一种业务公式。

### 必须先澄清的经营口径

1. `CARRYOVER_CONFIRMED` 在业务上是否已经扣除了尚待履约但已卖出的订单/成交承诺？
2. `PACKAGED_ACTUAL` 是当天新产出的包装总量，还是“扣除当天已履约后仍可用的包装量”？
3. Current Sales Commitment 需要表达“所有已成交数量”，还是“尚未履约的销售义务”？
4. 新经营周期的供给状态是面向“销售风险”还是面向“物理库存余额”？两者是否需要分开显示？

### 候选方向

**A. Gross facts + separate obligation**

- Carryover、Daily Supply 都记录 gross physical facts；
- Commitment 独立记录销售义务；
- 经营视图根据时间和履约状态计算净压力。

优点：证据清晰，不会把物理事实和销售义务混成一个数。  
风险：需要明确 fulfilled / unfulfilled 边界，否则仍会重复扣减。

**B. Carryover 已是 net available**

- Carryover 只记录已经扣除了旧周期销售义务后的“可继续销售剩余”；
- 新日 Commitment 只扣新周期形成的承诺。

优点：运营直观。  
风险：Carryover 需要稳定的人工/系统盘点口径，且历史追溯要知道已经吸收了哪些义务。

### 当前建议

优先考虑 **A 的事实分离原则**，但不在 owner 明确“Carryover 实际录入时包含/不包含哪些订单责任”前冻结公式。

### G1 需要产出

- Carryover 的一句话经营定义；
- Packaged Actual 的一句话经营定义；
- Commitment 是 gross sold 还是 outstanding obligation；
- 至少一个跨 20:00 的数量样例证明没有重复扣减。

---

## OD-02：Aggregate / Order / QUICK 的来源接管与 reconciliation

状态：`OPEN / G1 BLOCKER`

### 问题

同一平台、同一交易日、同一业务范围可能先后出现：

- QUICK-derived estimate；
- `CURRENT_DAY_SALES_AGGREGATE_OBSERVED`；
- `ORDER_OBSERVED` / 完整订单扫描。

后来的更高质量事实如何接管，才能既不重复累计，又保留估算误差和历史证据？

### 已确认约束

- 直接平台数量事实优于纯推导估算；
- “高质量”必须与目标平台、交易日、scope/granularity 匹配；
- 品种 + 等级的聚合事实不能任意拆到多个 SKU；
- 聚合窗口不是订单明细；
- PRA Exposure 调整必须从 QUICK 推导中扣除/解释；
- 历史来源不能为了得到一条“漂亮曲线”被删除。

### 候选方向

**A. Current selector / replacement**

每个 scope 选择一个当前最佳累计 Commitment：

```text
ORDER / qualifying aggregate
> QUICK-derived
```

新校准事实替换当前数值，不与旧估算相加；旧估算保留为 evidence。

**B. Increment ledger**

将所有来源都转换成增量流水，再做 reconciliation。

优点：统一。  
风险：聚合累计窗口天然不是增量；容易制造复杂的反推和重复更正规则。

### 当前建议

优先 **A：按 scope 选择当前最佳累计事实**。这更符合已有“累计销量 + 校准”的业务，也避免为了形式统一把累计观察硬转成增量账本。

但 G1 仍需裁决：

- ORDER 与 aggregate 同为直接事实时，什么条件下 ORDER 可以接管 aggregate；
- aggregate 显示 0 与页面缺失/读取失败如何区分；
- 订单取消/撤销是否已体现在平台累计数量中，以及无法确认时如何标记。

### G1 需要产出

- 来源优先级不是固定全局枚举，而是“同 scope 的 qualifying calibration”规则；
- 一个 QUICK → Aggregate → Order 的具体样例；
- 一条明确的“禁止重复相加”规则。

---

## OD-03：每日 19:00 经营日结与现有 Settlement/Summary 的关系

状态：`OPEN / G1 BLOCKER`

### 问题

项目 owner 要求：

> 每日指定 19:00 完整扫描形成/更新一次经营日结；普通盘中观察不能持续改写该日结。

现有代码已经拥有：

- `PlatformTradeDaySummary`；
- PROVISIONAL / OBSERVED / RECONCILED / FINAL；
- 迟到订单后 versioned supersedes；
- 20:00 后 settlement 作业。

两者应该如何收口？

### 已确认约束

- 不允许因为旧实现存在就默认增加第二个用户可见“日报”；
- 19:00 后普通 QUICK/订单观察可以更新实时经营状态，但不能顺手改写固定 19:00 日结；
- 历史数据修正仍可能有审计价值；
- 不能通过简单改名成“Closing Snapshot + Settlement Summary”绕开业务选择。

### 候选方向

**A. 一份经营日结 + 受控 correction version**

- 19:00 指定扫描生成正式经营日结；
- 后续普通观察不改它；
- 只有明确“更正/重结算”动作才能创建 successor version；
- UI 默认展示原 19:00 经营日结及“存在后续更正”的标识。

**B. 经营日结与 Settlement 完全分离**

- 19:00 是经营快照；
- 20:00/迟到订单维护另一个结算对象。

优点：职责纯粹。  
风险：增加双对象、双解释和维护成本；当前是否有足够业务价值尚未证明。

### 当前建议

优先审查 **A：一份经营日结 + 显式更正版本** 能否复用现有 Summary/version 设施。

只有证明“经营日报”和“财务/履约结算”确实服务不同用户与不同业务动作时，才选择 B。

### G1 需要 owner 决策

1. 19:00 日结生成后，迟到订单是否允许产生“更正版”？
2. 如果允许，用户查看历史时默认看 19:00 原始版本、最新更正版，还是两者同时展示？
3. 20:00 settlement 是否仍有独立业务意义，还是可以变成后台对 19:00 日结的 reconciliation/correction？
4. 指定 19:00 扫描失败时，补跑如何标记，是否还能称为当天正式日结？

### G1 需要产出

- 一份正式日结的用户语义；
- 一条普通观察不可改日结的规则；
- correction / rerun / late data 的明确处理。

---

## OD-04：Observation Health S0–S4 的 freshness / capability 阈值与行为

状态：`OPEN / G1 BLOCKER`

### 问题

如何从“观察略旧”逐步进入 S3 主动恢复和 S4 已确认链路失败，同时不把正常排队、人工停用或局部异常错误升级？

### 已确认约束

- S3 必须主动发起适合当前平台模式的 Recovery Calibration；
- 平台级/链路级 Recovery Calibration 确认失败后立即 S4；
- 不再额外等待一段时间才从 S3 升 S4；
- rollover 期间有健康 aggregate provider 时可以保持正常；
- 单 SKU 错误不自动升级整个平台 S4；
- Automation 被人工停用、扫描排队、UI lease 正在占用，不等于已确认链路失败；
- S4 不能自动等价于全平台下架授权。

### 需要一次性裁决的内容

1. freshness 计时从哪种“最后成功校准”开始；
2. S1 / S2 / S3 是否按固定分钟数、按 expected cadence 倍数，还是按 platform capability；
3. Recovery request 已成功排队但尚未执行时，Health 处于什么状态；
4. Recovery 因“平台 UI 被合法写任务占用”延期时如何表达；
5. S2/S3 时哪些人工风险增加操作需要额外确认；
6. S4 下哪些风险降低操作仍允许人工执行。

### 候选方向

优先使用 **expected cadence + capability**，而不是全平台统一 15/30/60 分钟常数。

例如：

- 根据当前可用 calibration provider 的预期 cadence 判断 stale；
- S3 代表“超出允许窗口，需要主动恢复”；
- Recovery 已接受排队但尚未得到执行结果时仍属于 S3/recovering，而不是 S4；
- 只有实际 probe 返回平台级不可用/失败才进入 S4。

### G1 需要产出

- S0/S1/S2/S3/S4 的用户可解释一句话定义；
- “排队 / 延期 / 执行失败 / 单 SKU 失败”的区分；
- 风险增加与风险降低操作的原则级行为。

具体分钟参数若需要真实运行数据支撑，可以在 G1 只冻结计算方式，把数值作为配置/13.7 验证参数。

---

## OD-05：Intent supersession 的范围、有效期与外部人工修改

状态：`OPEN / G1 BLOCKER`

### 问题

人工在 Web 中作出的“当前价格 / Exposure / 上下架”决定应该维持多久？当新决定或外部人工平台修改出现时，旧决定什么时候失效？

### 已确认约束

- 新人工决定必须能替代尚未产生副作用的旧决定；
- 已跨越 Queue/side-effect boundary 的旧执行不能通过删除/取消记录假装没有发生；
- UNKNOWN 必须先 reconciliation；
- 外部人工平台修改属于正常经营行为，PRA 不能无条件把平台改回旧目标；
- Coordinator 不能替管理者决定“哪个价格更好”。

### 需要 owner 决策

1. 人工 Intent 是“执行一次直到达到目标”的一次性指令，还是“在有效期内持续维持目标”的 standing instruction？
2. 默认有效期应该到：
   - 任务完成；
   - 指定 expires_at；
   - 下一次人工决定；
   - 卖家作业日/平台交易日切换？
3. 如果平台被外部人工改动：
   - 旧 Intent 自动失效；
   - 进入 Review 等待确认；
   - 若 Intent 仍有效则自动纠正？
4. Price、Exposure、Listing Status 是否可以分别 supersede，还是每次 Intent 必须是一整个目标状态包？

### 候选方向

当前更适合的默认方向是：

> **人工 Intent 是一次有明确范围、依据、有效期和完成条件的经营决定，不是永久维持平台状态的无限期策略。**

理由：

- 当前 Controller 是人；
- 外部人工改动是正常场景；
- 永久 standing instruction 容易让 PRA 在过时上下文下覆盖人工新判断；
- 未来若 Agent/策略需要持续维持目标，应作为版本化政策另行授权。

### G1 需要产出

- Intent 是 one-shot 还是 standing；
- 默认有效期原则；
- 外部人工改动后的默认行为；
- supersession 的最小 business key（平台 + 商品/业务粒度 + 动作/目标维度）。

---

## OD-06：Task 14 原综合验收职责与 Agent Intervention / Ops Agent 并行线

状态：`OPEN / G1 BLOCKER`

### 问题

旧文档把 Task 14 定义为 Task 13/13.5 后的综合验收，并明确“不承担 Agent 实现”。当前 owner 已决定把 Agent Intervention / Ops Agent 作为 Task 14 的并行工作线。

需要避免两种极端：

- Task 14 变成一个体量过大的“什么都做”任务；
- 为了加入 Agent 而丢掉原本必须做的综合真实旅程验收。

### 已确认约束

- Task 13.7 必须先完成可靠的人工作业闭环；
- Agent 不能成为确定性恢复和人工执行的前置依赖；
- Observation Health 的 S3/S4 可以预留 Agent Intervention Hook，但 13.7 不实现 Agent runtime；
- Agent 不能绕过正式执行安全边界。

### 候选结构

**Task 14-A：Integrated Acceptance / Freeze**

- 多品种、多动作；
- 阻塞恢复；
- UNKNOWN/RECONCILE；
- 正式授权；
- Observation provider rollover；
- 版本冻结与运维门禁。

**Task 14-B：Agent Intervention / Ops Agent**

- Observation Health / Incident 诊断接口；
- 运行状态解释与建议；
- 受控工具调用；
- 后续是否扩展到销售 Controller 的独立授权路线。

两条线可以并行开发，但最终接入前必须有共同集成门禁。

### 当前建议

采用 **14-A / 14-B 双工作线**，而不是把 Agent 直接塞回 13.7 或抹掉 14 的综合验收职责。

### G1 需要 owner 决策

- 是否接受 14-A / 14-B 结构；
- Agent 首个范围是否只做 Ops/diagnosis，不做自动销售 Controller；
- 两条线在哪个共同 Gate 汇合。

---

## 2. 非 G1 阻塞项

以下问题当前不进入 owner 决策：

- Supply / Commitment 最终表名与表数；
- Coordinator 类名、模块名；
- Dispatch Attempt 是否复用既有 execution_attempts；
- Event 是否使用现有 Automation Event / Task History；
- Observation Health 是否落表；
- 第二平台具体 cutoff；
- Exposure Allocator 算法；
- Agent 是否使用某个具体模型或线程协议；
- Web 页面布局和字段顺序。

这些均应在 13.6-2/13.7 基于 G1 结果做最小设计。

## 3. 当前 Decision Closure 状态

| ID | 主题 | 状态 | G1 |
| --- | --- | --- | --- |
| OD-01 | Supply / Carryover / Commitment 净额 | OPEN | BLOCKER |
| OD-02 | Aggregate / Order / QUICK 接管 | OPEN | BLOCKER |
| OD-03 | 19:00 日结 / Settlement | OPEN | BLOCKER |
| OD-04 | Observation Health S0–S4 | OPEN | BLOCKER |
| OD-05 | Intent supersession | OPEN | BLOCKER |
| OD-06 | Task 14 / Agent 边界 | OPEN | BLOCKER |

在 Business Decision Closure 完成前：

```text
G1 Business Baseline: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
