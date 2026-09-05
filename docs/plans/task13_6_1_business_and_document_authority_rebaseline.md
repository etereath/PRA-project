# Task 13.6-1：业务与文档权威重基线

更新时间：2026-09-06  
状态：`POST-CLOSURE / G1 RETEST READY`  
父任务：GitHub Issue #41  
基线：`main@6857254b136c36ba72d9bb89a0904b0570f906e6`  
前置：Task 13.6-0 Stage Goal = `PASS`

## 1. 任务定位

13.6-1 负责把 PRA 的历史材料重新整理成一套经过 owner Business Decision Closure 的业务基线候选。

本阶段不进行生产功能开发，也不把候选架构写死为实现方案。核心目标是：

> 明确 PRA 当前真实经营流程、关键数量和日期语义、现役与历史文档角色，以及后续 13.6-2 必须面对的实现差距。

13.6-1 的最终 Gate 是 **G1 Business Baseline Review**。G1 通过后才能进入 13.6-2。

## 2. 当前交付物

### 2.1 文档与证据权威盘点

`docs/rebaseline/task13_6_document_authority_inventory.md`

当前状态：`POST-CLOSURE / READY FOR G1 RETEST`。

作用：

- 对高影响入口、历史计划、实现文档、验证 evidence 和旧合同分配明确角色；
- 标明主要冲突是已解决业务问题、待 13.6-2 实现/架构问题，还是待 13.6-3 入口收口问题；
- 防止当前代码、旧 Issue 或历史报告反向成为永久业务权威。

### 2.2 业务基线候选

`docs/rebaseline/task13_6_business_baseline_draft.md`

当前状态：`POST-CLOSURE / G1 CANDIDATE / NOT YET CANONICAL`。

已吸收 OD-01～OD-06，包括：

- 18:00 单一销售日界；
- 20:00 planning checkpoint；
- Daily Supply 三阶段覆盖 + Carryover；
- Sales Exposure 非 reservation；
- Current Sales Commitment provider；
- `CurrentTradeDaySalesObservation`；
- 19:00 独立 Daily Sales Closing；
- Closing 重试/S2/管理员维护边界；
- Observation Health；
- one-shot Intent supersession；
- Task 14-A / 14-B 双工作线。

### 2.3 Decision Register

`docs/rebaseline/task13_6_open_decision_register.md`

当前角色已经从 OPEN input 转为 `CLOSED / DECISION HISTORY`。OD-01～OD-06 均已关闭，不得再作为 owner 待决问题重新提出。

### 2.4 Business Decision Closure

`docs/rebaseline/task13_6_business_decision_closure.md`

记录一次性 owner 裁决及 Closure 过程中识别出的当前实现缺口。

## 3. Business Decision Closure 已完成

执行顺序已经完成到：

```text
文档/证据盘点
→ 业务主链初稿
→ Open Decision Register
→ Business Decision Closure
→ 回灌 Business Baseline
→ 文档权威状态收口
→ G1 Retest   ← 当前
```

不存在仍待 owner 裁决的 G1 blocker。

若 G1 retest 发现问题，应先判断：

- **文档收口缺陷**：直接修复，不重新开启 Business Decision Closure；
- **实现/架构问题**：登记给 13.6-2；
- **真正业务矛盾**：只有新的平台事实、owner 新要求或基线内部不可成立时才允许显式 `BUSINESS BASELINE REOPENED`。

## 4. 已冻结的核心业务方向

### 4.1 Sales Controller

当前实时销售决策者是人类管理者。Evaluator、Automation 和未来 Agent 都不能因为已有技术能力自动成为 Sales Controller。

### 4.2 日期与日界

- 当前蚂蚁平台 18:00 `platform_trade_date` 是唯一销售业务换日点；
- 20:00 `seller_operation_date` 第二日界已经被 supersede；
- 20:00 左右仅作为 Supply / Strategy planning checkpoint；
- `production_date` 保留为生产供给日期；
- `order_page_visible_trade_date` 只作为订单页 UI/capability 事实。

### 4.3 Supply / Carryover

```text
PRODUCTION_FORECAST
→ HARVEST_ESTIMATE
→ PACKAGED_ACTUAL
```

同一生产日覆盖、不相加。

`CARRYOVER_CONFIRMED` 是进入新交易日后确认的、未被上一周期承诺占用、仍可继续销售/履约的剩余。

### 4.4 Current Sales Commitment

