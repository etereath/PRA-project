# 项目当前状态总览

本文档用于同步当前仓库的真实项目状态，避免早期规划、阶段计划和当前已实现能力混淆。

## 1. 当前项目定位

PRA 当前定位为：

鲜切花预测性销售决策系统 + 运行态任务运营后台。

PRA 主系统仍未形成真实销售平台和生产级 RPA 的无人值守调度闭环；但影刀微信小程序真实平台 UI 自动化实验已完成垂直切片、安全边界验证和核心故障注入。系统核心职责是：

- 从 Excel 读取业务输入。
- 根据规则和预测输入生成运行态任务。
- 用 SQLite 保存运行态事实。
- 通过 Web 后台、Mobile Review 和飞书通知完成运营复核闭环。
- 为后续生产级真实平台调度、真实 RPA 和 AI Agent 预留边界。

## 2. 当前已完成能力

### 2.1 业务输入

- Excel 仍是商品、规则、预测、产能、冷库等业务输入来源。
- Web 的 `Business Inputs` 页面已经从单纯 Excel 表格入口升级为业务输入入口。
- 商品资料与库存录入已提供日常表单：选择已有品种、通过“新增品种”弹窗维护新品种代码、补充公共库存、编辑商品基础资料，并保存回 `products.xlsx`。
- 价格规则管理已提供日常表单：新增、编辑、查看价格规则，并保存回 `price_rules.xlsx`。
- 价格规则适用范围已升级为 `variety_filter / grade_filter / platform_filter` 三维筛选；旧 `scope_type / scope_value` 已废弃。定价规则由旧的多条叠加改为单条胜出，按 `priority` 和具体度选择，冲突规则不会被静默随机选择。
- 上下架规则管理已提供日常表单：新增、编辑、查看上下架规则，并保存回 `listing_rules.xlsx`。
- 上下架规则已升级为 `variety_filter / grade_filter / platform_filter / stock_threshold / listing_strategy` 结构；旧 `condition_type / condition_value / action` 不再作为主路径。
- 包装产能计划已提供日常表单：按业务日期维护基础包装产能、临时工人数、单人临时工产能、确认包装能力和启用状态，并保存回 `capacity_plans.xlsx`。
- `capacity_plans.xlsx` 中的 `confirmed_packing_capacity_qty` 是 CapacityRuleEvaluator 的最终判断口径；如果为空，系统按“基础产能 + 临时工人数 × 单人临时工产能”计算。
- 冷库状态已提供日常表单：按业务日期维护冷库总容量、当前占用、预计入库、预计出库、预计占用、剩余容量、预警阈值和启用状态，并保存回 `cold_storage_status.xlsx`。
- `cold_storage_status.xlsx` 中的 `projected_occupied_qty` 和 `remaining_capacity_qty` 是 ColdStorageEvaluator 的判断口径；页面会按“当前占用 + 预计入库 - 预计出库”和“总容量 - 预计占用”默认计算，也允许运营人员人工确认。
- 旧 `/tables` 仍保留为高级兼容入口，适合批量维护和排障。
- 当前不迁移 Excel 主数据到 SQLite。

### 2.2 SQLite 运行态事实来源

SQLite 当前保存以下运行态表：

- `tasks`
- `review_tasks`
- `notification_logs`
- `execution_logs`
- `task_status_history`
- `review_tokens`
- `script_runs`
- `script_run_items`
- `shadowbot_operations`
- `shadowbot_execution_attempts`
- `shadowbot_side_effect_checkpoints`
- `retry_authorizations`

SQLite 只承接运行态任务系统，不替代 Excel 主数据。

当前 runtime schema 最新版本为 v6。v3 新增自动规则评估运行记录，v4 新增 ShadowBot Executor 账本，v5 新增 `instruction_hash`、`request_file_sha256`、`queue_request_path` 队列审计字段和 `retry_authorizations` 持久化结构，v6 新增事务型 `notification_outbox` 与 `notification_delivery_attempts`。`app.runtime_schema.LATEST_RUNTIME_SCHEMA_VERSION` 是唯一版本权威来源。

### 2.3 人工复核闭环

