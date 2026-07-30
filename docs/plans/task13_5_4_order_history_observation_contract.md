# 任务 13.5-4：订单历史只读观察开工合同

- 状态：准备完成，等待订单页无副作用探索
- 基线：`main` 合并 PR #24 后的 `6d46e3f`
- 权威范围：GitHub Issue #20 正文与
  `task13_5_operational_closed_loop_and_web_rewrite.md`
- 当前平台：蚂蚁花团供应商微信小程序

## 1. 阶段目标

13.5-4 只建设订单管理页面的历史只读观察链路：

```text
ORDER_SCAN Automation 子 run
→ 平台订单页只读 Adapter
→ 结构化只读结果
→ ORDER_HISTORY_IMPORT
→ v14 append-only 订单观察
```

本阶段必须交付：

- 订单页无副作用探索证据；
- 平台能力、字段、分页、日期范围和结束标记合同；
- 无稳定订单 ID 时的批次内多重集合语义；
- 平台无关输入合同、Importer、Repository 和测试；
- 当前平台只读 Adapter 及脱敏证据；
- `ORDER_SCAN` Automation handler 接入。

本阶段不实现销售估算、日结、Incident 自动处置、Web 重写或任何订单写操作。

## 2. 已有资产与实际缺口

继续复用：

- v14 `order_observation_batches / order_observation_items`；
- `OperationalTimeService` 的平台交易日、卖家作业日和阶段；
- 13.5-3 Automation run、父子 link、租约 fencing 和单 UI 通道；
- 任务 12/13 文件队列、Worker 生命周期、登录介入、结果归档和证据规范；
- `CompiledProductMappings` 的 `VERIFIED / UNMAPPED / AMBIGUOUS / DISABLED`；
- 商品观察 Importer 的“既有事实读取”和“新事实写入”分路原则。

当前尚未实现：

- 订单页 Adapter capability；
- 订单扫描 JSON 输入/结果 schema；
- 订单观察 Importer 和查询 Repository；
- 行指纹、多重集合比较和完整批次选择；
- `ORDER_SCAN` 正式 handler；
- 历史日、空页、分页、重复行、部分失败和取消量语义的实机证据。

真实 Runtime DB 尚未迁移到 v14；本阶段开发继续只使用临时数据库。

## 3. 平台能力合同

当前平台能力固定声明为：

```text
supports_order_scan = true
supports_current_trade_day = false
supports_historical_trade_day = true
```

结果语义：

- `UNSUPPORTED`：平台根本没有订单扫描能力；
- `UNAVAILABLE`：平台支持，但目标交易日或范围当前不可访问；
- `FAILED`：能力应可用，但本次登录、网络、页面、解析或执行失败；
- `SUCCEEDED`：目标范围已完成只读访问；是否可接受仍由批次完整性决定。

当前交易日不可读必须返回 `UNAVAILABLE`，数量、金额和订单观察数保持未知，不能用
空数组或 0 表示“无订单”。

## 4. 无副作用探索门禁

编码平台 Adapter 前，先完成一次独立的订单页无副作用探索：

1. 只进入订单管理页和历史日期筛选，不点击发货、退款、确认、支付、联系买家或其他
   写操作；
2. 记录可访问的最早/最晚平台交易日、默认日期、切换日期后的实际返回范围；
3. 确认列表、分页或滚动加载机制，以及可验证的结束标记；
4. 确认空页、加载失败、权限不足和当前交易日不可访问的页面差异；
5. 对每个候选字段确认页面标签、格式、空值、单位和业务含义；
6. 验证相同内容的真实重复行是否可能出现，以及页面顺序是否稳定；
7. 只在页面事实足以证明时冻结 `effective_qty` 和取消量推导公式；
8. 证据必须脱敏，不保存或提交买家姓名、电话、地址、聊天内容、账号或订单 ID。

探索阶段不得写真实 Runtime DB，不得把页面截图或控制台文本直接当作结构化数据真值。

## 5. 数据最小化白名单

允许进入公共订单观察合同的字段：

- `platform_name`
- `platform_trade_date`
- `platform_product_name`
- `grade`
- `internal_sku`（内部映射结果，可空）
- `mapping_status`
- `mapping_version`
- `order_created_at`
- `ordered_qty`
- `effective_qty`
- `cancelled_qty`
- `cancellation_derivation_method`
- `seller_received_amount`
- `purchase_sequence`
- `observed_at`
- `seller_operation_date`
- `seller_phase`
- `source_row_fingerprint`
- `occurrence_no`
- `raw_observation_sha256`

明确禁止持久化：

