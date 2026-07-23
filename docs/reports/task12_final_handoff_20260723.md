# 任务12最终交接报告：多商品正式改价队列

- 交接日期：2026-07-23
- 平台：蚂蚁花团供应商微信小程序
- ShadowBot 应用：`test2`
- 合同版本：v4
- 文档状态：待审查

> 本报告冻结任务12首次最终交接时的 v11 状态，不覆盖改写。审查问题修复后的
> v12 合同、最终自动化回归、正常 COMMIT 和受控 UNKNOWN→RECONCILE 证据，
> 以[任务12审查问题接续修复报告](task12_review_remediation_20260723.md)为准。

## 1. 交接结论

任务12当前实现已经不再是原计划中的“单商品垂直切片逐步扩展”，而是形成了任务中心到真实平台的单平台、多商品、单次投递、严格串行 COMMIT 闭环。本交接以当前代码、SQLite v11 合同、真实小程序结果和运行归档为准，不要求与原《任务12交接与实施计划》的章节逐项对齐。

当前已经具备：

- 任务中心正式改价任务读取。
- 内部 SKU 到页面“商品名称 + 等级”的唯一映射。
- 一个批次只创建并投递一个完整 v4 COMMIT 请求。
- 写操作前整页刷新、全目标唯一匹配和全批次旧价一致性门禁。
- 按当前页面实时行号从上到下执行，可跳过中间非目标商品。
- 第 4 行及以后商品根据实际元素边界滚动。
- 每个商品提交后的独立重新定位和平台价格回读。
- 完整页面价格、库存、在售状态快照回传。
- 请求、结果、哈希、逐商品绑定、计数恒等式和数据库回写校验。
- UNKNOWN 后停止剩余商品并进入唯一 RECONCILE 的既有安全边界。
- `test2` 长驻 Worker 生命周期记录和 `stop.signal → STOPPED → 关闭.flow` 收尾。

任务12的开发内容已形成可审查交接状态，但本报告不修改任务状态。只有审查方检查代码、文档、真实结果和待办边界后，才应决定是否将任务12标记为完成。

## 2. 当前正式业务口径

### 2.1 输入

批次顶层指定规范平台。每个商品的正式业务输入只需要：

```json
{
  "internal_sku": "AISHA-B-60-Z",
  "expected_old_price": "26.80",
  "target_price": "26.90"
}
```

任务中心还保留 `task_id`、`action_type=update_price`、任务状态、优先级和审计时间等运行态字段。页面位置、预先排序、`listing_status_id`、快照版本、READ_ONLY attempt、`read_batch_id` 和 FILL_PREVIEW 结果都不是正式 COMMIT 输入。

### 2.2 商品身份

```text
任务 internal_sku
→ data/samples/products.xlsx 中唯一商品记录
→ expected_product_name + expected_grade
→ 页面结构化“商品名称 + 等级”唯一匹配
```

平台页面没有可直接读取的 SKU。`internal_sku` 是 PRA 内部身份；ShadowBot 不以 SKU 或固定排列顺序点击页面。页面当前业务键为：

```text
(platform_name, variety, grade)
```

如果将来出现同一平台、同名、同等级但不同规格的商品，必须扩展稳定的页面身份字段，不能用视觉近似或行号代替。

### 2.3 授权

- 开发和实机验收：创建可执行队列前展示固定批次清单并取得明确授权。
- 正式运行：`execution_profile=production` 以发布前再次校验通过的 `pending update_price` 任务为执行权威，不再要求 Codex 对话中的临时用户确认。
- 两种模式都必须保留合同哈希、旧价核验、幂等和回读门禁；production 任务字段在发布前发生变化时必须停止。

## 3. 正式执行链路