已完成 Web 人工复核闭环：

- `/reviews` 是 Web 复核主入口。
- pending review 可处理为 `approved / rejected / adjusted / cancelled`。
- Web 场景下 `actor / resolved_by` 来自 Session 用户。
- 如需推动源任务状态，必须通过 `ReviewTaskService` 内部协作 `RuntimeTaskService` 完成。
- 任务状态变化写入 `task_status_history`。

### 2.4 Review Token / Mobile Review MVP

已完成 Mobile Review MVP：

- `review_tokens` 表已落地。
- `ReviewTokenService` 支持 token 创建、校验、使用记录和撤销。
- Mobile Review 路由已实现：
  - `GET /mobile/review/{review_task_id}?token=...`
  - `POST /mobile/review/{review_task_id}/resolve`
- 手机端复核仍调用 `ReviewTaskService.resolve_review_task(...)`。
- 成功提交后写入 `used_at`，重复提交会被拒绝。

### 2.5 飞书通知

已完成飞书 Webhook 真实通知：

- `NotificationSenderFactory` 支持 `mock / feishu`。
- `FeishuWebhookNotificationSender` 使用飞书自定义机器人 Webhook。
- 支持可选签名 `FEISHU_WEBHOOK_SECRET`。
- 默认 `FEISHU_MESSAGE_TYPE=post`，使用飞书富文本消息。
- `FEISHU_MESSAGE_TYPE=text` 可作为纯文本回退。
- 飞书通知中可包含 Mobile Review 处理链接。
- `notification_logs.message` 只保存摘要，不保存完整 `token=` 链接。

### 2.6 cpolar 外网访问链路

已验证通过 cpolar 暴露本地 Web 服务后：

- 飞书消息中的 Mobile Review 链接可在手机打开。
- 手机端可提交复核。
- `review_task / review_token / task_status_history` 可正常写回。
- 重复提交会被拒绝。

### 2.7 Web 运行态运营后台

当前 Web 后台已从早期 Excel 原型页升级为运行态运营后台，包含：

- `Dashboard`：运营总览。
- `Tasks`：运行态任务追踪。
- `Reviews`：Web 复核主入口。
- `Notifications`：通知排障与追踪。
- `Execution Logs`：执行日志入口。
- `Business Inputs`：Excel 业务输入入口。
- `System`：配置检查、schema 检查、运行态计数、飞书测试通知。

### 2.8 自动规则评估框架 MVP

已完成轻量自动规则评估框架第一版：

- 新增 `script_runs / script_run_items`，记录 evaluator 运行和 proposal 明细。
- 新增 evaluator / proposal / runner 结构。
- `dry-run` 只写脚本运行记录，不写业务 `tasks / review_tasks / notification_logs`。
- `apply` 通过现有 `RuntimeTaskService / ReviewTaskService / NotificationSender` 链路落成业务任务、复核和通知。
- apply 前基于 `proposal.dedupe_key` 做幂等检查，重复运行不会重复生成 `capacity_warning / labor_required` 复核。
- 新增 CLI：`python scripts/evaluate_business_rules.py`。
- 任务中心新增“脚本状态”分页：`/tasks?task_tab=automation`。
- `CapacityRuleEvaluator` 读取 `harvest_forecasts.xlsx` 和 `capacity_plans.xlsx`，按 `trade_date` 使用启用的包装产能计划，并以 `confirmed_packing_capacity_qty` 判断是否需要产能预警。语义是“预测产量超过确认包装能力”的预警，不是订单需求超过包装能力的预警。
- 已新增保守版 `ListingRuleEvaluator`，语义是“上下架规则建议下架时生成人工复核 proposal”，不会直接生成可执行平台动作。
- 已新增保守版 `ColdStorageEvaluator`，语义是“预计冷库占用超过容量或剩余容量低于阈值时生成冷库预警复核 proposal”，不会直接阻断任务或执行平台动作。

### 2.9 Mock 平台同步实验室

已完成 Mock Platform Sync Lab 第一版，用于在本地验证运行态任务执行和平台状态同步闭环。

已完成能力：

