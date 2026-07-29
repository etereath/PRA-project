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
8 条平台登记和 12 条从任务 13 只读基线迁入的蚂蚁平台候选映射。12 条候选记录
均把候选值写入 `candidate_internal_sku` 并保持 `DISABLED`；只有运营逐条复核后
才能把确认值迁入 `internal_sku` 并改为 `VERIFIED`。

编译结果：

```text
source_workbook_sha256 =
a47b7c298ad93a6e999694a02ec14d9a5636f982bd6f3198bdcfefd0cf3bc41e

mapping_version =
64aaf787eace33d959823c0e91c4037da47a960ddd666e72ca918723fc92567d
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
- 候选 SKU 与已验证内部 SKU 隔离；`DISABLED` 候选绝不参与解析。
- 原子写入不可变 JSON。

只有唯一有效的 `VERIFIED` 记录会返回 `internal_sku`。状态冲突或多个有效 SKU
一律降为 `AMBIGUOUS`。

### 1.3 扫描输入与 v14 导入

`app/services/product_observation.py` 已实现：

- 严格的 `product-observation-input-1.0` JSON 输入边界。
- `ONLINE_PULSE` 只接受 `observed_online=true` 的正观察。
- `LISTING_STATUS_SCAN` 接受在线与待上架两类页面观察。
- `scan_type` 与页面范围精确绑定；商品结果只接受类型、平台和时间策略一致的子
  run，新事实插入要求 `RUNNING`，终态仅允许返回已经存在的幂等事实。
- 任务 13 v5 完整双页快照到 v14 商品观察的适配器。
- 每项观察独立调用 `OperationalTimeService`，不以批次时间代替逐项归属。
- 每项时间必须位于批次区间，价格必须为有限、规范化正数，已接受或部分接受项
  的证据必须符合 `sha256:<64 位小写十六进制>`。
- 内容哈希排除传输批次/run ID、包含映射版本、稳定排序商品项并规范化页面顺序；
  同一 run 内跨批次 ID 的同内容重试返回该 run 最早的规范批次，不重复落库。
- 导入事务先校验 run 的类型、平台和时间策略，再查询同 ID 或同 run 同内容事实；
  终态 run 可幂等返回既有事实，只有准备插入新内容时才要求 `RUNNING`。
- 不同 run 的相同业务内容分别落批次，使调度和运营查询可以区分“未产生结果”和
  “已接收结果”，不修改已冻结的 Runtime Schema v14。
- `ACCEPTED / PARTIAL / UNAVAILABLE / FAILED` 的完整性、结束标记和错误字段执行
  明确状态矩阵；显式停用的内置平台不会被默认列表重新补回。
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
- `DISABLED` 候选 SKU 与 WEB 平台登记/商品行隔离。
- 重叠生效区间冲突。
- 不可变 JSON 与稳定哈希。
- ONLINE_PULSE 缺席不产生负观察。
- 同批同内容幂等、同批不同内容拒绝、同一 run 跨批同内容不重复累加。
- 跨 run 相同内容分别保留可查询批次；同 run 并发重试只生成一份事实 items。
- 终态 run 的原 batch ID 和新 batch ID 同内容重放均幂等成功；不同内容被拒绝，
  且幂等重放仍校验 run 静态身份。
- 四种批次状态的合法/非法组合和页面顺序规范化。
- run 类型/状态/平台/时间策略和精确页面范围绑定。
- 批次时间区间、有限规范化正价格和证据哈希格式校验。
- 18:00 和 20:00 逐项双日期边界。
- 任务 13 双页快照生成两类 v14 观察。
- 中文 XLSX/JSON 显式回读与内容抽查。

本地结果：

```text
pytest: 790 passed, 3 skipped, 97 subtests passed
system smoke: 16 passed, 0 failed
compileall: PASS
wheel/sdist build: PASS
package allowlist: PASS
secret scan: PASS
repository-external wheel install: PASS
Windows ShadowBot fixture/hash gates: PASS
```

## 4. 后续

本分支已完成本轮复审修复，后续只继续等待最终复审和 PR 收口。`ONLINE_PULSE` 的 ShadowBot 页面采集
需要在独立批次中先确认 Worker 停止和宿主 hash，再按本报告冻结的 JSON 边界接入；
调度频率、租约和补跑由 13.5-3 实现。
