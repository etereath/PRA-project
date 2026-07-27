# PRA 平台价格快照与 ShadowBot 改价对接说明

## 1. 对接目标与边界

本对接用于完成以下闭环：

1. PRA 可以在 SQLite 中维护每个平台商品最近一次已确认的平台状态，用于任务生成、展示和报告。
2. PRA 接收任务中心提供的完整改价列表；每项明确包含 `internal_sku`、`expected_old_price` 和 `target_price`。
3. PRA 把内部 SKU 解析为页面商品名和等级，并按“平台 + internal_sku + 旧价 + 目标价”形成不可变清单。
4. 开发/实机验收由操作人员确认固定清单；任务14前的正式入口由操作人员明确传入一个或多个 `--task-id`，PRA 只对这些任务重新校验 `pending update_price` 有效性并创建一个 COMMIT 队列合同。不得把全部 pending 自动视为执行授权。
5. ShadowBot 在同一次 Worker 请求中逐商品动态定位、核对旧价、提交并独立回读。
6. Result Importer 校验请求、结果和逐商品绑定后更新任务与操作账本。
7. 只有平台实际回读证明确认改价完成时，PRA 才回写 SQLite 当前平台价格。

SQLite 是 PRA 任务生成、授权和回写使用的业务数据中心。Excel 仅用于辅助整理、导入或导出，不得成为并行数据中心。

SQLite 中的价格是“最近一次已确认的平台快照”，不是对平台当前页面状态的无条件声明。真实 COMMIT 仍必须在提交前读取页面当前价；页面价与授权旧价不一致时，以页面核验结果触发 `OLD_PRICE_CHANGED` 并停止，不得自动覆盖 SQLite 或继续提交。

本文件只规范任务12的价格更新闭环。改价成功不得改变商品上下架状态；上架、下架和 OFFLINE 跨页面对账由任务13负责。

## 2. SQLite 平台状态快照

当前运行库结构版本为 v13，物理状态表名为 `listing_status`。该表的“平台 + 品种 + 等级”唯一身份结构在 v9 引入；v10 增加任务旧价字段，v11 增加多商品 COMMIT 批次账本，v12 增加逐商品操作/尝试身份、活动写锁、观察时间和技术结果回执；v13 在不改变任务12 v4 合同的前提下增加公共批次注册表、上下架 operation 字段、位置快照失效字段和任务13专用账本。

### 2.1 平台商品业务键

平台本身不提供 SKU，PRA 内部 SKU 也不绑定平台。因此平台快照的业务键必须是：

```text
(platform_name, variety, grade)
```

三个字段写入和查询前均做 Unicode、空白和大小写归一化；等级末尾的“级”会被归一化，因此 `C` 与 `C级` 是同一个身份。平台必须使用项目配置中的规范名称，不得使用 `default_platform`。`listing_status.internal_sku` 是来自“商品资料与库存录入”的 PRA 内部追踪引用，不参与物理唯一约束；正式任务中的 SKU 以该库存商品表为主映射源解析成商品名和等级，ShadowBot 再使用解析后的页面身份匹配。

### 2.2 字段语义

| 字段 | 含义 | 任务12约束 |
| --- | --- | --- |
| `listing_status_id` | 平台状态记录 ID | 仅供数据库内部追踪和审计，不进入任务输入、授权哈希或 COMMIT 门禁 |
| `platform_name` | 当前物理结构中的规范平台名称 | 不得使用 `default_platform` |
| `internal_sku` | “商品资料与库存录入”中的 PRA 内部商品编码 | 仅供内部追踪，不是平台业务键；主数据可匹配时必须落库 |
| `variety` | 品种/页面商品名 | 与平台、等级共同构成业务键 |
| `grade` | 页面商品等级 | 与平台、品种共同构成业务键 |
| `current_price` | 最近一次已确认的平台价格 | 下一次任务的页面价格基线；不得从基础成本推导 |
| `platform_stock_qty` | 最近一次平台库存观测值 | 新记录缺少观测时默认 `100`；有效 ShadowBot READ_ONLY 或完整 `SYNC_STATUS` 可更新 |
| `sold_qty` | 已销售数 | 改价流程不得修改 |
| `online_status` | 最近一次确认的上下架状态 | 改价流程只读取并保留，不得改写 |
| `source` | 最近一次数据来源 | 例如 `manual`、`shadowbot` |
| `updated_at` | 最近更新时间 | 可用于报告数据新鲜度，不进入任务输入、授权哈希或 COMMIT 门禁 |
| `inventory_source` | 库存来源 | 默认 `default`，ShadowBot 观测为 `shadowbot` |
| `inventory_observed_at` | 库存实际观测时间 | 用于阻止较旧结果覆盖新库存 |
| `inventory_source_attempt_id` | 库存来源执行尝试 ID | 用于结果绑定和幂等导入 |
| `price_source` | 价格观察来源 | `SYNC_STATUS` 投影为 `shadowbot_sync_status` |
| `price_observed_at` | 价格实际观测时间 | 与库存观察时间共同判断任务生成基线是否有效 |
| `price_source_attempt_id` | 价格来源执行尝试 ID | 必须与库存来源 attempt 一致，任务生成器才接受该组观察 |
| `last_listing_change_at` | 最近一次已确认上下架写操作时间 | 早于该时间的页面位置、价格和库存观察不得用于新的上下架任务 |