- 新增独立 Mock 平台数据库：`data/runtime/mock_platform.sqlite3`。
- Mock 平台状态不写入 runtime DB，也不回写 `products.xlsx` 公共库存。
- 新增 `MockPlatformExecutorService` 与 CLI：`python scripts/run_mock_platform_executor.py`。
- CLI 支持 `--dry-run / --apply / --init / --reset-sample / --platform / --task-id / --runtime-db / --mock-platform-db`。
- `dry-run` 只预览，不写 `tasks / execution_logs / mock_platform_products`。
- `apply` 通过 `RuntimeTaskService` 推动任务状态，并写入 `execution_logs`。
- Mock 执行器支持 `update_price / set_online / set_offline / sync_status`。
- 平台商品缺失、非法低价等场景会执行失败并写入执行日志。
- 新增 `PlatformSyncEvaluator`，用于对比 PRA 期望状态与 Mock 平台实际状态。
- 当前差异类型包括：`price_mismatch / listing_status_mismatch / stock_mismatch / platform_sync_warning`。
- `PlatformSyncEvaluator` 只生成 review proposal，不自动修复平台状态。
- 任务中心新增只读分页：`/tasks?task_tab=mock_platform`。

边界：

- Mock 平台同步实验室不是接入真实销售平台，也不是影刀真实平台实验室。
- Mock 平台同步实验室不接真实 RPA。
- 不直接发送飞书。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不把平台库存覆盖为 PRA 公共库存。

### 2.10 影刀微信小程序 RPA 接入实验

已完成真实桌面微信小程序 `蚂蚁花团供应商` 的影刀垂直切片实验，并已建立 PRA `ShadowBotExecutor` 最小执行边界。当前定位是“真实平台 UI 自动化实验室 + 执行器骨架”，尚不是生产无人值守调度闭环。

已验证能力：

