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
- `FULL_MARKET_SCAN → ORDER_SCAN` 父子集成。

本地统一回归：

```text
pytest: 881 passed, 3 skipped, 97 subtests passed
```

## 5. 受控实机 READ_ONLY

截至本报告首次写入时，生命周期记录是过期的 `RUNNING`，磁盘 heartbeat 为旧
`STOPPED`，队列为空且 `stop.signal` 不存在；影刀窗口仍处于应用设计器。

仓库与部署目录的 `shadowbot_contract_primitives.py`、
`vertical_slice_read_price.py`、`shadowbot_queue_worker.py` 哈希不一致。按项目门禁，
在编辑器关闭并回到应用列表前不得外部同步。因此本项尚未宣称通过，也没有向真实页面
投递请求。

完成实机验收后，本节必须补充脱敏 attempt ID、请求/结果 SHA-256、状态、交易日终态、
行数/合计的非敏感计数摘要、归档状态、队列清空和生命周期/heartbeat 一致性；不得
写入真实订单值、截图、平台订单号或买家 PII。

## 6. 当前限制

- 真实 Runtime DB 未迁移，也未写入订单事实；
- 订单卡片仍使用当前平台专属步长 `9`，但日期、字段解析和尾部标记均失败关闭；
- 取消、退款净额、财务实收和 `FINAL` 日结属于后续阶段；
- 当前实现不扩大到第二平台或多 Worker 并发。