`/business-inputs?input_tab=listing_status` 仅用于查看当前快照，不显示人工录入或修改窗口。正常状态下由通过合同校验的 ShadowBot READ_ONLY 结果新增或更新记录。为开发调试保留受登录、CSRF、路径策略和字段校验保护的 `save_listing_status` POST 动作，但页面不暴露该入口，写入来源标记为 `debug_web_request`。同一“平台 + 品种 + 等级”再次写入时更新原记录，不新增重复记录。

`100` 只是当前开发阶段在尚无平台观测时的新记录默认值，不是真实库存证据。任务11的 v2 READ_ONLY 请求以“商品资料与库存录入”的全部 SKU 生成 `platform`、`expected_product_name`、`expected_grade` 映射提示；逐商品结果为 `SUCCESS` 且 `inventory` 是非负整数时，Result Importer 按该平台身份更新库存、观测时间和执行尝试 ID。任务13的完整两页 `SYNC_STATUS` 则把同一次扫描观察到的价格和库存一起投影到 `listing_status`，并保留父快照和商品项作为原始证据。页面商品能在库存商品表中按“商品名 + 等级”唯一匹配时，状态记录必须写入对应 `internal_sku`。较旧观测不得覆盖较新观察；早于最近一次上下架写操作的观察也不得重新成为任务生成依据。任务12价格回写始终保留库存，不能再次写入默认值。

任务生成器读取 `SET_ONLINE` 基线时只接受同时满足以下条件的状态行：

1. 价格和库存均有实际观察时间；
2. `price_source_attempt_id` 与 `inventory_source_attempt_id` 相同且非空；
3. 两项观察均不早于 `last_listing_change_at`；
4. 平台、商品名称和等级能唯一映射到目标 SKU。

生成的 `SET_ONLINE` 任务把最近观察价格写入 `expected_old_price`，把观察价格和库存写入 `decision_trace.platform_observation`；目标价格仍由价格规则计算，目标库存仍来自商品库存与计划结果，不能把“页面当前值”误当成“业务目标值”。

## 3. 相对价格计算与正式任务输入

固定改价：

```text
TARGET_PRICE = OLD_PRICE + markup_value
```

百分比改价：

```text
TARGET_PRICE = OLD_PRICE × (1 + markup_value / 100)
```

完成相对计算后，再应用最低价和取整规则。`base_cost` 不再作为相对改价的起点。

示例：

```text
平台代码：MAYI_HUATUAN_SUPPLIER
规范平台名称：蚂蚁花团供应商
品种：艾莎
等级：C级
OLD_PRICE：18.50
固定改动价格：+2.00
TARGET_PRICE：20.50
```

PRA 自行应用相对价格规则时必须取得明确的 `OLD_PRICE`，不得回退到基础成本；该旧价可以来自当前平台状态，也可以由上游任务中心明确提供。只要正式任务已经包含有效的 `expected_old_price`，就不再要求附带状态行 ID 或快照版本。纯上架或下架任务不依赖价格，但属于任务13范围。

任务中心创建正式改价任务时，每项只需固化以下业务数据：

| 字段 | 作用 |
| --- | --- |
| `internal_sku` | 解析为当前平台页面商品名和等级 |
| `expected_old_price` | 提交前页面核价基准 |
| `target_price` | 本次目标价 |

`listing_status_id`、`updated_at`、快照版本、`read_batch_id` 和 READ_ONLY attempt ID 都不是正式任务输入。PRA 可以把它们作为非约束性的来源说明写入内部报告，但不得要求任务中心提供，也不得把它们加入授权哈希、COMMIT 合同或执行门禁。