- 影刀应用 `test2` 可控制桌面端微信小程序 `WeChatAppEx`。
- `vertical_slice_read_price.py` 已支持 `READ_ONLY / FILL_PREVIEW / COMMIT / RECONCILE`。
- 已在测试商品 `C级 艾莎` 上完成真实 `COMMIT`，并通过列表复核价格。
- 已配置共享证据目录 `\\LAPTOP-O9O76RQV\pra-evidence`，截图证据可复制到共享目录并记录 SHA-256。
- 已完成六条核心故障注入：旧价变化、商品找不到、缺参、输入回读不一致、提交后结果未知、结果未知后的只读对账。详见 [reports/shadowbot_fault_injection_20260625.md](reports/shadowbot_fault_injection_20260625.md)。
- PRA 后端已具备 `ShadowBotExecutor` 最小闭环骨架：校验已批准 review、创建 `operation_id` 和 `execution_attempt_id`、启动 runner、记录副作用检查点、接收结果、写入 `execution_logs`、更新 operation 和 task。
- 已覆盖三条核心结果分支的单元测试：成功归并 `VERIFIED` 并完成 task、提交前 `FAILED + NOT_STARTED` 写日志并按错误码保留重试决策空间、提交后 `NEEDS_RECONCILIATION + UNKNOWN` 冻结 operation 并阻止再次 `COMMIT`。
- Web 执行日志最小查看入口已能展示 `operation_id`、`execution_attempt_id`、`shadowbot_run_id`、`execution_mode`、`status`、`side_effect_state`、价格、证据状态和共享截图，并对 `NEEDS_RECONCILIATION` 等高风险状态显示告警。
- Web 执行日志已展示队列 heartbeat、working phase、Worker、三类 hash、隔离数量和自动对账 attempt；继续保留人工只读对账与确认人工处理入口，不提供强制重新提交按钮。
- 已新增 `ShadowBotFileQueueRunner`，按 `.ready.json + .sha256` 原子发布请求；`filedrop` 保留为兼容名称。
- 已实现有界常驻影刀 Worker、独立 `ShadowBotResultImporter` 和独立 `ShadowBotQueueWatchdog`。Importer 只导入结果，Watchdog 只监测 heartbeat、phase、超时和遗留 working。
- 已在真实影刀 `test2` 中完成空队列启动与安全停止冒烟：Worker 每 5 秒写入 `heartbeat.json`，状态从 `RUNNING` 正常转为 `STOPPED`，期间未领取任务、未操作微信小程序。
- 已完成文件队列真实 `READ_ONLY -> FILL_PREVIEW -> 后置 READ_ONLY` 验收：实际旧价 `9.80`，预览目标和输入回读均为 `10.30`，取消后列表实际价仍为 `9.80`；请求、结果、phase、数据库、执行日志和共享证据 SHA-256 均通过自动校验。
- 已新增 `scripts/verify_shadowbot_filequeue_acceptance.py` 和 [shadowbot_filequeue_real_machine_acceptance.md](shadowbot_filequeue_real_machine_acceptance.md)，用于生成可回读的 JSON 验收报告。
- 已新增 `scripts/prepare_shadowbot_commit_acceptance.py`：COMMIT 必须引用默认 10 分钟内完成且全项通过的 READ_ONLY attempt，自动使用实际旧价，并要求精确确认文本；本轮 `9.80 -> 10.30` 已通过该安全门投递。
- 已完成文件队列受控 COMMIT：`ATTEMPT-ACCEPT-COMMIT-94538902e3dd` 将 `C级艾莎` 从 `9.80` 修改为 `10.30`，结果为 `SUCCESS/VERIFIED`，42 项请求、结果、phase、数据库、执行日志和双证据 hash 校验全部通过；独立 post-COMMIT READ_ONLY 再次读取为 `10.30`。
- 已完成隔离 UNKNOWN→RECONCILE 恢复验收：真实 NTFS 队列上的 stale `SUBMIT_CLICKED` 由 Watchdog 写出 UNKNOWN，Importer 导入后 Executor 只创建一个确定性 RECONCILE，最终归并为 `NOT_APPLIED`，两个 attempt 均归档且无活动队列残留。
- 已完成执行中副作用前停止验收：在 READ_ONLY `UI_STARTED` phase 后写入 `stop.signal`，流程在安全检查点返回 `FAILED/WORKER_STOP_REQUESTED/NOT_STARTED`，Result Importer 归档后 Worker 转为 `STOPPED`，31 项专项校验通过。
- 实机等待期间发现“请求发布后过期”旧路径只写 quarantine、数据库 attempt 悬挂为 RUNNING；现已修复为可信请求写 `FAILED/REQUEST_EXPIRED/NOT_STARTED` 可导入结果，历史样本也已迁移归档。
- 2026-07-02 首轮 8 小时观察失败：heartbeat 在 Windows `os.replace` 时遇到 `PermissionError [WinError 5]`，未捕获异常导致心跳线程退出，但主 Worker 继续运行；同时固定行元素定位在商品位置变化后漏掉了实际仍在上架的 C级艾莎并误报 `PRODUCT_NOT_FOUND`。现已增加原子写重试、心跳线程自愈/错误字段、Watchdog stale 告警、严格健康检查和动态商品元素枚举。详见 [reports/shadowbot_8h_observation_failure_20260702.md](reports/shadowbot_8h_observation_failure_20260702.md)。
- 动态定位已由真实 READ_ONLY `ATTEMPT-READ-DYNAMIC-20260702-165627` 验证：命中 C级艾莎的实际父级 index `17` 并读取价格 `18.30`，共享证据完整。现场同时发现 Result Importer 会把瞬时 Windows 文件 I/O 错误立即误判为契约无效；现已改为 `RESULT_IO_RETRY_PENDING` 自动重试，确定性隔离会持久化 `.error.json` 原因。
- 第二轮 8 小时观察 T4 暴露 Watchdog 读取 heartbeat 的 Windows 文件竞争：单次 `PermissionError` 曾终止整个 PRA 队列服务，但 Worker 结果完整且恢复导入后验收通过。现已增加队列 JSON 读取重试和 Watchdog 循环异常隔离，失败时输出 `WATCHDOG_INSPECTION_FAILED` 并继续运行，最新相关回归 `83 passed`。
- 2026-07-03 第三轮 8 小时 READ_ONLY 连续运行已通过：固定代码和 run2 数据库下完成 T0/T4/T7:45 三次自动投递与归档，T1/T6 heartbeat 健康，Worker 在约 8 小时后自动写出 `STOPPED/processed=3`；无 heartbeat 错误、quarantine 新增或活动队列残留。详见 [reports/shadowbot_8h_readonly_observation_pass_20260703.md](reports/shadowbot_8h_readonly_observation_pass_20260703.md)。
- 2026-07-04 已完成证据共享目录不可用和共享截图 hash 不一致实机注入：不可用目录返回 `FAILED/EVIDENCE_UPLOAD_FAILED/NOT_STARTED`；篡改共享截图后 PRA 验收器以 `evidence_1_storage_hash` 拒绝，恢复原图后重新通过。详见 [reports/shadowbot_evidence_fault_injection_20260704.md](reports/shadowbot_evidence_fault_injection_20260704.md)。
- 2026-07-04 至 2026-07-06 已完成 UI 状态故障组：登录失效真实 READ_ONLY 返回 `FAILED/LOGIN_REQUIRED/NOT_STARTED`；从已登录首页真实断网后返回 `FAILED/NETWORK_OR_LOAD_ERROR/NOT_STARTED`，恢复网络后 READ_ONLY 成功读取 `C级艾莎` 价格 `12.00` 并校验证据 hash。白屏分类已实现单元测试，但当前平台无稳定、可重复的白屏注入机制，未伪造实机通过。另已将瞬时无效窗口句柄改为三次重试，最终返回明确的 `WINDOW_NOT_AVAILABLE`。详见 [reports/shadowbot_ui_fault_injection_20260704.md](reports/shadowbot_ui_fault_injection_20260704.md)。
- 2026-07-06 提交意图后 stop.signal 核心行为已通过实机验证：监视器在 `SUBMIT_INTENT_RECORDED` 后写入停止信号，COMMIT 未在副作用区退出，继续将 `C级艾莎` 从 `12.00` 修改为 `12.50` 并返回 `SUCCESS/VERIFIED`，前后证据 hash 完整。测试同时发现并修复 phase 原子替换的 Windows 文件共享冲突。Result Importer 已导入并归档该 attempt，SQLite 账本记录为 `SUCCESS/VERIFIED`；历史 `stop.signal` 后续已清除，队列也在后续运行中形成 `STOPPED` 心跳。该次测试因 PowerShell 被人工关闭，未单独保留“同一次 Worker 自然识别停止信号”的 STOPPED 样本，但这不再作为提交意图后副作用保护的未完成项。详见 [reports/shadowbot_post_intent_stop_acceptance_20260706.md](reports/shadowbot_post_intent_stop_acceptance_20260706.md)。
- 2026-07-09 完成真实商品 COMMIT 后 UNKNOWN→RECONCILE 样本：第一轮最新 READ_ONLY 发现旧价从 `12.50` 变为 `8.00` 后安全中止原确认；第二轮用户重新确认 `8.00 -> 13.00`，COMMIT 在 `submit_clicked_at` 后返回 `NEEDS_RECONCILIATION/UNKNOWN/retryable=false`，Importer 导入后 Executor 自动创建唯一 `RECONCILE-57cc1892fb8d24961556`，对账读取 `actual_price=13.00` 并将 operation 归并为 `VERIFIED`。本轮暴露的队列根目录与自动对账共享证据继承问题均已完成代码修复：`--queue-dir` 强制同步两个队列环境变量，Importer 从已校验源请求继承 `evidence_share_dir`、`applet_uri` 和 `window_title`。后续已完成 pytest ACL 排障，相关 4 项定向回归通过。详见 [reports/shadowbot_unknown_reconcile_attempt_20260709.md](reports/shadowbot_unknown_reconcile_attempt_20260709.md)。
- 2026-07-11 已完成商品列表强制刷新真实 READ_ONLY 验收：Worker 在价格读取前即使商品管理列表已存在，仍点击“商品管理”重新拉取数据，并写入 `product_list_refreshes`。`ATTEMPT-REFRESH-READ-20260711-135356` 返回 `READ_COMPLETED/NOT_STARTED`，读取 `C级艾莎` 价格 `9.00`，共享证据、hash、结果导入和归档校验均通过。提交后的 `AFTER_SUBMIT_VERIFY` 刷新路径留待后续受控 COMMIT 或独立对账实机样本验证。详见 [reports/shadowbot_product_list_refresh_readonly_20260711.md](reports/shadowbot_product_list_refresh_readonly_20260711.md)。
- 2026-07-13 已完成 URI 冷启动完整 READ_ONLY 验收：在小程序窗口关闭时，`ATTEMPT-URI-FINAL-READ-20260713-154700` 通过 `weixin://launchapplet/` 启动目标小程序（`URI_LAUNCHED`），随后按“登录检查/恢复 -> 商品管理刷新 -> 读取价格”执行，动态定位到第 `38` 行“艾莎 B级”，读取实际价格 `10.00`。结果为 `READ_COMPLETED/NOT_STARTED`；请求、结果和 phase checksum，instruction/request hash，PRA 执行日志，以及共享证据 SHA-256 均通过验收。该样本不代表业务改价完成。
- `NEEDS_RECONCILIATION` 的唯一自动对账入口位于 `ShadowBotExecutor.ensure_reconcile_attempt(...)`；Worker、Importer 和 Watchdog 都不能自行构造对账请求。
- 已新增 `YingdaoOpenApiJobRunner`，可通过影刀开放 API `JOB运行/启动应用` 启动指定 `robotUuid`，并返回 `shadowbot_run_id=yingdao-job:{jobUuid}`。
- 已新增 `check-yingdao-app-params` 只读预检命令，可在真实启动前确认影刀应用已暴露 `request_json` 入参和 `shadowbot_result_json` 出参。
- 已新增 `scripts/check_shadowbot_readiness.py` 离线就绪检查命令，可在真实启动前检查 runner、runtime DB 和必需环境变量；该命令不启动影刀、不访问影刀 OpenAPI，也不会输出密钥明文。
- 已新增 `poll-yingdao-result` 桥接命令，可通过影刀 `job/query` 读取 `shadowbot_result_json` 出参并回写 PRA `execution_logs`、operation 和 task。
- 当前项目体量不再使用环境变量 SKU/平台白名单；真实联调范围由 PRA 已审批任务、批准载荷 hash、单商品串行执行和人工对账共同约束。
- 已新增 `scripts/prepare_shadowbot_e2e_chain.py`，可一键准备首条 `update_price` task、approved review、批准载荷 hash，并在显式 `--start` 时调用 Executor 启动 `COMMIT`。
- 已新增 `scripts/run_shadowbot_e2e_local_demo.py`，可在本地 runtime DB 中演练成功、提交前失败、提交后未知再对账三条结果分支，并通过 Web 执行日志查看字段、告警和证据链接。
- 已新增 `scripts/run_shadowbot_executor.py` 桥接脚本，可从已批准 review 启动 ShadowBot 执行尝试，也可导入影刀结果 JSON 回灌 PRA 运行态。