```text
读取完整 pending update_price 任务列表
→ 校验同一平台和任务字段
→ SKU 映射为页面名称 + 等级
→ 检查批次内页面身份无重复
→ 生成一个 v4 COMMIT manifest/request
→ 原子发布一次队列请求
→ Worker 刷新当前“上架中”页面
→ 结构化读取当前页面所有商品
→ 匹配全部目标并确认均唯一存在
→ 比较全部目标的页面价格与 expected_old_price
→ 任一不一致：OLD_PRICE_CHANGED，全批次 NOT_STARTED
→ 全部一致：按实时页面行号从上到下生成执行顺序
→ 逐商品严格串行提交
→ 每项独立重新定位和回读
→ 返回逐商品结果 + 完整页面状态快照
→ Result Importer 校验合同并更新 SQLite
```

Worker 只在写操作前做一次全页预扫描。逐商品循环不再重复登录、导航和全页面枚举；但每次提交后的平台结果仍独立读取。

## 4. 代码和数据结构交接

### 4.1 PRA 批次合同和账本

- `app/services/shadowbot_commit_batch.py`：v4 manifest/request、字段规范化、批次和逐商品哈希。
- `app/services/shadowbot_commit_pipeline.py`：读取任务、SKU 映射、准备/发布批次、批次账本更新。
- `app/shadowbot_contract_primitives.py`：跨层共享的合同基础函数。
- `app/platform_identity.py`：平台名称规范化。
- `app/listing_identity.py`：页面商品身份规范化。
- `app/listing_status_policy.py`：平台状态写入策略。

SQLite 当前 schema 为 v11。与任务12直接相关的新增结构为：

- v7-v9：`listing_status` 及“平台 + 品种 + 等级”唯一身份。
- v10：`tasks.expected_old_price` 成为一等结构化字段。
- v11：`shadowbot_commit_batches` 和 `shadowbot_commit_batch_items`，保存单次请求的批次及逐商品状态。

### 4.2 队列、执行和导入

- `app/services/shadowbot_queue.py`：原子请求发布、结果导入、校验和归档。
- `app/services/shadowbot_executor.py`：既有 operation/attempt、副作用和 RECONCILE 边界。
- `app/services/shadowbot_product_read.py`：READ_ONLY v2 合同和页面快照处理。
- `scripts/run_shadowbot_commit_batch.py`：从任务中心准备或发布正式批次。
- `shadowbot/test2/shadowbot_queue_worker.py`：长驻队列 Worker。
- `shadowbot/test2/vertical_slice_read_price.py`：当前平台结构化读取、批次预扫描、视口恢复、COMMIT 和回读。

### 4.3 主数据和状态

