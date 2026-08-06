# 任务 13.5-7A：控制面入口盘点与合同冻结

- 修订日期：2026-08-07
- 状态：编码前设计审查草案
- Review Profile：R4（架构级审查）
- 当前 PR：#29
- 关联父任务：GitHub Issue #20
- 主分支基线：PR #28 合并提交 `418c605f4ab4434eee422eb0217de3cfe64b01b0`

## 1. 计划定位

PR #29 直接承担 **13.5-7A：入口盘点、复用矩阵和合同冻结**，不是 7A 的前置空计划。

本 PR 必须在进入 13.5-7B 编码前完成：

1. Web、主 CLI、Automation Scheduler、人工触发器、Queue Service、平台发布、结果导入、唯一 RECONCILE、运维和验收入口的真实盘点；
2. 每个具有独立副作用边界的路由动作、脚本子命令和内部组件单独分类；
3. `具体入口/子命令 → 当前真实能力 → EntryPointCapability → target allowed modes → Composition Root → 保留/迁移/退役` 矩阵；
4. 当前代码事实与 7B 目标合同分开记录，禁止用目标状态覆盖现有风险面；
5. 业务意图身份、调用来源身份和 Task 来源三层分离；
6. capability 级 canonical identity 与跨 18:00/20:00 重试规则；
7. 分层幂等、事务、恢复和 Importer 权威边界；
8. Incident 人工复核、SYSTEM_EMERGENCY、普通授权写、扫描和结算的完整优先级；
9. Web Runtime DB 从请求级选择迁移到进程级 Composition Root 的兼容和测试方案；
10. 旧入口退役清单、非持久化结果投影与 13.5-7B/7C 开工门禁。

本阶段不修改生产代码、Runtime Schema、Web 路由、CLI、Scheduler、v4/v5、ShadowBot、Importer、Worker 部署或生产开关。

## 2. 权威依据

冲突按以下顺序处理：

1. GitHub Issue #20；
2. `AGENTS.md` 的复用优先门禁；
3. 当前生产代码、Runtime Schema、服务和测试；
4. 已合并任务 12、13、13.5-1 至 13.5-6 的合同与实机证据；
5. `docs/pra_review_risk_and_complexity_governance.md`；
6. 本计划。

历史脚本行为与正式合同冲突时，以现行 Task、Review、Automation Run、Batch、Operation、Attempt、共享 UI 写锁、Importer 和唯一 RECONCILE 为准。不得为了保留旧 Web/CLI 而复制第二写链、第二导入链或第二业务判断。

## 3. 审查配置

```text
Review Profile: R4
真实平台写操作: 本 PR 无；后续 7B/7C 只连接既有 v4/v5 写入口
连接权威模块:
  Web / CLI / Automation Scheduler / Runtime Task / Review /
  Automation Run / Incident / Outbox / Batch / Operation /
  Attempt / Write Lock / Importer / ShadowBot File Queue
最坏事故:
  重复平台副作用、覆盖人工操作、错误来源授权、UNKNOWN 后二次写、
  请求级 Runtime DB 指向错误账本、跨时间边界形成第二业务事实、
  旧根路由继续形成平行 Task 写入口、旧 Executor 绕过正式能力边界、
  调度优先级绕过、长期双控制面或第二 Importer
人工恢复成本: 高
新增数据库表: 0
新增持久化字段: 0
新增状态: 0
新增锁: 0
新增队列/Worker/Importer: 0
新增全局幂等账本: 0
新增 Application Service 外观层: 最多 1 组
明确非目标:
  Web UI 重写、第二平台、第二账号并行、Schema v17、自动普通任务 COMMIT
```

## 4. 当前部署、时间与 Runtime DB 约束

本阶段冻结为：

- 单一 PRA Runtime SQLite；
- 单一 Automation Service 实例租约；
- 单一长期 ShadowBot Worker；
- 单一 `run_shadowbot_queue_services.py` Queue Service 实例锁；
- 当前仅一个真实平台和一个受控账号；
- Web、CLI、Scheduler 可为不同进程，但目标状态必须由各自 Composition Root 绑定同一受控 Repository；
- 真实平台写继续只走既有 v4/v5 文件队列；
- UNKNOWN 继续只进入唯一只读 RECONCILE；
- `automatic_emergency_offline=false` 保持生产默认值。

### 4.1 双时间轴

必须继续区分：

- `platform_trade_date`：平台交易日，18:00 截单；
- `seller_operation_date`：卖家经营日，20:00 截止并开始上一经营日结算。

二者不能互相替代，也不能在重试时根据“当前时间”重新归属。

### 4.2 当前账号边界

当前 Task、Automation Run、Operation、Attempt 和写锁没有完整 `account_id` 持久化维度，因此：

- 当前账号由部署配置绑定；
- 外部请求不得自由指定或切换账号；
- 非当前部署账号在服务边界直接拒绝；
- 本阶段不宣称实现多账号账本隔离；
- 真正多账号支持必须单独进行 R4 + Schema 评审。

### 4.3 Runtime DB：当前真实代码事实

目标合同是“Web 请求不能选择 Runtime DB”，但 **当前 `app/web.py` 尚未满足该目标**。13.5-7A 必须显式记录现状，不能把目标状态写成已实现事实。

当前 Web 行为：

1. `/runtime/login` GET 可以从 query 读取 `runtime_db`；
2. `/runtime/login` POST 可以从 form 读取 `runtime_db`，没有 form 值时还会回退到 query；
3. 登录 Session payload 当前保存 `runtime_db`；
4. `_runtime_db_for_request` 当前优先读取 Session 中的 `runtime_db`，其次允许页面 query 提供 `runtime_db`；
5. `_resolve_request_or_trusted_default` / 路径策略会对白名单路径做安全校验，这降低任意文件访问风险，但请求仍然拥有选择“哪个允许的 Runtime DB”的权威；
6. `_build_runtime_url`、登录跳转、PRG 和多个页面链接当前会继续携带 `runtime_db`；
7. `/runtime/logout` 也会读取 form 中的 `runtime_db` 用于跳转上下文。

因此当前状态是：**路径受白名单限制，但 Web 请求、Session 和 URL 仍参与 Runtime DB 选择**。

### 4.4 Runtime DB：7B 目标合同

7B 必须迁移为：

```text
Web Process Bootstrap / Composition Root
→ 从可信启动配置解析唯一 Runtime DB
→ 创建唯一 SQLiteRuntimeRepository / Unit of Work factory
→ Route / Handler / Application Service 只接收已绑定 Repository
```

硬规则：

- Web query、form、JSON payload、cookie、Session、`next` 参数不得选择或切换 Runtime DB；
- `_runtime_db_for_request` 的目标语义只能返回进程绑定的受控 Repository 标识，不再解析请求级数据库权威；
- 新 Session 不保存 `runtime_db`；
- `_build_runtime_url` 和 PRG 链路不再把 `runtime_db` 当作业务路由参数传播；
- `/health`、Dashboard、Tasks、Reviews、Notifications、Execution Logs、System 和 Task Generator 必须共享同一进程绑定 Repository；
- Mobile Review 继续使用同一固定 Runtime DB/Repository，不增加请求级选择；
- CLI 的 `--runtime-db` 仍是管理员进程启动/运维配置，不等价于普通 Web 请求字段；
- 不依赖当前工作目录推导生产数据库。

### 4.5 Runtime DB：7B 迁移与兼容策略

为了避免直接删除参数破坏登录、旧书签和 PRG，迁移按以下顺序执行：

