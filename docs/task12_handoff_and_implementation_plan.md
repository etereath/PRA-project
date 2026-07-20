# PRA 任务12交接与实施计划：单平台多商品串行改价

状态：本地规格已完成一致性修订，实施已开始；任务完成状态保持不变，等待审查方验收后再修改。

- 任务 ID：12
- 优先级：P0
- 阶段：第五阶段
- 依赖：任务11（PR #13 已合并）
- 实施基线：`main@340f363127e58a2563dcb30027f12813bc6e518b`；正式实施始终以分支创建时的最新 `origin/main` 为准
- 实施分支：`codex/pra-task-12-multi-product-price-update`
- 原始来源：Google Docs 修订版 9，最后修改时间 2026-07-20 16:11（北京时间）
- 本地规则：实施、报告和交接均通过 GitHub PR；除非用户另行明确要求，不写回 Google Drive

## 1. 任务定位

任务12把任务11的单平台多商品结构化读取扩展为同一平台上的多商品严格串行改价。批次只负责编排和汇总，不是审批边界或副作用边界。每个商品必须继续拥有独立的 Runtime Task、ReviewTask、operation、execution attempt、批准载荷哈希、最新旧价、提交后独立读回和 UNKNOWN/RECONCILE 账本。

不得把现有单商品 COMMIT 简单放入循环，不得使用批次级批准替代逐商品批准，也不得把多个商品合并为一个副作用账本。

## 2. 范围与非目标

范围：

1. 同一平台、多个已批准商品的严格串行 FILL_PREVIEW 或 COMMIT。
2. 批次和逐商品状态持久化，支持暂停、取消、重启恢复和部分完成。
3. 每项执行独立的最新 READ_ONLY、身份和旧价确认、审批重新校验、FILL_PREVIEW/COMMIT、刷新和独立 READ_ONLY 复核。
4. 逐商品 operation、attempt、phase、错误码、哈希和数据库记录独立。
5. 批次级机器可读结果、人工可读报告和通用验收器。
6. 保持旧单商品路径和任务11 contract_version=2 兼容。

非目标：

1. 不实现第二平台、跨平台混合批次、上架下架或 OFFLINE 对账。
2. 不建立价格历史事实表，不接入 AI 自动定价、自动批准或自动发起 COMMIT。
3. 不提供“批准全部”“一键重试全部”或无人值守真实写操作。
4. 截图、OCR 和视觉识别不是生产读取、提交或验收前提。

## 3. 必须保持的安全不变量

1. 每个副作用必须归属一个明确的 `operation_id` 和 `execution_attempt_id`。
2. COMMIT 必须具有逐商品已批准 ReviewTask、批准载荷哈希及允许 COMMIT 的授权范围。
3. FILL_PREVIEW 授权不能升级为 COMMIT 授权。
4. 每项执行前必须从可访问性树重新读取标准化商品名称、等级、价格、库存和 ONLINE 页面上下文。
5. `fresh_old_price != approved_expected_old_price` 时以 `OLD_PRICE_CHANGED` 在提交意图前失败关闭。
6. 页面身份无法唯一确认时，不得读取其他行、填写价格或点击提交。
7. `SUBMIT_INTENT_RECORDED` 后不得把停止、超时或进程退出归类为未执行；必须确认结果或进入 UNKNOWN。
8. `SUBMIT_CLICKED` 后不得自动再次 COMMIT；只能通过 `ShadowBotExecutor.ensure_reconcile_attempt(...)` 创建唯一只读 RECONCILE。
9. `capture_evidence` 默认 `false`；截图失败不得改变业务终态。
10. 报告、Importer 和验收器必须从逐商品记录独立重算身份集合、状态计数和字段类型。

## 4. 任务11来源快照的真实字段映射

任务12不得假设任务11结果存在 `page_context` 或 `account_context_hash`。当前已验证的任务11 contract_version=2 实机文件使用以下真实结构：

- 请求：`read_batch_id`、`platform_name`、`applet_uri`、`window_title`、`products`、`instruction_hash`、`execution_attempt_id`、`task_id`。
- 结果：`read_batch_id`、`platform_name`、`product_snapshots`、`product_list_refreshes`、`request_file_sha256`、`instruction_hash`、`started_at`、`ended_at` 和顶层计数。
- 每个 `product_snapshots[]`：`item_id`、`product_name`、`grade`、`price`、`inventory`、`listing_status`、`observed_at`、`row_identity`、`locator_summary`、`platform`、`platform_sku` 和逐项错误。
- sidecar/归档：请求、结果和 phase 的文件 SHA-256、归档路径及 Result Importer 状态。

