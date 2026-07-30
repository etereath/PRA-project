# 任务 13.5-4：订单只读观察实施报告

- Review Profile：`R3`
- 分支：`codex/task13-5-4-order-observation`
- 基线：`origin/main@4aa4c73`
- 权威：GitHub Issue #20 最新正文及评论 `5136623832`
- 平台写操作：`0`

## 1. 实施结果

本分支实现了以下只读经营事实链：

```text
FULL_MARKET_SCAN
└─ ORDER_SCAN
   └─ ORDER_HISTORY_IMPORT
```

具体包括：

- `MayiHuatuanOrderReadOnlyAdapter`：把订单页面捕获转换为平台无关输入，区分完整
  有数据页、可信空页、`PARTIAL / UNSUPPORTED / UNAVAILABLE / FAILED`；
- `OrderObservationImporter`：在同一事务校验 Automation claim、父子 run、平台、
  交易日、Mapping 版本和逐项时间，保存不可变批次与订单项；
- `ShadowBotOrderPageReader` 与 v6 合同：只允许 `READ_ONLY`，拒绝平台订单 ID、
  买家 PII、请求错绑、checksum 错误和任何平台写副作用声明；
- `ShadowBotFileQueueOrderTransport`：复用既有单 Worker 文件队列、heartbeat、phase、
  checksum 和归档；事实导入成功后才归档结果；
- 通用队列 Watchdog：识别 v6 `ORDER_SCAN` 绑定，避免把合法订单请求隔离为孤儿；
  通用 Result Importer 对 v6 让路给订单 Importer，超时恢复生成零写的 v6
  `FAILED` 结果；
- `FullMarketScanOrderCoordinator / OrderScanHandler`：复用 13.5-3 的父子 run、租约、
  完成接口和 `ORDER_SCAN_CHILD` 关系。

未新增订单控制状态机、第二队列、写锁或销售平台写动作。

## 2. 数据语义

当前交易日是截至逐项 `observed_at` 的 `OPEN` 快照，已截单历史交易日为 `CLOSED`。
`OPEN` 不构成完整闭市事实，也不能进入 `FINAL`。

订单数量使用 `order_qty`，成交金额使用 `order_transaction_amount`，汇总使用
`transaction_amount_total`。成交金额不解释为卖家实收、扣佣收入、退款净额或财务
到账。

平台订单 ID 不采集。订单身份由平台、交易日、下单时间、平台品种和等级生成；数量、
金额和观察时间进入原始内容哈希。相同指纹的真实重复订单以 `occurrence_no` 全部
保存。13.5-4 不伪造取消行，取消留到 13.5-5 比较相邻完整快照。

## 3. Runtime Schema v14 纠正

真实 Runtime DB 尚未写入订单事实，因此本分支把预留的
`seller_received_amount` 最小纠正为 `order_transaction_amount`，并把日结同义字段
纠正为 `transaction_amount_total`。

空的旧预留订单表可以受控重建；旧结构已有订单事实时迁移失败关闭，避免猜测金额、
有效量或取消语义。健康检查同时验证退休字段不存在、必填列、`OPEN/CLOSED` CHECK 和
多重集合唯一约束。

## 4. 自动测试

专项覆盖：

- 当前 `OPEN`、历史 `CLOSED`；
- 完整有数据页、可信空页、滚动/结束标记失败、日期错位；
- 相同指纹重复订单；
- 精确重放、同 ID 异内容冲突；
- `VERIFIED / UNMAPPED / AMBIGUOUS`；
- 平台或 Run 错绑；
- 数据库失败整体回滚；
- v6 请求/结果绑定、checksum、PII 拒绝、零平台写副作用；
- v6 Watchdog Run 绑定、通用 Importer 分流和超时恢复；
- `FULL_MARKET_SCAN → ORDER_SCAN` 父子集成。

本地统一回归：

```text
pytest: 886 passed, 3 skipped, 97 subtests passed
system smoke: 16 passed, 0 failed
```

## 5. 受控实机 READ_ONLY

2026-07-31 已完成受控真实页面 READ_ONLY 验收。验收使用一次性 v14 Runtime DB，
避免绕过真实 Runtime DB 中既有的 `NEEDS_RECONCILIATION` 写安全门禁；真实 Runtime
DB 未写入订单事实。

脱敏验收摘要：

```text
execution_attempt_id: ORDER-READ-T1354-20260730T233128Z
execution_mode: READ_ONLY
trade_day_status: OPEN
capability_result: SUCCEEDED
batch_status: ACCEPTED
scope_complete: true
end_marker_verified: true
item_count: 0
content_sha256: sha256:16cffc1f68d198424df7e851dd4e9d23068c2e852187ed2101c09b95b488f987
result_imported: true
result_archived: true
queue_counts: inbox=0, working=0, results=0
platform_write_operations: 0
```

该结果表示当前交易日页面已验证为可信空页，不表示 `OPEN` 是完整闭市事实。验收结束
后 `test2` Worker 保持新鲜 `RUNNING`，`stop.signal` 不存在，通用队列服务已恢复。
仓库未保存真实订单值、截图、平台订单号或买家 PII。

真实 Runtime DB 的尝试在父 run claim 前被既有
`READ-READ-BATCH-T11-20260719-082740` 的 `NEEDS_RECONCILIATION` 正确阻断；没有绕过、
修改或清理该账本，只精确回滚了本次验收临时创建的父 run/job。

## 6. 当前限制

- 真实 Runtime DB 未迁移，也未写入订单事实；
- 订单卡片仍使用当前平台专属步长 `9`，但日期、字段解析和尾部标记均失败关闭；
- 取消、退款净额、财务实收和 `FINAL` 日结属于后续阶段；
- 当前实现不扩大到第二平台或多 Worker 并发。
