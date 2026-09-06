# Task 13.6-1 G1 Business Baseline Review

日期：2026-09-06  
父任务：GitHub Issue #41  
工作 PR：#43  
业务基线输入：`docs/rebaseline/task13_6_business_baseline_draft.md`  
Owner 裁决：`docs/rebaseline/task13_6_business_decision_closure.md`

## 1. Review 结论

```text
Business Decision Closure: PASS / CLOSED
Business Semantics Review: PASS
Documentation Consistency Review: PASS
G1 Business Baseline: PASS
Task 13.6-1 Stage Goal: PASS
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
Next: Task 13.6-2
```

本报告是 G1 Gate 的当前状态证据，并 supersede 本 PR 其他 G1 输入文档中在 Review 前写入的 `READY FOR G1 RETEST` / `NOT YET VALIDATED` 状态块。那些状态块保留为各输入形成时的阶段记录，不代表本报告生成后的当前 Gate 状态。

G1 PASS 只冻结业务基线，不表示相关生产功能已经实现，也不授权跳过 13.6-2 的当前实现/目标架构审计。

## 2. Review 范围

本轮只审查 Task 13.6-1 应负责的内容：

1. Business Decision Closure 的 OD-01～OD-06 是否全部被业务基线吸收；
2. 当前经营主链是否自洽；
3. 是否仍残留旧 20:00 双日界、旧 Settlement 正常生命周期或 OPEN OD 作为现行业务语义；
4. Current Sales Commitment 与 Daily Sales Closing 是否真正分离；
5. Supply / Carryover / Exposure / Intent / Observation Health / Task 14 边界是否互相矛盾；
6. 是否把实现细节提前冻结为业务合同；
7. PR 是否保持纯文档、零生产副作用。

本轮不审查：

- 当前代码是否已实现新业务基线；
- Schema / Repository / Coordinator 的具体设计；
- `purchase_sequence` 的元素定位与迁移实现；
- Closing 管理入口实现；
- 第二平台适配；
- Agent runtime 实现。

这些属于 13.6-2 / 13.7 / Task 14。

## 3. 原 G1 阻塞项复审

### 3.1 原 P1：Post-Closure 文档未收敛

状态：`CLOSED`

修复后：