- 冻结期：`CurrentTradeDaySalesObservation` 直接校准当前平台交易日实时销售；
- 订单页 rollover 后：Full Scan 中 current-trade-day Order Observation 作为直接 provider；
- Light Scan 全时段提供价格、Exposure、上下架和 QUICK-derived 辅助；
- provider 接管，不重复相加；
- 直接观察只证明其真实粒度。

### 4.5 Daily Sales Closing

- 19:00 独立扫描冻结的上一交易日订单页；
- 与 Current Sales Commitment 完全分离；
- 首次失败：故障报告 + 自动重试一次；
- 第二次失败：Closing S2 + 人工复核，停止自动重试；
- 成功后自动链不得再次对同平台/交易日发起 Closing 扫描；
- 后续维护必须由管理员显式发起并审计；
- `purchase_sequence` 是当前明确最小采集缺口；
- 页面售价由 `order_transaction_amount / order_qty` 派生，不新增独立采集字段。

### 4.6 Intent / Execution

人工 Intent 是有范围、有效期和完成条件的 one-shot business intent。已跨越副作用边界的旧执行必须先收口，不得通过删除或假取消消失。

### 4.7 Observation Health

S0～S4 的实时观察风险按 provider expected cadence + capability 判断；S3 主动 Recovery，实际确认链路失败才 S4。Closing 自身故障链最高只到 Closing S2。

### 4.8 Task 14

- 14-A：Integrated Acceptance & Freeze；
- 14-B：Agent Intervention / Ops Agent；
- 14-B 首版不直接成为自动销售 Controller；
- 两线真实接入前共同 integration gate。

## 5. 当前明确留给 13.6-2 的问题

这些不是 G1 owner 决策：

- 当前代码中的 20:00 dual-boundary 如何退役/兼容；
- 旧 Settlement/Summary 哪些底层能力复用、哪些状态机退役；
- Supply / Commitment / Intent 的最小持久化方式；
- Runtime Task 端到端推进 owner 与 coordinator 最小职责；
- `purchase_sequence` 的最小元素定位、合同、持久化和 READ_ONLY 回归；
- Closing 管理员维护入口；
- 当前 `target_inventory <= real inventory` 等 stale business rules 的清理范围；
- Observation Health 的实现位置与配置；
- Agent interface 的最小受控边界。

## 6. 13.6-1 不做

不得：

- 修改 `app/`、`shadowbot/`、生产 `scripts/` 或 Runtime Schema；
- 修改真实 Runtime DB、Queue、Worker、Automation 配置或平台状态；
- 执行真实平台写；
- 实现 Supply、Commitment、Intent、Coordinator、Closing、Observation Health 或 Agent；
- 修改历史 evidence 或哈希绑定材料；
- 恢复 13.5-7G；
- 因为当前代码存在而重新定义 G1 业务语义。

## 7. G1 Retest 固定情景

G1 retest 至少验证：

1. **Supply convergence**：Carryover + Forecast → Harvest → Packaged，不重复扣上一周期 Commitment；
2. **18:00 rollover**：CurrentTradeDaySalesObservation 服务新交易日，冻结订单页服务旧日 Closing；
3. **19:00 Closing success**：成功后普通自动链不能重扫同日；
4. **Closing double failure**：第一次自动重试，第二次 Closing S2 + 人工，停止自动重试；
5. **Exposure adjustment**：PRA 自身 adjustment evidence 不被误判为销量；
6. **Intent supersession**：未副作用旧动作可替代，QUEUED/RUNNING/UNKNOWN 必须先收口；
7. **Observation Recovery**：S3 排队/合法等待不等于 S4，probe 真实失败才 S4；
8. **Task 14 boundary**：14-A 综合验收与 14-B Ops Agent 并行但不绕过确定性链。

## 8. 13.6-1 完成条件

- [x] 高影响文档与目录默认角色已完成盘点；
- [x] 主要冲突已登记并分类；
- [x] 业务主链初稿完成；
- [x] Open Decision Register 建立；
- [x] Business Decision Closure 完成；
- [x] OD-01～OD-06 全部关闭；
- [x] 裁决已回灌业务基线；
- [x] Decision Register 已转 CLOSED 历史角色；
- [x] Authority Inventory 已吸收 post-closure 状态；
- [x] 没有生产代码、Schema、真实数据或平台副作用；
- [ ] G1 Business Baseline Retest = PASS。

## 9. 当前状态

```text
Task 13.6-1: G1 RETEST READY
Business Decision Closure: CLOSED
G1 Business Baseline: NOT YET VALIDATED
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
```
