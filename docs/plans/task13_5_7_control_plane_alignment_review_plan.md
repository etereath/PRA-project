# 任务 13.5-7A：控制面入口盘点与合同冻结

- 修订日期：2026-08-07
- 状态：最终编码前设计审查草案
- Review Profile：R4（架构级审查）
- 当前 PR：#29
- 关联父任务：GitHub Issue #20
- 主分支基线：PR #28 合并提交 `418c605f4ab4434eee422eb0217de3cfe64b01b0`

## 1. 计划定位

PR #29 直接承担 **13.5-7A：控制面入口盘点、复用矩阵与合同冻结**。本 PR 不是 7B 实现 PR，不修改生产代码，不执行真实平台动作。

经过多轮设计复审，本轮同时执行治理文档要求的 **Final Audit / Freeze Gate**：不再按“发现一个补一个”的方式无限扩大审查，而是一次性冻结 Web mutation、Operation transition、入口 capability、幂等、优先级、Runtime DB 和旧入口退役边界。

7A 交付必须包含：

1. Web、CLI、Automation、Queue Service、真实写、Importer、RECONCILE、运维和兼容入口的当前事实；
2. 每个具有独立副作用边界的路由动作、脚本子命令和内部组件单独分类；
3. `current behavior → target capability → target mode → Composition Root → 保留/迁移/拒绝/退役`；
4. 当前代码事实与 7B 目标合同分开记录；
5. capability 级 canonical identity 与分层幂等；
6. 18:00 平台截单、20:00 卖家经营日切换的重试合同；
7. Runtime DB Current → Migration → Target → Tests；
8. 查询入口零业务写/零 Schema 写合同；
9. Operation、Attempt、Task、共享写锁和 Importer 的人工恢复收敛合同；
10. 三层优先级、唯一 RECONCILE 和独立服务生命周期；
11. 7B/7C 开工、验证和停止条件。

本阶段不修改 Runtime Schema、Web 路由、CLI、Scheduler、v4/v5、ShadowBot、Importer、Worker 部署或生产开关。

## 2. 权威依据与审查停止规则

冲突按以下顺序处理：

1. GitHub Issue #20；
2. `AGENTS.md` 复用优先门禁；
3. 当前生产代码、Runtime Schema、服务和测试；
4. 已合并任务 12、13、13.5-1 至 13.5-6 合同与实机证据；
5. `docs/pra_review_risk_and_complexity_governance.md`；
6. 本计划。

治理规则继续生效：

- 后续复审原则上只验证本计划冻结项及修复直接回归；
- 新问题只有满足治理 §8.2 的真实安全例外（错误真实平台动作、覆盖人工操作、重复副作用、关键事实损坏、审批/价格安全绕过、当前部署不可运行等）才新增为 blocker；
- 其他问题进入技术债或后续任务，不再形成无限追加 P1；
- 本轮 Final Audit 通过后，13.5-7A 结束，后续新增需求不得继续塞回 7A。

## 3. Review Profile 与复杂度预算

```text
Review Profile: R4
真实平台写操作: 本 PR 无；7B/7C 只连接既有正式写能力
连接权威模块:
  Web / CLI / Automation Scheduler / Runtime Task / Review /
  Automation Run / Incident / Outbox / Batch / Operation /
  Attempt / Write Lock / Importer / ShadowBot File Queue
最坏事故:
  重复平台副作用、覆盖人工操作、隐藏 Web 直写污染平台事实、
  UNKNOWN 后二次写、错误 Runtime DB、跨时间边界第二事实、
  legacy root 平行写、generic Executor 绕过正式能力、
  人工恢复覆盖 RUNNING/VERIFIED、查询触发迁移或过期任务、
  Web 退出连带停止 Queue、Automation 未启动、第二 Importer 或第二写链
人工恢复成本: 高
新增数据库表: 0
新增持久化字段: 0
新增状态: 0
新增锁: 0
新增队列/Worker/Importer: 0
新增全局幂等账本: 0
新增 Application Service 外观层: 最多 1 组
```

明确非目标：Web UI 重写、第二平台、第二账号并行、Schema v17、普通 pending Task 自动 COMMIT。

## 4. 当前部署、账号与双时间轴

本阶段冻结：

- 单一 PRA Runtime SQLite；
- 单一 Automation Service 实例租约；
- 单一长期 ShadowBot Worker；
- 单一 `run_shadowbot_queue_services.py` Queue Service 实例锁；
- 当前一个真实平台和一个受控账号；
- 当前账号由部署配置绑定，外部请求不得切换账号；
- 真正多账号支持单独 R4 + Schema 评审；
- 真实平台写继续只走既有 v4/v5 文件队列；
- `automatic_emergency_offline=false` 保持默认值。

双时间轴：

- `platform_trade_date`：18:00 截单；
- `seller_operation_date`：20:00 切换并开始上一经营日结算。

任何重试不得根据新的“当前时间”重新归属业务日期。

## 5. Runtime DB：Current → Migration → Target

### 5.1 Current

当前 `app/web.py` 仍允许：

1. `/runtime/login` GET 从 query 读取 `runtime_db`；
2. `/runtime/login` POST 从 form/query 读取；
3. Session 保存 `runtime_db`；
4. `_runtime_db_for_request` 优先读取 Session，其次页面 query；
5. Path Policy 只限制允许路径，并没有取消请求选择“哪个允许 DB”的权威；
6. `_build_runtime_url`、登录跳转、PRG 和旧页面链接继续传播 `runtime_db`；
7. `/runtime/logout` 也读取 form 中的 `runtime_db`。

### 5.2 7B Target

```text
Web Process Bootstrap / Composition Root
→ 可信启动配置解析唯一 Runtime DB
→ 创建唯一 Repository / Unit of Work factory
→ Handler / Application Service 只接收已绑定 Repository
```

硬规则：

- query、form、JSON、cookie、Session、`next` 不得选择 DB；
- 新 Session 不保存 `runtime_db`；
- PRG/URL 不再传播 DB 作为业务参数；
- `/health` 与全部运营页面使用同一 Repository；
- Mobile Review 使用同一固定 Repository；
- CLI `--runtime-db` 仍仅属于管理员进程启动/运维。

### 5.3 迁移兼容