- 平台订单 ID、订单行 ID或把页面行号伪装成长期 ID；
- 买家姓名、电话、地址、聊天内容、账号等个人信息；
- 退款数量、付款状态、支付时间、完成时间等尚未证明可得的字段；
- 独立规格、商品历史累计销量；
- 买家支付金额、标价、优惠后成交价等页面未提供的金额口径。

只保存 `seller_received_amount`。若后续派生单位金额，名称必须是
`seller_received_unit_amount`，且来源为卖家实收金额除以有效数量。

## 6. 不可变批次合同

订单观察批次继续使用 v14 现有字段，不新增 schema：

```text
observation_batch_id
automation_run_id
platform_name
requested_platform_trade_date
capability_result
batch_status
scan_started_at / scan_completed_at
requested_range_json
scope_complete
end_marker_verified
content_sha256
time_policy_version
error_code / error_message
created_at
```

`requested_range_json` 除用户请求范围外，必须保存可复核的内部来源绑定：

```text
contract_version
requested_platform_trade_date
requested_range
actual_range
adapter_capabilities
source_request_id
source_manifest_sha256
source_result_sha256
page_count
source_row_count
end_marker_kind
accepted_mapping_version（有商品项时）
```

这些内部字段由 Importer 写入或验证，外部调用方不得伪造。来源 manifest/result
摘要必须绑定脱敏结构化结果，不得绑定含个人信息的原始页面正文。
`UNSUPPORTED / UNAVAILABLE / FAILED` 等无商品项结果不得为了生成该字段而解析当前
映射；其幂等身份必须独立于映射工作簿。

## 7. 行指纹与多重集合冻结

### 7.1 行指纹

`source_row_fingerprint` 只表示规范业务字段相同的候选分组，不是订单身份：

```text
sha256(
  canonical_json(
    fingerprint_version,
    platform_name,
    platform_trade_date,
    normalized_platform_product_name,
    normalized_grade,
    order_created_at,
    ordered_qty,
    effective_qty,
    seller_received_amount,
    purchase_sequence
  )
)
```

规则：

- 版本固定为 `order-row-fingerprint-1.0`；
- JSON 使用 UTF-8、稳定键顺序和无多余空白；
- 时间转换为带时区的规范 ISO-8601；
- 金额由精确 Decimal 规范化，禁止 float；
- `NULL` 与 0、空字符串必须保持不同；
- 指纹格式为 `sha256:<64 位小写十六进制>`。

`cancelled_qty` 不进入 v1 指纹；它是可撤销的推导结果，其输入和方法进入
`raw_observation_sha256`。若探索证明页面直接给出稳定取消事实，必须先更新合同版本，
不能静默改变 v1 指纹。

### 7.2 批次内重复实例

相同指纹可能真实出现多次：

- `occurrence_no` 在单一批次、单一指纹组内从 1 连续编号；
- 编号只用于保存每个真实实例，不承担跨批次身份；
- `observation_item_id` 由 batch ID、指纹和 `occurrence_no` 稳定生成；
- 不得对 `source_row_fingerprint` 建立唯一索引；
- 重复行必须逐条写入，禁止 `INSERT OR IGNORE` 或集合去重。

v14 没有 `occurrence_count` 列；13.5-4 将其冻结为查询时派生值：

```sql
COUNT(*) GROUP BY observation_batch_id, source_row_fingerprint
```

派生计数必须等于该组最大 `occurrence_no`，并且编号连续无缺口。

### 7.3 跨批次比较

跨批次比较使用：

```text
source_row_fingerprint → occurrence_count
```

形成的多重集合。不同批次不得相加为销量，也不得按 `occurrence_no` 逐条配对为同一
订单。13.5-5 只能选择目标交易日最新的、已接受且完整的批次作为正式订单输入；旧批次
保留用于审计、差异和修订证据。

## 8. 批次状态矩阵

| capability_result | batch_status | items | 约束 |
| --- | --- | --- | --- |
| `SUCCEEDED` | `ACCEPTED` | 可空 | 范围完整、结束标记验证、字段满足合同；空页必须有可信空页结束证据 |
| `SUCCEEDED` | `PARTIAL` | 可有 | 读取到真实行，但分页、字段、映射或范围不完整；不得进入正式结算 |
| `UNSUPPORTED` | `UNAVAILABLE` | 必须空 | 平台能力明确不存在 |
| `UNAVAILABLE` | `UNAVAILABLE` | 必须空 | 目标日期或范围当前不可访问，不表示 0 |
| `FAILED` | `FAILED` | 必须空 | 未形成可接受观察 |
| `FAILED` | `PARTIAL` | 可有 | 失败前已有可验证行；仅作部分事实和诊断，不进入正式结算 |