关键安全结论：

- 提交前失败必须保持 `side_effect_state=NOT_STARTED`。
- 提交点击后、列表复核前失败必须进入 `NEEDS_RECONCILIATION`，`retryable=false`。
- 结果未知后只能执行 `RECONCILE` 只读核对，不允许自动重复 `COMMIT`。
- 当前小程序实际副作用边界按 `INNER_CONFIRM` 管理：价格弹窗“确认”后已经可能产生平台结果，不应依赖独立最终保存按钮。

边界：

- 当前已形成不依赖 OpenAPI 的本地文件队列代码闭环，并完成空队列、真实 READ_ONLY、FILL_PREVIEW、受控 COMMIT、post-COMMIT 读价、副作用前停止、提交意图后停止、隔离 UNKNOWN→RECONCILE、真实商品提交后 UNKNOWN→RECONCILE 和 8 小时 READ_ONLY 连续运行验收。尚未完成长期告警和证据运维观察，因此仍不是无人值守生产调度承诺。
- 影刀 OpenAPI runner 继续保留为可选能力；申请成本较高，不再作为当前阶段的上线前置条件。
- 当前未形成无人值守生产运行承诺。
- 当前仅验证单平台、单窗口、单测试商品路径。
- 本轮针对性回归共通过 115 项测试及 7 个子测试：其中 Executor、文件队列和影刀垂直切片 42 项，运行时持久化与 Web 73 项。