清单固化后，`internal_sku`、`expected_old_price` 或 `target_price` 任一变化都会使原合同失效。SQLite 中的状态记录随后发生变化，不会单独使合同失效；真正阻止过期旧价提交的机制，是 COMMIT 在任何写操作前读取页面当前价并与 `expected_old_price` 比较。

任务的 `decision_trace.rule_steps` 至少记录：

```json
[
  "old_price=18.50",
  "winning_rule:PRICE-001:priority=1:specificity=1",
  "rule:PRICE-001:fixed_markup->20.50",
  "rule:PRICE-001:rounded->20.50"
]
```

## 4. 页面身份的唯一定位

PRA 接收内部 SKU，并在创建授权提案时通过“商品资料与库存录入”的 SKU 主数据解析为平台页面身份：

```text
platform + internal_sku
→ expected_product_name + expected_grade
→ ShadowBot 动态定位
```

每个执行项至少包含：

| 字段 | 说明 |
| --- | --- |
| `platform` | 规范平台名称 |
| `internal_sku` | 正式任务提供的内部商品编码 |
| `expected_product_name` | 由库存商品表中的 SKU 记录得到的页面商品名称 |
| `expected_grade` | 由库存商品表中的 SKU 记录得到的页面商品等级 |

以下情况必须在授权前停止，不能生成 COMMIT 合同：

- 缺少规范平台、SKU，或 SKU 无法解析为唯一启用的商品名称和等级；
- 同一批次存在重复的“平台 + 商品名称 + 等级”身份；
- 页面枚举结果无法唯一匹配商品名称和等级。

视觉识别只能作为人工辅助证据，不能替代“商品名称 + 等级”的结构化唯一定位。SKU 用于任务输入和映射查询；ShadowBot 在页面上仍以解析后的“商品名称 + 等级”唯一匹配，不依赖页面排列顺序。

## 5. 正式 COMMIT 输入、授权与单次投递

### 5.1 业务输入

每个改价项目只接收：

```json
{
  "internal_sku": "ROSE-AISHA-C",
  "expected_old_price": "18.50",
  "target_price": "20.50"
}
```

平台由批次顶层指定；商品名称和等级由 SKU 映射得到。页面位置、排序、行号、`listing_status_id`、快照版本、READ_ONLY 结果和 FILL_PREVIEW 结果都不是正式输入。

### 5.2 授权边界

开发和实机验收阶段必须遵循：

1. 接收完整任务列表。
2. 校验全部 SKU 都能解析为唯一页面身份，并校验旧价和目标价。
3. 生成不可执行的授权提案，列出每一项平台、SKU、解析后的商品名称和等级、旧价和目标价。
4. 在创建任何可执行 COMMIT 队列文件前，向项目负责人申请对这份固定清单的明确授权。
5. 授权文本与清单哈希完全一致后，才创建一个 COMMIT 队列合同。

开发/实机验收的一次授权可以覆盖同一消息中逐项明确列出的完整固定清单。正式运行的 `execution_profile=production` 不依赖 Codex 对话确认，但任务14前必须由操作人员明确传入一个或多个 `--task-id`；系统只把这些明确选择且发布前再次校验通过的 `pending update_price` 任务作为本批次执行权威。统一任务审查和无人值守调度授权延期到任务14，完成并审查前禁止扫描全部 pending 后自动发布。逐商品载荷哈希绑定 `platform + internal_sku + expected_product_name + expected_grade + expected_old_price + target_price`；批次 `manifest_sha256` 绑定平台及规范化项目清单。解析后的商品名称和等级同时受 `item_payload_sha256` 和最终 `instruction_hash` 保护，但不引入额外的快照版本门禁。清单固化后不得添加、删除或修改项目；发生变化时必须生成新合同。

### 5.3 一个队列合同

一个批次只允许投递一个 COMMIT 请求。顶层至少包含：

- `schema_version=shadowbot-commit-batch-request-1.2` 和 `contract_version=4`；
- `task_id` 或稳定的批次任务 ID；
- 兼容用的批次顶层 `operation_id`；
- 批次级 `execution_attempt_id`；
- `execution_mode=COMMIT`；
- `batch_id`；
- 规范平台代码和名称；
- 完整 `items` 数组；
- `manifest_sha256` 与逐商品 `item_payload_sha256`；
- `instruction_hash`；
- 创建时间和过期时间。

每个 `items` 项至少绑定：