1. **先建立唯一 Web Composition Root**：在进程启动时完成 Runtime DB 解析和 Repository 装配；
2. **请求参数失去权威性**：旧 `runtime_db` query/form 只作为兼容输入检查，不再决定 Repository；
3. **同路径兼容**：若旧参数规范化后与进程绑定路径相同，可接受当前请求，但下一次重定向必须去掉该参数；
4. **异路径拒绝**：若旧参数规范化后与进程绑定路径不同，返回明确的 4xx/配置错误，不静默切库；
5. **Session 迁移**：新 Session payload 不再保存 `runtime_db`；旧形状 Session 不得继续作为数据库权威，部署切换时通过进程重启自然清除内存 Session，并在专项测试中验证旧形状 Session 被忽略或旋转；
6. **登录迁移**：登录页可以短期识别旧 hidden/query 参数用于兼容提示，但认证成功后的 Session 和 redirect 不再携带数据库选择；
7. **PRG/链接迁移**：`next`、页面筛选、POST/Redirect/GET 和 logout 跳转保留业务页面上下文，但移除 `runtime_db` 传播；
8. **最后删除兼容读取**：完成页面和 Runbook 引用审计后，7C 删除 query/form/session 的旧数据库选择逻辑。

### 4.6 Runtime DB：专项回归门禁

7B/7C 至少覆盖：

- `/runtime/login` GET/POST 正常登录、错误密码、rate limit、Session rotation；
- 登录 CSRF、业务写 CSRF、PRG；
- 无 `runtime_db` 参数的正常页面访问；
- 旧参数等于绑定 DB 时规范化到无参数 URL；
- 旧参数指向另一白名单 DB 时明确拒绝；
- Session 中伪造/遗留 `runtime_db` 不改变绑定 Repository；
- Dashboard、Tasks、Reviews、Notifications、Execution Logs、System、Task Generator 页面回归；
- logout 和 `next` 跳转不重新引入请求级 DB 权威；
- Web 请求不能通过 query/form/session 切换到测试库、备份库或其他允许路径。

## 5. 模式合同

模式表示副作用边界，不是调用方可自行升级的权限等级。

### 5.1 `DIAGNOSTIC`

- 读取健康、配置、平台参数、队列和账本；
- 不创建业务 Task、Review、Run、Batch 或 Operation；
- 可记录既有安全/运维日志，但不得改变业务生命周期。

### 5.2 `READ_ONLY`

- 可读取真实平台并持久化 Automation Run、扫描批次、观察事实和审计事件；
- 不得创建平台写 Task；
- 不得获取真实写锁；
- 不得生成 COMMIT 请求。

“READ_ONLY”表示对平台无写入，不表示 Runtime 必须零写。

### 5.3 `DRY_RUN`

- 可写预览运行和预览条目；
- 不得创建正式 Task、Review 或真实 Outbox；
- 不得触发平台写。

### 5.4 `APPLY`

- 可创建或更新持久化、但尚未触发平台副作用的正式内部事实；
- 包括 Task、Review、Outbox 意图、Automation/Script Run、PREPARED Batch/Operation、人工恢复状态等；
- 不得点击平台或发布 COMMIT 请求。

### 5.5 `COMMIT`

- 只接受既有有效授权 Task/Batch/Operation；
- 必须复用 gate、Batch、Operation、Attempt、共享 UI 写锁、队列发布和 Importer；
- 正式入口不得重建批准载荷、修改来源或绕过更高优先级 lane；
- 不得默认启用。

### 5.6 `RECONCILE`

- 只接受既有 UNKNOWN/NEEDS_RECONCILIATION Operation；
- 只执行唯一只读平台核实；
- 不得创建第二次平台写。

### 5.7 内部能力

`RESULT_IMPORT`、`QUEUE_RECOVERY`、`NOTIFICATION_DELIVERY` 和 `MAINTENANCE` 是内部或管理员能力，不接受普通用户传入 `requested_mode`。

## 6. 不可破坏的既有资产

| 资产 | 冻结 API/职责 |
|---|---|
| v5 请求与合同校验 | `build_listing_action_request`、`build_listing_action_reconcile_request`、相应 validate 函数 |
| v5 上下架提案/发布/导入 | `propose_listing_action_batch`、`publish_listing_action_batch`、`import_listing_action_result` |
| 唯一平台对账 | `ensure_listing_action_reconcile_attempt`、Importer/Watchdog 的自动 reconcile payload、`ShadowBotExecutor.start_reconcile_attempt` |
| 两页只读同步 | `prepare_listing_sync_batch`、`publish_listing_sync_batch`、`import_listing_sync_result` |
| v4 改价发布 | `prepare_task_commit_batch`、`publish_task_commit_batch`、`import_task_commit_result` |
| 审批和门禁 | Review/Token/Outbox 原子服务、`evaluate_automation_gate`、`review_block_reasons` |
| 写身份和执行身份 | Batch、Operation、Attempt、`write_identity_key`、`execution_attempt_id` |
| 共享 UI 写锁 | 现有平台/任务写锁和发布事务 |
| Importer | 现有 Result Importer 原子投影、receipt、ACK、archive/quarantine |
| Worker | 现有领取、请求校验、phase、结果发布和登录人工介入 |
| Incident | Incident Event、通知、S4 授权、人工竞态和最终点击 fence |
| Automation 优先级 | 现有 `UI_CHANNEL_PRIORITY`，不得复制第二份 |
| Web 安全门禁 | Session、登录、CSRF、PRG、Path Policy、Legacy Route fail-closed gate |

## 7. 当前入口真实盘点

分类只使用：

1. 原样复用；
2. 参数化复用；
3. 抽取公共能力；
4. 确需新增；
5. 归档或删除。

### 7.1 Web 正式入口

| 具体入口/动作 | 当前函数/服务 | 当前真实能力/副作用 | Target Capability | Target Mode | Composition Root | 7B/7C 结论 |
|---|---|---|---|---|---|---|
| WSGI 总入口 | `app.web::_application` | 路由、认证、CSRF、路径策略 | `WEB_ROUTER` | 无业务模式 | `app.web.serve` | 原样复用安全门禁；路由变薄 |
| `/health` | Repository health | 只读健康检查 | `HEALTH_DIAGNOSTIC` | `DIAGNOSTIC` | Web 可信启动配置 | 原样复用 |
| `/dashboard` GET | Ops dashboard queries | Runtime 查询 | `RUNTIME_QUERY` | `READ_ONLY` | Web Repository | 原样复用 |
| `/tasks` GET | Task/Automation/Projection queries | Runtime 查询 | `RUNTIME_QUERY` | `READ_ONLY` | Web Repository | 原样复用 |
| `/notifications` GET | Notification queries | Runtime 查询 | `RUNTIME_QUERY` | `READ_ONLY` | Web Repository | 原样复用 |
| `/execution-logs` GET | `list_runtime_execution_logs` 等 | Runtime/Queue 查询 | `EXECUTION_LOG_QUERY` | `READ_ONLY` | Web Repository | 原样复用查询 |
| `/execution-logs` POST `start_shadowbot_reconcile` | `ShadowBotExecutor.start_reconcile_attempt` | 为既有 Operation 创建并启动只读 RECONCILE attempt | `UNKNOWN_RECONCILE` | `RECONCILE` | Web Repository + Runner | 保留为受控恢复入口；必须复用唯一 RECONCILE 合同 |
| `/execution-logs` POST `confirm_shadowbot_manual_handled` | `ShadowBotExecutor.confirm_manual_handled` | 将既有 Operation 更新为 `MANUAL_HANDLED` | `MANUAL_OPERATION_RECOVERY` | 管理员 `APPLY` | Web Repository | 保留受控人工恢复；不得伪装为查询 |
| `/system` GET | 配置与运行状态查询 | 诊断 | `SYSTEM_DIAGNOSTIC` | `DIAGNOSTIC` | Web 可信启动配置 | 原样复用 |
| `/task-generator` GET/POST preview | workflow preview | 预览，无正式 Task | `TASK_PREVIEW` | `DRY_RUN` | Web Composition Root | 抽取公共能力 |
| `/task-generator` POST persist | workflow persist | 创建 Runtime Task/Review/通知事实 | `TASK_APPLY` | `APPLY` | Web Composition Root | 使用统一 Application Service |
| `/reviews`、`/runtime` 查询 | Runtime/Review query | 查询 | `REVIEW_QUERY` | `READ_ONLY` | Web Repository | 原样复用 |
| `/reviews`、`/runtime` 处理 | `resolve_runtime_review_task` | 更新 Review/Task | `REVIEW_APPLY` | `APPLY` | Web Composition Root | 参数化复用 |
| `/runtime/login` GET/POST | login + Session 创建 | 当前可从 query/form 选择允许的 Runtime DB，并写入 Session | `RUNTIME_AUTH` | 安全基础设施 | Web 可信绑定 Repository | 7B 迁移：DB 参数失去权威；登录/CSRF/Session 保留 |
| `/runtime/logout` POST | Session 清理 | 当前可读取 form `runtime_db` 用于跳转上下文 | `RUNTIME_AUTH` | 安全基础设施 | Web 可信绑定 Repository | 7B 移除 DB 权威；保留 logout/CSRF |
| `/mobile/review/*` GET | `get_mobile_review_detail` | 只读详情 | `MOBILE_REVIEW_QUERY` | `READ_ONLY` | 固定 Web Repository | 原样复用 |
| `/mobile/review/*` resolve | `resolve_mobile_review` | 原子更新 Review/Task/Incident 授权 | `MOBILE_REVIEW_APPLY` | `APPLY` | 固定 Repository + Token 服务 | 原样复用原子事务 |
| `/business-inputs` | workbook input services | 写受控工作簿 | `WORKBOOK_MAINTENANCE` | 管理员维护 | Web Path Policy | 不并入平台控制面 |
| `/tables` | legacy workbook maintenance | 写受控工作簿；受 legacy gate | `WORKBOOK_MAINTENANCE` | 管理员维护 | Legacy Web + Path Policy | 7C 归档/重定向到业务输入 |
| `/execution` | `simulate_execution_from_tasks` | 测试文件副作用，无平台写；受 legacy gate | `LEGACY_SIMULATION` | 维护/测试 | Legacy Web | 归档候选 |
| `/manual-intervention` | `list_manual_intervention_tasks` | 只读；正式 resolve 已拒绝；受 legacy gate | `LEGACY_MANUAL_READ` | `READ_ONLY` | Legacy Web | 保留只读说明或归档 |
| `/system/test-feishu-notification` | 通知测试处理器 | 受控外部测试通知 | `NOTIFICATION_DIAGNOSTIC` | 管理员运维能力 | Web 运维配置 | 不进入业务模式 |

