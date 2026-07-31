# 任务 13.5-4：订单只读观察合同

- Review Profile：`R3`
- 当前平台：蚂蚁花团供应商微信小程序
- 权威：GitHub Issue #20 最新正文及评论 `5136623832 / 5139601975`
- 基线：`origin/main@4aa4c73`
- 范围：只读经营事实链；不得扩张为新的 R4 控制面

## 1. 目标和边界

本任务复用 13.5-3 已有 Automation Run、父子关系、租约、完成接口和 Runtime
Schema v14，在以下既有拓扑中补齐订单事实：

```text
FULL_MARKET_SCAN
└─ ORDER_SCAN
   └─ ORDER_HISTORY_IMPORT
```

平台 Adapter 只读取订单管理页，不修改订单、发货、退款、支付、资金或销售平台状态。
ShadowBot v6 合同固定为 `execution_mode=READ_ONLY`、
`business_operation_completed=false`、`side_effect_state=NOT_STARTED`。订单结果不得包含
平台订单 ID、买家姓名、电话、地址、聊天内容、原始页面正文或截图。

### 1.1 复用优先实施约束

13.5-4 只允许新增订单合同、订单字段解析和订单专属选择器；同一影刀宿主中已经由任务
12/13 验证的登录、窗口准备、列表物化、文件队列、Worker 生命周期、phase、Importer、
Watchdog 和归档控制流必须优先复用。

订单列表必须使用任务 12 已验证的列表物化语义：

```text
商品列表：聚焦首项
订单列表：定位“订单管理_容器”并点击右边缘中点附近的空白带
→ END（订单懒加载时允许在同一共享助手内重复发送）
→ 等待加载
→ 验证“没有更多了”；未出现则在有界次数/时间内继续 END
→ HOME
→ 验证首张卡片恢复
→ 仅按冻结 index 读取允许的订单字段
```

实现方式应当是从既有成功链路抽取参数化公共助手，让商品和订单调用方分别提供容器、
安全聚焦动作、尾部标记、END 上限及行解析器。订单卡片内的品种、等级等字段会触发
页面导航，不得作为订单列表聚焦点。不得为订单新建一套独立的 `END/HOME`、滚轮、进度或结束
判断并与任务 12 长期平行。平台日期滚轮是订单专属控件，可以独立适配，但不能替代或
弱化订单列表物化门禁。

只有保存“既有列表助手在订单页不可用”的可复现只读证据，并完成替代方案和风险评审
后，才允许新增列表控制流。单页 `page_count=1`、尾部标记初始可见、合成 fixture 或
CI 通过，都不能作为滚动分支已经验收的证据。

执行结果中的 `page_count` 表示本次覆盖的订单页面范围，不再用来推断是否执行过滚动。
列表动作证据另行记录：

- `scroll_count`：共享列表助手实际发送的 `END` 加载动作次数；允许大于 1，失败结果也
  必须保留已发送次数；
- `scroll_progress_verified`：`END` 已发送并在随后验证明确尾部；可信空页无需滚动时
  也可以为真，但必须同时记录 `trusted_empty_marker_visible=true`；
- 尾部验证后必须执行 `HOME`，只有首张订单卡片身份字段恢复后才允许读取订单行。

历史验收报告必须同时给出 `page_count / scroll_count /
scroll_progress_verified / no_more_marker_visible`，不得再以 `page_count` 单字段宣称已经
覆盖滚动分支。

订单行解析不得逐条、逐字段重新执行全页面元素查找，也不得为提速而读取整张订单卡片
文本。执行端一次枚举具有稳定等级样式的安全锚点，从锚点取得共同的列表索引容器，按
`2 / 3 / 5 / 6 / 7` 和步长 `9` 只访问等级、品种、数量、单价和下单时间对应的子元素；
平台订单号、买家和地域等其他 index 不得调用文本读取接口。等级锚点数量、冻结 index
范围或锚点回读任一不一致时，整页必须以不完整失败关闭。

## 2. 平台能力和交易日终态

当前能力冻结为：

```text
supports_order_scan = true
supports_current_trade_day = true
supports_historical_trade_day = true
```

当前平台交易日是截至 `observed_at` 的开放快照，标记 `OPEN`；已经截单的历史平台
交易日标记 `CLOSED`。`OPEN` 不代表完整闭市事实，不得进入 `FINAL` 日结。

每小时 `FULL_MARKET_SCAN` 固定在本地时间 `HH:10`，因此关键换日扫描为 `18:10`：
此时前一平台交易日为 `CLOSED`，新交易日为 `OPEN`。订单扫描的
`scan_started_at / scan_completed_at` 必须属于同一平台交易日；跨越 18:00 的批次
以 `ORDER_SCAN_CROSSED_TRADE_DAY_CUTOFF` 整批失败关闭，不接受为完整事实。
`PRE_CUTOFF_FULL_SCAN` 不派生 `ORDER_SCAN`，避免在 18:00 附近启动完整订单读取。

