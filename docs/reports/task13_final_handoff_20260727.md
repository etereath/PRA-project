# 任务13最终交接报告：单平台商品上下架与状态对账闭环

- 交接日期：2026-07-27
- 平台：蚂蚁花团供应商微信小程序
- ShadowBot 应用：`test2`
- Runtime Schema：v13
- 上下架合同：v5
- 文档状态：待审查

> 本报告以当前代码、SQLite v13、真实小程序运行归档和仓库内脱敏证据为准。
> 原《任务13交接与实施计划》用于追溯初始范围；开发中形成的四维状态模型、严格
> 串行轨迹、嵌入式同步和恢复语义，以本报告及所引用的冻结文档为当前事实。

> 本报告不修改任务13状态。只有 GitHub CI、代码审查、证据复核和运行边界均通过
> 后，审查方才应决定是否将任务13标记为完成。

## 1. 交接结论

任务13已经形成从任务中心到真实平台的单平台上下架与状态对账闭环：

- `SYNC_STATUS` 完整扫描“上架中”和“待上架”，生成父快照与逐商品项。
- `SET_ONLINE` 在一次完整 COMMIT 队列内完成状态门禁、资料写入、上架确认和
  独立回读。
- `SET_OFFLINE` 完整扫描“上架中”，记录操作前价格，完成下架确认和独立回读。
- 单商品和多商品均根据页面实时位置严格串行执行，不依赖任务输入顺序。
- 已处于目标状态且资料一致时返回 `ALREADY_APPLIED`，不产生写点击。
- 任一目标身份、状态或资料门禁失败时，整批在首次写入前停止。
- 最终确认后结果不明时进入 `UNKNOWN`，保留共享写锁，只允许唯一只读
  `RECONCILE`。
- RECONCILE 已分别实机覆盖 `VERIFIED` 和 `NOT_APPLIED`。
- 改价、上架和下架共用同一“平台 + 内部 SKU”写锁。
- Result Importer 先完整校验，再在单个 SQLite 事务中投影批次、逐商品、
  operation、attempt、任务、平台状态、Review 和 Outbox。
- Web 任务详情已经能够只读展示 v5 批次、operation、attempt、UNKNOWN、
  RECONCILE、回读时间和错误信息。
- 脱敏实机证据进入仓库，由 Windows 和 Linux CI 独立复算合同、哈希、回执、
  ACK、数据库账本和计数恒等式。

计划中的受控实机验收矩阵已经全部覆盖。当前剩余事项是 GitHub PR 检查和人工审查，
不是继续增加真实平台写操作。

## 2. 当前正式状态语义

### 2.1 四维分离

平台事实、页面观察、自动化处置和写操作结果不再混为一个状态：

| 维度 | 作用 | 典型值 |
|---|---|---|
| `online_status` | 平台可售事实 | `online / offline` |
| `listing_location` | 最新有效两页快照的位置事实 | `online_only / waiting_only / both / neither / ambiguous` |
| `automation_disposition` | 由位置、Review 和写锁实时派生 | `actionable / manual_review / reconciliation_pending` |
| operation 状态 | 写操作及其副作用事实 | `VERIFIED / NOT_APPLIED / PARTIALLY_APPLIED / NEEDS_RECONCILIATION` |

`manual_review` 不是第三种上下架状态；`UNKNOWN` 也不是页面位置。页面异常与写操作
副作用不确定分别进入 Review 和 RECONCILE，不互相替代。

### 2.2 页面位置投影

完整 `SYNC_STATUS` 的投影规则为：

| `listing_location` | `online_status` 投影 | 自动化处置 |
|---|---|---|
| `online_only` | `online` | 无其他阻断时可执行 |
| `waiting_only` | `offline` | 无其他阻断时可执行 |
| `both` | `online` | 人工介入 |
| `neither` | `offline` | 人工介入 |
| `ambiguous` | 不覆盖旧事实 | 人工介入 |

改变上下架状态的已确认写操作会使旧位置快照失效。价格更新不会使位置快照失效。
下一次完整两页同步才产生新的有效 `listing_location`。

### 2.3 人工运营变化

平台允许正常人工运营。不同运行之间的商品状态、价格、库存和排序变化属于正常外部
事实；旧快照和旧提案不能作为持续写入授权。每次写操作必须以当次预扫描、当前任务
字段和当前写锁为准。

## 3. 正式执行链路

### 3.1 `SYNC_STATUS`

```text
刷新商品管理页
→ 扫描“上架中”
→ 扫描“待上架”
→ 根据页面身份映射内部 SKU
→ 计算 online_only / waiting_only / both / neither / ambiguous
→ 完整校验父快照、商品项和结束标记
→ 单事务写入快照、平台事实、异常、Review 和通知 Outbox
→ 生成 request/result/phase/receipt/ACK 与人工报告
```