- 由“批次 + source_task_id”确定的稳定 `item_id`；
- 由“source_task_id + 逐项载荷哈希”确定、跨同一业务操作发布重试保持稳定的 `operation_id`；
- 由“批次 execution_attempt_id + item_id”确定的 `item_execution_attempt_id`；
- 仅用于 UI 唯一定位的 `page_identity_key=平台 + 规范商品名 + 等级`；
- 用于并发写入互斥的 `write_identity_key=平台 + internal_sku`；
- `internal_sku`，以及由其解析得到的 `platform`、`expected_product_name` 和 `expected_grade`；
- `expected_old_price`；
- `target_price`；
- `item_payload_sha256`。

正式合同禁止包含 `page_position`、`page_position_hint`、预先排序或其他页面坐标字段。

## 6. Worker 内部串行执行

Worker 收到一个完整 COMMIT 合同后，先完成整页预扫描和全目标门禁，再按当前页面实时行号从上到下严格串行处理。合同 `items` 的输入顺序不作为页面执行顺序：

```text
主动刷新并动态枚举当前页面全部商品名称和等级
→ 以“商品名称 + 等级”唯一匹配全部目标
→ 确认全部目标均唯一存在
→ 读取全部目标页面当前价并与 expected_old_price 比较
→ 任一不一致时在写操作前停止整个批次
→ 全部一致时按动态行号从上到下生成执行计划
→ 按实际元素边界决定是否滚动
→ 打开正确商品的改价弹窗
→ 再次核对弹窗商品身份和当前价
→ 填入 target_price 并提交
→ 独立重新定位并回读实际价格
→ 记录逐商品结果
→ 继续下一项
```

动态行号只允许作为当前运行时的临时 UI 状态，不能写回授权合同作为后续商品的定位依据。商品处于第4行或之后时，应在已经确认动态位置后滚动；不得通过“点击失败后再滚动”的方式试错。

提交前页面价与 `expected_old_price` 不一致时，当前项返回：

```text
status = FAILED
error_code = OLD_PRICE_CHANGED
side_effect_state = NOT_STARTED
```

随后停止整个批次，未执行项目标记为 `NOT_ATTEMPTED`。不得继续执行剩余商品，也不得自动修改 SQLite 旧价或重新授权。

任何商品在提交后进入不确定状态时，也必须停止批次；该商品进入 UNKNOWN/RECONCILE，剩余项目保持 `NOT_ATTEMPTED`。

## 7. ShadowBot 批次结果与 Result Importer

结果文件放入队列的 `results/*.result.json`，并配套校验文件。Result Importer 必须校验：

- 请求文件和结果文件校验和；
- 批次、任务、操作和执行尝试 ID；
- 执行模式和指令哈希；
- 授权清单哈希；
- 每个 item 的平台、`internal_sku`、页面商品名、等级、旧价、目标价和 `item_payload_sha256`；
- 每个 item 的操作 ID、尝试 ID、页面身份、写入身份、严格布尔值、规范价格和带时区观察时间；
- 计数恒等式和未执行项目状态。

新结果合同为 `shadowbot-commit-batch-result-1.1`。Worker 的通用异常和结果体积降级也必须保留与请求等长的完整 `items` 骨架，不允许用空数组丢失逐商品边界。Importer 先生成只读 `ImportPlan`，全部验证通过后，才在同一个 SQLite 事务中写入页面快照、任务状态、批次/逐项账本、operation/attempt/checkpoint、活动写锁和 `shadowbot_commit_result_receipts`。数据库回执是“结果已接受”的唯一真值；归档目录中的 import ACK 是事务后的文件投影，写入失败可以重试，不能回滚或重复业务写入。

成功 COMMIT 项的规范状态为 `VERIFIED`，不再在新合同中生成旧状态 `SUCCESS`：

```json
{
  "item_id": "ITEM-001",
  "platform": "蚂蚁花团供应商",
  "expected_product_name": "艾莎",
  "expected_grade": "C级",
  "status": "VERIFIED",
  "run_success_flag": true,
  "business_operation_completed": true,
  "side_effect_state": "VERIFIED",
  "expected_old_price": "18.50",
  "target_price": "20.50",
  "actual_price": "20.50",
  "retryable": false,
  "error_code": ""
}
```

批次结果至少记录：

- 总数、已尝试数、成功数、失败数、UNKNOWN 数和未执行数；
- 每个商品的独立结果与回读证据；
- 停止原因和停止项目；
- `total = verified + not_applied + failed + unknown + not_attempted`；
- `attempted` 只统计 `submit_attempted=true` 的项目，不能通过字符串真值或状态名称推导。

