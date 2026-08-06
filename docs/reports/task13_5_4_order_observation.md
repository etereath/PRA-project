# 任务 13.5-4：订单只读观察实施报告

- Review Profile：`R3`
- 分支：`codex/task13-5-4-order-observation`
- 基线：`origin/main@4aa4c73`
- 权威：GitHub Issue #20 最新正文及评论 `5136623832 / 5139601975`
- 平台写操作：`0`
- 收口：PR #26 已合并，merge commit `1ef2068`；Linux Core 与 Windows Core 均通过

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
  checksum 和归档；文件校验与 Worker 新鲜度检查使用通用队列函数，事实导入成功后
  才归档结果；
- 通用队列 Watchdog：识别 v6 `ORDER_SCAN` 绑定，避免把合法订单请求隔离为孤儿；
  通用 Result Importer 对 v6 让路给订单 Importer，超时恢复生成零写的 v6
  `FAILED` 结果；
- `FullMarketScanOrderCoordinator / OrderScanHandler`：复用 13.5-3 的父子 run、租约、
  完成接口和 `ORDER_SCAN_CHILD` 关系。
- `ORDER_SCAN_TARGET_SELECTED`：在队列发布前冻结精确当前/历史目标日期；
  Watchdog、请求和 Importer 拒绝未冻结、错日期或多重冻结；
- 每小时 `FULL_MARKET_SCAN` 按本地 `HH:10` 对齐，关键换日扫描为 `18:10`；
  跨 18:00 的订单批次整批失败，`PRE_CUTOFF_FULL_SCAN` 不派生订单扫描；
- 正式 Automation Service 可通过 `--enable-order-read-only` 注册
  `FULL_MARKET_SCAN → ORDER_SCAN`，组合根只包含只读 Handler，父 run 仅声明子 run
  调度结果，不伪造页面事实。

未新增订单控制状态机、第二队列、写锁或销售平台写动作。

## 2. 数据语义

当前交易日是截至逐项 `observed_at` 的 `OPEN` 快照，已截单历史交易日为 `CLOSED`。
`OPEN` 不构成完整闭市事实，也不能进入 `FINAL`。

订单数量使用 `order_qty`，成交金额使用 `order_transaction_amount`，汇总使用
`transaction_amount_total`。执行端读取页面展示的单价与数量并使用 `Decimal` 精确
相乘，不单独定位页面合计金额元素。成交金额不解释为卖家实收、扣佣收入、退款净额
或财务到账。

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
- 历史目标精确冻结、错日期拒绝和跨 18:00 失败关闭；
- `18:10` 小时扫描对齐及正式只读服务组合；
- `FULL_MARKET_SCAN → ORDER_SCAN` 父子集成。
- 商品与订单共用列表物化助手，统一执行“聚焦首项 → `END` → 尾部验证 → `HOME`
  → 首项恢复”；订单结果使用独立的 `scroll_count` 和
  `scroll_progress_verified`，不再从 `page_count` 猜测滚动动作。
- 订单只执行一次安全等级锚点集合查询，并按列表全局 index 和步长读取允许字段；
  覆盖锚点错位、列表结构不完整和禁止字段不可访问。

本地统一回归：

```text
pytest: 907 passed, 3 skipped, 97 subtests passed
system smoke: 16 passed, 0 failed
```

完整 pytest 首轮仅出现一个未修改的任务 12 并发归档竞态型不稳定失败；该单项立即
重跑通过，随后第二次完整统一回归通过；同库 Watchdog 门禁补充 3 项测试后，最终统一
回归以上述 `907 passed` 通过。

复用优先整改后的首轮受影响专项回归：