`/execution-logs` 不能再被概括成单一 `RUNTIME_QUERY`：GET、RECONCILE 和人工恢复必须拥有不同 capability 和服务端门禁。

### 7.2 旧根路由 `/`：受限但仍存在的平行任务生成入口

当前代码事实：

- `/` 位于 `LEGACY_WEB_ROUTES`；
- 默认 fail-closed，只有显式 `PRA_ENABLE_LEGACY_WEB=1`、`PRA_LEGACY_ACCESS_MODE=direct_loopback`、无 reverse proxy/public tunnel 异常且已有登录 Session 时才可进入；
- 该限制降低暴露面，但 **没有改变 `_handle_dashboard` POST 仍可 preview 和 `confirm_generate` 的业务能力**；
- `confirm_generate` 当前可通过 `persist_task_generation_summary` 写 Runtime Task，因此它仍是受限的平行 Task 生成入口。

| `/` 动作 | 当前真实能力 | Target Capability | 7B 兼容策略 | 7C 终态 |
|---|---|---|---|---|
| GET | 旧任务生成 UI/兼容说明 | `LEGACY_TASK_GENERATOR_VIEW` | 保留现有 legacy fail-closed gate；可显示迁移提示 | `/` 仅重定向到正式首页 `/dashboard` |
| POST preview | 可执行 workflow preview | `TASK_PREVIEW` | 不再调用独立业务逻辑；必须委托同一个 Application Service | 旧 POST 不执行，明确 405/410，提示使用 `/task-generator` |
| POST `confirm_generate` | 可直接持久化 Runtime Task | `TASK_APPLY` | 不再直接调用 `persist_task_generation_summary`；必须委托同一个 Application Service 与 canonical identity | 旧 POST 不执行，明确 405/410，提示使用 `/task-generator` |

硬规则：

- 7B 后 `/` 与 `/task-generator` 即便短期同时存在，也只能是两个 UI adapter，不得是两个业务控制面；
- 两者必须共享同一个 `TASK_PREVIEW/TASK_APPLY` Application Service、canonical identity、幂等和 Review/Outbox 原子事务；
- 不允许 `/` 使用独立 Runtime DB、独立 Task dedupe 或直接 persist；
- 7C 完成引用审计后移除旧写表单和 root POST 业务能力。

### 7.3 主 CLI：`app/cli.py`

| 子命令 | 当前服务/行为 | Capability | 允许模式 | Composition Root | 7B/7C 结论 |
|---|---|---|---|---|---|
| `templates` | 创建模板工作簿 | `WORKBOOK_TEMPLATE_MAINTENANCE` | `MAINTENANCE` | CLI 文件路径 | 保留管理员工具 |
| `validate` | 校验输入工作簿 | `SOURCE_VALIDATE` | `DIAGNOSTIC` | CLI source paths | 参数化复用 |
| `import-data` | 校验并输出摘要；不导入 Runtime | `SOURCE_VALIDATE` | `DIAGNOSTIC` | CLI source paths | 保留，文案避免误解为 Importer |
| `preview-tasks` | workflow 任务预览 | `TASK_PREVIEW` | `DRY_RUN` | CLI Composition Root | 使用统一 Service |
| `generate-tasks` | 输出旧 Excel 任务 | `LEGACY_TASK_EXPORT` | `MAINTENANCE` | CLI file paths | 兼容工具，不进入 Runtime 主线 |
| `mock-ai-decision` | 单 SKU Mock AI 决策预览 | `MOCK_DECISION_PREVIEW` | `DRY_RUN` | CLI workbook services | 测试/演示能力 |
| `simulate-execution` | 模拟执行并写日志/任务文件 | `LEGACY_SIMULATION` | `MAINTENANCE` | CLI file paths | 测试/归档候选 |
| `list-manual-tasks` | 旧 Excel 人工任务查询 | `LEGACY_MANUAL_READ` | `READ_ONLY` | CLI file path | 保留只读兼容 |
| `resolve-manual-task` | 已硬失败 | `DENIED_LEGACY_WRITE` | 无 | CLI | 保持拒绝，7C 删除或保留硬失败 |
| `init-runtime-db` | 初始化/迁移 Runtime Schema | `RUNTIME_MAINTENANCE` | `MAINTENANCE` | CLI Repository | 管理员工具，不进入业务 Service |
| `health` / `check-runtime-health` | Schema/operational health | `HEALTH_DIAGNOSTIC` | `DIAGNOSTIC` | CLI Repository | 原样复用 |
| `generate-runtime-tasks` | `generate_runtime_tasks_from_sources` | `TASK_APPLY` | `APPLY` | CLI Composition Root | 抽取统一 Service；保留薄 CLI |
| `list-tasks` | Runtime Task 查询 | `RUNTIME_QUERY` | `READ_ONLY` | CLI Repository | 原样复用 |
| `show-task-history` | Task history 查询 | `RUNTIME_QUERY` | `READ_ONLY` | CLI Repository | 原样复用 |
| `list-review-tasks` | Review 查询 | `REVIEW_QUERY` | `READ_ONLY` | CLI Repository | 原样复用 |
| `resolve-review-task` | `resolve_runtime_review_task` | `REVIEW_APPLY` | `APPLY` | CLI Composition Root | 薄适配 |
| `expire-review-tasks` 无 `--apply` | 超时预览 | `REVIEW_TIMEOUT_PREVIEW` | `DRY_RUN` | CLI Composition Root | 参数化复用 |
| `expire-review-tasks --apply` | 超时 Review/Task/通知事实 | `REVIEW_TIMEOUT_APPLY` | `APPLY` | CLI Composition Root | 参数化复用 |
| `notification-worker` | Outbox watchdog + delivery | `NOTIFICATION_DELIVERY` | 内部能力 | CLI Repository + channel adapter | 保持独立 Worker |
| `serve-web` | 启动 WSGI | `PROCESS_BOOTSTRAP` | 无业务模式 | Web Composition Root | 7B 在此绑定唯一 Web Repository |