所有 item 的 `platform_trade_date` 必须等于请求交易日；`observed_at` 必须位于批次
区间内。`order_created_at` 必须是页面提供的准确时间；若只有无法消除歧义的局部时间，
批次不得标为 `ACCEPTED`。

## 9. 映射、时间与取消量

- 商品映射以扫描验收时的 `CompiledProductMappings` 为准并冻结版本；
- `VERIFIED` 保存明确 SKU；其他状态保存空 SKU，不进入自动业务规则；
- 批次内映射版本必须一致；
- `seller_operation_date / seller_phase` 由每条 `observed_at` 经
  `OperationalTimeService` 计算；
- `platform_trade_date` 以订单事实所属交易日为准，不使用扫描发生日替代；
- 在无副作用探索冻结公式前，`cancelled_qty=NULL` 且
  `cancellation_derivation_method=''`；
- 后续推导必须保存版本化方法及可复核输入，且满足
  `0 <= effective_qty <= ordered_qty` 和
  `cancelled_qty = ordered_qty - effective_qty` 才可接受。

## 10. Importer 与 Automation fencing

Importer 分为两条路径：

1. 先在事务内校验 run、平台、时间策略、交易日、来源信封和规范原始内容；
2. 同 batch ID 或同 run 同规范内容已存在时，返回数据库原批次和原 hash；
3. 只有新增事实才要求 `ORDER_SCAN` run 为 `RUNNING`、claim 实时有效，并解析当前
   映射后插入；
4. 同 run 的不同内容不得形成第二套权威事实；
5. append-only 表禁止 UPDATE、DELETE 和静默覆盖。

`ORDER_SCAN` 必须是 `FULL_MARKET_SCAN / PRE_CUTOFF_FULL_SCAN` 的合法 `CHILD_ONLY`
子 run，并继承父 run 冻结的双日期、阶段和时间策略。订单子结果成功、失败或不可用均
不得撤销已接受的商品子结果，也不得参与 `ONLINE_PULSE` 覆盖判定。

## 11. 实施切分

按以下顺序实施，不把实机探索和全部业务代码压成一次不可审查变更：

1. **13.5-4A 合同与探索**：只读探索、脱敏 fixture、字段和分页语义冻结；
2. **13.5-4B 公共核心**：JSON 边界、指纹、多重集合、Importer、Repository；
3. **13.5-4C 平台 Adapter**：当前平台历史日只读解析及来源 hash；
4. **13.5-4D Automation 接入**：注册 `ORDER_SCAN` handler、父子隔离和运行证据；
5. **13.5-4E 回归与交接**：全量测试、打包、双平台 CI 和实施报告。

## 12. 验收矩阵

至少覆盖：

- 支持历史日、当前日 `UNAVAILABLE`、平台 `UNSUPPORTED`；
- 单页、多页/滚动、可信空页和结束标记缺失；
- 同指纹 1 条、2 条和多组重复行，编号连续且不丢实例；
- 新 batch ID 的相同多重集合幂等返回原规范批次；
- 相同 batch ID 不同内容拒绝；
- 跨批次多重集合差异不累加；
- `ACCEPTED / PARTIAL / UNAVAILABLE / FAILED` 状态矩阵；
- 映射四状态、映射漂移、时间策略和交易日不匹配；
- 终态 run 的既有事实重放、新事实的 claim fencing；
- 订单子结果失败不影响商品子结果；
- PII、订单 ID 和敏感本机路径不进入 JSON、SQLite、日志或证据；
- Linux/Windows pytest、系统冒烟、wheel、包边界和 UTF-8 门禁。

## 13. 开工判定

已满足：

- [x] PR #24 已合并，本地 `main` 与 `origin/main` 对齐；
- [x] v14 订单观察表和 append-only 约束已存在；
- [x] 多重集合核心语义已在父 Issue 和本合同中冻结；
- [x] 当前平台能力的预期值已明确；
- [x] 实现切分、安全边界和验收矩阵已明确。

开始公共核心编码前仍必须满足：

- [ ] 完成一次订单页无副作用探索；
- [ ] 冻结页面字段标签、日期范围、分页/滚动和结束标记；
- [ ] 确认准确 `order_created_at` 的格式和时区语义；
- [ ] 确认 `ordered_qty / effective_qty / seller_received_amount /
  purchase_sequence` 的页面含义和空值；
- [ ] 决定取消量保持未知或采用有证据的版本化推导方法；
- [ ] 形成不含 PII 和订单 ID 的脱敏结构化 fixture。

在这些门禁完成前，不实现真实订单页解析，不注册生产 `ORDER_SCAN` handler，也不宣称
当前交易日订单可用。