- `task13_6_business_baseline_draft.md` 已改为 post-closure G1 candidate；
- 20:00 `seller_operation_date` 第二日界已从现行业务语义中移除；
- 18:00 `platform_trade_date` 成为当前蚂蚁平台唯一销售日界；
- `CurrentTradeDaySalesObservation` 已成为冻结期 Current Sales Commitment 的直接 provider；
- 19:00 `Daily Sales Closing` 已与实时 Commitment 分离；
- 旧 `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 被降为 13.6-2 的历史实现/复用输入；
- OD-01～OD-06 不再出现在主基线中作为 OPEN blocker。

### 3.2 原 P2：阶段计划与 PR 元数据仍停留在 Closure 前

状态：`CLOSED`

修复后：

- 阶段计划已推进到 `POST-CLOSURE / G1 RETEST READY`；
- Decision Register 已转为 `CLOSED / DECISION HISTORY`；
- Authority Inventory 已区分已解决业务冲突、13.6-2 架构/实现 gap 和 13.6-3 入口文档 gap；
- PR #43 描述已更新为 post-closure 状态。

## 4. 固定业务情景验证

### Scenario A：Supply convergence

输入：

```text
CARRYOVER_CONFIRMED = 40
Forecast = 120
Harvest Estimate = 115
Packaged Actual = 113
Current Trade Day Commitment = 20
```

期望：

```text
Daily Supply: 120 → 115 → 113
Operating pressure: 140 → 135 → 133
```

结果：`PASS`

原因：Daily Supply 同轴覆盖，不相加；Carryover 已定义为未被上一周期 Commitment 占用的可继续销售剩余，因此不会重复扣旧承诺。

### Scenario B：18:00 rollover / frozen order page

18:30：

```text
platform_trade_date              = D+1
CurrentTradeDaySalesObservation  = D+1 current sales
order_page_visible_trade_date    = D frozen page
```

结果：`PASS`

D+1 realtime provider 与 D frozen Closing source 服务不同职责，没有 D / D+1 互相污染。

### Scenario C：19:00 Closing success

Closing Order Scan(D) 完成目标日期、范围、尾部/可信空页验证并成功写入。

结果：`PASS`

成功后自动链锁定 `(platform, D)`，后续普通 retry / late-data refresh 不得重扫；历史维护只能由管理员显式发起。

### Scenario D：Closing double failure

```text
attempt #1 FAIL
→ fault report + one retry
attempt #2 FAIL
→ Closing S2 + human review + stop automatic retry
```

结果：`PASS`

Closing 自身风险较低，故障链封顶 S2；若实时 observation provider 同时失败，则 realtime Observation Health 独立处理 S3/S4。

### Scenario E：Exposure adjustment evidence

PRA 将 Exposure 100 → 150，后续平台显示 142。

结果：`PASS`

不能机械解释为销售 8；必须先绑定 PRA 自身 +50 adjustment evidence，再依据 observation contract 判断 QUICK-derived 销量。

### Scenario F：Intent supersession

旧价格 Intent=9.5，新人工 Intent=10.5。

结果：`PASS`

- 未跨副作用边界的旧动作可 supersede；
- QUEUED/RUNNING/UNKNOWN 旧动作必须先完成、回读或 reconcile；
- 外部人工平台修改后，过时 Intent 不自动把平台改回。

### Scenario G：Observation Recovery

结果：`PASS`

- S3 立即发起 Recovery Calibration；
- 已排队或合法 UI 占用保持 `S3/RECOVERING`；
- 只有实际 probe 确认平台级/链路级失败才 S4；
- 不靠单纯等待时间升级 S4。

### Scenario H：Task 14 boundary

结果：`PASS`

- 14-A 保留 Integrated Acceptance & Freeze；
- 14-B 首版只做 Ops Agent / diagnosis / controlled tools；
- 14-B 不直接成为自动 Sales Controller；
- 两线真实接入前共同 integration gate。

## 5. Daily Sales Closing 研究字段核对

业务基线要求的订单历史事实包括：

- 品种；
- 等级；
- `order_qty`；
- `order_transaction_amount`；
- `order_created_at`；
- `purchase_sequence`。

当前实现证据：

- `order_created_at` 已采集并持久化；
- `purchase_sequence` 尚未实现，是明确的后续最小采集缺口；
- `occurrence_no` 只是相同订单指纹真实重复行的多重集合序号，不等于复购序号；
- 页面售价不新增第二采集字段，按 `order_transaction_amount / order_qty` 从当前已保存事实派生。

结论：`PASS AS BUSINESS CONTRACT / IMPLEMENTATION GAP RECORDED`

## 6. 13.6-2 必须接手的问题

G1 PASS 后以下问题转入 13.6-2，不重新打开业务基线：

1. 当前代码中的 18:00/20:00 dual-boundary 如何最小退役/兼容；
2. 旧 Settlement/Summary 哪些底层能力复用、哪些生命周期退役；
3. Current Sales Commitment 的最小持久化/投影方案；
4. Supply / Carryover 的最小权威事实结构；
5. one-shot Intent 与现有 Runtime Task/operation/attempt 的最小映射；
6. 普通 Runtime Task 端到端推进 owner / coordinator 责任；
7. `purchase_sequence` 的最小 READ_ONLY 采集与持久化；
8. Closing 成功锁定、一次重试、S2 人工与管理员维护入口；
9. `target_inventory <= real inventory` 等 stale business rule 的清理范围；
10. Observation Health 的 provider/capability 与恢复机制实现边界；
11. Task 14-B Agent interface 的最小受控边界。

若 13.6-2 只是发现当前实现与 G1 不一致，应登记 Implementation Gap，不得静默改变上述业务语义。

只有新的真实平台事实、owner 主动改变经营要求或发现 G1 内部逻辑无法成立时，才允许显式：

`BUSINESS BASELINE REOPENED`

## 7. 证据与仓库门禁

G1 修复后的审查 head：`3472a29f7b5d9a23f14f03146d2611d50e7c462a`。

Core CI #160：`SUCCESS`。

该 CI 覆盖 Linux Core 与 Windows Core，并包括测试、Task 12/13 evidence binding、system smoke、编译、secret/package audit 和 checkout clean 等仓库门禁。

PR 相对 `main@6857254` 仍仅包含 Task 13.6-1 Markdown 文档，不包含生产代码、Runtime Schema、真实数据或运行配置修改；本阶段没有真实平台副作用。

## 8. Gate 输出

```text
Implementation / Documentation Review: PASS
Business Semantics Review: PASS
G1 Business Baseline: PASS
Task 13.6-1 Stage Goal: PASS
Task 13.6 Overall: NOT YET VALIDATED
Task 13.7 Readiness: NOT READY
Next: Task 13.6-2 — System Architecture & Implementation Gap Rebaseline
```