来源绑定必须基于已经归档并校验的“请求 + 结果 + sidecar”联合构造，不得只依赖结果顶层字段：

```text
source_read_batch_id = result.read_batch_id
source_item_id = result.product_snapshots[i].item_id
source_platform = 由可信核心/adapter registry 显式提供的稳定平台键（当前为 ant_flower_wechat）
source_platform_name = request.platform_name，并与 request.products[].platform、result.platform_name 和 snapshot.platform 交叉核对
source_applet_identity_sha256 = SHA-256(UTF-8(normalized request.applet_uri))
source_window_title = request.window_title
source_listing_status_filter = "ONLINE"，且目标 snapshot.listing_status 必须为 ONLINE
source_observed_at = 所有被选中 snapshot.observed_at 的最早值
```

任务11 v2 当前把 `products[].platform` 和 `snapshot.platform` 也写成中文展示名，因此不能从 v2 文件自动推导稳定 adapter key。来源绑定函数必须由可信核心显式传入稳定平台键和允许的 v2 展示名集合，并验证所有观察值一致；不得在公共契约函数中写死中文平台名。

`account_context_hash` 当前没有可信字段来源，contract_version=3 首版不得要求或伪造该值。未来若核心凭据层提供稳定、非敏感的账号身份哈希，必须单独版本化后再加入。

`business_snapshot_view` 只包含：contract version、read batch、platform、applet identity hash、window title、ONLINE 过滤器、顶层计数，以及按任务11原顺序排列的逐项 `item_id/product_name/grade/price/inventory/listing_status/observed_at`。排除截图、证据路径、调试文本、绝对路径和凭据。

`source_snapshot_sha256 = SHA-256(RFC 8785 JCS(business_snapshot_view))`。

`source_page_context_sha256` 对以下精确对象计算：

```json
{
  "v": 1,
  "platform": "<trusted stable platform key>",
  "platform_name": "<normalized platform_name>",
  "applet_identity_sha256": "sha256:<hex>",
  "window_title": "<normalized request.window_title>",
  "page_name": "商品管理/上架中",
  "listing_status_filter": "ONLINE",
  "read_contract_version": 2
}
```

任务11来源快照在批次启动预检时默认不得超过 300 秒。当前 5 商品实机读取约 51–55 秒，但 20 商品和人工审批的时间预算尚未验证；在完成容量测试前，COMMIT 批次默认上限保持 5 项。

## 5. contract_version=3 请求契约

任务12新增独立写批次契约，不放宽任务11的 contract_version=2。

顶层至少包含：

- `contract_version=3`
- `batch_id`
- `platform`
- `batch_type=SERIAL_PRICE_UPDATE`
- `execution_mode=FILL_PREVIEW|COMMIT`
- `stop_policy`
- `capture_evidence=false`
- `source_read_batch_id`
- `source_snapshot_sha256`
- `source_page_context_sha256`
- `source_observed_at`
- `source_snapshot_max_age_seconds=300`
- `items[]`

每项至少包含：`item_id`、`ordinal`、`source_item_id`、`task_id`、`review_task_id`、`operation_id`、`approved_payload_hash`、可空 `platform_sku`、`expected_product_name`、`expected_grade`、`approved_expected_old_price` 和 `target_price`。

规则：

1. 批次内不得混合执行模式或平台。
2. `items` 非空，默认上限 5，硬上限 20；真实 COMMIT 在容量验证前仍限制为 5。
3. `ordinal` 从 1 连续递增，数组顺序必须与 ordinal 一致。
4. `item_id/source_item_id/task_id/review_task_id/operation_id` 必须非空且绑定同一商品、平台、来源快照和批准载荷。
5. 同一批次不得重复 `write_identity_key` 或 `page_identity_key`。
6. `platform_sku` 只是外部主数据和写锁引用，不是页面证据。
7. 价格必须是非空、有限、非负的十进制定点字符串；拒绝 int、float、NaN、Infinity 和科学计数法。
8. 同 `batch_id`、同规范化摘要为幂等重放；同 ID 不同摘要返回 `PRICE_BATCH_ID_CONFLICT`。
9. 请求最大 256 KiB，结果最大 4 MiB；诊断文件不得嵌入 JSON。

## 6. 确定性身份与哈希

名称和等级必须调用任务11冻结的 `task11-v1` 标准化函数。

```text
page_identity_key = SHA-256(JCS({v, platform, normalized_product_name, normalized_grade}))
write_identity_key = SHA-256(JCS({v, platform, platform_sku|null, page_identity_key}))
```

只要活动 operation 的 `write_identity_key` 或 `page_identity_key` 任一相同，即构成写冲突。