- `data/samples/products.xlsx`：正式 SKU→商品名称/等级映射来源。
- SQLite `tasks`：任务中心运行态事实。
- SQLite `listing_status`：最近一次已确认的平台价格、库存和在售状态快照。
- `outputs/task12/`：仓库内批次 request、manifest、日志和部分人工报告。
- `D:\PRA_Runtime\shadowbot_queue\archive\`：真实运行请求、结果、phase 和校验归档。

## 5. 最终实机证据

完整 Run ID、结果文件 SHA-256 和核对步骤见[任务12实机证据索引](task12_evidence_index_20260723.md)。

### 5.1 已验证动作基线

`BATCH-T12-OPTIMIZED-COMMIT-20260721-02` 是后续开发锁定的成功动作基线。对应四个叶执行记录：

| Run ID | 商品 | 价格 | 结果 |
| --- | --- | ---: | --- |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-01` | 卡布奇诺 B级 | 24.20 → 24.30 | `VERIFIED` |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-02` | 艾莎 B级 | 9.20 → 9.30 | `VERIFIED` |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-03` | 卡布奇诺 C级 | 26.90 → 27.00 | `VERIFIED` |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-04` | 艾莎 D级 | 15.10 → 15.20 | `VERIFIED` |

后续没有另写一套弹窗提交动作，而是在该基线上增加 v4 单次请求、全页预扫描、动态编排和批次导入。

### 5.2 v4 正式成功样本

`BATCH-T12-FORMAL-COMMIT-20260722-02` / `ATTEMPT-c8976769bbcf471b`：

- 一次投递两个商品。
- 页面实时行号为 1 和 4，跳过中间两个非目标商品。
- 卡布奇诺 B级 `46.30 → 46.40`。
- 艾莎 B级 `26.30 → 26.40`。
- 两项均独立回读为目标价。
- 结果为 2/2 `VERIFIED`，任务中心和 `listing_status` 回写成功。
- 人工报告：[BATCH-T12-FORMAL-COMMIT-20260722-02.report.md](../../outputs/task12/BATCH-T12-FORMAL-COMMIT-20260722-02.report.md)。

### 5.3 全批次旧价阻断样本

`BATCH-T12-FORMAL-COMMIT-20260722-03` / `ATTEMPT-0a4da7c0645f4f85`：

- 任务包含 5 个商品。
- 预扫描发现至少两个任务旧价与页面不一致。
- 结果为 `FAILED/OLD_PRICE_CHANGED/NOT_STARTED`。
- 提交次数为 0，5 项均未执行。
- 同时成功回传页面 5 个商品的价格、库存和在售状态。
- 人工报告：[BATCH-T12-FORMAL-COMMIT-20260722-03.report.md](../../outputs/task12/BATCH-T12-FORMAL-COMMIT-20260722-03.report.md)。

### 5.4 清理后的回归样本

`BATCH-T12-POST-CLEANUP-VALIDATION-20260722-01` / `ATTEMPT-b6c493e6eac54972`：

- 核心代码去重后 4/4 `VERIFIED`。
- 证明清理没有破坏成功动作链路。
- 总用时约 69 秒。

### 5.5 非默认视口失败与修复

`BATCH-T12-FAST-PATH-VALIDATION-20260722-01` / `ATTEMPT-354799e7788a4b92`：

- 页面保留上一批次滚动位置，首目标位于视口上方。
- 快速路径第一次点击失败。
- 结果为 `FAILED/ELEMENT_NOT_FOUND/NOT_STARTED`，没有平台副作用。

修复后 `BATCH-T12-FAST-PATH-VALIDATION-20260723-02` / `ATTEMPT-a023675861d24d34`：

- 批次开始前按实际元素边界恢复上方视口。
- 4/4 `VERIFIED`。
- 冷态预扫描为 50.750 秒，总用时 104.984 秒。

### 5.6 最终暖态性能样本

`BATCH-T12-WARM-FAST-PATH-20260723-01` / `ATTEMPT-52710408e5e1488a`：

| 顺序 | 商品 | 价格 | 单项耗时 | 结果 |
| ---: | --- | ---: | ---: | --- |
| 1 | 卡布奇诺 E级 | 12.40 → 12.50 | 9.297 秒 | `VERIFIED` |
| 2 | 艾莎 D级 | 16.90 → 17.00 | 9.328 秒 | `VERIFIED` |
| 3 | 艾莎 C级 | 13.20 → 13.30 | 9.625 秒 | `VERIFIED` |
| 4 | 艾莎 B级 | 26.80 → 26.90 | 11.766 秒 | `VERIFIED` |

- 预扫描：11.078 秒。
- 总用时：51.094 秒。
- `prepared_window_reused=true`。
- 每项均跳过重复全页面枚举。
- 相比此前约 68 秒的常规四商品批次，缩短约 24.9%。

该 Run 的结果文件 SHA-256 为 `0c4943a005ed18391b2819bd5f775ec6a3b44dde9ae06485a8129306a5feb25c`。

### 5.7 READ_ONLY 最终样本

`ATTEMPT-PLATFORM-ENDMARKER-READONLY-20260722-01` / `READ-BATCH-PLATFORM-ENDMARKER-20260722-01`：

- 4/4 `READ_COMPLETED`。
- 1 页、1 次身份扫描、0 次滚动。
- 结束原因为 `INDEX_SEQUENCE_COMPLETE_WITH_END_MARKER`。
- 总用时 27.445 秒。
- `side_effect_state=NOT_STARTED`。

早期最快样本 `ATTEMPT-T12-LOGIN-FAST-READ-20260721-01` / `READ-BATCH-T12-LOGIN-FAST-20260721-01` 为 16.228 秒，但使用目标字段读取策略；当前样本读取完整页面快照，二者范围不同。

## 6. 回归测试

交接文档生成前重新执行：

```text
tests/test_shadowbot_commit_batch.py
tests/test_shadowbot_commit_pipeline.py
tests/test_shadowbot_commit_success_baseline.py
tests/test_shadowbot_commit_v4_orchestration.py
tests/test_shadowbot_readonly_snapshot_baseline.py
tests/test_shadowbot_product_read.py
tests/test_listing_status.py
```

最终文档整理后的复测结果：`62 passed in 10.72s`。

同时运行 `python scripts/run_system_smoke_tests.py --temporary-db`，结果为 `16` 项通过、`0` 项失败；输出确认 schema 精确为 v11，任务旧价、平台身份、库存观测、Outbox 和 COMMIT 批次结构完整。

该结果是代码层合同和分支回归，不替代第 5 节的真实小程序证据。

## 7. Worker 最终运行状态

本次交接前，用户要求停止 `test2`，当前记录为：

| 项目 | 值 |
| --- | --- |
| 生命周期文件 | `D:\PRA_Runtime\shadowbot_queue\control\shadowbot_lifecycle_state.json` |
| `recorded_state` | `STOPPED` |
| 最后执行 ID | `ATTEMPT-52710408e5e1488a` |
| 窗口记录 | `APP_LIST` |
| `inbox/` | 空 |
| `working/` | 空 |
| `results/` | 空 |
| `stop.signal` | 不存在 |

该状态说明本轮已正常收尾，不表示以后无需按运行手册重新核对实际状态。

## 8. 已知限制

1. 只验证蚂蚁花团供应商单平台、单小程序窗口和单 Worker 严格串行执行。
2. 上架、下架和“不再上架中”页面属于任务13，不在任务12中补做。
3. 页面没有 SKU，当前唯一身份只有商品名称和等级；未来出现重复身份时必须扩展稳定规格字段。
4. 暖态四商品为 51.094 秒，冷态成功样本为 104.984 秒；冷启动仍有优化空间。
5. 长期告警、证据保留策略、磁盘清理和服务账号运维尚未形成生产级闭环。
6. 当前不是第二平台模板，也不允许把平台元素细节写入公共业务服务。
7. 当前文档和代码尚未形成最终 GitHub PR；工作区包含任务12期间的多项未提交修改，需按交接范围审查后统一提交。

## 9. 审查步骤

建议审查方按以下顺序检查：

1. 阅读本报告和[任务12开发报告](task12_development_report_20260723.md)。
2. 对照 `app/services/shadowbot_commit_batch.py` 和 `shadowbot_commit_batches/items` 表确认 v4 单次请求合同。
3. 对照 `tests/test_shadowbot_commit_success_baseline.py` 确认成功动作基线未被替换。
4. 对照 `tests/test_shadowbot_commit_v4_orchestration.py` 检查全页预扫描、动态排序和视口恢复。
5. 抽查第 5 节每个 Run 的 request/result/phase 和 SHA 文件。
6. 核对任务中心、批次账本、逐商品账本和 `listing_status` 回读。
7. 检查当前文档入口、归档说明和链接，不再把旧单商品规范当作当前计划。
8. 审查通过后再修改任务12状态，并整理统一 GitHub PR。

## 10. 后续可直接复用的内容

后续任务应优先复用而不是重写：

- 文件队列的原子发布、checksum、phase、Importer、Watchdog 和归档。
- operation/attempt/side-effect 状态机及 UNKNOWN→RECONCILE。
- v4 批次 manifest、逐商品哈希、计数恒等式和批次账本。
- SKU→页面身份映射及平台/等级规范化。
- 当前平台的结构化等差索引枚举、完整页面快照和结束标记。
- 全目标唯一匹配、旧价总门禁、页面实时顺序编排。
- 视口实际边界检查和安全滚动。
- 提交后独立重新定位回读。
- `listing_status` 新增/更新和库存观测新鲜度保护。
- 长驻 `test2` 生命周期记录及正常/异常停止路径。
- 成功基线测试、负向门禁测试和人工可读报告原则。

更具体的复用边界见[任务12可复用资产清单](../task12_reusable_assets.md)。