扫描使用商品列表聚焦后 `END → 等待 0.8 秒 → HOME` 完成延迟加载，再从顶部进行
结构化全量读取。上架中商品卡步长为 16，待上架商品卡步长为 15；商品名称和等级
分别使用已冻结的等差索引选择器，不枚举整个页面的无关元素。

### 3.2 `SET_ONLINE`

```text
读取明确指定的任务 ID
→ 嵌入式两页状态预扫描
→ 全目标唯一匹配和全批次门禁
→ 生成按页面从上到下的操作轨迹
→ 对每件商品依次完成价格/库存写入与回读
→ 在同一商品仍处于可操作视口时点击上架并最终确认
→ 独立回读状态、价格和库存
→ 首个 UNKNOWN 后停止后续项
→ Result Importer 原子投影
```

已经唯一在线且价格、库存符合任务目标时返回 `ALREADY_APPLIED`。唯一在线但资料不
符合目标、身份不唯一、关键字段不可读或页面位置异常时，整批在首次写入前阻断。

### 3.3 `SET_OFFLINE`

```text
读取明确指定的任务 ID
→ 完整扫描“上架中”
→ 全目标唯一匹配和全批次门禁
→ 按页面实时位置生成轨迹
→ 记录每件商品下架前价格
→ 依次点击下架和最终确认
→ 每项独立回读“上架中”
→ 目标出现次数为 0 时 VERIFIED
→ 首个 UNKNOWN 后停止后续项
→ Result Importer 原子投影
```

下架不修改价格或库存，也不需要扫描“待上架”来证明本次下架动作已经生效。旧的
完整两页位置快照在已确认上下架写操作后失效，后续 `SYNC_STATUS` 再恢复完整位置
事实。

### 3.4 `RECONCILE`

RECONCILE 由 UNKNOWN 结果自动创建，绑定原 operation、原 item attempt 和原
result，只允许一条确定性只读请求。它不包含保存、上架、下架或最终确认动作：

- 当前事实满足目标：`VERIFIED`；
- 当前事实仍为旧状态：`NOT_APPLIED`；
- 资料与状态只完成一部分：`PARTIALLY_APPLIED / REVIEW_BLOCKED`；
- 仍无法确认：继续 `UNKNOWN` 并保留写锁。

## 4. 任务与授权边界

任务13沿用任务12并为任务14保留以下边界：

1. 任务中心是正式输入来源。
2. `pending` 只表示候选任务，不等于执行授权。
3. 任务14完成并审查前，操作人员必须明确传入一个或多个 `task_id`。
4. 禁止扫描并自动发布全部 `pending`。
5. `SYNC_STATUS` 只产生观察事实，不能自动触发写操作。
6. 开发实机测试在发布固定 COMMIT 批次前取得一次清单授权。
7. 正式运行不依赖 Codex 对话中的临时确认，但仍必须经过后续统一任务审查和调度
   准入；任务13没有实现无人值守自动调度。

## 5. 代码和数据结构交接

### 5.1 核心服务

- `app/services/shadowbot_listing_sync.py`：两页同步、快照接受和原子投影。
- `app/services/shadowbot_listing_action_contract.py`：v5 request/result/phase、
  哈希、计数和语义校验。
- `app/services/shadowbot_listing_action_pipeline.py`：任务读取、提案、发布、账本和
  Result Importer 衔接。
- `app/services/listing_automation_gate.py`：按动作统一计算 Review、位置和写锁门禁。
- `app/review_policy.py`：Review 对不同写动作的阻断策略。
- `app/shadowbot_listing_contract.py`：跨层共享的状态和合同基础定义。
- `app/services/shadowbot_queue.py`：v5 结果导入、ACK、归档和唯一 RECONCILE。
- `app/repositories/sqlite_runtime_repository.py`：v13 事务、查询和投影。
- `app/web.py`：任务详情中的上下架运行投影。

### 5.2 ShadowBot

- `shadowbot/test2/shadowbot_queue_worker.py`：v5 请求领取、phase、结果骨架和异常恢复。
- `shadowbot/test2/vertical_slice_read_price.py`：两页扫描、等差索引读取、轨迹计算、
  上架、下架和只读 RECONCILE。
- `shadowbot/test2/product_identity_mapping.json`：内部 SKU 到平台页面身份映射。
- `scripts/sync_shadowbot_test2.py`：规范代码到真实 `test2` 应用目录的受控同步。

### 5.3 Runtime Schema v13

v13 在保留任务12 v4 数据的基础上新增或扩展：