## 3. 当前主控流程

当前主控流程如下：

1. 运营维护 Excel 业务输入。
2. 系统根据 Excel 输入生成 runtime `tasks`。
3. 高风险、提醒或需要人工确认的内容进入 `review_tasks`。
4. pending `review_task` 创建后触发 `notification_logs`。
5. 飞书通知发送到群，消息中携带 Mobile Review 链接。
6. 运营人员通过 Web `/reviews` 或手机 Mobile Review 处理复核。
7. 复核处理结果写回 `review_tasks`。
8. 如绑定源任务且满足条件，通过 `RuntimeTaskService` 推动 `tasks` 状态。
9. 源任务状态变化写入 `task_status_history`。
10. 后续执行器可读取 `tasks` 并写入 `execution_logs`。

Mock 平台测试流程在当前阶段作为本地验证链路：

1. Mock Platform Executor 读取 runtime `tasks`。
2. `apply` 时通过 `RuntimeTaskService` 推动任务状态，并修改 `mock_platform.sqlite3` 中的模拟平台状态。
3. 执行结果写入 `execution_logs`。
4. `PlatformSyncEvaluator` 对比 PRA 期望状态与 Mock 平台实际状态。
5. 发现差异时生成 review proposal，再由现有复核和通知链路处理。

## 4. 数据流

