# 任务 13.5-5：销售估算、日结与计划输入实施报告

- 实施日期：2026-08-01
- Review Profile：`R4`
- 数据库结构版本：Runtime Schema v14，未扩表、未增字段
- 平台动作：0；未修改 ShadowBot Worker、队列、Adapter 或真实平台
- 合同权威：GitHub Issue #20 评论 `5145772862` 与
  `docs/plans/task13_5_5_sales_estimation_settlement_contract.md`

## 1. 实施结果

本阶段在现有 v14 结构上补齐了从商品库存观察、订单观察到交易日日结和下一销售计划
输入的只读经营事实链。新代码只写 Runtime SQLite 中已经存在的
`sales_estimate_segments`、`platform_trade_day_summaries`、汇总输入和事件，以及既有
Automation Run 的输入/输出 hash；没有销售平台副作用。

复杂度预算实际结果：

```text
新增数据库表：0
新增数据库字段：0
新增全局锁或租约：0
新增平台合同版本：0
新增真实平台动作：0
新增结算状态：0
```

## 2. 销量估算

`SalesEstimateService` 使用相邻商品观察构造确定性 segment 身份，执行有符号调整公式，
并冻结 HIGH 不超过 15 分钟、MEDIUM 不超过 25 分钟、LOW 不保存精确估算量的规则。
不合格 segment 的 `estimated_sold_qty` 保持 NULL；库存不可读时不创建 segment，也不会
把缺失库存伪造成 0。

已知调整继续使用现有账本和审核表：

- 已验证的上架库存重设从 `shadowbot_listing_action_batch_items` 读取前后库存、读回时间和
  payload hash，同时记录 `SET_ONLINE_INVENTORY_RESET` 与 `TARGET_INVENTORY`；跨越该
  重设的区间按合同保持不合格；
- `PARTIALLY_APPLIED / NEEDS_RECONCILIATION / UNKNOWN` 继续阻断估算；
- 人工平台修改、对账修正和调整覆盖声明使用现有 `review_tasks`，只接受
  `inventory-adjustment-attestation-v1` 结构化 resolution payload；
- 没有覆盖声明时固定为 `ADJUSTMENT_COVERAGE_UNPROVEN`，不能把库存下降默认当作销售。

Repository 对 segment 使用确定性 ID、精确重放和同 ID 异内容冲突拒绝。`created_at`
只是落库元数据，不会让同一算法输入的安全重放误报冲突。

## 3. 订单权威与取消

`SalesFactSelectionService` 固定执行以下顺序：

```text
完整 CLOSED 订单 > 部分或 OPEN 订单 > 合格扫描估算 > UNAVAILABLE
```

不同订单批次被视为重复快照，只选择最新批次，不跨批次求和。平台总量不因商品映射
缺口降级；SKU 和品种等依赖映射的范围仍要求映射完整。部分订单与估算各自作为输入
保留，汇总数量绝不执行“部分订单 + 估算”。扫描估算不生成订单数或成交金额。

`OrderCancellationService` 只比较同平台、同交易日、相邻、完整 CLOSED 快照。相同指纹
先按 `(order_qty, order_transaction_amount)` 执行多重集合抵消；内容无法唯一匹配时返回
`CANCELLATION_AMBIGUOUS`。取消结果只作为对账输入，正式销量直接取最新完整订单快照，
不会再次扣减取消量。

## 4. 日结、FINAL 与统计范围

`TradeDaySettlementService` 在 20:00 作业中只创建或幂等修订 PROVISIONAL。存在订单或
估算事实时，自动建立以下范围：

- 平台合计；
- 品种；
- 等级；
- 内部 SKU；
- 24 个 Asia/Shanghai 本地小时桶。

品种维度复用 `listing_status` 的 SKU/品种关系，不建立第二份主数据。可信空订单页会在
各范围生成完整的 0；无可信事实时指标保持 NULL 和 UNAVAILABLE。

后续状态仍严格使用：

```text
PROVISIONAL → OBSERVED → RECONCILED → FINAL
```

OBSERVED 必须绑定已接受订单批次。非零订单/估算差异必须有结构化决定；无可比估算或
完全一致可以生成确定性自动决定。FINAL 除既有阻断 Incident 查询外，还要求显式策略
版本和同事务证据验证器，并在 `BEGIN IMMEDIATE` 内重新读取同一个 CLOSED 完整订单
批次、复算数量/订单数/成交金额、验证 hash、映射和时间策略，并重算相邻取消比较。
若对账后出现更新订单批次，即使汇总数值恰好相同，也必须重新对账，不能直接 FINAL。

## 5. Automation 与计划输入

Automation Service 现在为既有作业类型注册两个无 UI、无平台写 Handler：

- `PLATFORM_TRADE_DAY_SETTLEMENT`；
- `SALES_PLAN_INPUT_BUILD`。

Handler 复用现有 Run、租约、心跳、输入 manifest 绑定和完成接口。每次 segment 追加和
PROVISIONAL 写入都会在事务内重新验证当前 Automation claim；同一运行的多范围汇总
形成一个确定性组合 manifest。

计划输入只输出可追溯的已结束交易日汇总、下一交易日早期订单聚合、库存轨迹、质量和
输入引用。输出明确保存 `prediction_performed=false` 和
`platform_write_performed=false`；本阶段不预测未来销量、不创建销售任务、不写平台。

## 6. 测试与边界

专项测试覆盖：

- 正、负、零库存调整和调整去重；
- 缺少覆盖声明、未知或未决写、上架重设、target inventory、映射和扫描间隔门禁；
- segment 精确重放与冲突；
- 完整/部分/OPEN/可信空订单和订单优先级；
- 重复订单、取消多重集合、歧义和禁止重复扣减；
- 多范围 PROVISIONAL、单向状态机和 FINAL 同事务复算；
- Automation 租约、manifest、输出事件及零平台副作用。

本地 Ready-for-review 验证结果：

- 13.5-5 专项与直接依赖：`98 passed`；
- 完整 pytest：`941 passed, 3 skipped, 97 subtests passed`；
- 临时 v14 Runtime DB 系统冒烟：16 项通过、0 项失败。

GitHub Linux/Windows CI 结果需在 Draft PR 推送后记录。本阶段未改变真实页面读取或平台
动作，因此不重复 13.5-4 实机 READ_ONLY。

## 7. 当前限制

- 当前平台没有可自动读取的人工库存操作审计。没有结构化审核确认的区间会安全降级为
  不可估算，这是合同要求，不是待绕过的错误。
- 数值预测模型、计划任务生成、Incident 状态机、S0–S4、Web 销售分析和紧急下架仍属于
  后续子阶段。
- 真实 Runtime DB 是否迁移和启用由既有迁移与运行门禁单独决定；本实现没有操作真实
  Runtime DB 或常驻 Worker。