- `shadowbot_batch_registry`：v4/v5 共用批次外键。
- `shadowbot_operations`：通用 `update_price / set_online / set_offline` operation。
- `shadowbot_write_locks`：`ACTIVE / UNKNOWN / REVIEW_BLOCKED / RELEASED`。
- `shadowbot_listing_action_batches`。
- `shadowbot_listing_action_batch_items`。
- `shadowbot_listing_result_receipts`。
- `listing_sync_snapshots`。
- `listing_sync_snapshot_items`。
- `listing_anomaly_cases`。
- `listing_status` 的位置快照失效和观察来源字段。

v12→v13 迁移已验证新库、带数据迁移、重复迁移、历史 v4 成功批次、
UNKNOWN/RECONCILE 账本、历史写锁保留、外键完整性和失败事务回滚。

## 6. 最终实机证据

统一入口为[任务13脱敏实机证据索引](../evidence/task13/index.md)。所有证据包都保留
业务身份、价格、库存、时间、批次/operation/attempt/result ID、计数和数据库事实，
只替换本机路径与 Worker 设备标识。

| 验收项 | 代表性批次或运行 ID | 结果 |
|---|---|---|
| 完整两页同步 | `ATTEMPT-T13-POST-PREFLIGHT-RESCAN-20260727-01` | `VERIFIED`，2 个在线、10 个待上架、17 个快照项 |
| 同一 SKU 上架再下架 | `BATCH-T13-AISHA-A-SET-ONLINE-20260726-02` / `BATCH-T13-AISHA-A-SET-OFFLINE-20260726-04` | `AISHA-A-70-Z` 往返均 `VERIFIED` |
| 多商品正常上架 | `BATCH-T13-OPTIMIZED-SET-ONLINE-20260726-02` | 2/2 `VERIFIED` |
| 多商品正常下架 | `BATCH-T13-OPTIMIZED-SET-OFFLINE-20260726-02` | 2/2 `VERIFIED` |
| 已处于目标状态 | `BATCH-T13-ALREADY-APPLIED-20260727-01` | `ALREADY_APPLIED`，0 写点击 |
| 全批次预检异常 | `BATCH-T13-PREFLIGHT-ZERO-WRITE-20260727-01` | 0 次资料保存、0 次最终确认 |
| 严格串行 UNKNOWN | `BATCH-T13-CONTROLLED-UNKNOWN-20260726-01` | 首项成功、次项 UNKNOWN、后续停止 |
| UNKNOWN→VERIFIED | `BATCH-T13-AUTO-RECONCILE-CONTROLLED-UNKNOWN-20260727-03` | 唯一 RECONCILE 后 `VERIFIED` |
| UNKNOWN→NOT_APPLIED | `BATCH-T13-UNKNOWN-NOT-APPLIED-20260727-01` | 授权外部恢复后唯一 RECONCILE 为 `NOT_APPLIED` |
| 跨动作共享写锁 | 自动化交叉矩阵 | 三种动作均被 `ACTIVE / UNKNOWN / REVIEW_BLOCKED` 正确阻断 |
| phase/result 中断恢复 | 自动化恢复矩阵 | 已知部分副作用不被误记为未尝试 |
| Web 运营投影 | 当前真实 v13 数据库 | v5 批次、operation、attempt 和回读事实可见 |

特别说明：`UNKNOWN→NOT_APPLIED` 样本在 UNKNOWN 与 RECONCILE 之间进行了用户明确
授权的人工恢复。因此 `NOT_APPLIED` 证明的是“对账时目标下架状态没有保留”，不证明
原始点击从未短暂生效。

## 7. 自动化回归和 CI

最终本地完整回归：

```text
658 passed, 3 skipped, 97 subtests passed
```

全部任务12/13脱敏证据校验器通过，包括：

- SYNC_STATUS。
- 单商品状态往返。
- 多商品正常上下架。
- 严格串行 UNKNOWN。
- UNKNOWN→RECONCILE→VERIFIED。
- UNKNOWN→RECONCILE→NOT_APPLIED。
- `ALREADY_APPLIED`。
- 全批次预检零写。

`.github/workflows/core-ci.yml` 已同时在 Windows 和 Linux 作业中加入上述证据复算。
本地还通过：

- 临时 v13 数据库系统冒烟：16 项通过、0 项失败；
- Python 编译检查；
- CI YAML 解析；
- UTF-8/UTF-8-SIG 编码自检；
- `git diff --check`。

PR 创建后仍必须等待 GitHub Windows Core、Linux Core、wheel、smoke、打包审计和
证据复算全部通过；本报告不把本地测试替代为远端 CI 结论。

## 8. Worker 最终运行状态

交接报告生成时的已核实状态：