### 7.4 Automation Service

| 具体入口 | 当前函数/服务 | Capability | 允许模式 | Composition Root | 结论 |
|---|---|---|---|---|---|
| Service 启动 | `scripts/run_automation_service.py::main` | `AUTOMATION_BOOTSTRAP` | 无用户模式 | 启动参数与环境变量 | 原样复用 Composition Root |
| 窗口物化/claim | `AutomationService.run_cycle` | `AUTOMATION_SCHEDULE` | `APPLY` | Automation Repository | 原样复用 |
| 结算 handlers | `build_sales_settlement_handlers` | `SETTLEMENT_APPLY` | `APPLY` | 已 claim 的 Automation Run | 原样复用/参数化 |
| 订单只读 handlers | `build_order_read_only_handlers` | `ORDER_SCAN_READ_ONLY` | `READ_ONLY` | 已 claim 的 Automation Run | 原样复用 |
| Incident handlers | `build_incident_notification_handlers` | `INCIDENT_MAINTENANCE_APPLY` | `APPLY` | 已 claim 的 Automation Run | 原样复用 |
| Worker recovery | `build_worker_recovery_coordinator_from_environment` | `HOST_RECOVERY_MAINTENANCE` | 内部能力 | 显式开关 + reviewed helper | 独立运维能力 |

Automation Service 当前不注册普通平台写 handler，且心跳声明 `platform_write_handlers_registered=false`。13.5-7 不得把它扩展为扫描全部 pending Task 后自动 COMMIT。

### 7.5 人工只读与规则评估脚本

| 具体入口 | 当前服务 | Capability | 允许模式 | Composition Root | 结论 |
|---|---|---|---|---|---|
| `run_shadowbot_listing_sync.py` | `prepare_listing_sync_batch` + `publish_listing_sync_batch` | `LISTING_SYNC_READ_ONLY` | `READ_ONLY` | CLI Repository + Queue Runner | 统一只读扫描 Service 的薄触发器 |
| `evaluate_business_rules.py` 默认 | `BusinessRuleRunner.run` | `RULE_EVALUATION_PREVIEW` | `DRY_RUN` | CLI Repository + workbook paths | 保留薄 CLI |
| `evaluate_business_rules.py --apply` | `BusinessRuleRunner.run` | `RULE_EVALUATION_APPLY` | `APPLY` | CLI Composition Root | 统一 Service 生成来源与身份 |
| `run_mock_platform_executor.py --dry-run` | Mock 平台服务 | `MOCK_PLATFORM_PREVIEW` | `DRY_RUN` | 隔离 Mock DB | 测试工具 |
| `run_mock_platform_executor.py --apply` | Mock 平台服务 | `MOCK_PLATFORM_APPLY` | `APPLY` 但仅 Mock | 隔离 Mock DB | 不得连接真实平台 |

### 7.6 正式 Queue Service：`run_shadowbot_queue_services.py`

该脚本是长驻 Composition Root。一个周期按既有顺序运行：

```text
登录验证码监控
→ Result Importer
→ Queue Watchdog
→ 超期人工复核提醒
→ Notification Outbox Watchdog/Delivery
```

| 内部组件 | 当前函数/对象 | Capability | 副作用边界 | 结论 |
|---|---|---|---|---|
| 进程启动与单实例锁 | `main`、`pra_queue_services.lock` | `QUEUE_SERVICE_BOOTSTRAP` | 装配 Repository、Queue、Runner；不直接平台写 | 原样复用唯一实例 |
| 登录验证码监控 | `ShadowBotLoginVerificationMonitor.inspect` | `LOGIN_VERIFICATION_MONITOR` | 可创建/更新 Incident、Review、通知事实 | 内部 `APPLY`，原样复用 |
| v2/v4/v5 正式结果导入 | `ShadowBotResultImporter.import_available/import_one` | `RESULT_IMPORT` | 原子投影结果、receipt、ACK、archive/quarantine；ORDER_SCAN 明确 deferred 给 Automation importer | v2/v4/v5 正式 Importer 主线 |
| v4 UNKNOWN 自动恢复 | Importer 中 `ensure_reconcile_attempt` | `UNKNOWN_RECONCILE` | 只为既有 UNKNOWN 创建唯一只读 attempt | 原样复用，不新建入口 |
| Queue Watchdog | `ShadowBotQueueWatchdog.inspect` | `QUEUE_RECOVERY` | 处理 stale/timeout、安全恢复结果；不得推测成功 | 原样复用 |
| 复核提醒续期 | `ReviewTaskService.renew_overdue_manual_reviews` | `REVIEW_REMINDER_APPLY` | 更新提醒/通知事实 | 内部 `APPLY` |
| 通知租约恢复 | `NotificationOutboxWorker.run_watchdog` | `NOTIFICATION_RECOVERY` | 恢复通知 lease | 内部能力 |
| 通知投递 | `NotificationOutboxWorker.run_once` | `NOTIFICATION_DELIVERY` | 对外发送通知并更新 Outbox | 内部能力 |

硬边界：

- Queue Service 是 v2/v4/v5 队列结果导入和自动恢复主线；
- ORDER_SCAN 结果继续由既有 Automation observation importer 处理，这不是第二条平台写 Importer；
- Web、普通 Scheduler handler 和兼容 CLI 不得为同一 v2/v4/v5 合同建立第二 Importer；
- Watchdog 只能生成现有合同允许的安全恢复或唯一 RECONCILE；
- `--queue-dir` 同时绑定新旧环境变量，避免 RECONCILE 请求落到第二队列；
- `--once` 只改变进程循环，不改变 capability。

### 7.7 v4 改价入口：`run_shadowbot_commit_batch.py`

| 子命令 | 当前服务 | Capability | 允许模式 | Composition Root | 7B/7C 结论 |
|---|---|---|---|---|---|
| `prepare` | `prepare_task_commit_batch` | `AUTHORIZED_WRITE_PREPARE` | `APPLY` | CLI Repository + mapping | 保留薄适配；只写 PREPARED 账本，不发布队列 |
| `publish` | `publish_task_commit_batch` | `AUTHORIZED_WRITE_COMMIT` | `COMMIT` | CLI Repository + Queue Runner | 只消费已准备 manifest；复用 gate/锁/Attempt |
| `production-run` | prepare + publish | `AUTHORIZED_WRITE_COMPOSITE_COMMIT` | `COMMIT` | CLI Composition Root | 兼容入口；内部仍按 prepare→publish 边界执行 |
| `import-result` | `import_task_commit_result` | `LEGACY_RESULT_IMPORT` | 内部/管理员 | CLI Repository + result file | 7C 引用审计后由 Queue Service Importer 取代 |

`production-run` 不得被理解为新的自动 pending 扫描器；仍要求显式 task IDs、batch ID、mapping、queue 和 applet URI。

### 7.8 旧 Executor：必须分开记录 Current 与 Target

#### 7.8.1 当前真实风险面

`scripts/run_shadowbot_executor.py start` 的 argparse 行为是：

- `--execution-mode` 默认值为 `COMMIT`；
- argparse 本身没有把参数限制为单一 COMMIT；
- 底层 `ALLOWED_EXECUTION_MODES` 当前实际接受：

```text
READ_ONLY
COMMIT
RECONCILE
```

因此“当前 `{COMMIT}`”是错误的现状描述。该入口当前还是一个 generic executor adapter，并由 CLI 参数重建 approved payload。

| 子命令 | 当前真实行为 | Current Accepted Modes | Current Capability | 7B Target | 7C 结论 |
|---|---|---|---|---|---|
| `start` | CLI 重建 approved payload；调用者可指定 execution mode | `{READ_ONLY, COMMIT, RECONCILE}` | `LEGACY_GENERIC_EXECUTION` | 不接入统一 Application Service；不得由新 Web/Scheduler 调用 | 完成引用审计后退役或限制为隔离测试工具 |
| `import-result` | `ShadowBotExecutor.record_result` | internal/admin | `LEGACY_RESULT_IMPORT` | 不成为正式 Importer | Queue Service 覆盖后退役 |
| `poll-yingdao-result` | 查询 Yingdao job 并导入结果 | internal/admin | `LEGACY_OPENAPI_RESULT_IMPORT` | 不成为正式 Importer | OpenAPI 引用审计后隔离/退役 |
| `check-yingdao-app-params` | 查询机器人参数 | `DIAGNOSTIC` | `PLATFORM_ADAPTER_DIAGNOSTIC` | 管理员诊断 | 保留 |