```text
Excel 业务输入
  -> runtime tasks
  -> review_tasks
  -> notification_logs
  -> Web Review / Mobile Review
  -> task_status_history
  -> execution_logs
```

Mock 平台测试数据流：

```text
runtime tasks
  -> Mock Platform Executor
  -> mock_platform.sqlite3
  -> execution_logs
  -> PlatformSyncEvaluator
  -> review_tasks / notification_logs
```

关键边界：

- Excel 是业务输入来源。
- SQLite 是运行态事实来源。
- Web 和 Mobile 只是复核入口，不直接写 SQLite 表。
- 所有复核处理必须走 `ReviewTaskService`。
- 所有源任务状态变化必须走 `RuntimeTaskService`。
- 通知发送必须走 `NotificationSender`。

## 5. Web 后台页面结构

### Dashboard

展示最小运营指标：

- pending review 数。
- 即将超时 review 数。
- failed notification 数。
- pending task 数。
- expired task/review 数。

### Tasks

用于追踪运行态任务：

- 支持任务筛选。
- 支持任务详情。
- 展示状态历史、关联复核、关联通知、执行日志摘要。
- 支持“脚本状态”分页，查看自动规则评估运行记录和 proposal 明细。
- 支持“Mock 平台测试台”分页，只读查看本地模拟平台状态。

### Reviews

Web 复核主入口：

- 支持复核筛选。
- 支持复核详情。
- 支持处理 pending review。
- 展示源任务、通知、状态历史、review token 摘要。

### Notifications

通知排障入口：

- 支持通知筛选。
- 支持通知详情。
- 展示关联 review/task。
- 展示当前飞书消息类型摘要。
- 不展示完整 token URL。

### Execution Logs

执行日志入口：

- 当前以 mock/RPA 执行日志查看为主。
- 尚未接入生产级真实 RPA 调度闭环。

### Business Inputs

业务输入入口：

- 承接商品资料与库存录入，保存回 `products.xlsx`。
- 支持补充公共库存，而不是将初始库存拆分到具体平台。
- 承接价格规则管理，保存回 `price_rules.xlsx`。
- 价格规则按品种、等级、平台三维筛选；平台只用于规则命中，不参与库存匹配或 SKU。
- 承接上下架规则管理，保存回 `listing_rules.xlsx`。
- 上下架规则按品种、等级、平台三维筛选，并通过规则策略和库存阈值决定是否建议上架/下架。
- 保留 Excel 表格管理作为高级兼容入口。
- 承接任务生成入口。