`run_success_flag` 只代表技术运行结果。只有逐商品状态为 `VERIFIED`、`business_operation_completed=true`、`side_effect_state=VERIFIED`，并且 `actual_price=target_price` 时，PRA 才允许完成对应业务任务和回写平台价格。

## 8. 当前平台价格回写规则

任务12价格回写必须满足全部条件：

1. 请求、结果、授权和逐商品绑定校验通过；
2. 对应执行尝试存在且尚未导入冲突结果；
3. `status=VERIFIED`；
4. `business_operation_completed=true`；
5. `side_effect_state=VERIFIED`；
6. `actual_price` 是有效十进制价格；
7. `actual_price=target_price`；
8. 对应 `listing_status` 记录仍能以“平台 + 解析后的商品名称 + 等级”唯一找到。

回写只允许修改：

```text
current_price = actual_price
source        = shadowbot
updated_at    = 当前 UTC 时间
```

必须保留已有记录的：

- `platform_stock_qty`；
- `sold_qty`；
- `online_status`；
- 稳定业务身份。

不得执行以下行为：

- 在 `actual_price` 缺失或非法时回退到 `target_price`；
- 因改价成功把 `online_status` 强制改为 `online`；
- 因改价成功把库存写成固定值；
- 找不到平台状态记录时自动创建一条 `online` 记录；
- 使用结果文件中未被原请求和授权绑定的平台商品身份回写。

找不到对应平台状态记录或页面身份不唯一时，Result Importer 应保留结果证据并进入人工复核，不得猜测性回写。状态行 ID 或更新时间变化本身不是冲突。

### 8.1 ShadowBot 库存观测回写

库存回写只接收 Task 11 的 v2 `READ_ONLY` 结果，并要求：

1. 请求和结果的批次、执行尝试、指令哈希与逐商品 `item_id` 已通过校验；
2. 请求商品包含非空 `platform`、`expected_product_name` 和 `expected_grade`；
3. `item_status=SUCCESS`；
4. `inventory` 是非负整数；
5. 结果包含有效的 `completed_at` 或 `ended_at`；
6. 新观测时间不早于当前 `inventory_observed_at`。

已有状态记录只更新：

```text
platform_stock_qty          = inventory
inventory_source            = shadowbot
inventory_observed_at       = READ_ONLY 完成时间
inventory_source_attempt_id = execution_attempt_id
updated_at                  = READ_ONLY 完成时间
```

READ_ONLY 以当前“上架中”页面为完整快照。页面商品在“商品资料与库存录入”中按“商品名 + 等级”唯一存在、但“当前平台商品状态”尚无记录时，只要结果同时提供有效价格、库存和明确的 `ONLINE` 状态，就创建平台状态记录，写入库存表中的 `internal_sku` 并标记为 `online`；这只是同步已观测的平台事实，不执行平台上架点击。只有页面商品在库存商品表中不存在时才报告 `UNMAPPED_PRODUCT_DISCOVERED`。库存主数据出现重复页面身份时报告独立的映射歧义，不得误报为未映射。

## 9. UNKNOWN 与 RECONCILE

COMMIT 在提交意图之后若无法证明“确认按钮未被点击”，必须进入 UNKNOWN，不得直接重试写操作。只有 `ShadowBotExecutor.ensure_reconcile_attempt(...)` 可以为该商品创建确定性 ID 的唯一只读 `RECONCILE` 尝试；Worker、Importer 和 Watchdog 只能记录或请求该入口，不能自行构造第二份对账：

| 平台实际价 | 对账结果 | 是否回写 |
| --- | --- | --- |
| 等于 `target_price` | `VERIFIED`，`business_operation_completed=true` | 按第8节回写实际价 |
| 等于 `expected_old_price` | `NOT_APPLIED`，`business_operation_completed=false` | 不回写 |
| 两者都不等 | `SIDE_EFFECT_UNKNOWN` 或人工复核 | 不回写 |

`RECONCILE` 只能动态定位并读取页面，不得打开价格输入框或执行提交。对账成功也不得改变库存和上下架状态。

## 10. 联调验收清单

### 10.1 数据和合同