`normalized_request_digest` 对除 `batch_id`、生成时间和传输路径外的所有执行相关字段计算。必须包含来源绑定和按原顺序排列的完整 items；商品重排或 ordinal 改变必须改变摘要。

所有 canonical JSON 统一使用 RFC 8785 JCS 的 UTF-8 输出，并提供固定测试向量。不得用普通 `json.dumps` 默认格式代替。

## 7. 冻结状态、计数和错误码

`WRITE_LOCK_STATES` 冻结为：

```text
PENDING, STARTING, RUNNING, SUBMIT_INTENT_RECORDED,
SUBMIT_CLICKED, UNKNOWN, NEEDS_RECONCILIATION
```

批次状态：`PENDING/RUNNING/PAUSED/COMPLETED/PARTIAL/FAILED/CANCELLED`。

项目状态分为：待处理 `PENDING/READY`；执行中 `RUNNING`；互斥处理结果 `PREVIEWED/VERIFIED/FAILED/SKIPPED/CANCELLED/NEEDS_RECONCILIATION`。`RECONCILED` 不是终态；对账结论记录在 `reconcile_attempt_id/reconciliation_outcome/reconciled_at`。

计数恒等式：

```text
processed_count = previewed_count + verified_count + failed_count
                + skipped_count + cancelled_count + needs_reconciliation_count
total_count = pending_count + ready_count + running_count + processed_count
```

`reconciled_item_count` 是可与 VERIFIED/FAILED 重叠的诊断计数，不参与恒等式。

contract_version=3 的冻结错误码必须至少包括：

```text
PRICE_BATCH_ID_CONFLICT
UNSUPPORTED_CONTRACT_VERSION
SINGLE_PLATFORM_REQUIRED
UNSUPPORTED_EXECUTION_MODE
EMPTY_BATCH
BATCH_CAPACITY_EXCEEDED
REQUEST_TOO_LARGE
RESULT_TOO_LARGE
DUPLICATE_ITEM_ID
DUPLICATE_OPERATION_ID
DUPLICATE_WRITE_IDENTITY
DUPLICATE_PAGE_IDENTITY
INVALID_ORDINAL
INVALID_PRICE_TYPE
TARGET_PRICE_INVALID
SOURCE_REQUEST_NOT_FOUND
SOURCE_RESULT_NOT_FOUND
SOURCE_SIDECAR_NOT_FOUND
SOURCE_ARCHIVE_NOT_VERIFIED
SOURCE_BATCH_ID_MISMATCH
SOURCE_ITEM_NOT_FOUND
SOURCE_ITEM_IDENTITY_MISMATCH
SOURCE_SNAPSHOT_HASH_MISMATCH
SOURCE_PAGE_CONTEXT_HASH_MISMATCH
SOURCE_SNAPSHOT_EXPIRED
SOURCE_CONTEXT_UNAVAILABLE
NORMALIZED_REQUEST_DIGEST_MISMATCH
BATCH_ITEM_BINDING_MISMATCH
APPROVAL_REQUIRED
APPROVAL_EXPIRED
APPROVED_PAYLOAD_HASH_MISMATCH
APPROVAL_MODE_NOT_ALLOWED
WRITE_LOCK_CONFLICT
LIST_NOT_LOADED
PRODUCT_NOT_FOUND
AMBIGUOUS_MATCH
PRODUCT_IDENTITY_MISMATCH
CURRENT_PRICE_PARSE_FAILED
FRESH_READ_EXPIRED
OLD_PRICE_CHANGED
PREVIEW_INPUT_MISMATCH
SUBMIT_RESULT_UNKNOWN
WORKER_STOP_REQUESTED
BATCH_PAUSED
BATCH_CANCELLED
RESULT_CONTRACT_INVALID
RECONCILIATION_REQUIRED
RECONCILIATION_CONFLICT
```

未知错误码不得静默映射为普通失败。新增错误码必须先更新冻结集合和测试。

## 8. Runtime Schema v7

新增 `shadowbot_batches` 和 `shadowbot_batch_items`。批次表保存编排状态和可重算计数；项目表保存逐项绑定和编排投影。operation、attempt 和 side effect checkpoint 仍是副作用权威账本。

`shadowbot_batch_items` 必须包含 `reconcile_attempt_id`、`reconciliation_outcome` 和 `reconciled_at`。`run_ids` 不作为批次表中的权威 JSON 列；批次结果和报告从逐项 attempt/run 关系派生去重后的 run ID 集合。

为 `shadowbot_operations` 增加 `write_identity_key` 和 `page_identity_key`，并通过数据库约束或同一事务内的冲突检查应用完整 `WRITE_LOCK_STATES`。