- 先建立唯一 Web Composition Root；
- 旧 `runtime_db` 参数只做兼容检查，不决定 Repository；
- 旧参数与绑定路径相同：接受一次并重定向到无参数 URL；
- 旧参数与绑定路径不同：明确 4xx/配置错误，绝不静默切库；
- 部署重启自然清除当前内存 Session；新 Session 不再保存 DB；
- `next`、logout、页面筛选、PRG 保留页面上下文但移除 DB 传播；
- 7C 完成引用审计后删除兼容读取。

### 5.4 回归门禁

至少覆盖 login 成功/失败/rate limit、Session rotation、登录 CSRF、业务写 CSRF、PRG、无 DB 参数访问、旧同路径规范化、旧异路径拒绝、伪造 Session DB 不生效、各运营页面回归、logout/`next` 不重新引入 DB 权威。

## 6. 模式合同

- `DIAGNOSTIC`：健康、配置、平台参数、队列和账本读取；不改变业务生命周期。
- `READ_ONLY`：可读取真实平台并持久化扫描/观察审计；对平台零写。
- `DRY_RUN`：预览，不创建正式 Task/Review/真实 Outbox，不触发平台写。
- `APPLY`：可写 Task、Review、Outbox 意图、Run、PREPARED Batch/Operation、受控人工恢复等内部事实；不触发平台副作用。
- `COMMIT`：只消费既有有效授权 Task/Batch/Operation；复用 gate、Batch、Operation、Attempt、写锁、队列、Importer。
- `RECONCILE`：只接受既有 UNKNOWN/NEEDS_RECONCILIATION Operation，只做唯一只读平台核实。
- `RESULT_IMPORT`、`QUEUE_RECOVERY`、`NOTIFICATION_DELIVERY`、`MAINTENANCE`：内部/管理员能力，不接受普通用户 `requested_mode`。

调用方只能降级，不能升级；capability 由 Composition Root 固定绑定，不能来自请求参数。

## 7. 不可破坏的既有资产

必须复用：

- v5 request/validate、proposal/publish/import API；
- `ensure_listing_action_reconcile_attempt`、`ShadowBotExecutor.start_reconcile_attempt` 与 Watchdog 自动 reconcile payload；
- listing sync prepare/publish/import；
- v4 `prepare_task_commit_batch` / `publish_task_commit_batch` / `import_task_commit_result`；
- Review/Token/Outbox 原子服务和自动化 gate；
- Batch/Operation/Attempt、`write_identity_key`、`execution_attempt_id`；
- 共享 UI 写锁；
- Queue Service Result Importer；
- Worker 请求校验、phase、结果发布、登录人工介入；
- Incident/S4 authorization/final fence；
- 现有 `UI_CHANNEL_PRIORITY`，不得复制第二份。

## 8. Final Web Mutation Audit

本节是 7A 的最终 Web mutation 冻结表。任何 `app/web.py` POST/写分支若不在此表或其明确子动作中，7A 不得通过。

| Route / action family | Current | Target capability | Target | 7B/7C |
|---|---|---|---|---|
| `/runtime/login` GET/POST | 认证 + 当前可选 Runtime DB | `RUNTIME_AUTH` | 安全基础设施，不含 DB 选择权 | 7B 迁移 |
| `/runtime/logout` POST | 清 Session + 当前 DB 跳转上下文 | `RUNTIME_AUTH` | 只清 Session | 7B 迁移 |
| `/task-generator` preview | workflow preview | `TASK_PREVIEW` | `DRY_RUN` | 统一 Service |
| `/task-generator` persist | 直接持久化 Task/Review/通知事实 | `TASK_APPLY` | `APPLY` | 统一 Service |
| legacy `/` preview | 受 legacy gate 的独立 preview | `TASK_PREVIEW` | `DRY_RUN` | 7B 委托统一 Service，7C 移除 POST |
| legacy `/` persist | 受 legacy gate 的直接 persist | `TASK_APPLY` | `APPLY` | 7B 委托统一 Service，7C 移除 POST |
| `/reviews`、`/runtime` resolve | Review/Task 更新 | `REVIEW_APPLY` | `APPLY` | 参数化复用 |
| `/mobile/review/*` resolve | Review/Task/Incident 原子处置 | `MOBILE_REVIEW_APPLY` | `APPLY` | 原样复用 |
| `/execution-logs` GET | 执行/Queue 查询 | `EXECUTION_LOG_QUERY` | `READ_ONLY` | 保留 |
| `/execution-logs` `start_shadowbot_reconcile` | 启动只读 reconcile attempt | `UNKNOWN_RECONCILE` | `RECONCILE` | 保留唯一恢复入口 |
| `/execution-logs` `confirm_shadowbot_manual_handled` | 当前无状态白名单地写 `MANUAL_HANDLED` | `MANUAL_OPERATION_RECOVERY` | admin `APPLY` | 7B 加资格/并发/CAS 门禁 |
| `/business-inputs` 正常业务输入动作 | 写受控 workbook | `WORKBOOK_MAINTENANCE` | 管理员维护 | 不进平台控制面 |
| `/business-inputs` `save_listing_status` | **隐藏 debug POST，直接 upsert Runtime `listing_status`** | `DEBUG_LISTING_STATUS_WRITE` | **production DENY** | 7B 禁用；调试迁出生产 Web |
| legacy `/tables` | workbook maintenance | `WORKBOOK_MAINTENANCE` | 管理员维护 | 7C 归档/重定向 |
| legacy `/execution` | mock/simulation 文件副作用 | `LEGACY_SIMULATION` | 维护/测试 | 7C 归档 |
| legacy `/manual-intervention` | 只读；正式 resolve 已拒绝 | `LEGACY_MANUAL_READ` | `READ_ONLY` | 归档候选 |
| `/system/test-feishu-notification` | 发送受控测试通知 | `NOTIFICATION_DIAGNOSTIC` | 管理员运维 | 不进业务 Service |

`/dashboard`、`/tasks`、legacy `/runtime` 和 CLI `list-tasks` 的 **Current** 并非真正零写：它们会进入
`app.services.workflow.list_runtime_tasks`，后者先调用 `init_schema()`，再调用
`expire_overdue_pending_tasks()`，最后才查询。`init_schema()` 还可能执行 Runtime Schema 迁移。因此这些
入口的 Target 才是 `RUNTIME_QUERY / READ_ONLY`；入口盘点不得用目标标签覆盖当前风险。

`/notifications`、`/system`、`/health` 的查询/诊断同样必须保持零业务生命周期写；若健康检查需要读取
Schema 健康，只能使用只读连接，不得顺带初始化或升级。

### 8.1 `save_listing_status` Current 风险

当前隐藏 POST：