1. SQLite 已升级到 v12，`listing_status`、逐商品操作/尝试、活动写锁和技术回执结构存在。
2. 测试平台商品已按“平台 + 品种 + 等级”维护当前价格，平台名称符合唯一规范。
3. 每个执行项都有唯一的页面商品名称和等级。
4. 固定改价满足 `TARGET_PRICE = OLD_PRICE + markup_value`。
5. 百分比改价以 `OLD_PRICE` 为基数。
6. 缺少 SKU、OLD_PRICE、TARGET_PRICE，或 SKU 无法解析为唯一页面身份时不生成授权提案。
7. 正式业务输入每项只包含 SKU、旧价和目标价，平台在批次顶层指定。
8. 授权发生在 COMMIT 队列文件创建前，并绑定完整固定清单。
9. 一个批次只生成和投递一个 COMMIT 请求。
10. 正式合同不包含页面位置、READ_ONLY 或 FILL_PREVIEW 依赖。

### 10.2 实机执行

1. 每件商品均根据“商品名称 + 等级”动态唯一定位。
2. 商品顺序变化后仍能定位，不依赖投递时排列顺序。
3. 第4行及以后商品在确认实际位置后滚动，不通过误点击试错。
4. `OLD_PRICE_CHANGED` 在任何提交动作前停止整个批次。
5. 每个成功商品提交后均有独立回读，且 `actual_price=target_price`。
6. 某项失败或 UNKNOWN 后，剩余商品均为 `NOT_ATTEMPTED`。
7. 批次计数满足两条恒等式。

### 10.3 导入与回写

1. 非 `VERIFIED`、非法结果和绑定校验失败结果不修改当前平台价格。
2. 成功 COMMIT 只把 `listing_status.current_price` 更新为 `actual_price`。
3. 价格回写保留原库存、已销售数和上下架状态。
4. 缺少实际回读价格时不回退到目标价。
5. UNKNOWN 只创建唯一 RECONCILE，不直接重试 COMMIT。
6. `inbox/`、`working/`、`results/` 活动文件已归档，Worker 收尾符合项目影刀操作约束。
7. READ_ONLY 有效库存可替换默认 `100`，旧观测不能覆盖新库存；平台身份仍按“商品名 + 等级”匹配，但匹配成功后必须回填库存商品表中的内部 SKU。

## 11. 当前实现状态与剩余边界

本文件要求的任务12核心闭环已经实现：

1. 任务表以 `internal_sku`、`expected_old_price` 和 `target_price` 无损构建正式合同。
2. v4 单次请求多商品合同、`manifest_sha256`、逐项哈希和 SQLite v12 批次/逐项/技术回执账本已经落地。
3. 批次创建前检查重复内部 SKU 和重复页面身份；Worker 运行时再次确认全部目标均唯一存在。
4. Worker 写操作前校验所有目标旧价，失败时全批次 `NOT_STARTED`。
5. 成功项只有存在独立 `actual_price=target_price` 回读时才回写平台价格，不回退到任务目标价。
6. Result Importer 校验请求、结果、逐项绑定和计数恒等式，并回传完整页面状态快照。
7. 页面实时执行顺序和视口恢复已经覆盖跳过中间商品、第 4 行以后商品和非默认滚动位置。

剩余边界不属于任务12核心实现缺口：

1. 上架、下架和 OFFLINE 跨页面状态对账由任务13负责。
2. 当前只支持蚂蚁花团供应商单平台、单窗口、单 Worker 严格串行批次。
3. 平台没有 SKU；若以后出现同名同等级但规格不同的商品，必须扩展稳定页面身份字段。
4. 长期告警、磁盘清理、证据保留和服务账号运维尚未达到无人值守生产标准。
5. 冷态批次仍有明显启动/首次渲染成本，性能验收必须分别报告冷态和暖态。

## 12. 相关实现位置

- SQLite 结构与读写：`app/repositories/sqlite_runtime_repository.py`
- 结构健康检查：`app/runtime_schema.py`
- 相对价格计算：`app/services/pricing.py`
- 任务生成取价：`app/services/workflow.py`、`app/services/task_generation.py`
- ShadowBot 结果导入：`app/services/shadowbot_queue.py`
- ShadowBot 成功回写：`app/services/shadowbot_executor.py`
- 正式多商品 COMMIT 合同：`app/services/shadowbot_commit_batch.py`
- 正式批次管线与账本：`app/services/shadowbot_commit_pipeline.py`
- 业务数据页面：`app/web.py`
- 当前平台 ShadowBot adapter/executor：`shadowbot/test2/`
- 最终交接与实机证据：`docs/reports/task12_final_handoff_20260723.md`
