# 任务 13.5-5：销售估算、日结与计划输入实施报告

- 实施日期：2026-08-01
- Review Profile：`R4`
- 数据库结构版本：Runtime Schema v14，未扩表、未增字段
- 平台动作：0；未修改 ShadowBot Worker、队列、Adapter 或真实平台
- 收口：PR #27 已合并，merge commit `36cf2ba`；Linux Core 与 Windows Core 均通过
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

Repository 对 segment 使用确定性 ID、精确重放和同 ID 异内容冲突拒绝。调整引用及
未决写、冲突、关键扫描失败等规范化证据 manifest 已进入 v2 segment 身份；事后补充
人工证明会追加新 segment，旧证据仍保留审计。选数只采用同算法、同观察区间最新的
不可变证据版本；`created_at` 不会让相同证据的安全重放误报冲突。

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

历史订单导入回调已复用同一多范围事务：在一个 `BEGIN IMMEDIATE` 中冻结 current
summaries、订单、估算和 SKU 维度，重新计算全部范围；非 FINAL 原版本刷新，FINAL 创建
OBSERVED supersedes，新出现的品种、等级、SKU 和时间桶按既有状态机创建。任一范围
故障会回滚全部修改，不会出现部分范围已修订、其余范围仍引用旧证据。

## 5. Automation 与计划输入

Automation Service 继续注册两个无 UI、无平台写 Handler：

- `PLATFORM_TRADE_DAY_SETTLEMENT`；
- `SALES_PLAN_INPUT_BUILD`。

Handler 复用现有 Run、租约、心跳、输入 manifest 绑定和完成接口。20:00
`PLATFORM_TRADE_DAY_SETTLEMENT` 是唯一计算入口：在同一 `BEGIN IMMEDIATE` 事务中冻结
订单、估算和商品维度，完成所有范围的结算写入；提交后使用新连接回读，再从同一份
`SettlementSnapshot` 派生销售计划输入、销售管理报告和技术审计回执。任一范围写入失败
都会整体回滚，不会留下半套日结。

20:05 `SALES_PLAN_INPUT_BUILD` 只承担故障恢复与可回读性校验：它读取对应成功结算 Run
保存的完整计划投影，重新计算 hash 并与审计回执比对，不再重复查询和计算销售事实。
缺少成功结算或快照时失败；质量不合格时返回 `SKIPPED/PLAN_INPUT_INELIGIBLE`，不得伪装
成成功计划输入。

计划投影复用唯一业务日期 `seller_operation_date`。卖家作业日 D 的边界仍是 D-1 20:00
（含）至 D 20:00（不含）；`plan_for_seller_operation_date` 只说明计划用途，不建立第三套
交易日或数据库主键。历史回补生成 `AUDIT_ONLY` 投影，不能覆盖当前运营计划。

投影汇总以下销售过程中已能采集的事实：

- 已结算销量、成交金额、观察订单数、品种、等级、SKU 和本地小时桶；
- 当前计划前 18:00–20:00 的 OPEN 订单早期信号，严格按 `order_created_at` 归属；只有
  20:00 后10分钟内完成的完整已接受快照、且其后没有失败或不完整扫描，才能确认总量
  或可信零值；20:00 前快照只能作为部分领先证据，否则输出 `EVIDENCE_INSUFFICIENT`
  和 NULL；
- 每个 SKU 的开盘/收盘/最低/最高库存和价格、价格变化次数、在售观察次数；
- 数据质量、投影资格和全部来源引用。

完整、有界的 Snapshot 投影保存在结算 Run 的 `RUN_FINISHED` Event 中，后续可从 Runtime
DB 回读并校验。事件大小设有 64 KiB 门禁；高频原始观察压缩成 SKU 轨迹，原始证据仅以
引用保留。输出继续明确 `prediction_performed=false` 和
`platform_write_performed=false`；本阶段不预测未来销量、不创建销售任务、不写平台。

页面可见“第 N 次购买”，但 Worker、Importer 和 v14 正式订单表尚未采集
`purchase_sequence`，因此当前不能输出复购订单占比，也不会从订单号或买家 PII 推断。

当前范围只要求平台销售过程中可采集事实的汇总。采摘、包装、冷库等线下经营数据，
佣金/退款，模型联网取得的日历/天气，以及今日花价指数、平台价格指数和特定品种外部
价格/销量只在计划中保留 Provider 边界，不进入本阶段实现。平台不提供曝光口径，系统
不得伪造曝光量或转化率。

人工可读输出已从统一 Snapshot 派生成两个互不混写的投影：