```text
action=save_listing_status
```

直接调用 `SQLiteRuntimeRepository.upsert_listing_status`，可写：

- `current_price`；
- `platform_stock_qty=100`；
- `online_status`；
- `source=debug_web_request`；
- 以及 manual upsert 对观察来源字段的投影/清理。

它不经过 ShadowBot READ_ONLY request/validate、结果绑定和正式 Importer，因此不能被视为平台观察事实的合法来源。

### 8.2 7B/7C 冻结处置

- production Web 对 `save_listing_status` 明确返回拒绝，例如 `403 DEBUG_LISTING_STATUS_WRITE_DISABLED`；
- 拒绝必须发生在任何 Repository 写之前；
- 不接入 `ControlPlaneApplicationService`；
- 如测试仍需要人工构造状态，只能迁到隔离 dev/test helper 或测试 DB 工具；
- dev/test 工具产生的状态不得满足正式平台观察资格（不得伪造 ShadowBot attempt/evidence）；
- `docs/shadowbot_listing_status_integration.md` 中“保留该调试 Web POST”的描述从本计划起降级为 **历史 Current 事实**，不再代表目标合同；7B 禁用该动作时必须同步改写/删除该段说明；
- 7B 测试必须构造隐藏 POST，断言被拒绝且 `listing_status` 零变化。

### 8.3 查询零写合同

7B 必须把查询与维护拆开：

- `list_runtime_tasks`、Dashboard Presenter、Task Presenter 和 CLI list adapter 只接收已经完成 bootstrap
  的 Repository；查询函数内部不得调用 `init_schema()`、迁移、过期任务或其他 maintenance；
- Runtime Schema 初始化/迁移只允许可信进程 bootstrap 或显式管理员 maintenance；Web GET、CLI list、
  Presenter 和模板渲染都无权触发；
- 过期 Task/Review 的推进改由既有 Automation Service 维护窗口或显式管理员 `APPLY`，必须有 Run/Event
  或管理员审计，不能伪装成一次读页面；
- 只读入口可以更新纯进程内缓存/指标，但不得改变 Runtime DB、workbook、队列、Outbox、文件证据或平台；
- 测试以同一 DB 的全表内容哈希/`total_changes` 与 Schema version 前后对比证明零写，不能只断言页面返回 200。

必须覆盖 `/dashboard` GET、`/tasks` GET、legacy `/runtime` GET、CLI `list-tasks`、空库/旧版本库拒绝以及
已过期 Task 存在时的查询；旧版本库应返回明确“需要管理员迁移”，不得由查询静默升级。

## 9. 完整入口矩阵与服务生命周期

### 9.1 盘点基线与纠错规则