Schema v7 必须覆盖 v6→v7 迁移、重复迁移、失败回滚、只读旧库检测、health、备份恢复和旧库升级测试。

## 9. 串行执行与副作用边界

每个项目执行：强制刷新 → fresh READ_ONLY → 身份/旧价确认 → ReviewTask 和哈希复核 → FILL_PREVIEW/COMMIT → 刷新 → 独立 READ_ONLY。

FILL_PREVIEW 从 fresh `observed_at` 到 `PREVIEW_STARTED` 不得超过 60 秒；COMMIT 到 `SUBMIT_INTENT_RECORDED` 不得超过 60 秒。超时必须重新读取并重新校验审批。

FILL_PREVIEW 只能填写、回读并取消；退出后价格必须仍为 `fresh_old_price`。COMMIT 必须先持久化 `SUBMIT_INTENT_RECORDED`，再点击提交并记录 `SUBMIT_CLICKED`，最后通过独立读取判断 VERIFIED、NOT_APPLIED 或 NEEDS_RECONCILIATION。

任何项目进入 NEEDS_RECONCILIATION 时批次默认 PAUSED；只能通过唯一只读 RECONCILE 归并为 VERIFIED、FAILED 或继续保持 NEEDS_RECONCILIATION。

## 10. stop.signal 的两类受控语义

### 10.1 日常开发收尾

只有在本轮结果已由 Result Importer 导入归档，且 `inbox/working/results` 均无活动文件时，操作人员才创建 `control/stop.signal`。Worker 写出 `heartbeat.status=STOPPED` 并从 `module1` 返回；主流程等待 1 秒后调用 `关闭.flow`。确认任务日志出现“执行结束”和用户可见残留窗口关闭后，立即删除信号并回读确认不存在。

这一路径用于结束常驻 Worker、准备下一轮测试，不是强制中断活动请求。

### 10.2 任务12受控安全停止验收

仅在明确的自动测试或实机验收场景中，允许向正在处理的批次注入 stop.signal：

1. `SUBMIT_INTENT_RECORDED` 之前，Worker 只能在已实现的安全检查点协作退出，结果为 `WORKER_STOP_REQUESTED/NOT_STARTED`；未开始项目不再领取。
2. `SUBMIT_INTENT_RECORDED` 之后，信号不得截断副作用区；当前项目必须完成读回或进入 NEEDS_RECONCILIATION，然后才停止领取下一项。
3. stop.signal 不保证打断卡死的 UI 或无响应进程。卡死场景保留证据后走 `Ctrl+Alt+Q` 异常停止路径。
4. 验收结果归档后仍必须执行 10.1 的 STOPPED、`关闭.flow` 和删除信号门禁。

## 11. Importer、Web、报告和验收器

Importer 必须识别 v3，同时保留旧单商品和任务11 v2；从 item results 重算计数并隔离任何绑定、哈希、字段类型或 ID 集合异常。Importer 不创建第二次 COMMIT。

Web 提供只读批次摘要、逐项明细和受审计的 pause/resume/cancel pending items；不得提供重试 COMMIT、跳过 UNKNOWN、修改批准哈希或批准全部。

人工可读报告必须以自然语言说明整体结果，并逐商品列出品种、等级、前价、目标价、后价、最终状态、错误、batch/run/task/review/operation/attempt ID；单独汇报 Importer、归档、数据库回读、关键哈希、计数恒等式和无跨商品副作用。报告不得只是 JSON 或代码抄录。

## 12. 实施顺序

1. 契约、错误码、任务11来源映射、JCS 哈希和纯逻辑测试。
2. Schema v7、batch/batch item repository、写锁和迁移测试。
3. 批次编排、逐项审批复核、停止/恢复和兼容路径。
4. Executor、Worker 与平台流程串行接入。
5. Importer、Web、报告和通用验收器。
6. 自动测试、受控实机验收、GitHub PR 交接。

可以在一个任务12分支和一个草稿 PR 中持续集成，但必须按以上阶段形成可审查提交；前一阶段测试未通过时不得进入下一阶段真实副作用测试。

## 13. 验收门槛

自动测试至少覆盖：v3 正常化和大小上限、重复身份和绑定冲突、价格类型、来源请求/结果/sidecar 映射、快照和上下文哈希、300 秒/60 秒时限、确定性 JCS 向量、批次幂等、Schema v7 迁移、完整写锁状态、审批模式、旧价漂移、FILL_PREVIEW 无副作用、COMMIT 独立读回、stop.signal 前后边界、UNKNOWN 唯一 RECONCILE、恢复不重复、计数重算、无截图成功、旧单商品和任务11 v2 回归。