| 项目 | 值 |
|---|---|
| 生命周期记录 | `RUNNING` |
| 最后业务执行 | `RECONCILE-bcd7f9f2293440cf2d38fef9` |
| Worker 故障注入开关 | `false` |
| Queue Service | 运行中 |
| `inbox/` | 空 |
| `working/` | 空 |
| `results/` | 空 |
| `stop.signal` | 不存在 |

Worker 继续按长期监听规范运行。该记录只是最后一次已核实状态；下次使用前仍需读取
生命周期文件并核对新鲜 heartbeat 和活动队列。

## 9. 已知限制

1. 仅覆盖蚂蚁花团供应商单平台、单小程序窗口和单 Worker 严格串行执行。
2. 页面不能读取平台 SKU；当前通过内部 SKU 映射到“商品名称 + 等级”。出现同名、
   同等级、不同规格时必须扩展稳定页面身份，不能使用视觉近似或固定行号。
3. `SYNC_STATUS` 只扫描“上架中”和“待上架”。两个页面均不存在或同时存在的商品
   进入人工介入，不自动猜测审核中、未通过或未录入。
4. 空“上架中”列表的就绪判断仍有性能问题；代表性 `ALREADY_APPLIED` 样本约
   73.419 秒，其中约 71.056 秒用于刷新和空列表等待。
5. 平台元素结构、商品卡步长和结束标记变化时需要重新探索并更新 adapter；公共服务
   不应直接依赖这些页面细节。
6. 长期告警、磁盘清理、证据保留、服务账号和无人值守恢复尚未达到生产级。
7. 任务14前禁止自动扫描全部 pending、自动审批或无人值守发布 COMMIT。
8. 真实运行数据库、原始队列归档、账号凭据、故障注入开关和本机生命周期文件不得
   提交到 GitHub。

## 10. 审查步骤

建议审查方按以下顺序：

1. 阅读本报告、[状态模型冻结](../plans/task13_t13_1_contract_freeze.md)和
   [验收覆盖矩阵](task13_acceptance_status_20260727.md)。
2. 审查 Runtime Schema v13 迁移、公共批次注册表、通用 operation 和共享写锁。
3. 审查 v5 request/result/phase 合同及全批次门禁。
4. 审查 Worker 的两页扫描、页面轨迹、最终确认边界和 phase 恢复。
5. 审查 Result Importer 的完整预校验、单事务投影、ACK 和唯一 RECONCILE。
6. 抽查[证据索引](../evidence/task13/index.md)中的 request/result/phase/receipt/ACK、
   数据库回读和 validation report。
7. 核对 `UNKNOWN→VERIFIED` 与 `UNKNOWN→NOT_APPLIED` 两种终态的不同语义。
8. 核对任务14前明确 `task_id` 的运行边界没有被 Web 或调度器绕过。
9. 等待 GitHub Windows/Linux/wheel/smoke 和证据校验全部通过。
10. 审查通过后再决定任务13状态，不在合并前由开发方自行标记完成。

## 11. GitHub 交接范围

任务13应使用独立分支和独立 PR，相对已合并的任务12 PR #18 审查。PR 包含：

- Runtime Schema v13、状态模型、同步、action gate、v5 流水线和 Web 投影。
- ShadowBot 两页读取、上下架动作、轨迹和恢复。
- 单元测试、迁移测试、恢复测试和 CI 证据校验器。
- 任务13计划、冻结文档、人工报告和脱敏证据。
- 更新后的当前状态、SQLite、部署和对接文档。

明确排除：

- `.codex_tmp/`。
- `data/runtime/`、`D:\PRA_Runtime` 和任何 SQLite 运行数据库。
- 原始未脱敏队列归档。
- 账号、密码、token、本机配置和生命周期文件。
- 任务12审查附件 DOCX。

## 12. 后续可直接复用的内容

任务14和后续调度阶段应直接复用：

- `task_id` 明确选择边界和统一 `evaluate_automation_gate`。
- 公共批次注册表与跨动作共享写锁。
- operation/attempt/side-effect 状态机。
- v5 单次完整队列、逐商品哈希和计数恒等式。
- 完整预扫描、全目标唯一匹配和按页面实时位置编排。
- phase 的点击边界和 Worker 最外层恢复。
- Result Importer 的完整预校验、单事务投影、receipt 和 ACK。
- UNKNOWN 后唯一只读 RECONCILE。
- 两页父快照与商品项、异常事实、Review 和 Outbox 原子链路。
- 脱敏证据导出、离线校验和双平台 CI 复算。
- 长期 Worker 生命周期和正常/异常恢复规范。

后续不得为了接入自动调度而重写已验收的上架、下架、改价或 RECONCILE 动作链路。