#### 7.8.2 7B 兼容门禁

7B 不能把 generic `start` 的当前 accepted set 直接映射成新的服务端 allowed modes。

冻结规则：

- 新 `ControlPlaneApplicationService` 永远不调用 generic `run_shadowbot_executor.py start`；
- 普通 Web、Scheduler 和新 CLI adapter 不得把用户提供的 `execution_mode` 原样透传；
- 正式 COMMIT 继续只走 v4/v5 授权发布服务；
- 正式 READ_ONLY 继续只走已盘点的 listing sync / Automation read-only handler；
- 正式 RECONCILE 继续只走既有 UNKNOWN Operation → `start_reconcile_attempt` / `ensure_listing_action_reconcile_attempt`；
- 在删除 generic `start` 前先完成 repository call-site、Runbook、部署脚本和测试引用审计；
- 若审计发现仍有必须保留的兼容调用，7B 只能增加薄 compatibility gate：
  - `RECONCILE` 必须委托唯一 `start_reconcile_attempt`，不能 generic start 自建第二恢复路径；
  - `READ_ONLY` 必须委托既有正式只读 capability，或明确拒绝 generic path；
  - `COMMIT` 只能消费既有有效授权对象并委托正式发布服务，不能重建批准事实；
- 不新增任何 generic executor 的新调用方。

### 7.9 v5 上下架服务级入口

| 服务能力 | Capability | 模式 | 权威身份 | 结论 |
|---|---|---|---|---|
| 提案/准备 | `LISTING_ACTION_PREPARE` | `APPLY` | 授权 Task、policy、write identity | 原样复用 |
| 发布 | `LISTING_ACTION_COMMIT` | `COMMIT` | Batch/Operation/Attempt | 原样复用 |
| 结果导入 | `RESULT_IMPORT` | 内部 | result/attempt/hash | 只走 Queue Service Importer |
| UNKNOWN 对账 | `UNKNOWN_RECONCILE` | `RECONCILE` | 既有 Operation + 唯一 reconcile attempt | 唯一恢复入口 |
| SYSTEM_EMERGENCY 下架 | `SYSTEM_EMERGENCY_COMMIT` | `COMMIT` | S4 authorization + final fence | 原样复用；默认开关仍关闭 |

### 7.10 迁移、运维、诊断和验收工具

以下入口不并入统一业务 Application Service：

- `reconcile_shadowbot_listing_skus.py`：SKU 数据迁移，不是平台 RECONCILE；
- `repair_shadowbot_expired_attempt.py`：受控恢复工具；
- `check_runtime_env.py`、`check_shadowbot_readiness.py`、`check_shadowbot_worker_health.py`；
- `release_backup.py`、`sync_shadowbot_test2.py`、`verify_shadowbot_deployment.py`；
- 所有 `export_task12/13_*`、`verify_task12/13_*`、故障注入和受控实机脚本；
- `build_task11_*`、`freeze_task13_v12_baseline.py`、`run_shadowbot_e2e_local_demo.py` 等归档候选。

它们只能执行维护、证据、迁移或受控测试职责，不能被 Web/Scheduler 当作生产业务 Service。

## 8. 具体入口能力绑定门禁

所有正式生产入口必须在 Composition Root 中获得固定 `EntryPointCapability`。调用方不得通过请求参数声明 capability。

```text
entrypoint_capability = composition_root.bind(entrypoint)
requested_mode ∈ allowed_modes(entrypoint_capability)
```

规则：

- 入口只能在绑定集合内选择更保守模式，不能升级；
- `prepare` 与 `publish` 必须是不同 capability；
- Importer、Watchdog、通知投递不接受用户模式；
- `COMMIT` 只接受既有有效授权 Task/Batch/Operation；
- `RECONCILE` 只接受既有 UNKNOWN/NEEDS_RECONCILIATION Operation；
- `/execution-logs` 的 GET/RECONCILE/人工恢复是三个不同 capability；
- 旧 `/` 不能保留独立 `TASK_APPLY` 实现；
- Web Runtime DB 只能来自进程 Composition Root；
- 旧 generic Executor 的 current accepted modes 不是新服务端白名单；
- 当前普通 Web 不提供通用 COMMIT；
- Scheduler 不注册普通平台写 handler。

### 8.1 高风险入口强制绑定

| 入口 | Current | Target Capability | Target Allowed Modes | Repository/Adapter 来源 | 处置 |
|---|---|---|---|---|---|
| Web `/task-generator` preview | workflow preview | `TASK_PREVIEW` | `{DRY_RUN}` | Web Composition Root | 保留 |
| Web `/task-generator` persist | 直接 workflow persist | `TASK_APPLY` | `{APPLY}` | Web Composition Root | 变薄 |
| Legacy `/` preview | 可独立 preview | `TASK_PREVIEW` | `{DRY_RUN}` | 同一 Web Composition Root | 7B 委托统一 Service，7C 移除旧 POST |
| Legacy `/` persist | 可直接 persist Runtime Task | `TASK_APPLY` | `{APPLY}` | 同一 Web Composition Root | 7B 委托统一 Service，7C 移除旧 POST |
| `/execution-logs` GET | 查询 | `EXECUTION_LOG_QUERY` | `{READ_ONLY}` | Web Repository | 保留 |
| `/execution-logs` start reconcile | 启动 reconcile attempt | `UNKNOWN_RECONCILE` | `{RECONCILE}` | 既有 Operation + Runner | 保留受控恢复 |
| `/execution-logs` manual handled | 更新 Operation | `MANUAL_OPERATION_RECOVERY` | `{APPLY}` admin only | Web Repository | 保留受控恢复 |
| Web Runtime login/session | 当前 query/form/session 可选择允许 DB | `RUNTIME_AUTH` | 无业务模式 | Web 可信进程 Repository | 7B 移除 DB 权威 |
| CLI `generate-runtime-tasks` | Runtime Task apply | `TASK_APPLY` | `{APPLY}` | CLI Composition Root | 变薄 |
| Automation order scan handler | READ_ONLY scan | `ORDER_SCAN_READ_ONLY` | `{READ_ONLY}` | claimed Run | 保留 |
| Automation settlement handler | settlement writes | `SETTLEMENT_APPLY` | `{APPLY}` | claimed Run | 保留 |
| `run_shadowbot_listing_sync.py` | READ_ONLY scan | `LISTING_SYNC_READ_ONLY` | `{READ_ONLY}` | CLI Composition Root | 变薄 |
| commit batch `prepare` | writes PREPARED ledger | `AUTHORIZED_WRITE_PREPARE` | `{APPLY}` | CLI Composition Root | 保留 |
| commit batch `publish` | real publish | `AUTHORIZED_WRITE_COMMIT` | `{COMMIT}` | CLI Composition Root | 保留 |
| commit batch `production-run` | prepare + real publish | `AUTHORIZED_WRITE_COMPOSITE_COMMIT` | `{COMMIT}` | CLI Composition Root | 兼容 |
| commit batch `import-result` | manual import | `LEGACY_RESULT_IMPORT` | internal/admin | CLI Repository | 退役候选 |
| executor `start` | current `{READ_ONLY, COMMIT, RECONCILE}` | `LEGACY_GENERIC_EXECUTION` | **不进入新业务白名单** | CLI Repository + Runner | 隔离并退役；无新调用方 |
| executor `import-result` | manual import | `LEGACY_RESULT_IMPORT` | internal/admin | CLI Repository | 退役 |
| executor `poll-yingdao-result` | OpenAPI import | `LEGACY_OPENAPI_RESULT_IMPORT` | internal/admin | OpenAPI + CLI Repository | 隔离/退役 |
| Queue Service Importer | formal v2/v4/v5 import | `RESULT_IMPORT` | internal only | Queue Service Composition Root | 唯一正式主线 |
| Queue Service Watchdog | formal recovery | `QUEUE_RECOVERY` | internal only | Queue Service Composition Root | 唯一恢复主线 |
| v5 publish | real listing action | `LISTING_ACTION_COMMIT` | `{COMMIT}` | 正式发布 Composition Root | 保留 |
| unique reconcile | UNKNOWN recovery | `UNKNOWN_RECONCILE` | `{RECONCILE}` | 既有 Operation | 唯一入口 |

