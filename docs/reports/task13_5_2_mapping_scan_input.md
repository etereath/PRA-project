# 任务 13.5-2：商品映射与扫描输入首批实施报告

- 实施日期：2026-07-29
- 分支：`codex/task13-5-2-mapping-scan-input`
- 状态：核心输入与持久化本地验收完成，等待 PR/CI；ShadowBot 宿主采集尚未部署
- 合同：
  [商品映射与扫描输入合同](../plans/task13_5_2_mapping_and_scan_input_contract.md)

## 1. 已实现范围

### 1.1 运营映射源

`data/samples/platform_mappings.xlsx` 保持为运营权威源，并扩展为两类记录：

- `PLATFORM`：继续供现有 WEB 业务输入页生成平台选项。
- `PRODUCT`：按“平台 + 规范化商品名 + 等级”解析内部 SKU。

商品记录严格使用 `VERIFIED / UNMAPPED / AMBIGUOUS / DISABLED`。当前样例包含
8 条平台登记和 12 条从任务 13 只读基线迁入的蚂蚁平台商品映射。

编译结果：

```text
source_workbook_sha256 =
d66b4c1ed13fbca72e1476ccf0d452f4f93b5d425a538b09a6a40e7e9b85e3ac

mapping_version =
f4e2cc039bc4ab09722e472d3c181b03dee04c760afa611169288dfb5e7e03d5
```

`scripts/compile_product_mappings.py` 可重复生成
`data/samples/platform_mappings.immutable.json`。不可变 JSON 使用 UTF-8，
内容包含源 XLSX SHA-256；`mapping_version` 是完整 JSON 字节的 SHA-256。

### 1.2 映射合同

`app/services/product_mapping.py` 已实现：

- NFKC、空白折叠和大小写折叠规范化。
- 可选 UTC 生效区间。
- 同一身份、重叠区间映射到不同 SKU 时拒绝加载。
- 四种冻结映射状态的解析和决议。
- 平台登记行与商品映射行隔离。
- 原子写入不可变 JSON。

只有唯一有效的 `VERIFIED` 记录会返回 `internal_sku`。状态冲突或多个有效 SKU
一律降为 `AMBIGUOUS`。

### 1.3 扫描输入与 v14 导入

`app/services/product_observation.py` 已实现：

- 严格的 `product-observation-input-1.0` JSON 输入边界。
- `ONLINE_PULSE` 只接受 `observed_online=true` 的正观察。
- `LISTING_STATUS_SCAN` 接受在线与待上架两类页面观察。
- 任务 13 v5 完整双页快照到 v14 商品观察的适配器。
- 每项观察独立调用 `OperationalTimeService`，不以批次时间代替逐项归属。
- 内容哈希幂等；哈希包含映射版本。
- 单事务追加 `product_observation_batches/items`。

导入器不调用任务 13 的 `listing_status` 投影，也不根据脉冲扫描中缺失的商品生成
离线事实。完整双页快照的权威状态投影仍由原任务 13 Importer 负责。

## 2. 兼容与安全

- 原 WEB 新增平台和价格规则平台选项测试保持通过。
- 原任务 13 双页扫描、异常和投影测试保持通过。
- 未修改 `shadowbot/test2`，未启动 Worker，未向真实平台发布请求。
- 未迁移或写入真实 Runtime DB；所有数据库测试使用临时 SQLite。
- 13.5-3 的定时调度、租约和父子 run 编排未在本批提前实现。
- 13.5-4 的订单扫描能力与结果表未塞入商品扫描合同。

## 3. 验收

本批覆盖：

- 四种映射状态。
- 重叠生效区间冲突。
- 不可变 JSON 与稳定哈希。
- ONLINE_PULSE 缺席不产生负观察。
- 同批同内容幂等、同批不同内容拒绝。
- 18:00 和 20:00 逐项双日期边界。
- 任务 13 双页快照生成两类 v14 观察。
- 中文 XLSX/JSON 显式回读与内容抽查。

本地结果：

```text
pytest: 757 passed, 3 skipped, 97 subtests passed
system smoke: 16 passed, 0 failed
compileall: PASS
wheel/sdist build: PASS
package allowlist: PASS
secret scan: PASS
repository-external wheel install: PASS
```

## 4. 后续

本分支后续只继续完成质量门禁和 PR 收口。`ONLINE_PULSE` 的 ShadowBot 页面采集
需要在独立批次中先确认 Worker 停止和宿主 hash，再按本报告冻结的 JSON 边界接入；
调度频率、租约和补跑由 13.5-3 实现。