实机验收要求：

1. 约 5–10 个 ONLINE 商品 READ_ONLY 回归。
2. 至少 3 个不同品种或等级的 FILL_PREVIEW。
3. 仅在项目负责人逐商品明确授权后，完成 2 个不同页面身份商品的受控 COMMIT。
4. 提供旧价漂移阻断、提交意图前安全停止、暂停/恢复及唯一 RECONCILE 样本；不得通过重复真实点击验证幂等。
5. 每轮完成请求、结果、phase、归档、数据库、验收 JSON 和人工可读 Markdown，并执行影刀收尾门禁。

## 14. 完成与交接规则

任务12的代码、测试、报告和文档通过 GitHub 草稿 PR 交接，不直接覆盖 Google Docs。PR 必须列出精确测试文件、定向测试、完整测试、Linux/Windows Core 状态和实机证据。

即使全部实施和实机证据已经生成，也不得由实施方自行把任务状态修改为“完成”。只有审查方审查合格后，才允许在单独的审查结论或后续 PR 中修改任务状态。

## 15. 2026-07-21 阶段5接续实施记录

本节以接续记录形式追加，不改写前述任务要求和任务状态。时间为北京时间墙钟近似值；token 是根据本轮上下文、推理和输出规模估算的模型消耗，误差可能达到约 30%，不等同于 API 计费账单。

| 步骤 | 工作内容 | 大概耗时 | 估算 token |
|---|---|---:|---:|
| 1 | 读取任务12计划及现有 Importer、Web、报告实现，确认阶段5差距 | 约 2 分钟 | 约 7.5k |
| 2 | 实现 v3 Result Importer 校验、Fresh READ/WRITE/RECONCILE 投影、计数回读及篡改隔离 | 约 6 分钟 | 约 10k |
| 3 | 实现批次 Web 摘要、逐商品详情、pause/resume/cancel pending 入口和持久审计 | 约 5 分钟 | 约 8k |
| 4 | 实现机器可读验收器、自然语言 Markdown 报告和报告生成 CLI | 约 5 分钟 | 约 9k |
| 5 | 定向/完整回归、严格字段类型检查、UTF-8 及临时 JSON/Markdown 生成自检 | 约 4 分钟 | 约 4k |
| 6 | 文档接续、提交范围复核和 GitHub 草稿 PR 交接 | 约 4 分钟 | 约 3k |
| **合计** | **本次阶段5接续开发** | **约 26 分钟** | **约 41.5k** |

本阶段新增两个冻结错误码扩展：`SUBMIT_NOT_APPLIED` 表示独立读回确认目标价未生效；`PLATFORM_EXECUTION_FAILED` 表示提交意图前的平台执行失败。平台原始错误码继续保存在错误说明中；提交意图后的未知平台错误统一进入 `SUBMIT_RESULT_UNKNOWN`，不得自动重试 COMMIT。

本阶段自动测试结果为 `509 passed, 3 skipped, 96 subtests passed`。源码、JSON 和 Markdown 编码门禁通过；本阶段未启动影刀、未执行真实 FILL_PREVIEW 或 COMMIT，因此这些结果只证明文件内容正确和离线业务逻辑通过，不构成影刀实机验收证据。任务状态保持不变，等待审查方审查。

## 16. 2026-07-21 阶段6接续实施记录：来源读取实机预检与修复

本节继续追加阶段6记录，不覆盖前述要求，也不修改任务状态。时间是北京时间墙钟近似值；token 是根据本轮上下文、推理和工具输出规模估算的模型消耗，误差可能达到约 30%，不等同于 API 计费账单。

| 步骤 | 工作内容 | 大概耗时 | 估算 token |
|---|---|---:|---:|
| 1 | 读取规则、计划和阶段6验收矩阵，检查队列、Worker、影刀应用目录及部署哈希 | 约 6 分钟 | 约 6k |
| 2 | 新增任务12来源 READ_ONLY 准备入口及数据库/队列绑定测试 | 约 4 分钟 | 约 8k |
| 3 | 同步影刀并执行第一轮 5 商品来源读取，定位“待上架被误认为上架中”的实机问题 | 约 7 分钟 | 约 5k |
| 4 | 增加显式 ONLINE 标签选择，执行第二轮并定位全树枚举超时和 v2 Watchdog 恢复契约缺口 | 约 10 分钟 | 约 12k |
| 5 | 改为精确 `acc-name=上架中` 选择器，修复 v2 恢复结果，完成第三轮实机复验与归档 | 约 8 分钟 | 约 8k |
| 6 | 定向、完整、Windows Core、Linux Core 和编码门禁 | 约 6 分钟 | 约 4k |
| 7 | 文档接续、差异复核及 GitHub PR 交接 | 约 4 分钟 | 约 3k |
| **合计** | **本次阶段6接续开发与实机预检** | **约 45 分钟** | **约 46k** |