## 9. 业务意图、调用来源和 Task 来源分离

### 9.1 业务意图身份

`business_intent_identity` 判断是否针对同一业务目标。它必须是 capability 级 canonical identity，不能使用一套固定字段覆盖所有能力。

### 9.2 调用来源身份

`invocation_identity` 只用于审计：

```text
entrypoint_type
actor_type
actor_ref
trigger_type
trigger_ref_id
requested_at
manual_reason
scheduled_for
parent_run_id
request_trace
```

这些字段保留 Web、CLI、Scheduler 和人工补跑的真实差异，不参与业务幂等身份，除非某个既有合同明确将稳定 trigger reference 作为业务引用。

### 9.3 Task 来源

| 场景 | `origin_type` | `origin_ref_id` |
|---|---|---|
| Scheduler 正常运行 | `AUTOMATION` | `automation-run:<run_id>` |
| Web 人工业务动作 | `MANUAL` | `web:<stable intent ref>` |
| CLI 人工业务动作 | `MANUAL` | `cli:<stable intent ref>` |
| 人工补跑 | `MANUAL` | `manual-rerun:<new run/request id>` |
| Incident 人工处置 | `MANUAL` | `incident-review:<review_task_id>` |
| 系统紧急保护 | `SYSTEM_EMERGENCY` | `emergency:<authorization_id>` |

人工补跑必须记录原计划时间、原因、操作者和原 Run 链接，不得伪装成 Scheduler。

## 10. Capability 级 canonical identity

### 10.1 总规则

- 只使用服务端规范化后的稳定业务字段；
- `requested_at`、当前时钟、HTTP session、trace ID、actor 显示名、`manual_reason` 等审计字段绝不参与业务幂等键或 canonical payload fingerprint；
- 字符串统一 trim/case/枚举规范化；
- 集合类输入排序后再哈希；
- 金额、数量和时间按既有合同格式规范化；
- 空值必须按 capability 明确允许，不能靠字符串 `"None"` 或空白隐式参与哈希；
- 已有权威对象 ID 优先于重新计算字段；
- 不新增全局 identity 表。

### 10.2 canonical identity 矩阵

| Capability | Canonical identity | 可空字段 | 明确排除 |
|---|---|---|---|
| `AUTOMATION_SCHEDULE` | 既有 `logical_run_key` | 按现有 Run 合同 | 当前时间、进程实例 ID |
| `TASK_PREVIEW` | input manifest hash + 显式目标业务日期 + rule/evaluator version | 仅业务合同允许的可选输入 | actor、requested_at |
| `TASK_APPLY` | platform + 冻结双日期 + scope/action + policy/rule version + logical target | seller date 仅对不适用能力可空 | entrypoint、actor、当前时钟 |
| `RULE_EVALUATION_APPLY` | evaluator ID/version + platform + 冻结目标日期 + normalized input manifest | 不适用数据源可空 | CLI 启动时间 |
| `REVIEW_APPLY` | `review_task_id` + resolution action + normalized adjustment payload hash | adjustment 可按动作为空 | actor、note、requested_at |
| `MANUAL_OPERATION_RECOVERY` | existing `operation_id` + recovery action + normalized recovery payload | note 可空 | actor、requested_at |
| `REVIEW_TIMEOUT_APPLY` | `review_task_id` + 已持久化 required_by + timeout policy version | 无 | 扫描发生时间 |
| `LISTING_SYNC_READ_ONLY` | platform + scan type + frozen target date/run ID + mapping manifest hash | seller date按合同 | 触发入口和当前时间 |
| `AUTHORIZED_WRITE_PREPARE` | ordered task IDs + approved payload hashes + batch/write identity | 无 | CLI actor、文件输出路径 |
| `AUTHORIZED_WRITE_COMMIT` | existing batch ID + operation/write identity + approved manifest hash | 无 | publish 调用时间 |
| `LISTING_ACTION_COMMIT` | existing authorized Task/Batch/Operation + write identity | 无 | 当前价格读取时间以外的审计字段 |
| `UNKNOWN_RECONCILE` | existing operation ID + source attempt ID + reconcile contract version | source attempt按既有合同 | 重试时间、人工说明 |
| `RESULT_IMPORT` | result ID + result file hash + execution attempt ID | 无 | 文件发现时间 |
| `MOBILE_REVIEW_APPLY` | review task ID + one-time token binding + normalized resolution | adjustment按动作可空 | IP、User-Agent、requested_at |
| `INCIDENT_MAINTENANCE_APPLY` | incident ID/type + authoritative scope + policy version | 按 incident 合同 | 通知时间 |
| `SYSTEM_EMERGENCY_COMMIT` | authorization ID + immutable approved payload hash + final fence scope | 无 | 当前触发入口 |
| `MAINTENANCE`/`DIAGNOSTIC` | 不参与业务事实幂等；使用各自文件/任务/审计身份 | 依工具合同 | 不得生成业务 dedupe key |

### 10.3 跨 18:00/20:00 重试

同一调用不得因重试跨越边界而产生第二业务意图。

权威取值顺序：

1. 已有 Task/Review/Automation Run/Batch/Operation/Authorization 中冻结的日期；
2. 首次已接受请求返回的 canonical identity；
3. 调用方显式提交且经服务端验证的目标日期/`intent_effective_at`；
4. 仅首次调用、且没有任何已有权威对象时，才由 `OperationalTimeService` 计算双日期。

重试规则：

- Scheduler 重试沿用 Automation Run 的 `scheduled_for`、`platform_trade_date`、`seller_operation_date`；
- Review、COMMIT、RECONCILE、Importer 从已有对象读取日期和身份；
- Web/CLI 创建新业务事实时必须提交稳定 intent reference 和冻结目标日期，或重用首次返回的 canonical identity；
- 任何重试不得使用新的 `requested_at` 重新计算双日期；
- 同一稳定 reference + 同一 canonical payload 返回已有结果；
- 同一稳定 reference + 不同 canonical payload 明确冲突；
- 人工确实要创建新意图时，必须使用新 reference 并记录原因，不得伪装成重试。

## 11. 分层幂等矩阵

| 层级 | 现有权威身份 | 防止的事故 | 合法新增规则 |
|---|---|---|---|
| Adapter 请求 | stable intent/trigger reference + canonical payload fingerprint | 双击、HTTP/CLI 重试、跨时间边界重算 | 同引用同内容返回已有结果 |
| 同引用异内容 | reference + canonical fingerprint | 旧引用覆盖新内容 | 明确冲突并拒绝 |
| Automation Run | `logical_run_key` | 同一计划窗口重复 Run | 人工补跑可建新 Run并链接原 Run |
| Task | `dedupe_key` | 重复业务任务 | 新业务意图必须有新稳定来源 |
| Review/Outbox | 现有稳定业务键/唯一约束 | 重复复核和通知 | 复用原子服务 |
| Batch/Operation | `batch_id`、`operation_id`、`write_identity_key` | 重复平台写发布 | 同授权写意图唯一 |
| Attempt | `execution_attempt_id` | 重复领取、回执串用 | 仅按既有状态机创建合法新 attempt |
| RECONCILE | Operation + 唯一 reconcile attempt | UNKNOWN 后第二恢复链 | 只允许唯一只读核实 |
| Importer | result/receipt/attempt/hash | 重复或冲突导入 | 精确重放幂等；异内容冲突 |