Adapter 只能报告已验证的页面能力，不得声称平台内部修复了错误。未来日期、不可访问
日期和页面能力故障必须明确表示为 `UNAVAILABLE` 或 `FAILED`，不得伪造成空订单。

## 3. 原始订单观察字段

`order_observation_items` 仅保存以下不可变原始事实和映射结果：

```text
platform_name
platform_trade_date
trade_day_status
order_identity_fingerprint
occurrence_no
order_created_at
platform_product_name
grade
internal_sku
mapping_status
mapping_version
order_qty
order_transaction_amount
observed_at
seller_operation_date
seller_phase
raw_observation_sha256
```

业务口径：

- `order_qty`：页面展示的订单数量；
- `order_transaction_amount`：使用页面展示的单价与 `order_qty` 通过 `Decimal`
  精确相乘得到的成交金额；不单独定位页面合计金额元素；
- `transaction_amount_total`：批次或日结中的成交金额合计。

成交金额不代表卖家实收、扣佣收入、退款净额或财务到账。当前范围不使用
`effective_qty / refund_qty / invalid_qty / seller_received_amount`，也不保留两套等价
金额字段。

## 4. 身份、多重集合和哈希

平台订单 ID 禁止采集。`order_identity_fingerprint` 由以下规范化可观察字段生成：

```text
platform_name
platform_trade_date
order_created_at
platform_product_name
grade
```

数量和成交金额不进入身份指纹，因此跨快照内容变化仍可比较同一可观察身份；它们连同
`trade_day_status` 和 `observed_at` 进入 `raw_observation_sha256`。

同一快照内相同指纹的真实重复订单必须全部保存，并按确定性顺序写入
`occurrence_no=1..N`。唯一约束只允许
`(observation_batch_id, order_identity_fingerprint, occurrence_no)`，不得用单指纹唯一
约束吞掉重复订单。

13.5-4 不写取消行、不写负数量。13.5-5 只有在以下条件全部满足时，才能通过相邻快照
多重集合减少推导取消：

1. 同一平台、同一平台交易日；
2. 两份快照都完整；
3. 比较不是 18:00 换日造成；
4. 前一快照的某身份出现次数大于后一快照；
5. 减少不是滚动、解析、范围或能力失败造成。

## 5. 页面完成矩阵

订单页结果必须落入以下互斥状态：

| 页面事实 | capability_result | batch_status | 可作为完整事实 |
| --- | --- | --- | --- |
| 有数据且日期、滚动和“没有更多了”均验证 | `SUCCEEDED` | `ACCEPTED` | 是 |
| 无数据且“暂无订单”可信空页验证 | `SUCCEEDED` | `ACCEPTED` | 是 |
| 已读部分行但滚动、字段关联或结束标记失败 | `FAILED` | `PARTIAL` | 否 |
| 页面加载、日期或解析失败且无可信行 | `FAILED` | `FAILED` | 否 |
| 日期或能力确实不可用 | `UNAVAILABLE` | `UNAVAILABLE` | 否 |
| Adapter 明确不支持订单扫描 | `UNSUPPORTED` | `UNAVAILABLE` | 否 |

日期选择后必须回读并精确等于请求日期；历史列表必须有界滚动并验证“没有更多了”；
空页必须验证“暂无订单”。每一项独立记录读取完成时的 `observed_at`。下一条元素定位
失败本身不是完成证据。

列表物化还必须证明：`END` 前焦点已绑定到订单列表、尾部加载确实前进、`HOME` 后首张
卡片恢复。滚动前后以稳定的订单多重集合指纹、可见首尾身份、明确尾部转换或加载计数
证明进度；重复指纹不得继续盲目滚动。共享 `END` 路径只执行一次加载动作，尾部仍不可
见时立即失败，不得追加订单专属滚轮循环。任何一步不可证明时整批保持
`FAILED/PARTIAL`，不得接受为完整订单事实。

订单卡片采用平台专属候选步长 `9`，但运行时仍逐卡片读取品种、等级、数量、单价和
下单时间，以单价乘数量计算成交金额，并以日期、字段解析和尾部标记共同验收；结构
漂移必须失败关闭。

## 6. Adapter、Importer 和映射

蚂蚁花团 Adapter 只把页面捕获转换为平台无关
`OrderObservationBatchInput / OrderObservationInput`。平台专属选择器、日期控件和滚动
逻辑不得进入公共服务。

Importer 在单一 `BEGIN IMMEDIATE` 事务中：

1. 校验 Automation claim、`ORDER_SCAN` 类型、平台、交易日和
   `ORDER_SCAN_CHILD` 父子绑定；
