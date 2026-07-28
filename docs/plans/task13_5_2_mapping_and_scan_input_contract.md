# 任务 13.5-2：商品映射与扫描输入合同

- 形成日期：2026-07-29
- 上游：
  [任务 13.5-1 冻结合同](task13_5_1_quality_and_settlement_contract_review.md)
- 当前状态：`READY_FOR_IMPLEMENTATION`
- 范围：商品映射、`ONLINE_PULSE`、`FULL_MARKET_SCAN` 的商品状态子结果和
  v14 不可变商品观察

## 1. 安全边界

- 本阶段只产生只读商品观察，不直接生成或执行平台写动作。
- 复用任务 13 的 v5 `SYNC_STATUS`、Importer、公共批次注册表和两页完整快照。
- 小扫描不得推断未出现商品处于待上架、下架或不存在。
- 只有完整两页扫描可以更新完整 `listing_location`。
- 映射不唯一、页面不完整、价格或库存不可读时不得产生写授权。
- 不修改 ShadowBot 宿主代码前，必须另行执行 Worker 停止、同步和 hash 门禁。

## 2. 映射输入

运营源保持为 `platform_mappings.xlsx`，逻辑字段至少包括：

```text
platform_name
platform_product_name
normalized_platform_product_name
grade
internal_sku
mapping_status
effective_from
effective_to
remark
```

`mapping_status` 精确值：

```text
VERIFIED
UNMAPPED
AMBIGUOUS
DISABLED
```

加载器必须：

- 使用“平台 + 规范化商品名 + 等级”作为候选身份。
- 拒绝同一生效范围映射到多个 `internal_sku`。
- 生成不可变 JSON，并保存源文件与 JSON 的 SHA-256。
- 不把平台名称或页面行号当作内部 SKU。
- 只有 `VERIFIED` 可以进入精确 SKU 观察和后续自动动作候选。

CSV/TSV 导入如后续增加，必须使用 UTF-8-SIG；XLSX 写入继续使用现有原子保存流程。

## 3. `ONLINE_PULSE`

默认每 10 分钟，只读取“上架中”，输出
`product_observation_batches/items`：

```text
platform_name
platform_product_name
grade
observed_price
observed_inventory
observed_online = true
observed_at
page_identity_key
internal_sku
mapping_status
mapping_version
platform_trade_date
seller_operation_date
seller_phase
time_policy_version
evidence_sha256
```

批次保存开始/结束时间、页面范围、结束标记、完整性、内容 hash 和
`automation_run_id`。逐项双日期只能由 `OperationalTimeService` 计算。

## 4. `FULL_MARKET_SCAN`

父级合同保持：

```text
FULL_MARKET_SCAN
├─ LISTING_STATUS_SCAN
│  ├─ 上架中
│  └─ 待上架
└─ ORDER_SCAN
```

13.5-2 只实现或对接 `LISTING_STATUS_SCAN` 商品子结果；订单子结果留到
13.5-4。父子 run 使用 `automation_run_links` 关联，一个子结果失败不能撤销其他
已接受事实。

商品子结果继续以完整两页 `SYNC_STATUS` 为权威；新 v14 观察是不可变审计事实，
不得在 schema migration 中从旧快照猜测生成。

## 5. 接受条件

- 映射源、不可变 JSON 和 SHA-256 可重复验证。
- VERIFIED、UNMAPPED、AMBIGUOUS、DISABLED 均有测试。
- 小扫描缺席不产生离线推断。
- 大扫描两页完整性继续满足任务 13 合同。
- 跨 18:00/20:00 的逐项观察归属正确。
- 重复结果按内容 hash 幂等，不跨批次累加。
- 商品子结果失败、订单能力不可用和父 run 状态彼此隔离。
- 临时数据库、完整 pytest、系统冒烟、wheel 和 CI 通过。

本合同不授权真实 COMMIT、普通自动业务任务或 `SYSTEM_EMERGENCY`。