```text
共享列表物化、任务12/13读取基线、订单Adapter/Transport、动态选择器、通用队列：
145 passed
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

同日页面出现订单后，执行端最初因错误复用单价选择器定位合计金额而失败关闭。按业务
裁决改为“页面单价 × 数量”后重新同步并完成真实页面复验：

```text
execution_attempt_id: ORDER-READ-T1354-20260731T041749Z
trade_day_status: OPEN
capability_result: SUCCEEDED
batch_status: PARTIAL
scope_complete: true
end_marker_verified: true
item_count: 1
mapping_status: UNMAPPED
result_imported: true
result_archived: true
queue_counts: inbox=0, working=0, results=0
platform_write_operations: 0
```

`PARTIAL` 仅由隔离验收使用空 Mapping 集合导致；页面日期、订单卡片、成交金额计算和
尾部标记均读取成功。仓库仍不保存真实订单值、截图、平台订单号或买家 PII。

真实 Runtime DB 的尝试在父 run claim 前被既有
`READ-READ-BATCH-T11-20260719-082740` 的 `NEEDS_RECONCILIATION` 正确阻断；没有绕过、
修改或清理该账本，只精确回滚了本次验收临时创建的父 run/job。

### 5.1 三日期真实页面矩阵

2026-07-31 使用三个一次性 v14 Runtime DB 对 7 月 31 日、7 月 30 日和 7 月 22 日
执行真实页面 READ_ONLY。脱敏结果如下：

| 目标日期 | 交易日状态 | 条目数 | page_count | 页面能力 | 范围/尾部 | 导入/归档 | 平台写操作 |
| --- | --- | ---: | ---: | --- | --- | --- | ---: |
| 2026-07-31 | `OPEN` | 3 | 1 | `SUCCEEDED` | 完整/已验证 | 完成/完成 | 0 |
| 2026-07-30 | `CLOSED` | 5 | 1 | `SUCCEEDED` | 完整/已验证 | 完成/完成 | 0 |
| 2026-07-22 | `CLOSED` | 4 | 1 | `SUCCEEDED` | 完整/已验证 | 完成/完成 | 0 |

对应成功尝试为：

```text
2026-07-31: ORDER-READ-T1354-20260731T083228Z
2026-07-30: ORDER-READ-T1354-20260731T082801Z
2026-07-22: ORDER-READ-T1354-20260731T082835Z
```

三个批次均因隔离验收继续使用空 Mapping 集合而保存为 `PARTIAL`，不是页面范围失败。
各批次均完成 checksum 校验、Importer、归档和活动队列清空，未保存真实订单值、截图、
平台订单号或买家 PII。

本轮发现并关闭了历史日期自动选择缺陷：日期轮无障碍树会暴露当前视口外的全部日期，
旧实现误把“树中存在”当作“当前可见”，点击隐藏的 7 月 22 日后页面仍停在 7 月 30 日，
随后确认按钮捕获选择器返回 `ELEMENT_NOT_FOUND`。失败尝试
`ORDER-READ-T1354-20260731T080402Z` 正确保存为 0 条、范围不完整、尾部未验证且平台
写操作为 0。修复增加日期项相对滚轮容器的可视边界判断，并通过按钮父节点文本重新
定位“确认”；从 7 月 30 日出发的 `ORDER-READ-T1354-20260731T082835Z` 已在无人工
介入的情况下自动向上滚动并确认 7 月 22 日，随后完成 4 条订单观察、Importer 和归档；
`ORDER-READ-T1354-20260731T083228Z` 又从 7 月 22 日自动向下滚动回 7 月 31 日，
完成当前交易日 3 条订单观察。日期轮两个方向均由最终部署版本验证。

### 5.2 订单列表滚动门禁补验

2026-07-31 根据真实页面操作复盘，确认订单卡片内的品种/等级字段会触发页面导航，
汇总栏静态文本也不能把键盘焦点交给内部滚动容器。最终复用共享
`_materialize_list_with_end_and_restore` 助手，仅将订单调用方的聚焦动作参数化为：
动态定位新捕获元素 `订单管理_容器`，点击其右边缘中点附近的空白带。订单懒加载期间
共享助手重复发送 `END` 并逐次等待，只有明确读取“没有更多了”后才发送 `HOME`、验证
首卡恢复并解析订单。

7 月 10 日受控 READ_ONLY 的脱敏结果：

```text
execution_attempt_id: ORDER-READ-T1354-20260731T130321Z
platform_trade_date: 2026-07-10
trade_day_status: CLOSED
worker_status: SUCCESS
capability_result: SUCCEEDED
batch_status: PARTIAL
scope_complete: true
page_count: 1
scroll_count: 2
scroll_progress_verified: true
no_more_marker_visible: true
item_count: 20
result_imported: true
result_archived: true
queue_counts: inbox=0, working=0, results=0
platform_write_operations: 0
```

`PARTIAL` 仍仅由一次性验收 DB 使用空 Mapping 集合导致；执行端读取、重复 END、尾部、
HOME、范围完整性、Importer 和归档均已通过。本次证据关闭了“至少一个历史日期实际进入
订单列表滚动分支”的合并门禁；`page_count=1` 继续只表示单个订单页面范围，不用于
推断是否发生滚动。

### 5.3 订单字段按全局 index 批量读取

原实现对每条订单的等级、品种、数量、单价和下单时间分别执行一次全页面元素查找，
20 条订单共需约 100 次查找，真实页面首条至末条 `observed_at` 跨度约 29 秒。整改后
执行端只执行一次安全等级锚点集合查询，再从共同列表容器按全局 index
`2 / 3 / 5 / 6 / 7`、步长 `9` 读取允许字段；未访问订单号、买家或地域对应元素。

第一次实机校验 `ORDER-READ-T1354-20260731T133226Z` 将 index 错误地解释为单张卡片
内部 index，在第 3 条等级变化时由锚点一致性检查以 `ORDER_CARD_INDEX_MISMATCH`
关闭；结果为 0 条、未导入订单事实、平台写操作为 0。修正为列表全局 index 后，
`ORDER-READ-T1354-20260731T133619Z` 的脱敏结果为：

```text
platform_trade_date: 2026-07-10
worker_status: SUCCESS
capability_result: SUCCEEDED
batch_status: PARTIAL
scope_complete: true
scroll_count: 1
scroll_progress_verified: true
no_more_marker_visible: true
item_count: 20
first_to_last_observed_at_span: 10s
result_imported: true
result_archived: true
queue_counts: inbox=0, working=0, results=0
platform_write_operations: 0
```

逐项读取区间由约 29 秒降至 10 秒，约为 0.5 秒/条；完整请求耗时仍同时包含进入页面、
日期选择、列表物化和尾部验证，不能用逐项读取区间替代全流程耗时。`PARTIAL` 仍仅由
隔离验收 DB 的空 Mapping 集合导致。

### 5.4 同库常驻 Watchdog 合并门禁

2026-07-31 使用新的单个一次性 Runtime Schema v14 数据库，把常驻
`run_shadowbot_queue_services.py`、验收 Automation Run、唯一
`ORDER_SCAN_TARGET_SELECTED` 事件和 `OrderObservationImporter` 绑定到同一数据库，
再次读取 2026-07-10。Watchdog 在 Worker 领取前输出一次去重的
`READY_REQUEST_VALIDATED`；审计事件中的 attempt、Run 和目标日期与数据库记录精确
一致。随后 Worker 生成 v6 零副作用结果，订单 Importer 原子写入批次和 20 条原始观察，
最后归档请求、phase、结果及各自 checksum：

```text
execution_attempt_id: ORDER-READ-T1354-20260731T144733Z
runtime_schema: v14 healthy
platform_trade_date: 2026-07-10
trade_day_status: CLOSED
watchdog_validated: true
capability_result: SUCCEEDED
batch_status: PARTIAL
scope_complete: true
end_marker_verified: true
item_count: 20
result_imported: true
result_archived: true
queue_counts: inbox=0, working=0, results=0
platform_write_operations: 0
```

`PARTIAL` 仍只由一次性数据库使用空 Mapping 集合而按合同降级为 `UNMAPPED`；页面读取、
范围、尾部、数据库提交和归档均完整成功。验收 CLI 现将“队列/导入链通过”与“商品映射
完整”分开判定，合法的完整 `PARTIAL / UNMAPPED` 快照可通过链路门禁，但页面能力失败、
不完整范围、未归档、残留队列、平台写副作用或缺少 Watchdog 精确审计仍返回失败。

验收完成后临时队列服务已停止，绑定真实 Runtime DB 的原常驻服务按原参数恢复；
`test2` Worker 全程保持新鲜 `RUNNING`，`stop.signal` 不存在。真实 Runtime DB 未迁移、
未写入订单事实，仓库未保存真实订单值、截图、平台订单号或买家 PII。

## 6. 当前限制

- 真实 Runtime DB 未迁移，也未写入订单事实；
- 当前日和两个历史日期已完成真实页面只读读取；7 月 30 日覆盖 5 张连续卡片，
  7 月 22 日覆盖 4 张连续卡片；7 月 10 日覆盖 20 张卡片并以 2 次 `END` 完成真实
  订单列表滚动门禁；
- 较早历史日期全自动日期选择和订单列表实际滚动均已通过；常驻 Watchdog、Worker、
  订单 Importer 与归档已经使用同一个一次性 v14 Runtime DB 形成合并证据；
- 复用优先整改已经删除订单专属 `_order_scroll_to_end`，商品与订单共同调用
  `_materialize_list_with_end_and_restore`；商品调用方聚焦首项，订单调用方点击
  `订单管理_容器` 右边缘空白带；共享助手重复 `END`、验证尾部、`HOME` 回顶并验证首项
  恢复。通用队列读取同时统一了 checksum JSON 和 Worker 新鲜度检查；
- 取消、退款净额、财务实收和 `FINAL` 日结属于后续阶段；
- 当前实现不扩大到第二平台或多 Worker 并发。