本节不另造一份缩略脚本清单，而是以
[`task13_5_0_kickoff_baseline.md` §4](task13_5_0_kickoff_baseline.md#4-脚本与入口盘点)
的 **全部文件** 为底表，再叠加截至本 PR 基线的代码差异与纠错。底表 §4.1–§4.5 中没有在下表
逐项重复的文件，其分类、生产可达性和退役要求仍全部有效。

必须先纠正底表的一个历史误分类：`scripts/reconcile_shadowbot_listing_skus.py` 是对
`listing_status.internal_sku` 做 DRY_RUN/APPLY 的数据修正工具，**不是** UNKNOWN 后的唯一平台
RECONCILE。该文件不得取得 `UNKNOWN_RECONCILE` capability。

13.5-0 之后新增、因此必须叠加到完整清单的入口：

| 文件 | Current | Capability / mode | Composition Root | 结论 |
|---|---|---|---|---|
| `scripts/run_automation_service.py` | 独立计划、租约、Run/Handler 进程 | `AUTOMATION_SERVICE` / internal | Automation bootstrap | 正式服务，保留 |
| `scripts/compile_product_mappings.py` | 编译/校验商品映射文件 | `MAPPING_MAINTENANCE` / maintenance | 管理员 CLI | 保留，不进入平台控制面 |
| `scripts/run_task13_5_4_order_readonly_acceptance.py` | 受控订单只读验收 | `ACCEPTANCE_READ_ONLY` / acceptance | 验收工具 | 仅验收，不进入生产调度 |
| `scripts/shadowbot_windows_host_helper.ps1` | 受控 Windows 宿主恢复 | `HOST_RECOVERY` / internal admin | Automation Incident recovery | 保留 fail-closed，不由 Web 直接启动 |

`scripts/local_env.ps1` 是本机私有配置而非入口，继续不提交；`scripts/local_env.example.ps1` 是配置
模板而非业务 capability。根目录 `start_web.bat` 仅是 Web bootstrap，保持 ASCII + CRLF。

### 9.2 正式长驻服务与启动入口

| Entry / component | Current behavior | Target capability / mode | Composition Root | 保留/迁移 |
|---|---|---|---|---|
| `pra serve-web` / `start_web.bat` | Web 进程 | `WEB_SERVICE` / bootstrap | Web bootstrap 固定唯一 Runtime Repository | 保留 |
| `scripts/start_local.ps1` | 当前把 Queue 作为 Web-owned 子进程启动，并在 Web 退出时停止；不启动 Automation | `WEB_DEV_BOOTSTRAP` | Web bootstrap | 7B 移除 Queue 所有权，降为 Web-only 本地兼容启动器 |
| `scripts/run_shadowbot_queue_services.py` | Queue 单实例长驻进程 | `QUEUE_SERVICE` / internal | Queue bootstrap | 正式服务，独立启停/健康 |
| Queue login monitor | 登录人工介入事实 | `LOGIN_MONITOR` / internal | Queue Service | 原样复用 |
| Queue v2/v4/v5 Importer | 结果校验、回写、归档 | `RESULT_IMPORT` / internal | Queue Service | 唯一正式 v2/v4/v5 Importer |
| Queue Watchdog/auto reconcile | 租约恢复、唯一 RECONCILE | `QUEUE_RECOVERY` / internal | Queue Service | 原样复用 |
| Queue review reminders | 复核续期/提醒 | `REVIEW_MAINTENANCE` / internal | Queue Service | 保留现有所有权 |
| Queue notification delivery/watchdog | Outbox 投递/租约恢复 | `NOTIFICATION_DELIVERY` / internal | Queue Service | 保留现有所有权 |
| `scripts/run_automation_service.py` | 计划、租约、Run、扫描/日结/Incident handler | `AUTOMATION_SERVICE` / internal | Automation bootstrap | 正式服务，独立启停/健康 |
| ORDER_SCAN observation importer | 订单只读结果导入 | `ORDER_OBSERVATION_IMPORT` / internal | Automation Handler | 保持独立于 v2/v4/v5 Queue Importer |

独立生命周期冻结为：

- Web、Queue Service、Automation Service 是三个独立进程；任一进程退出不得连带停止另外两个；
- 每个进程分别拥有 start/status/health/stop 运行手册、PID/单实例边界和日志；`start_local.ps1` 不再
  充当 Queue 的隐式父进程，也不得让“Web 可访问”冒充完整闭环已启动；
- 完整闭环 ready 必须同时证明 Web health、Queue 单实例锁/Importer/Watchdog 健康、Automation
  租约/heartbeat 健康以及三者绑定同一规范化 Runtime DB；
- 7B 同步更新 README 与运行环境手册，写清三个进程的独立命令；增加“停止 Web 后 Queue/Automation
  仍运行”“缺少任一服务时整体 readiness 降级但其他服务不被杀死”的生命周期测试。

### 9.3 主 CLI

| 子命令 | Current / Target capability | Mode | Composition Root | 结论 |
|---|---|---|---|---|
| `templates` | `WORKBOOK_TEMPLATE_MAINTENANCE` | maintenance | 管理员 CLI | 保留 |
| `validate`、`import-data` | `SOURCE_VALIDATE` | diagnostic/maintenance | 管理员 CLI | 保留；不等于 Result Importer |
| `preview-tasks` | `TASK_PREVIEW` | `DRY_RUN` | 目标统一 Application Service | 薄适配 |
| `generate-tasks` | legacy workbook export | maintenance | legacy CLI | 兼容，不进 Runtime 主线 |
| `mock-ai-decision` | mock preview | `DRY_RUN` | test CLI | 测试隔离 |
| `simulate-execution` | legacy simulation | maintenance | legacy CLI | 归档候选 |
| `list-manual-tasks` | legacy read | `READ_ONLY` | CLI query adapter | 只读兼容；必须满足 §8.3 |
| `resolve-manual-task` | denied | none | CLI adapter | 保持硬失败 |
| `init-runtime-db` | `RUNTIME_SCHEMA_MAINTENANCE` | maintenance | 可信管理员 bootstrap | 唯一显式 Schema 初始化/迁移入口之一 |
| `health`、`check-runtime-health` | `RUNTIME_HEALTH` | `DIAGNOSTIC` | read-only health adapter | 保留，禁止隐式迁移 |
| `generate-runtime-tasks` | `TASK_APPLY` | `APPLY` | 目标统一 Application Service | 薄适配 |
| Runtime/Review list commands | `RUNTIME_QUERY` | `READ_ONLY` | CLI query adapter | 保留；移除隐式 init/expire |
| `resolve-review-task` | `REVIEW_APPLY` | `APPLY` | existing atomic review service | 参数化复用 |
| `expire-review-tasks` | `REVIEW_MAINTENANCE` | `DRY_RUN`/显式 `APPLY` | existing review service | 管理员或 Automation 维护 |
| `notification-worker` | `NOTIFICATION_DELIVERY` | internal | notification worker | 保持独立 Worker |
| `serve-web` | `WEB_SERVICE` | bootstrap | Web Composition Root | 固定唯一 Repository |

### 9.4 业务、平台与人工入口

| Entry / subcommand | Current behavior | Target capability / allowed mode | Composition Root | 保留/退役 |
|---|---|---|---|---|
| `evaluate_business_rules.py --dry-run` | evaluator 预览 | `TASK_PREVIEW / {DRY_RUN}` | 统一 Application Service | 保留薄 CLI |
| `evaluate_business_rules.py --apply` | 写 Task/Review/通知 | `TASK_APPLY / {APPLY}` | 统一 Application Service | 保留薄 CLI |
| `run_shadowbot_listing_sync.py` | 随机默认 Batch/Attempt，直接 prepare+publish；硬编码 Schema v13 | `MANUAL_SCAN_TRIGGER / {READ_ONLY}` | 既有 Automation scan Service | 7B 参数化迁移，禁止沿用 Current 直发 |
| `run_shadowbot_commit_batch.py prepare` | 写 PREPARED 账本 | `AUTHORIZED_WRITE_PREPARE / {APPLY}` | existing v4 service | 保留薄 CLI |
| 同脚本 `publish` | 投递既有 PREPARED | `AUTHORIZED_WRITE_COMMIT / {COMMIT}` | existing v4 service | 保留受控入口 |
| 同脚本 `production-run` | prepare→publish composite | `AUTHORIZED_WRITE_COMMIT / {COMMIT}` | existing v4 service | 兼容；不得扫描全部 pending |
| 同脚本 `import-result` | 手工执行正式 v4 Importer | `LEGACY_RESULT_IMPORT / internal admin` | legacy CLI | 7C 引用审计后由 Queue Service 取代 |
| `run_shadowbot_executor.py start` | 外部可选 READ_ONLY/COMMIT/RECONCILE | `LEGACY_GENERIC_EXECUTION` / 新控制面无 allowed mode | legacy Executor | 7C 退役/测试隔离 |
| 同脚本 `import-result` | 手工结果导入 | `LEGACY_RESULT_IMPORT / internal admin` | legacy Executor | 7C 退役，不保留第二正式 Importer |
| 同脚本 `poll-yingdao-result` | 外部 runner 结果轮询 | `LEGACY_RUNNER_DIAGNOSTIC` / diagnostic | legacy Executor | 引用审计后隔离 |
| 同脚本 `check-yingdao-app-params` | 影刀参数检查 | `RUNNER_DIAGNOSTIC / diagnostic` | 管理员 CLI | 保留运维诊断或抽取 |
| `repair_shadowbot_expired_attempt.py` | 生成受控 expired rejected result 并直接 `import_one` | `QUEUE_REPAIR_RESULT_IMPORT / internal admin` | 隔离管理员修复 | 仅精确 attempt、Queue 停止/无竞态时使用；不得成为常规第二 Importer |
| `reconcile_shadowbot_listing_skus.py` dry-run/apply | SKU 数据修正 | `SKU_DATA_MIGRATION / {DRY_RUN, MAINTENANCE}` | 管理员迁移工具 | 保留并纠正命名误导；绝非平台 RECONCILE |
| `run_mock_platform_executor.py --dry-run/--apply` | 修改 Mock DB/测试 execution log | `MOCK_PLATFORM_LAB / test only` | Mock composition root | 测试隔离，生产不可达 |
| `create_sample_workbooks.py` | 示例 workbook | `SAMPLE_DATA / maintenance` | 开发工具 | 不进入运营主线 |
| `generate_shadowbot_markdown_report.py` | 报告生成 | `REPORT_EXPORT / READ_ONLY` | 报告 adapter | 保留薄适配或下沉服务 |
| `compile_product_mappings.py` | 映射编译 | `MAPPING_MAINTENANCE / maintenance` | 管理员 CLI | 保留 |

正式人工 READ_ONLY 不再等同于直接运行 `run_shadowbot_listing_sync.py`：7B 必须调用既有 Automation
scan service，创建 `origin_type=MANUAL` 的稳定 Automation Run/intent，复用 `UI_CHANNEL_PRIORITY`、
租约、父子 Run、Worker、Importer 和完成接口。调用方可选择既有 scan capability/目标日期，但不能提供
随机身份或提升模式。listing sync 的 Schema 检查改用 `LATEST_RUNTIME_SCHEMA_VERSION`；订单人工只读也走
同一入口，不再新增脚本/队列/Importer。

### 9.5 运维、验收与归档入口

- 13.5-0 §4.3 的 `check_runtime_env.py`、readiness/worker health、backup、ShadowBot sync/hash、
  migration、evidence share 全部继续按管理员运维分类；其中 `repair_shadowbot_expired_attempt.py` 的
  额外副作用边界已在 §9.4 单独覆盖；
- `shadowbot_windows_host_helper.ps1` 仅能由已启用且通过 final fence 的 Incident host recovery 调用，
  普通 Web/CLI 不得触发；
- 13.5-0 §4.4 列出的每一个证据导出、故障注入、prepare、验收、Linux/Windows/packaging/verify 脚本，
  加上 `run_task13_5_4_order_readonly_acceptance.py`，统一为 `ACCEPTANCE_ONLY`：不注册到生产 Composition
  Root，不取得 Scheduler/COMMIT/Importer capability；
- 13.5-0 §4.5 的四个归档候选继续保留引用审计门禁；`run_shadowbot_e2e_local_demo.py` 不得成为正式入口。

### 9.6 legacy generic Executor Current → Target

`run_shadowbot_executor.py start` 当前真实 accepted modes 为 `READ_ONLY / COMMIT / RECONCILE`；`COMMIT`
只是 argparse 默认值。7B 新 Application Service 永远不调用 generic `start`，新 Web/Scheduler/CLI 不透传
用户 `execution_mode`：正式 READ_ONLY 走 Automation scan，正式 COMMIT 走 v4/v5 已授权发布，正式
RECONCILE 走 existing UNKNOWN Operation 的唯一 reconcile service。7C 完成 call-site、Runbook、部署和
测试引用审计后退役或隔离 generic `start`，且不新增调用方。

## 10. Capability 绑定硬门禁

高风险绑定：

| Entry | Target capability | Allowed mode |
|---|---|---|
| task generator preview | `TASK_PREVIEW` | `{DRY_RUN}` |
| task generator persist | `TASK_APPLY` | `{APPLY}` |
| Dashboard/Task/legacy Runtime/CLI list | `RUNTIME_QUERY` | `{READ_ONLY}`，零 Schema/业务写 |
| execution logs query | `EXECUTION_LOG_QUERY` | `{READ_ONLY}` |
| execution logs reconcile | `UNKNOWN_RECONCILE` | `{RECONCILE}` |
| execution logs manual handled | `MANUAL_OPERATION_RECOVERY` | `{APPLY}` admin only |
| hidden `save_listing_status` | `DEBUG_LISTING_STATUS_WRITE` | **production none / denied** |
| formal manual listing/order scan | `MANUAL_SCAN_TRIGGER` | `{READ_ONLY}`，通过 Automation scan service |
| current listing sync direct CLI | `LEGACY_LISTING_SYNC_DIRECT` | **不进入新业务白名单** |
| commit prepare | `AUTHORIZED_WRITE_PREPARE` | `{APPLY}` |
| commit publish | `AUTHORIZED_WRITE_COMMIT` | `{COMMIT}` |
| v5 listing publish | `LISTING_ACTION_COMMIT` | `{COMMIT}` |
| Queue Service Importer | `RESULT_IMPORT` | internal only |
| Queue Watchdog | `QUEUE_RECOVERY` | internal only |
| expired attempt repair | `QUEUE_REPAIR_RESULT_IMPORT` | internal admin、精确 attempt only |
| SKU reconcile script | `SKU_DATA_MIGRATION` | `{DRY_RUN, MAINTENANCE}`，绝非 `RECONCILE` |
| Mock Platform executor | `MOCK_PLATFORM_LAB` | test only，production none |
| generic executor start | `LEGACY_GENERIC_EXECUTION` | **不进入新业务白名单** |

每一个具体 route action、CLI subcommand 和脚本先由 Composition Root 绑定上述 capability，再校验 mode。
请求中的 `requested_mode`、`execution_mode` 或 action 名称不能改变 capability；未知 entry/action fail closed。

## 11. 业务意图、来源与 canonical identity

三层必须分离：

- `business_intent_identity`：稳定业务目标；
- `invocation_identity`：Web/CLI/Scheduler/人工来源审计；
- Task `origin_type/origin_ref_id`：持久化任务来源。

`invocation_identity` 可包含 actor、trigger、requested_at、manual_reason、trace 等，但这些审计字段不得参与业务幂等身份。

### 11.1 canonical identity

| Capability | Canonical identity | 审计排除 |
|---|---|---|
| Automation | 既有 `logical_run_key` | 当前时间、进程实例 |
| Task preview/apply | manifest + 冻结日期 + scope/action + policy/rule version + logical target | actor、requested_at、entrypoint |
| Review apply | `review_task_id` + resolution + normalized adjustment hash | actor、note、requested_at |
| Manual operation recovery | `operation_id` + `confirm_manual_handled` + `reason_code` + evidence type/hash | actor、free-text note、requested_at |
| Listing sync | platform + scan type + frozen run/date + mapping manifest | 当前时间、入口 |
| Commit prepare/publish | existing task/batch/operation/write identity + approved hashes | CLI actor、publish time |
| Reconcile | existing operation + source attempt + reconcile contract version | retry time、note |
| Importer | result ID + result hash + execution attempt | discovery time |
| Emergency | authorization ID + immutable payload hash + final fence scope | entrypoint |

不新增全局 identity 表。

### 11.2 18:00/20:00 重试

权威顺序：已有 Task/Review/Run/Batch/Operation/Authorization → 首次 canonical identity → 服务端验证的显式目标日期 → 仅首次无权威对象时由 OperationalTimeService 计算。

同 reference + 同 canonical payload 返回已有结果；同 reference + 不同 payload 冲突；新业务意图必须新 reference。

## 12. 分层幂等

继续复用：

- Adapter stable reference + canonical fingerprint；
- Automation `logical_run_key`；
- Task `dedupe_key`；
- Review/Outbox 稳定业务键；
- Batch/Operation IDs + `write_identity_key`；
- Attempt `execution_attempt_id`；
- Operation + unique reconcile attempt；
- Importer result/receipt/attempt/hash。

不得创建全局幂等账本。

## 13. Operation Transition Final Audit

7B 不得新增第二状态机。Operation 状态仍以现有 `OperationStatus` 为准：

```text
PENDING
RUNNING
FAILED
RETRY_AUTHORIZED
NEEDS_RECONCILIATION
VERIFIED
NOT_APPLIED
MANUAL_REVIEW
MANUAL_HANDLED
```

非人工恢复的既有 transition 继续由 Executor、Retry Authorization、Importer/Watchdog 的原子方法拥有；新 Application Service 只委托，不复制 transition 逻辑。

必须审计并复用以下权威 mutation families：

- attempt claim/lease；
- start outcome；
- result import/attempt completion；
- retry authorization；
- unique reconcile start/result；
- manual recovery。

任何 direct SQL 或 `update_shadowbot_operation_status` 调用若绕过这些权威方法，7B 不得通过。

## 14. `MANUAL_OPERATION_RECOVERY` 最终合同

### 14.1 Current 问题

当前 `confirm_manual_handled` 只检查 Operation 存在，然后直接：

- `status = MANUAL_HANDLED`；
- `lock_owner = ''`；
- 写 success execution log。

它没有状态资格、活动 Attempt、写锁、retry authorization 或并发 RECONCILE 门禁。

### 14.2 允许状态白名单

`confirm_manual_handled` 不是通用“解除卡住”按钮。它只表达：操作人员已经在平台完成原批准动作，
且后续正式 READ_ONLY 结果已验证目标状态。generic action 的来源状态白名单仅为：

```text
FAILED + latest terminal attempt.side_effect_state in {NOT_STARTED, NOT_APPLIED}
```

这不是说“未执行也算已处理”：若没有人工完成动作，只能走既有 Retry Authorization 或明确取消流程；
只有人工完成后、正式只读结果证明批准目标已经成立，才允许 `MANUAL_HANDLED`。

状态/动作矩阵：

| Current Operation | 允许路径 | Operation/Task/共享写锁结果 | generic `confirm_manual_handled` |
|---|---|---|---|
| `PENDING` / `RUNNING` | 既有执行/Watchdog | 不变 | 拒绝 |
| `FAILED` + `NOT_STARTED/NOT_APPLIED` | 无人工动作则 Retry Authorization；人工已完成且 Importer 证据验证目标时受控 close | close 时 Operation→`MANUAL_HANDLED`、Task→`SUCCESS`、锁必须此前已 `RELEASED`/不存在 | 仅后一种允许 |
| `NOT_APPLIED` | 既有 Retry Authorization 或显式取消 | 不变直到权威路径推进 | 拒绝 |
| `RETRY_AUTHORIZED` | 消费现有授权创建合法新 Attempt | 不变直到结果导入 | 拒绝 |
| `NEEDS_RECONCILIATION` | **唯一 READ_ONLY RECONCILE** | 未决时 Task/UNKNOWN 写锁保持；结果只由 reconcile Importer 推进 | 拒绝，人工证据不能旁路 |
| `VERIFIED` | 已由 Importer 终结 | 已完成事实不变 | 拒绝 |
| `MANUAL_REVIEW` | 按 `quarantine_reason` 和 attempt evidence 进入专用 resolver | 见下文；不得永久悬挂，也不得一键放行 | generic action 拒绝 |
| `MANUAL_HANDLED` | 仅同 canonical identity 精确重放 | 全部事实不变 | 返回 `ALREADY_HANDLED` |

`MANUAL_REVIEW` 专用 resolver 不新增状态，按事实分流：

1. 任一相关 Attempt 仍是 `STARTING/RUNNING`：拒绝并由 Watchdog 处理；
2. 任一相关 Attempt 副作用未知：转入/保持 `NEEDS_RECONCILIATION`，保留 `UNKNOWN` 共享写锁，启动唯一 RECONCILE；
3. 全部相关 Attempt 已确定 `NOT_STARTED/NOT_APPLIED`：进入既有 manual Retry Authorization 或明确取消；
4. 正式 Importer 已验证批准目标完成，或人工完成后新的正式 READ_ONLY Importer 证据验证目标：允许专用
   controlled close，把 Operation/Task/共享写锁一次性收敛；
5. `DUPLICATE_ACTIVE_COMMIT_ATTEMPT` 等隔离原因必须先证明所有重复 Attempt 都已终止，并逐个归类；不得只改
   Operation 状态掩盖重复 Attempt。

所以 `MANUAL_REVIEW` 不再被简单列为“永远拒绝”，也不加入 generic 白名单；它有明确、按原因可完成的恢复路径。

### 14.3 并发与资格门禁

人工确认必须在同一数据库事务/CAS 中读取并校验全部权威对象：

1. Operation 存在，状态/最新 terminal Attempt 符合 §14.2；
2. `shadowbot_operations.lock_owner` 为空；它只是 Operation claim owner，**不能代替**共享业务写锁；
3. `shadowbot_write_locks` 对该 Operation 不存在，或已经是 `RELEASED`；generic recovery 不得把
   `ACTIVE/UNKNOWN` 写锁直接释放；
4. 无 `STARTING/RUNNING` Attempt、无活动 RECONCILE、无 ACTIVE retry authorization；
5. Task 仍绑定该 Operation/批准 payload，且没有被 supersede、取消或由其他结果推进；
6. 证据来自已校验并导入的正式 READ_ONLY/RECONCILE 结果，证明平台当前状态等于该 Operation 的批准目标；
7. 只有全部条件成立才执行一次原子收敛；不得先清 `lock_owner`、先释放共享锁或先写成功日志。

专用 `MANUAL_REVIEW` controlled close 可在同一事务把属于该 Operation 的 `ACTIVE/UNKNOWN` 共享锁改为
`RELEASED`，但前提是 reason-specific resolver 已完成 §14.2 的全部 Attempt 归类且正式 Importer 证据已证明
目标状态；它不能复用 generic action 绕过这些条件。

非法状态或竞态统一返回 conflict/blocked，Operation、Attempt、共享 Lock、Task 和执行日志均不得改变。

### 14.4 幂等与异内容冲突

canonical recovery identity：

```text
operation_id
+ action=confirm_manual_handled
+ reason_code
+ evidence_type
+ evidence_hash
```

`actor`、free-text `note`、`requested_at` 仅审计。`evidence_type` 只接受服务端白名单（例如已导入的
`READ_ONLY_TARGET_VERIFIED`），不接受自由文本或客户端自报“已检查”。`evidence_hash` 由服务端对以下
canonical JSON 计算 SHA-256：contract version、operation/task、approved payload hash、平台/商品身份、
证据 result/attempt/observation ID、规范化已验证结果；UTF-8、键排序、无无意义空白。客户端不能直接指定 hash。

- 首次合法确认：一个 `BEGIN IMMEDIATE`/Repository 原子方法同时完成 Operation→`MANUAL_HANDLED`、
  Task→`SUCCESS`、必要的 reason-specific 共享锁→`RELEASED` 和一条成功审计 log；
- 原 Attempt 保持不可变 terminal 事实，不改写成成功；人工处置通过 Operation/Task projection 和审计表达；
- 已是 `MANUAL_HANDLED` 且 canonical recovery identity 相同：返回 `ALREADY_HANDLED`，不重复写成功 log；
- 已是 `MANUAL_HANDLED` 但 canonical identity 不同：409 conflict；
- 不新增表/字段；必要的 recovery identity/evidence 可写入现有 execution log `raw_output`。

`listing_status`、平台价格/上下架位置、订单观察等平台事实只能由现有正式 Importer 写入。人工恢复事务
只引用已经导入的 evidence ID/hash，绝不根据人工 note 或 Web 表单直接“补写平台成功”。

## 15. 优先级

### 15.1 恢复门禁

`UNKNOWN / 唯一 RECONCILE` 高于所有正常写和扫描，并阻断同平台/同商品不安全新写。

### 15.2 业务写 lane

```text
Incident 人工复核任务
→ SYSTEM_EMERGENCY
→ 普通授权写任务
```

### 15.3 Automation UI 作业顺序

直接复用现有 `UI_CHANNEL_PRIORITY`：

```text
UNKNOWN_OR_RECONCILE              = 0
SYSTEM_EMERGENCY_SET_OFFLINE      = 10
AUTHORIZED_WRITE                  = 20
PRE_CUTOFF_FULL_SCAN              = 30
POST_CUTOFF_PULSE                 = 35
PLATFORM_TRADE_DAY_SETTLEMENT     = 40
FULL_MARKET_SCAN                  = 50
ONLINE_PULSE                      = 60
```

不得复制、重建或压平。

## 16. 目标调用图

```text
Web / CLI / Manual / Scheduler adapters
             │
             ▼
ControlPlaneApplicationService（最多一组）
  ├─ OperationalTimeService
  ├─ Runtime Task / Review / Incident
  ├─ Automation Repository/Service
  ├─ existing v4/v5 services
  └─ non-persistent ApplicationResult

Web Composition Root
  ├─ fixed Runtime Repository
  ├─ fixed platform/current account
  ├─ queue/adapter
  └─ fixed EntryPointCapability

ShadowBot Worker
  ├─ v2/v4/v5 result → Queue Service Importer
  └─ ORDER_SCAN result → Automation observation importer
```

统一 Service 不操作页面、不拼队列 JSON、不持有第二状态机、不接收请求级 Runtime DB，也不接收 generic execution mode 作为权限。

## 17. ApplicationResult

允许投影：`CREATED / ALREADY_EXISTS / ALREADY_RUNNING / ALREADY_HANDLED / DEFERRED_BY_HIGHER_PRIORITY / BLOCKED_BY_REVIEW / BLOCKED_BY_UNKNOWN / BLOCKED_BY_UI_CHANNEL / BLOCKED_BY_IDENTITY / MIGRATION_REQUIRED / CONFLICT / READ_ONLY_COMPLETED / DRY_RUN_COMPLETED / FAILED_BEFORE_SIDE_EFFECT / START_UNKNOWN / NEEDS_RECONCILIATION / COMPLETED`。

它只是非持久化适配器投影，必须同时返回底层领域状态、错误码、canonical intent、invocation audit、关联 Run/Task/Review/Batch/Operation/Attempt IDs、副作用和安全重试建议。不得形成第二生命周期。

## 18. 事务与恢复边界

- Task/Review/Token/Outbox/Incident Event 继续走现有原子服务；
- Automation claim/lease/Run Event 继续走 Automation Repository；
- PREPARED Batch/Operation 属于 APPLY，不等于平台副作用；
- v4/v5 gate、Batch、Operation、Attempt、写锁和 Task running 继续走既有事务；
- Queue Service Importer 是 v2/v4/v5 结果主线；
- ORDER_SCAN 继续由 Automation importer；
- Web/CLI query 不得执行 Schema 初始化、迁移或 Task/Review 过期推进；
- Web 不得在 Importer 之外“补写平台成功”；
- `save_listing_status` 不得成为平台观察替代入口；
- UNKNOWN 后只允许唯一 RECONCILE；
- manual recovery 必须按 §14 把 Operation/Attempt/Task/共享写锁/日志一次性收敛；
- Web、Queue、Automation 独立持有生命周期，Web 退出不得停止另外两个进程。

## 19. 保留、迁移与退役

保留：Automation Service、Queue Service、business rules、参数化后的正式人工 scan trigger、commit batch
prepare/publish、主 CLI Runtime 查询/复核/APPLY、正式 Web query/review/reconcile 能力，以及 13.5-0 全量
运维/验收工具分类。

7B 迁移：

- Web Runtime DB 请求级权威 → fixed Composition Root；
- `list_runtime_tasks` 等 query → 零 Schema/业务写 Presenter；Schema migration 和过期推进迁到 bootstrap/Automation maintenance；
- legacy `/` preview/persist → 同一 Application Service；
- execution-log manual recovery → §14 门禁；
- `save_listing_status` → production deny；
- `run_shadowbot_listing_sync.py` → 既有 Automation scan service 的稳定 MANUAL Run adapter，并使用 `LATEST_RUNTIME_SCHEMA_VERSION`；
- `start_local.ps1` → Web-only 本地启动；Queue/Automation 独立启停和健康；
- generic Executor 不新增调用方。

7C 退役：

- legacy `/` POST；
- Web Runtime DB query/form/session/URL 兼容读取；
- generic Executor `start`/legacy imports；
- commit-batch manual `import-result`；
- legacy Excel manual paths、阶段性 demo/baseline 工具。

删除前完成 call-site、Runbook、部署、测试和运维引用审计。

## 20. 测试矩阵

### 20.1 Web mutation

- 每个正式 POST/action 对应唯一 target capability；
- 构造隐藏 `save_listing_status` POST → 生产明确拒绝；
- 拒绝前后 `listing_status` 完全不变；
- 不产生 `debug_web_request` 正式平台事实；
- legacy `/` 与 `/task-generator` 同 Service/canonical identity；
- `/execution-logs` GET、RECONCILE、manual recovery 三能力分离；
- CSRF、登录和 PRG 保持。

### 20.2 Query zero-write

- `/dashboard`、`/tasks`、legacy `/runtime` GET 和 CLI `list-tasks` 前后全表内容、Schema version、
  `connection.total_changes` 均不变；
- 已过期 Task/Review 存在时，查询仍零写；显式 Automation/admin maintenance 才推进，并留下审计；
- 空库或旧版本库由 query 明确拒绝并提示管理员 bootstrap/migration，不静默创建表或升级；
- `/health` 只读报告 Schema 状态，不调用 `init_schema()`。

### 20.3 Manual operation recovery

必须覆盖：

- `FAILED + NOT_STARTED/NOT_APPLIED` 但没有已导入目标验证：拒绝并提示 retry/cancel；
- `FAILED + NOT_STARTED/NOT_APPLIED`、人工已完成且正式 Importer 证据验证批准目标：合法 controlled close；
- `NEEDS_RECONCILIATION` 无论是否附人工文本/附件都不能旁路，只能唯一 RECONCILE；
- `PENDING/RUNNING/VERIFIED/NOT_APPLIED/RETRY_AUTHORIZED` 全拒绝；
- `MANUAL_REVIEW` generic action 拒绝；按 quarantine reason 覆盖活动 Attempt、UNKNOWN、确定未执行、
  Importer 已验证四个专用分支，并证明不会永久悬挂；
- 活动 STARTING/RUNNING Attempt 拒绝；
- 活动 RECONCILE 拒绝；
- 非空 Operation `lock_owner` 拒绝；共享写锁 ACTIVE/UNKNOWN 对 generic close 拒绝；
- ACTIVE retry authorization 拒绝；
- controlled close 同事务更新 Operation/Task/允许的共享锁/单条日志，Attempt 不变；任一步失败整体回滚；
- 人工 note/表单不能写 `listing_status` 或其他平台事实，只有现有 Importer 可以写；
- 同 canonical recovery 重放幂等且不重复 log；
- 异 canonical recovery 返回 conflict；
- 非法请求前后 Operation/Attempt/Task/Lock/日志零变化。

### 20.4 Runtime DB

覆盖 login/session/CSRF/PRG、同路径兼容、异路径拒绝、伪造 Session DB、各运营页同 Repository、旧书签不静默切库。

### 20.5 入口、生命周期、幂等与优先级

覆盖 13.5-0 §4 全量入口 + §9 delta 无遗漏、每个高风险子命令 capability 绑定、Web 双击、CLI 重跑、
Scheduler 重启、同引用精确重放、异内容冲突、18:00/20:00 跨界、PREPARED/COMMIT 分离、Queue
Importer 重放、Watchdog、Review/Outbox 原子回滚、完整 UI priority、人工任务压制 emergency、
UNKNOWN→唯一 RECONCILE、正式人工 listing/order scan 复用 Automation Run/租约/priority/Importer、
Schema v16+ 不再被 listing sync 的 v13 常量拒绝，以及 Web/Queue/Automation 独立启动/停止/健康。

### 20.6 Ready

- 完整 pytest；
- 系统冒烟；
- Linux Core；
- Windows Core；
- `git diff --check`；
- 工作区清洁；
- 修改凭据/日志/通知/配置时 Secret Scan。

当前 Head 必须有自己的 CI 结果，不能沿用旧 Head。

## 21. Final Audit / Freeze Gate

7A 最终复审一次性检查以下六组：

1. **Web mutation inventory**：所有生产/legacy/debug POST 写入口均已分类，尤其 `save_listing_status`；
2. **Query zero-write**：Web/CLI 查询不再隐式迁移 Schema 或推进过期任务；
3. **Operation transition inventory**：所有 Operation mutation 使用既有权威方法，manual recovery 满足 §14；
4. **Complete entry/lifecycle inventory**：13.5-0 全量底表 + 当前 delta、三个独立长驻服务、人工 scan 和 legacy 退役均有结论；
5. **Control-plane contracts**：capability/mode、Runtime DB、canonical identity、Importer、priority、RECONCILE 均与现有代码一致；
6. **Validation**：当前 Head CI + diff/link/UTF-8 check，通过且无生产代码/Schema/开关变化。

若复审发现其他非 §8.2 安全例外问题，记录为后续技术债，不再重开 7A。

## 22. 13.5-7B 开工门禁

必须同时满足：

- PR #29 保持 Draft 并完成 7A 最终复审；
- hidden listing status write、query 隐式写、manual recovery 全状态/锁/Task 收敛已确认关闭；
- 13.5-0 全量入口 + 当前 delta、listing/order 手工 scan 和三服务生命周期已确认关闭；
- Final Audit / Freeze Gate 通过；
- Web mutation 表与代码一致；
- manual recovery 状态/竞态/幂等合同通过；
- Runtime DB Current→Migration→Target→Tests 通过；
- generic Executor current/target 分离通过；
- canonical identity 与 18:00/20:00 合同通过；
- Queue Service v2/v4/v5 Importer 所有权通过；
- 三层优先级通过；
- 复杂度预算未突破；
- 当前 Head Linux/Windows Core 与仓库检查通过。

计划文档通过只授权开始 7B 编码，不授权真实平台动作、结束 Draft 或自动合并。

## 23. 后续边界

- 13.5-8：Web 架构拆分；
- 13.5-9：运营信息层级和移动体验；
- 13.5-10：完整交易日观察和任务 14 交接；
- 第二平台、真正多账号支持单独评审。

---

Refs #20