不得用一个统一 key 替代各层账本。

## 12. 调度优先级与 UI 通道

优先级必须分三层冻结，不能压成“普通任务/普通扫描”两级。

### 12.1 恢复门禁

```text
UNKNOWN / 唯一 RECONCILE
→ 阻断同平台/同商品的不安全新写
```

该门禁高于所有正常写和扫描。不得通过显式 task ID、人工补跑或 Scheduler 绕过。

### 12.2 业务写任务 lane

```text
Incident 人工复核任务
→ SYSTEM_EMERGENCY 自动紧急任务
→ 普通授权写任务
```

- 人工任务在最终副作用边界前可使 SYSTEM_EMERGENCY 失效；
- SYSTEM_EMERGENCY 必须继续通过 S4 authorization 和 final fence；
- 普通授权写不得抢占更高优先级任务。

### 12.3 Automation UI 作业顺序

13.5-7B 必须直接复用现有 `UI_CHANNEL_PRIORITY`：

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

不得在新 Service 中复制、重建或压平该 map。

约束：

- 18:00 截单前扫描、截单后 Pulse、平台交易日结算、大扫描和小扫描保持现有相对顺序；
- 20:00 卖家经营日切换不改变平台交易日作业身份；
- Scheduler 和人工触发器不能同时占用同一平台 UI 通道；
- 只读扫描服从写任务与恢复链；
- 价格回升本身不撤销已形成的 S4 授权，但 final fence 仍检查 Review、人工任务、写锁、UNKNOWN、身份、开关和授权有效性。

## 13. 目标调用图

```text
Web Route ───────┐
CLI Adapter ─────┼─> ControlPlaneApplicationService（最多一组轻量外观）
Manual Trigger ──┤       ├─ OperationalTimeService
Scheduler Handler┘       ├─ Runtime Task / Review / Incident Services
                          ├─ Automation Service / Repository
                          ├─ v4/v5 Proposal + Publish Services
                          └─ Structured ApplicationResult

Web Composition Root
  ├─ 从可信启动配置绑定唯一 Runtime Repository
  ├─ 绑定部署 platform/current account
  ├─ 绑定 queue/adapter
  └─ 为具体入口注入固定 EntryPointCapability

Legacy Web `/`
  → 7B 只作为同一 Application Service 的受限 adapter
  → 7C 移除旧 POST 业务能力

Legacy Executor generic `start`
  → 不进入新 Application Service
  → current accepted modes 与 target 正式能力严格分离

ShadowBot Worker
  → v2/v4/v5 Result Files → Queue Service Importer
  → ORDER_SCAN Result Files → Automation observation importer
  → Runtime authoritative ledgers
```

统一 Service 不直接操作页面、不直接拼接队列 JSON、不写第二套结果投影、不持有第二状态机，也不接受请求级 Runtime DB 或 generic execution mode 作为业务权限。

## 14. 结构化结果投影

统一返回 `ApplicationResult`，但不新增持久化生命周期。

```text
CREATED
ALREADY_EXISTS
ALREADY_RUNNING
DEFERRED_BY_HIGHER_PRIORITY
BLOCKED_BY_REVIEW
BLOCKED_BY_UNKNOWN
BLOCKED_BY_UI_CHANNEL
BLOCKED_BY_IDENTITY
READ_ONLY_COMPLETED
DRY_RUN_COMPLETED
FAILED_BEFORE_SIDE_EFFECT
START_UNKNOWN
NEEDS_RECONCILIATION
COMPLETED
```

每个结果同时返回：

- 底层真实领域状态；
- 底层错误码；
- canonical business intent reference；
- invocation audit reference；
- 关联 Run/Task/Review/Batch/Operation/Attempt IDs；
- 是否发生平台副作用；
- 是否允许安全重试；
- 下一步处理建议。

`result_code` 只是非持久化展示/适配器投影，不得成为第二套 Task、Run、Operation 或 Attempt 状态机。

## 15. 事务与恢复边界

- Task/Review/Token/Outbox/Incident Event 继续由既有原子服务创建；
- Scheduler claim、lease、Run Event 继续由 Automation Repository/Service 管理；
- PREPARED Batch/Operation 属于 `APPLY`，不等于平台副作用；
- v4/v5 gate、Batch、Operation、Attempt、写锁和 Task running 投影继续在既有发布事务完成；
- `/execution-logs` 的 RECONCILE 必须调用唯一现有 reconcile 服务，不在 Web 中复制恢复状态机；
- `/execution-logs` 的 `MANUAL_OPERATION_RECOVERY` 只更新既有 Operation 的受控人工恢复状态，不得创建新写意图；
- Queue Service Importer 原子提交 v2/v4/v5 receipt、operation/attempt、Task、Lock、平台事实和 Incident 投影；
- ORDER_SCAN 继续由既有 Automation observation importer 导入，不得被新 Service 吸收；
- 兼容 CLI Importer 不得长期成为第二正式主线；
- 旧 root adapter 不得绕开 Task/Review/Outbox 原子事务；
- 入口层不得在 Importer 之外“补写成功”；
- 超时只能精确重放，不得推测副作用状态；
- UNKNOWN 后禁止第二次不安全写，只允许唯一 RECONCILE。

## 16. 保留、迁移与退役清单

### 16.1 保留为正式 Composition Root/薄适配器

- `scripts/run_automation_service.py`；
- `scripts/run_shadowbot_queue_services.py`；
- `scripts/evaluate_business_rules.py`；
- `scripts/run_shadowbot_listing_sync.py`；
- `scripts/run_shadowbot_commit_batch.py prepare/publish/production-run`；
- 主 CLI 的 Runtime 查询、复核和受控 APPLY 命令；
- `/execution-logs` 查询、唯一 RECONCILE 与受控人工恢复动作；
- Web 登录、Session、CSRF 和 PRG 安全基础设施，但 Runtime DB 权威从请求迁移到 Composition Root。

### 16.2 7B 兼容迁移

- 旧 `/` 仍受现有 legacy fail-closed gate；其 preview/persist 必须委托统一 Application Service；
- 旧 `runtime_db` query/form/session 只允许兼容检查，不再决定 Repository；
- 旧 generic Executor `start` 不增加新调用方，不接入 Application Service；
- 如审计发现仍有兼容调用，按正式 READ_ONLY/COMMIT/RECONCILE capability 委托，不保留 generic mode 作为业务权限。

### 16.3 受控运维/诊断

- `run_shadowbot_executor.py check-yingdao-app-params`；
- `reconcile_shadowbot_listing_skus.py`；
- `repair_shadowbot_expired_attempt.py`；
- 部署同步、hash 验证、备份和健康检查脚本。

### 16.4 7C 归档或退役候选

- 旧 `/` POST preview/persist 业务能力；
- Web 请求、登录 form/query、Session 和 URL 中的 Runtime DB 选择兼容逻辑；
- `run_shadowbot_executor.py start` generic executor；
- `run_shadowbot_executor.py import-result`；
- `run_shadowbot_executor.py poll-yingdao-result`；
- `run_shadowbot_commit_batch.py import-result`；
- 旧 Excel `resolve-manual-task` 和 Web `/manual-intervention` 正式处理路径；
- 阶段性 demo、baseline build 和旧证据脚本。

删除前必须完成 CI、Runbook、部署脚本、Web 旧链接、repository call-site、测试和运维引用审计。

## 17. 阶段拆分

### 17.1 13.5-7A：本 PR

交付本文的真实入口清单、Current/Target 分离、子命令级 capability 矩阵、Web Runtime DB 迁移合同、canonical identity、完整优先级、退役清单和开工门禁。零业务代码、零真实平台副作用。

### 17.2 13.5-7B：统一 Application Service 与迁移

仅在 7A 复审通过后开始：