### System

系统检查入口：

- 检查环境变量配置。
- 检查 runtime DB、schema、运行态表计数。
- 展示运行态摘要。
- 手动发送飞书测试通知。
- 不自动探测 cpolar / Mobile Review 外网链路。

## 6. 当前明确未做事项

当前明确不做：

- 不承诺真实销售平台无人值守生产改价；当前保留单商品串行、人工审批、旧价校验和人工可对账边界。
- 不承诺生产级无人值守 RPA；本地文件队列、自动对账和审计闭环代码已完成，但常驻实机样本、告警和长期证据运维仍未达到生产级。
- 不接 AI Agent 自动决策。
- 不引入 React / Vue。
- 不做前后端分离。
- 不迁移 Excel 主数据。
- 不新增完整权限系统。
- 不做飞书交互卡片审批。
- 不做飞书长连接或回调。
- 不做完整移动端 UI。

## 7. 安全边界

不得提交：

- `.env.local`
- `.env`
- `.env.*`
- `scripts/local_env.ps1`
- `REVIEW_TOKEN_SECRET`
- `RUNTIME_ADMIN_PASSWORD`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_WEBHOOK_SECRET`
- 带 `token=` 的 Mobile Review URL
- `data/runtime/`
- `*.sqlite3`
- `*.db`

展示与持久化要求：

- 不展示 secret。
- 不展示 raw token。
- 不展示完整 webhook。
- 不展示完整 `mobile_review_url`。
- `notification_logs.message` 不应保存 `token=`。
- Web 后台运行态页面需要登录。
- Mobile Review token 只用于指定 `review_task`。

## 8. 后续推荐优先级

当前下一步不是扩大无人值守真实 RPA，也不是做 AI Agent。

Code Review 后的高中低风险问题已完成修复，系统冒烟测试、全量单元测试和主控端到端流程测试均已通过。修复详情见 [reports/risk_fix_report_20260610.md](reports/risk_fix_report_20260610.md)。

推荐顺序：

1. 持续运行 `python scripts/run_system_smoke_tests.py` 和完整单元测试，保持主控流程基线稳定。
2. 进入下一轮功能开发前，先做小范围设计审查，确认不会绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
3. 基于自动规则评估框架继续规划上下架、冷库、包装产能等 evaluator，但保持 dry-run/apply 和 service 边界。
4. 继续打磨业务输入、Web 可用性和运行态排障体验。
5. 在已通过 8 小时 READ_ONLY、提交意图后停止和真实 UNKNOWN 自动对账验收的基础上，补充长期告警、磁盘清理、证据保留和服务账号运维样本；继续保持单商品串行、人工审批、旧价校验和对账边界。
6. AI Agent 自动决策应放在真实平台 / RPA 执行链路跑通并具备足够审计边界后再推进。

## 9. 推荐验收命令

```powershell
python scripts/run_system_smoke_tests.py
python -m unittest discover -s tests
python -m pytest tests/test_shadowbot_evidence_share.py tests/test_shadowbot_vertical_slice_reconcile.py
```

系统冒烟测试脚本已落地，建议与完整单元测试一起作为后续功能开发前、发布前和回归排查时的基线检查。

pytest 运行环境注意事项：

- 使用系统 Python 执行 `python -m pytest`。
- 当前已确认系统 Python `C:\Users\etere\AppData\Local\Programs\Python\Python314\python.exe` 安装了 `pytest 9.1.1`。
- 不要使用 Codex bundled runtime Python `C:\Users\etere\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` 运行 pytest；该环境未安装 pytest。
- 若在 Codex 沙箱中遇到 pytest 临时目录 `PermissionError: [WinError 5] 拒绝访问`，优先视为沙箱临时目录权限问题，而不是 pytest 未安装。
- 当前本机已为 `etere` 与 `CodexSandboxUsers` 授予 `.pytest_cache` 和 `%TEMP%\pytest-of-etere` 的修改权限；需要可信测试结果时，仍应在普通 Windows 用户环境运行 `py -3.14 -m pytest`，而不是把 Codex 文件沙箱的目录限制当作应用回归失败。