### 16.1 新增受控来源读取入口

新增 `scripts/prepare_task12_source_read.py`。它从已验收的任务11 v2 请求读取商品身份模板，为每一轮生成新的 `read_batch_id/task_id/operation_id/execution_attempt_id/item_id`，创建独立 Runtime Task 和 attempt，并通过 `ShadowBotFileQueueRunner` 生成带校验和的 READ_ONLY 队列请求。入口固定 `capture_evidence=false`，不包含 FILL_PREVIEW 或 COMMIT 能力。

### 16.2 实机问题与修复

第一轮 `ATTEMPT-T12-SOURCE-20260720-203008` 证明小程序会保留上一次的商品状态标签。旧代码只点击“商品管理”，却假定进入后必然位于“上架中”；实际页面仍为“待上架”，导致两个卡布奇诺的价格定位读到“报名秒杀”，其余三个目标不存在。结果为 `0/5`，`side_effect_state=NOT_STARTED`，已由 Importer 归档。

第一版修复在刷新后枚举整个可访问性树查找“上架中”。第二轮 `ATTEMPT-T12-SOURCE-20260720-203917` 表明该方案会在大型小程序树中超过 300 秒。异常停止后又发现 Watchdog 生成的 v2 恢复结果没有继承 `contract_version/read_batch_id`，Importer 正确隔离为 `RESULT_CONTRACT_INVALID`。该 attempt 在数据库中保持 `START_UNKNOWN/NOT_STARTED`；原始 working 文件和恢复结果保存在 `D:\PRA_Runtime\task12_failed_attempts\ATTEMPT-T12-SOURCE-20260720-203917`，隔离文件保留在队列 quarantine。

最终修复使用精确 `StaticText` 选择器和 `acc-name=上架中`，不再枚举整棵树；结果新增 `active_listing_filter=ONLINE` 和选择时间。Watchdog 的 v2 恢复结果现在继承批次身份、平台、空快照和失败计数，可被 Importer 一次性接受并归档，不再形成“隔离后重复恢复”的循环。

### 16.3 第三轮实机结果与当前阻塞

第三轮正式来源读取：

- `read_batch_id`：`READ-BATCH-T12-SOURCE-20260720-205106`
- `execution_attempt_id`：`ATTEMPT-T12-SOURCE-20260720-205106`
- `shadowbot_run_id`：`filequeue:ATTEMPT-T12-SOURCE-20260720-205106`
- 请求 SHA-256：`ce61c0a811df3d9ace40cd373aab85a29cae91b6f9e6a8c6a334b9deb61cab77`
- 页面上下文：`active_listing_filter=ONLINE`
- 结果：`PARTIAL`，成功 `2/5`，失败 `3/5`，`side_effect_state=NOT_STARTED`
- 成功商品：艾莎 B级，价格 `9.00`、库存 `10`；艾莎 C级，价格 `8.00`、库存 `5`
- 当前 ONLINE 列表未找到：卡布奇诺 B级、卡布奇诺 C级、艾莎 D级
- 归档：`D:\PRA_Runtime\shadowbot_queue\archive\ATTEMPT-T12-SOURCE-20260720-205106`
- 数据库：`D:\PRA_Runtime\task12_acceptance_20260721.sqlite3`

当前平台目标范围只能确认两个 ONLINE 页面身份，因此尚不满足第13节“约 5–10 个 ONLINE 商品 READ_ONLY 回归”和“至少 3 个不同品种或等级的 FILL_PREVIEW”。本阶段未生成 FILL_PREVIEW 请求，更未生成或执行 COMMIT。需要项目负责人先确认是否把至少第三个目标商品恢复为 ONLINE，或提供新的 ONLINE 商品身份，才能继续正式 FILL_PREVIEW 验收。

### 16.4 自动验证和安全终态

- 定向测试：`37 passed`；精确 ONLINE 选择器相关组合测试：`16 passed`。
- 完整测试：`512 passed, 3 skipped, 96 subtests passed`。
- Windows Core 部署夹具：通过。
- Linux Core：`297 passed, 3 skipped, 6 deselected, 96 subtests passed`。
- 影刀规范文件与真实 `test2` 部署哈希一致，部署结构检查通过。
- 第三轮结果已由 Result Importer 导入归档；`inbox/working/results` 均为空。
- Worker 为 `STOPPED`，`stop.signal` 不存在，主流程已执行末端 `关闭.flow`。