- 销售管理报告只展示卖家作业日销量、订单观察数、成交金额、平均每扎成交金额、前三
  品种/等级、主要销售高峰、库存或取消异常以及自然语言数据质量；默认一个屏幕内读完；
- 技术审计回执保存数据库回读、版本、计数、输入引用和 hash，只供系统维护与排障，不
  进入销售报告正文。

销售管理报告不出现 Run/Summary/Batch ID、订单指纹、manifest、SHA-256、mapping/
algorithm version、原始 JSON 或 supersedes ID。可信空页显示“当天暂无成交”；证据不足与
无数据/不可读使用不同提示。没有异常或历史比较时省略对应区块，不输出低价值空表。

## 6. 测试与边界

专项测试覆盖：

- 正、负、零库存调整和调整去重；
- 缺少覆盖声明、未知或未决写、上架重设、target inventory、映射和扫描间隔门禁；
- segment 精确重放、冲突、事后补证重物化和当前证据版本选择；
- 完整/部分/OPEN/可信空订单和订单优先级；
- 重复订单、取消多重集合、歧义和禁止重复扣减；
- 多范围 PROVISIONAL、单向状态机和 FINAL 同事务复算；
- 历史订单回补后的同版本刷新、FINAL supersedes、新范围创建、多范围故障整体回滚和
  精确重放；
- 18:10空页、19:55空/非空快照、20:01/20:10边界、后续失败或不完整扫描、较新完整
  快照、20:00尾段覆盖和历史 `AUDIT_ONLY`；
- 统一流水线、快照回读、计划资格、价格/库存轨迹、报告三态和事件大小门禁；
- Automation 租约、manifest、输出事件及零平台副作用。

本地 Ready-for-review 验证结果：

- 最新尾段覆盖专项：`17 passed`；受影响集成：`65 passed`；
- 完整 pytest：`974 passed, 3 skipped, 97 subtests passed`；
- 复审整改后临时 v14 Runtime DB 系统冒烟：16 项通过、0 项失败。

2026-08-01 追加执行蚂蚁花团订单管理页实机 READ_ONLY，目标平台交易日为
`2026-07-10`。主 Runtime DB 中既有 `NEEDS_RECONCILIATION` 正确阻断了 UI 通道，验收
没有绕过该门禁，而是切换到 Watchdog、Worker、Importer 和 Archive 共用的一次性 v14
Runtime DB。结果如下：

- 页面能力 `SUCCEEDED`，交易日状态 `CLOSED`，读取 20 条订单；
- 列表范围完整、尾部“没有更多了”已确认，页面内下滑 2 次；`page_count=1` 表示始终在
  同一订单页面，不代表没有执行列表滚动；
- Worker 请求总耗时 40 秒，窗口在请求开始后 1 秒可用，登录检查在 3 秒内完成；
- Automation/批次状态为 `PARTIAL`，唯一原因是验收脚本使用空商品映射，20 条均为
  `UNMAPPED`；页面读取、日期选择、滚动和尾部确认本身均成功；
- 平台写操作为 0；未保存客户姓名、电话、地址、聊天内容，也未向仓库提交真实订单值、
  截图、平台订单号或买家 PII；
- Watchdog 产生精确 `READY_REQUEST_VALIDATED`，Importer 落库后结果归档，request/result
  UTF-8 JSON 与 SHA-256 sidecar 均校验通过，活动队列最终为 0；
- 实际影刀解释器为 Python 3.10.11。验收后主 Runtime DB 队列服务已恢复，Worker 保持
  `RUNNING`，`stop.signal` 不存在。

PR #27 最终修复推送后的 Linux Core 与 Windows Core GitHub Actions 均通过。

## 7. 当前限制

- 当前平台没有可自动读取的人工库存操作审计。没有结构化审核确认的区间会安全降级为
  不可估算，这是合同要求，不是待绕过的错误。
- 数值预测模型、计划任务生成、Incident 状态机、S0–S4、Web 销售分析和紧急下架仍属于
  后续子阶段。
- `purchase_sequence` 仍待未来订单观察合同和 Runtime Schema 明确后采集；当前计划输入
  不提供复购占比。
- 外部花价/平台指数、特定品种外部价格销量、佣金退款以及线下采摘/包装/冷库数据只
  保留后续 Provider 规划，本阶段不采集。
- 真实 Runtime DB 是否迁移和启用由既有迁移与运行门禁单独决定；本次验收未向真实
  Runtime DB 写入订单事实。因首次验收尝试被既有 `NEEDS_RECONCILIATION` 阻断而产生的
  测试 Run 已按既有调度回收机制标记为 `MISSED`，测试 Job 已禁用。