- 最多一组轻量 Application Service；
- Web Composition Root 固定绑定唯一 Repository、平台、账号和 capability；
- 薄 Web/CLI/Scheduler/manual adapters；
- `/execution-logs` GET/RECONCILE/人工恢复拆分能力；
- legacy `/` 委托统一 Service，不再直接 persist；
- Runtime DB 请求级权威迁移；
- generic Executor 与正式能力隔离；
- capability/mode 服务端白名单；
- capability 级 canonical identity；
- 分层幂等复用；
- 统一非持久化结果投影；
- 小型集成和并发测试。

### 17.3 13.5-7C：旧入口退役

- 删除重复业务判断；
- 移除 legacy `/` POST 业务能力并收口 root 导航；
- 删除请求级 Runtime DB 兼容读取和 URL 传播；
- 收口第二 Importer/旧 generic Executor；
- 更新 Runbook 和启动脚本；
- 重启、登录/CSRF/PRG、精确重放、跨 18:00/20:00 和人工补跑测试；
- 系统冒烟与 Linux/Windows CI。

## 18. 复杂度预算

```text
新增数据库表: 0
新增数据库字段: 0
新增 Schema 版本: 0
新增持久化状态: 0
新增 Task/Review/Automation 状态: 0
新增平台动作: 0
新增 Worker/队列/Importer: 0
新增全局锁: 0
新增全局幂等账本: 0
新增 Application Service 外观层: 最多 1 组
长期平行业务入口: 0
```

Web Composition Root 和 compatibility guard 必须是薄适配/装配层，不得演化为第二套 Repository 或状态机。若实现要求突破预算，停止编码并单独评审。

## 19. P1 阻塞清单

1. 入口盘点按脚本名/路由名合并，遗漏不同动作的副作用边界；
2. `/execution-logs` 被整体标为 READ_ONLY，掩盖 RECONCILE 和人工恢复；
3. legacy `/` 保留独立 `persist_task_generation_summary` 写链；
4. Web query/form/session 可以继续选择 Runtime DB；
5. Runtime DB 只写目标禁令，没有旧 Session/登录/PRG/链接迁移方案；
6. 具体入口未绑定固定 capability 和 allowed modes；
7. capability 由请求参数决定；
8. 旧 generic Executor 的 current `{READ_ONLY, COMMIT, RECONCILE}` 被错误写成 target `{COMMIT}`；
9. 新 Service 信任或透传 generic `execution_mode`；
10. READ_ONLY/DIAGNOSTIC 获得 APPLY/COMMIT；
11. COMMIT 接受未授权、过期或错误平台 Task；
12. RECONCILE 接受非 UNKNOWN Operation 或执行第二次写；
13. 业务身份使用 `requested_at` 或重试时当前时钟；
14. 跨 18:00/20:00 重试形成第二业务事实；
15. 人工补跑伪装成 Scheduler；
16. 外部参数伪造来源、审批或策略字段；
17. 一个统一幂等键覆盖 Run/Task/Operation/Attempt；
18. 为同一 v2/v4/v5 合同在 Queue Service 之外保留第二正式 Importer/Watchdog；
19. 调度优先级被复制、压平或绕过；
20. UNKNOWN 被普通补跑或新写覆盖；
21. 结构化结果变成第二持久化状态机；
22. 第二队列、第二 Worker、第二写链；
23. `automatic_emergency_offline` 被提前开启。

后续复审原则上只验证这些冻结问题及修复直接引入的回归。

## 20. 测试矩阵

### 20.1 7A 静态核对

- 对照真实代码核对所有入口、路由动作、子命令、函数和副作用；
- 对照 `/execution-logs` GET/POST 分支；
- 对照 legacy `/` 的 guard、preview 与 persist；
- 对照 Runtime login/session/request DB 选择代码；
- 对照 `ALLOWED_EXECUTION_MODES` 和 `run_shadowbot_executor.py --execution-mode`；
- 对照 `UI_CHANNEL_PRIORITY`；
- Markdown/链接检查；
- 确认 diff 无业务代码、Schema、生产开关变化。

### 20.2 7B/7C capability 与跨入口

- 每个具体入口绑定预期 capability；
- capability → allowed modes 全矩阵；
- `/execution-logs` GET 只能查询；
- `/execution-logs` start reconcile 只接受既有 UNKNOWN/NEEDS_RECONCILIATION Operation；
- `/execution-logs` manual handled 只执行受控管理员 APPLY；
- legacy `/` 与 `/task-generator` 使用同一 Service 和 canonical identity；
- 7C 后 legacy root POST 不执行 Task preview/persist；
- Web/CLI/Scheduler 使用同一业务 Service；
- 同业务意图、不同真实来源；
- 旧 generic Executor 的 current accepted modes 测试与 target 隔离测试；
- 新 Service 不通过 `execution_mode` 获得能力升级；
- generic RECONCILE 不成为第二恢复入口；
- generic READ_ONLY 不成为第二正式扫描入口。

### 20.3 Runtime DB 迁移回归

- Web 进程启动只绑定一个受控 Repository；
- query/form/session 不能切换 Repository；
- legacy 同路径参数规范化后被移除；
- legacy 异路径参数明确拒绝；
- 新 Session 不保存 Runtime DB；
- 旧 Session 不再拥有数据库权威；
- login、logout、CSRF、Session rotation、PRG、`next` 参数回归；
- Dashboard/Tasks/Reviews/Notifications/Execution Logs/System/Task Generator 均使用同一 Repository；
- `/health` 与业务页面数据库身份一致；
- 旧书签和旧页面链接不会静默切库。

### 20.4 幂等、事务与恢复

- Web 双击、HTTP 重试、CLI 重跑、Scheduler 重启；
- 同引用同 canonical payload 精确重放；
- 同引用异 canonical payload 冲突；
- 跨 18:00、20:00 以及同时跨两个边界的重试；
- Automation `logical_run_key`、Task `dedupe_key`、Operation/Attempt 分层断言；
- PREPARED `APPLY` 与真实 `COMMIT` 分离；
- Queue Service Importer 精确重放和异内容冲突；
- Watchdog 只生成安全恢复或唯一 RECONCILE；
- Review/Outbox 原子回滚；
- 完整 UI priority 顺序；
- 人工任务在 final fence 前压制自动紧急任务；
- UNKNOWN → 唯一 RECONCILE；
- 错误账号拒绝；
- 旧入口退役引用审计。

### 20.5 Ready for review

- 完整 pytest；
- 系统冒烟；
- Linux Core；
- Windows Core；
- `git diff --check`；
- 工作区清洁检查；
- 修改配置、日志、通知或凭据读取时执行 Secret Scan。

若 7B/7C 未修改 v4/v5 发布、写锁、Worker 请求或 Importer，不重复真实平台写验收；一旦修改这些边界，重新评估最小受控实机验收。

## 21. 13.5-7B 开工门禁

必须同时满足：

- PR #29 保持 Draft 并完成设计复审；
- 第三轮复查的 3 个 P1 已确认关闭：Web 混合能力/legacy root、Runtime DB 迁移、旧 Executor current/target 分离；
- 本文入口盘点与代码一致；
- `/execution-logs` 查询、RECONCILE、人工恢复已独立冻结；
- legacy `/` 的 7B 兼容和 7C 退役路径已冻结；
- Web Runtime DB 的 Current → Migration → Target → Tests 已冻结；
- 所有高风险子命令有 current capability、target capability/mode、Composition Root 和退役结论；
- canonical identity 与跨 18:00/20:00 重试合同通过；
- 单账号边界通过；
- 分层幂等矩阵通过；
- Queue Service 是唯一正式 v2/v4/v5 Importer/Watchdog 主线，ORDER_SCAN 保持 Automation-owned importer；
- 三层优先级合同通过；
- 退役清单和复杂度预算通过；
- 未修改生产开关或真实平台链路。

计划文档合并只授权开始 7B 代码工作，不授权真实平台动作、结束 Draft 或合并后续实现。

## 22. 后续边界

- 13.5-8 才进行 `app/webapp/`、路由、模板、Presenter 和静态资源拆分；
- 13.5-9 才进行运营一级入口、信息层级和移动端体验重写；
- 13.5-10 才进行完整交易日连续观察和任务 14 交接；
- 第二平台和真正多账号支持分别单独评审。

---

Refs #20