2. 验证批次状态、完成标记、逐项时间和内容摘要；
3. 用当前不可变 Mapping 版本解析 `VERIFIED / UNMAPPED / AMBIGUOUS / DISABLED`；
4. 追加批次和全部订单项；
5. 任一数据库错误整体回滚。

未映射或歧义商品仍保留原始订单事实，但完整页面的批次状态降为 `PARTIAL`，不得编造
`internal_sku`。映射版本必须随每一行持久化。

同一 `observation_batch_id`、同一规范内容精确重放返回原结果，即使 run 已终态；同 ID
异内容冲突。终态精确重放不依赖当前映射漂移，新事实仍要求有效实时 claim。

`ORDER_SCAN` 在发布队列请求前必须以唯一
`ORDER_SCAN_TARGET_SELECTED` Run Event 冻结精确的
`requested_platform_trade_date`。当前日和历史日使用同一机制；目标日期不得晚于 run
冻结的 `platform_trade_date`，重复绑定只能使用同一日期。Watchdog、Worker 请求和
Importer 必须共同校验该精确目标，不能用宽松的“早于或等于 run 日期”代替绑定。

## 7. ShadowBot 文件队列

订单读取使用 v6 请求/结果合同，复用既有文件队列、单 Worker、checksum、phase、心跳
和归档目录，不建立第二队列或新的写锁状态机。

提交请求前要求新鲜 `RUNNING` heartbeat；等待结果期间续租 Automation claim。结果
必须先通过 schema、请求绑定、checksum、PII 黑名单和零副作用校验，再进入 Importer。
数据库导入成功后才归档队列结果；数据库失败时保留结果用于精确重放。

既有通用队列服务不得把 v6 结果作为旧版结果导入，也不得把合法 v6 请求误判为孤儿。
v6 请求进入 `working` 前，Watchdog 必须确认其绑定到同平台、唯一冻结目标日期且处于
`RUNNING` 的 `ORDER_SCAN`；超时恢复只能生成 `FAILED`、零写副作用的 v6 结果，后续
仍由订单 Importer 完成业务导入和归档。

正式 Automation Service 通过显式 `--enable-order-read-only` 门禁注册
`FULL_MARKET_SCAN` 只读派生 Handler 与 `ORDER_SCAN` Handler。父 run 的成功只表示
子 run 调度完成，不声明页面扫描事实；订单事实仍只由子 run 和 Importer 接受。该组合
不得注册 COMMIT、上下架或其他平台写 Handler。

## 8. Runtime Schema v14 最小纠正

真实 Runtime DB 尚未写入订单事实。v14 的预留字段
`seller_received_amount` 最小纠正为 `order_transaction_amount`，日结同义字段纠正为
`transaction_amount_total`。

若旧预留订单表为空，迁移器可以重建为正式合同；若已经存在旧结构订单事实，必须失败
关闭并要求人工迁移，不能猜测转换。健康检查必须拒绝退休字段、缺失 NOT NULL、错误
`OPEN/CLOSED` 约束或会吞掉重复订单的唯一索引。

## 9. 验收矩阵

开发专项必须覆盖：

- `OPEN` 当前交易日和 `CLOSED` 历史交易日；
- 有数据完整页、可信空页、滚动/结束标记失败、日期错位；
- 复用的列表物化助手覆盖焦点绑定、`END`、尾部标记、`HOME`、首项恢复和无进展停止；
- 相同指纹重复订单及 `occurrence_no`；
- 精确重放、同 ID 异内容冲突；
- 未映射、歧义商品；
- 平台或 Run 错绑；
- 未冻结目标、目标漂移和历史目标 Watchdog 绑定；
- `18:10 FULL_MARKET_SCAN` 对齐及跨 18:00 整批失败；
- 正式 Automation Service 只读 Handler 组合且零写 Handler；
- 数据库失败整体回滚；
- v6 checksum、请求/结果绑定、PII 拒绝和零平台写副作用。

Ready for review 前统一运行订单专项、受影响集成测试、完整 pytest、系统冒烟、
Windows/Linux CI 和受控真实页面 READ_ONLY 验收。真实值、截图、平台订单号和买家
PII 不进入仓库；仓库只保留合成 fixture、结构化测试和脱敏验收摘要。

合并前真实页面历史批次至少覆盖两个不同历史日期：一个日期连续读取不少于三张订单
卡片并验证候选步长 `9` 的同卡字段关联；一个日期具备足够订单触发滚动并验证尾部
“没有更多了”、可见数与读取数一致、任一中间失败整批失败。两项可以在同一日期合并，
但总计仍需两个不同历史日期，并验证目标日期从 Watchdog、Worker、Importer 到归档
保持一致。

触发滚动的真实批次必须记录共享列表物化助手实际执行了焦点绑定、`END`、尾部验证、
`HOME` 和首项恢复；仅有日期选择器滚动或订单列表 `page_count=1` 不满足本门禁。
