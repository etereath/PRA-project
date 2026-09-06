# Task 13.6 Decision Register

状态：`CLOSED / DECISION HISTORY`  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
父任务：GitHub Issue #41  
权威裁决记录：`docs/rebaseline/task13_6_business_decision_closure.md`

## 1. 角色

本文件原先用于在 13.6-1 Business Decision Closure 前集中 owner 待决事项。

Business Decision Closure 已完成。OD-01～OD-06 全部关闭，不再是 G1 blocker，也不得被后续 AI 当作仍需重新询问 owner 的问题。

历史候选方案、讨论过程和修改轨迹由 Git 历史保留；当前业务语义以：

1. `task13_6_business_decision_closure.md` 的 owner 裁决；
2. 已吸收裁决的 `task13_6_business_baseline_draft.md`；

为准。

若 G1 通过后确因新的真实平台事实、owner 新要求或内部逻辑矛盾需要重新打开业务基线，必须显式记录：

`BUSINESS BASELINE REOPENED`

不得通过修改本表静默恢复旧候选。

## 2. 已关闭决策

### OD-01 — CLOSED：Supply / Carryover / Commitment

冻结结论：

- `CARRYOVER_CONFIRMED` 是进入新交易日后确认的、未被上一周期销售承诺占用、仍可继续销售/履约的剩余量；
- `PACKAGED_ACTUAL` 是目标生产日新包装出的生产总量事实；
- `PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL` 是覆盖关系，不相加；
- Current Sales Commitment 表示当前平台交易日累计已形成的销售承诺；
- 基础经营压力可按 `Carryover + current effective Daily Supply - current Commitment` 理解，安全缓冲、损耗和履约风险独立表达。

### OD-02 — CLOSED：Current Sales Commitment observation provider

冻结结论：

- Commitment 不把多个来源相加；
- 订单冻结期，蚂蚁平台使用 `CurrentTradeDaySalesObservation` 作为当前交易日实时销售直接 observation provider；
- 订单页 rollover 到当前交易日后，由 Full Scan 中 current-trade-day Order Observation 作为直接 provider；
- Light Scan 始终提供价格、Exposure、上下架和 QUICK-derived 辅助信息；
- qualifying provider 接管当前 Commitment，不与旧估算重复累计；
- 直接观察只证明其真实粒度，1:N SKU 映射不得任意拆分。

### OD-03 — CLOSED：18:00 单一日界与 Daily Sales Closing

冻结结论：

- 取消 20:00 `seller_operation_date` 作为第二销售日界；
- 当前蚂蚁平台以 18:00 `platform_trade_date` 为唯一销售换日点；
- `Current Sales Commitment` 是当前交易日动态盘中状态；
- `Daily Sales Closing` 是上一已结束交易日的独立历史日结，两者不共享 Summary 生命周期；
- 19:00 对冻结的上一交易日订单页执行独立 Closing Order Scan；
- Closing 成功后自动链不得再为同平台/交易日重扫；后续历史维护必须由管理员显式发起；
- Closing 第一次失败生成故障报告并自动重试一次；第二次失败升级为 Closing S2、呼叫人工复核并停止自动重试；Closing 自身不继续自动升级 S3/S4；
- 20:00 左右仅作为 Carryover / Forecast / Strategy planning checkpoint。

Daily Sales Closing 的业务事实至少包括：

- 品种；
- 等级；
- `order_qty`；
- `order_transaction_amount`；
- `order_created_at`；
- `purchase_sequence`。

页面售价不新增第二采集源；按 `page_unit_price = order_transaction_amount / order_qty` 从现有事实派生。

### OD-04 — CLOSED：Observation Health

冻结结论：

- S0 正常；
- S1 首次超出 provider expected cadence；
- S2 主校准连续缺失但仍有可信 fallback；
- S3 无足够可信实时校准，立即请求 Recovery Calibration；已排队或合法 UI 占用时保持 `S3/RECOVERING`；
- S4 只有主动 Recovery Calibration 已确认平台级/链路级失败才进入，不额外靠等待时间升级；
- freshness 优先按 provider expected cadence + capability，而不是固定全平台分钟数；
- 单 SKU 故障不自动成为平台 S4；
- S4 不自动授权全平台下架。

### OD-05 — CLOSED：Intent supersession

冻结结论：

- 当前人工 Sales Control Intent 是有范围、有效期和完成条件的 one-shot business intent；
- 新 Intent 只 supersede 明确涉及的业务维度；
- 已跨越 Queue / side-effect boundary 的旧执行必须先完成、回读或 reconcile；
- 外部人工平台修改属于正常经营场景，旧 Intent 默认不得自动改回；
- 未来 Agent 的持续目标应使用独立版本化策略，不把人工 Intent 变成无限纠正循环。

### OD-06 — CLOSED：Task 14 / Agent

冻结结论：

- `14-A Integrated Acceptance & Freeze` 负责综合真实旅程验收和版本冻结；
- `14-B Agent Intervention / Ops Agent` 首版负责诊断、运行解释、Incident / Observation Health 辅助和受控工具调用；
- 14-B 首版不直接成为自动销售 Controller；
- 两线可以并行，但真实接入前必须经过共同 integration gate；
- Agent 不得绕过确定性业务校验、授权、执行、回读与恢复基础设施。

## 3. 不重新打开的实现问题

以下不是 owner 业务决策，不应重新进入本 Decision Register：

- Supply / Commitment 的表名和表数；
- Intent 是否必须新表；
- Dispatch Attempt 是否复用既有 attempt；
- Coordinator 类名、模块名；
- Observation Health 是否持久化；
- Closing 管理维护入口的具体 UI/Schema；
- Event bus / message queue；
- 第二平台具体 cutoff；
- Exposure Allocator 算法；
- Agent 使用的具体模型或线程协议。

这些问题归 13.6-2 / 13.7 基于 G1 业务基线做最小设计。

## 4. 当前状态

```text
OD-01: CLOSED
OD-02: CLOSED
OD-03: CLOSED
OD-04: CLOSED
OD-05: CLOSED
OD-06: CLOSED

Business Decision Closure: CLOSED
G1 Business Baseline: READY FOR RETEST
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