以上分别证明代码和文件检查通过、自动逻辑测试通过、第三轮 READ_ONLY 实机页面上下文修复有效；不代表 FILL_PREVIEW 或 COMMIT 已验收。任务状态保持不变，等待后续实机覆盖和审查方审查。

## 17. 任务12流程修订计划：批次单次确认与既有单商品执行器复用（最高优先级）

本节记录项目负责人对任务12执行流程的最新决策。自本节起，与本节冲突的第9节、第12节、第13节及既有阶段记录均以本节为准；未冲突的逐商品审批、写锁、提交意图、独立回读、UNKNOWN、唯一 RECONCILE、报告和影刀收尾要求继续有效。本节只修改后续实施计划，不修改任务状态。

### 17.1 修订目标

任务12不再重新开发或重复验证一套 `READ_ONLY → fresh READ → FILL_PREVIEW` 垂直切片。任务11已经提供批量 READ_ONLY，既有单商品流程已经验证改价的可行性和稳定性；任务12的新增价值应限于严格串行队列、逐商品审批、结果汇总和恢复控制。

正常批次改价流程调整为：

```text
一轮批次 READ_ONLY
→ 冻结来源快照和逐商品审批数据
→ 队列严格串行领取一个商品
→ 直接复用既有单商品 COMMIT 执行器
→ 导入、归档并更新该商品终态
→ 无不确定状态时领取下一个商品
→ 批次报告和 Worker 收尾
```

以5个商品为例，页面执行从“一轮批次 READ_ONLY + 5轮独立 fresh READ + 5轮写流程”的约11次页面遍历，缩减为“一轮批次 READ_ONLY + 5轮单商品 COMMIT”的约6次页面遍历。效率优化不得削弱提交前旧价阻断、逐商品授权和提交后独立回读。

### 17.2 唯一批次 READ_ONLY

每一批次改价前只执行一轮任务11 contract v2 多商品 READ_ONLY，用于：

1. 确认全部目标商品唯一存在且处于 ONLINE。
2. 读取并冻结商品名、等级、平台 SKU（若平台可提供）、当前价格和库存。
3. 生成 `read_batch_id`、请求/结果哈希、业务快照哈希和页面上下文哈希。
4. 为逐商品审批和 COMMIT 提供 `approved_expected_old_price` 来源。

任一目标商品读取失败、身份歧义、状态不是 ONLINE、请求/结果绑定不一致或计数不成立时，不得创建可执行改价批次。该轮 READ_ONLY 结果由 Importer 一次性导入归档；不得为每个商品再生成独立的队列级 fresh READ 请求。

来源快照的300秒时限只约束“READ_ONLY 完成到批次创建/审批数据冻结”的入口。批次执行期间不因后续商品排队时间较长而重复整批 READ_ONLY；实时价格安全由每个 COMMIT 内部的旧价检查承担。

### 17.3 直接复用既有单商品 COMMIT

队列领取商品后，直接调用既有单商品 COMMIT 状态机，不创建独立 `FRESH_READ` stage 或 attempt。单商品执行器内部原有的以下步骤必须保留：

1. 刷新商品管理 ONLINE 列表并唯一定位审批商品。
2. 在打开价格弹窗和进入提交副作用区之前读取列表当前价格。
3. 将页面当前价格与批次快照绑定的 `approved_expected_old_price` 比较。
4. 不一致时返回 `OLD_PRICE_CHANGED`，保持 `side_effect_state=NOT_STARTED`，默认暂停批次等待人工决定；不得自动刷新审批旧价或继续提交。
5. 一致时复核逐商品 ReviewTask、批准模式、批准哈希、审批有效期和写锁，然后进入既有填写与提交步骤。
6. 从该次内联旧价读取的 `observed_at` 到 `SUBMIT_INTENT_RECORDED` 不得超过60秒；超时按提交前安全失败处理，不新增一次 fresh READ 队列任务。
7. COMMIT 必须先持久化 `SUBMIT_INTENT_RECORDED`，再执行真实点击；提交后继续使用既有独立页面回读判断 VERIFIED、NOT_APPLIED 或 NEEDS_RECONCILIATION。

这里移除的是“独立队列级 fresh READ 编排”，不是提交前旧价校验。旧价校验仍在每个单商品 COMMIT 内完成，因此后续排队商品不会仅凭过期批次价格直接提交。

### 17.4 FILL_PREVIEW 的新定位

FILL_PREVIEW 已完成开发前期可行性验证，不再属于任务12正常批次流程，也不再是任务12实机验收的必做项目。

- 正常批次不得在 COMMIT 前自动运行 FILL_PREVIEW。
- FILL_PREVIEW 底层能力可以暂时保留，供平台页面改版、元素重新捕获或专项故障诊断使用。
- 诊断性 FILL_PREVIEW 必须由人员明确发起，使用独立批次/数据库/证据范围，不得混入正常改价报告。
- 正常批次的 `previewed_count` 应为0；`PREVIEWED` 状态只保留为兼容和诊断状态。

### 17.5 严格串行队列和停止语义

批次编排层只负责领取、锁定、调用既有单商品执行器、导入结果和决定是否领取下一项，不复制平台页面逻辑。

1. 同一批次同时最多一个商品处于写锁状态或活动 attempt。
2. 当前商品结果完成导入和归档前，不得领取下一商品。
3. 提交意图前失败且副作用明确为 NOT_STARTED 时，记录该商品失败；是否继续必须服从批次 stop policy，`OLD_PRICE_CHANGED` 默认暂停。
4. 任何 UNKNOWN、SUBMIT_CLICKED 后失联或 NEEDS_RECONCILIATION 都必须立即暂停批次；不得自动重复 COMMIT。
5. 恢复批次时只能从数据库权威状态继续，已终态商品不得重放。
6. `stop.signal` 继续只在安全检查点阻止领取下一商品；提交意图后的当前商品必须完成读回或进入唯一 RECONCILE。

### 17.6 数据和兼容策略

为控制改动范围，现有 fresh-read 字段和 PREVIEWED 计数在第一轮实现中不强制删除，但降级为兼容字段，不参与正常执行路径。后续只有在迁移、备份恢复和历史报告兼容得到单独评估后，才允许通过独立 Schema 变更清理。

正常路径不再要求：

- `fresh_read_attempt_id`
- `fresh_read_result_sha256`
- `fresh_old_price`
- 独立 `FRESH_READ` 请求、结果、phase 和归档目录
- 正常批次 FILL_PREVIEW 请求及其验收报告

批次来源仍以任务11 READ_ONLY 归档和哈希为准；副作用事实仍以 operation、COMMIT attempt、side-effect checkpoint、独立回读和 RECONCILE 为准。

### 17.7 修订后的验收门槛

后续实现 PR 的自动测试应聚焦：单轮来源绑定、严格串行领取、逐商品审批、内联旧价漂移阻断、写锁、提交意图边界、独立回读、UNKNOWN 唯一 RECONCILE、暂停恢复不重放、Importer 计数重算、报告和旧单商品回归。删除“每项 fresh READ 及60秒 fresh-read 绑定”和“至少3项 FILL_PREVIEW”作为正常流程验收条件。

修订后的实机验收要求：

1. 一轮约5–10个 ONLINE 商品的批次 READ_ONLY，完整保存请求、结果、`read_batch_id`、run ID、哈希、归档和数据库回读。
2. 证明改价队列同时最多执行一个商品，且前一商品完成导入归档后才领取下一商品。
3. 仅在项目负责人逐商品明确授权后，对至少2个不同页面身份商品执行受控 COMMIT。
4. 提供至少一个 COMMIT 内联旧价漂移阻断样本，证明不新增 fresh READ attempt 仍能在提交前 fail closed。
5. 保留提交意图前安全停止、UNKNOWN/唯一 RECONCILE、暂停恢复不重放和计数恒等式证据。
6. 人工可读 Markdown 和机器可读 JSON 继续逐商品列出前价、目标价、后价、最终状态、错误、审批和 operation/attempt/run ID，并汇报来源 READ_ONLY、Importer、归档、数据库回读及无跨商品副作用。

FILL_PREVIEW 不再计入正常验收；如因平台变化单独执行，只作为诊断附件，不改变批次验收结论。

### 17.8 后续实施顺序

后续代码工作必须另开实现 PR，并按以下顺序控制范围：

1. 先审计并移除或隔离重复的批次验收驱动和独立 fresh READ 正常路径；不得重新实现任务11读取或单商品垂直切片。
2. 将批次 orchestrator 改为领取商品后直接调用既有单商品 COMMIT executor。
3. 将批次快照旧价映射到 COMMIT 的 `expected_old_price`，保留执行器内部读取和 `OLD_PRICE_CHANGED` 阻断。
4. 更新 Importer、批次计数、Web 和报告，使正常路径不依赖 fresh-read 字段或 PREVIEWED。
5. 更新自动测试后再进行受控实机验收；没有逐商品明确授权时不得执行 COMMIT。
6. 通过 GitHub PR 交接实现和证据，任务状态仍由审查方在审查合格后单独修改。

本计划修改 PR 不包含代码、Schema、影刀同步或实机操作，也不把此前未通过或部分通过的实验批次计入正式验收。
