# 任务 13.5-7A：控制面入口盘点与合同冻结

- 修订日期：2026-08-06
- 状态：编码前设计审查草案
- Review Profile：R4（架构级审查）
- 当前 PR：#29
- 关联父任务：GitHub Issue #20
- 主分支基线：PR #28 合并提交 `418c605f4ab4434eee422eb0217de3cfe64b01b0`

## 1. 计划定位

PR #29 直接承担 **13.5-7A：入口盘点、复用矩阵和合同冻结**，不是 7A 的前置空计划。

本 PR 必须在进入 13.5-7B 编码前完成：

1. Web、主 CLI、Automation Scheduler、人工触发器、Queue Service、平台发布、结果导入、唯一 RECONCILE、运维和验收入口的真实盘点；
2. 每个具有独立副作用边界的脚本子命令单独分类；
3. `具体入口/子命令 → EntryPointCapability → allowed modes → Composition Root → 保留/退役` 矩阵；
4. 业务意图身份、调用来源身份和 Task 来源三层分离；
5. capability 级 canonical identity 与跨 18:00/20:00 重试规则；
6. 分层幂等、事务、恢复和 Importer 权威边界；
7. Incident 人工复核、SYSTEM_EMERGENCY、普通授权写、扫描和结算的完整优先级；
8. 旧入口退役清单；
9. 非持久化结构化结果投影；
10. 13.5-7B/7C 的测试与开工门禁。

本阶段不修改生产代码、Runtime Schema、Web 路由、CLI、Scheduler、v4/v5、ShadowBot、Importer、Worker 部署或生产开关。

## 2. 权威依据

冲突按以下顺序处理：

1. GitHub Issue #20；
2. `AGENTS.md` 的复用优先门禁；
3. 当前生产代码、Runtime Schema、服务和测试；
4. 已合并任务 12、13、13.5-1 至 13.5-6 的合同与实机证据；
5. `docs/pra_review_risk_and_complexity_governance.md`；
6. 本计划。

历史脚本行为与正式合同冲突时，以现行 Task、Review、Automation Run、Batch、Operation、Attempt、共享 UI 写锁、Importer 和唯一 RECONCILE 为准。不得为了保留旧 CLI 而复制第二写链或第二导入链。

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
  错误 Runtime DB、跨时间边界形成第二业务事实、调度优先级绕过、
  长期双控制面或第二 Importer
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

## 4. 当前部署与时间约束

本阶段冻结为：

- 单一 PRA Runtime SQLite；
- 单一 Automation Service 实例租约；
- 单一长期 ShadowBot Worker；
- 单一 `run_shadowbot_queue_services.py` Queue Service 实例锁；
- 当前仅一个真实平台和一个受控账号；
- Web、CLI、Scheduler 可为不同进程，但由各自 Composition Root 绑定同一受控 Repository；
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

### 4.3 Runtime DB 边界

`runtime_db_path` 不是普通业务请求字段。

- Web 请求、表单和业务 payload 不得选择任意 Runtime DB；
- CLI 的 `--runtime-db` 仅是管理员启动/运维配置；
- Composition Root 解析路径并实例化 Repository；
- Application Service 接收 Repository/Unit of Work，不接收请求传入的路径字符串；
- `/health` 继续只读取进程可信配置；
- 不依赖当前工作目录推导生产数据库。

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
- 包括 Task、Review、Outbox 意图、Automation/Script Run、PREPARED Batch/Operation 等；
- 不得点击平台或发布 COMMIT 请求。

### 5.5 `COMMIT`

- 只接受既有有效授权 Task/Batch/Operation；
- 必须复用 gate、Batch、Operation、Attempt、共享 UI 写锁、队列发布和 Importer；
- 入口不得重建批准载荷、修改来源或绕过更高优先级 lane；
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
| 唯一平台对账 | `ensure_listing_action_reconcile_attempt`、Importer/Watchdog 的自动 reconcile payload |
| 两页只读同步 | `prepare_listing_sync_batch`、`publish_listing_sync_batch`、`import_listing_sync_result` |
| v4 改价发布 | `prepare_task_commit_batch`、`publish_task_commit_batch`、`import_task_commit_result` |
| 审批和门禁 | Review/Token/Outbox 原子服务、`evaluate_automation_gate`、`review_block_reasons` |
| 写身份和执行身份 | Batch、Operation、Attempt、`write_identity_key`、`execution_attempt_id` |
| 共享 UI 写锁 | 现有平台/任务写锁和发布事务 |
| Importer | 现有 Result Importer 原子投影、receipt、ACK、archive/quarantine |
| Worker | 现有领取、请求校验、phase、结果发布和登录人工介入 |
| Incident | Incident Event、通知、S4 授权、人工竞态和最终点击 fence |
| Automation 优先级 | 现有 `UI_CHANNEL_PRIORITY`，不得复制第二份 |

## 7. 当前入口真实盘点

分类只使用：

1. 原样复用；
2. 参数化复用；
3. 抽取公共能力；
4. 确需新增；
5. 归档或删除。

### 7.1 Web 入口

| 具体入口 | 当前函数/服务 | Capability | 模式/副作用 | Composition Root | 7B/7C 结论 |
|---|---|---|---|---|---|
| WSGI 总入口 | `app.web::_application` | `WEB_ROUTER` | 路由、认证、CSRF、路径策略 | `app.web.serve` | 原样复用安全门禁；路由变薄 |
| `/health` | 固定 Repository health | `HEALTH_DIAGNOSTIC` | `DIAGNOSTIC` | Web 进程可信配置 | 原样复用 |
| `/dashboard`、`/tasks`、`/notifications`、`/execution-logs` | 查询服务 | `RUNTIME_QUERY` | `READ_ONLY` | Web 进程 Repository | 原样复用查询 |
| `/system` | 配置与运行状态查询 | `SYSTEM_DIAGNOSTIC` | `DIAGNOSTIC` | Web 进程可信配置 | 原样复用 |
| `/task-generator` 预览 | workflow preview | `TASK_PREVIEW` | `DRY_RUN` | Web Composition Root | 抽取公共能力 |
| `/task-generator` 持久化 | workflow persist | `TASK_APPLY` | `APPLY` | Web Composition Root | 使用统一 Service |
| `/reviews`、`/runtime` 查询 | Runtime/Review query | `REVIEW_QUERY` | `READ_ONLY` | Web Repository | 原样复用 |
| `/reviews`、`/runtime` 处理 | `resolve_runtime_review_task` | `REVIEW_APPLY` | `APPLY` | Web Composition Root | 参数化复用 |
| `/mobile/review/*` GET | `get_mobile_review_detail` | `MOBILE_REVIEW_QUERY` | `READ_ONLY` | 固定 Runtime DB | 原样复用 |
| `/mobile/review/*` resolve | `resolve_mobile_review` | `MOBILE_REVIEW_APPLY` | `APPLY` | 固定 Runtime DB + Token 服务 | 原样复用原子事务 |
| `/business-inputs`、`/tables` | workbook 输入服务 | `WORKBOOK_MAINTENANCE` | 管理员维护；写受控工作簿 | Web 路径策略 | 不并入平台控制面 |
| `/execution` | `simulate_execution_from_tasks` | `LEGACY_SIMULATION` | 测试文件副作用，无平台写 | Web 路径策略 | 归档候选/兼容入口 |
| `/manual-intervention` | `list_manual_intervention_tasks` | `LEGACY_MANUAL_READ` | `READ_ONLY`；正式 resolve 已拒绝 | Web 路径策略 | 保留只读说明或归档 |
| `/system/test-feishu-notification` | 通知测试处理器 | `NOTIFICATION_DIAGNOSTIC` | 受控外部测试通知 | Web 运维配置 | 管理员能力，不进入业务模式 |

Web 不得通过 subprocess 调用 CLI，也不得直接拼接 ShadowBot 队列 JSON。

### 7.2 主 CLI：`app/cli.py`

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
| `serve-web` | 启动 WSGI | `PROCESS_BOOTSTRAP` | 无业务模式 | Web Composition Root | 保持启动器职责 |

### 7.3 Automation Service

| 具体入口 | 当前函数/服务 | Capability | 允许模式 | Composition Root | 结论 |
|---|---|---|---|---|---|
| Service 启动 | `scripts/run_automation_service.py::main` | `AUTOMATION_BOOTSTRAP` | 无用户模式 | 启动参数与环境变量 | 原样复用 Composition Root |
| 窗口物化/claim | `AutomationService.run_cycle` | `AUTOMATION_SCHEDULE` | `APPLY` | Automation Repository | 原样复用 |
| 结算 handlers | `build_sales_settlement_handlers` | `SETTLEMENT_APPLY` | `APPLY` | 已 claim 的 Automation Run | 原样复用/参数化 |
| 订单只读 handlers | `build_order_read_only_handlers` | `ORDER_SCAN_READ_ONLY` | `READ_ONLY` | 已 claim 的 Automation Run | 原样复用 |
| Incident handlers | `build_incident_notification_handlers` | `INCIDENT_MAINTENANCE_APPLY` | `APPLY` | 已 claim 的 Automation Run | 原样复用 |
| Worker recovery | `build_worker_recovery_coordinator_from_environment` | `HOST_RECOVERY_MAINTENANCE` | 内部能力 | 显式开关 + reviewed helper | 独立运维能力 |

Automation Service 当前不注册普通平台写 handler，且心跳声明 `platform_write_handlers_registered=false`。13.5-7 不得把它扩展为扫描全部 pending Task 后自动 COMMIT。

### 7.4 人工只读与规则评估脚本

| 具体入口 | 当前服务 | Capability | 允许模式 | Composition Root | 结论 |
|---|---|---|---|---|---|
| `run_shadowbot_listing_sync.py` | `prepare_listing_sync_batch` + `publish_listing_sync_batch` | `LISTING_SYNC_READ_ONLY` | `READ_ONLY` | CLI Repository + Queue Runner | 统一只读扫描 Service 的薄触发器 |
| `evaluate_business_rules.py` 默认 | `BusinessRuleRunner.run` | `RULE_EVALUATION_PREVIEW` | `DRY_RUN` | CLI Repository + workbook paths | 保留薄 CLI |
| `evaluate_business_rules.py --apply` | `BusinessRuleRunner.run` | `RULE_EVALUATION_APPLY` | `APPLY` | CLI Composition Root | 统一 Service 生成来源与身份 |
| `run_mock_platform_executor.py --dry-run` | Mock 平台服务 | `MOCK_PLATFORM_PREVIEW` | `DRY_RUN` | 隔离 Mock DB | 测试工具 |
| `run_mock_platform_executor.py --apply` | Mock 平台服务 | `MOCK_PLATFORM_APPLY` | `APPLY` 但仅 Mock | 隔离 Mock DB | 不得连接真实平台 |

### 7.5 正式 Queue Service：`run_shadowbot_queue_services.py`

该脚本不是单一“Importer 入口”，而是长驻 Composition Root。一个周期按既有顺序运行：

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

### 7.6 v4 改价入口：`run_shadowbot_commit_batch.py`

| 子命令 | 当前服务 | Capability | 允许模式 | Composition Root | 7B/7C 结论 |
|---|---|---|---|---|---|
| `prepare` | `prepare_task_commit_batch` | `AUTHORIZED_WRITE_PREPARE` | `APPLY` | CLI Repository + mapping | 保留薄适配；只写 PREPARED 账本，不发布队列 |
| `publish` | `publish_task_commit_batch` | `AUTHORIZED_WRITE_COMMIT` | `COMMIT` | CLI Repository + Queue Runner | 只消费已准备 manifest；复用 gate/锁/Attempt |
| `production-run` | prepare + publish | `AUTHORIZED_WRITE_COMPOSITE_COMMIT` | `COMMIT` | CLI Composition Root | 兼容入口；内部仍按 prepare→publish 边界执行 |
| `import-result` | `import_task_commit_result` | `LEGACY_RESULT_IMPORT` | 内部/管理员 | CLI Repository + result file | 7C 引用审计后由 Queue Service Importer 取代 |

`production-run` 不得被理解为新的自动 pending 扫描器；仍要求显式 task IDs、batch ID、mapping、queue 和 applet URI。

### 7.7 旧 Executor 入口：`run_shadowbot_executor.py`

| 子命令 | 当前行为 | Capability | 允许模式 | Composition Root | 7B/7C 结论 |
|---|---|---|---|---|---|
| `start` | 由 CLI 参数重建 approved payload，并接受 `--execution-mode` | `LEGACY_AUTHORIZED_WRITE_COMMIT` | 当前可 COMMIT | CLI Repository + Runner | 7B 不接入公共控制面；7C 引用审计后退役或限制为测试 |
| `import-result` | `ShadowBotExecutor.record_result` | `LEGACY_RESULT_IMPORT` | 内部/管理员 | CLI Repository + result file | 由 Queue Service Importer 取代后退役 |
| `poll-yingdao-result` | 查询影刀任务并导入结果 | `LEGACY_OPENAPI_RESULT_IMPORT` | 内部/管理员 | Yingdao OpenAPI + CLI Repository | OpenAPI 引用审计后退役或隔离维护 |
| `check-yingdao-app-params` | 查询机器人输入/输出参数 | `PLATFORM_ADAPTER_DIAGNOSTIC` | `DIAGNOSTIC` | Yingdao OpenAPI | 保留管理员诊断 |

在 `start` 退役前：

- 普通 Web/Scheduler 不得调用；
- `execution_mode` 不能被新统一 Service 原样信任；
- 新 Service 只能从既有批准 Task/Operation 读取不可变载荷；
- 不得把该脚本作为第二条正式 COMMIT 主线。

### 7.8 v5 上下架服务级入口

| 服务能力 | Capability | 模式 | 权威身份 | 结论 |
|---|---|---|---|---|
| 提案/准备 | `LISTING_ACTION_PREPARE` | `APPLY` | 授权 Task、policy、write identity | 原样复用 |
| 发布 | `LISTING_ACTION_COMMIT` | `COMMIT` | Batch/Operation/Attempt | 原样复用 |
| 结果导入 | `RESULT_IMPORT` | 内部 | result/attempt/hash | 只走 Queue Service Importer |
| UNKNOWN 对账 | `UNKNOWN_RECONCILE` | `RECONCILE` | 既有 Operation + 唯一 reconcile attempt | 唯一恢复入口 |
| SYSTEM_EMERGENCY 下架 | `SYSTEM_EMERGENCY_COMMIT` | `COMMIT` | S4 authorization + final fence | 原样复用；默认开关仍关闭 |

### 7.9 迁移、运维、诊断和验收工具

以下入口不并入统一业务 Application Service：

- `reconcile_shadowbot_listing_skus.py`：SKU 数据迁移，不是平台 RECONCILE；
- `repair_shadowbot_expired_attempt.py`：受控恢复工具；
- `check_runtime_env.py`、`check_shadowbot_readiness.py`、`check_shadowbot_worker_health.py`；
- `release_backup.py`、`sync_shadowbot_test2.py`、`verify_shadowbot_deployment.py`；
- 所有 `export_task12/13_*`、`verify_task12/13_*`、故障注入和受控实机脚本；
- `build_task11_*`、`freeze_task13_v12_baseline.py`、`run_shadowbot_e2e_local_demo.py` 等归档候选。

它们只能执行维护、证据、迁移或受控测试职责，不能被 Web/Scheduler 当作生产业务 Service。

## 8. 具体入口能力绑定门禁

所有生产入口必须在 Composition Root 中获得固定 `EntryPointCapability`。调用方不得通过请求参数声明 capability。

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
- 旧 CLI 的 `--execution-mode` 不得穿透新 Service 的能力上限；
- 当前普通 Web 不提供通用 COMMIT；
- Scheduler 不注册普通平台写 handler。

高风险入口的强制绑定：

| 入口 | 固定 Capability | allowed modes | Repository/Adapter 来源 | 处置 |
|---|---|---|---|---|
| Web `/task-generator` preview | `TASK_PREVIEW` | `{DRY_RUN}` | Web Composition Root | 保留 |
| Web `/task-generator` persist | `TASK_APPLY` | `{APPLY}` | Web Composition Root | 变薄 |
| CLI `generate-runtime-tasks` | `TASK_APPLY` | `{APPLY}` | CLI Composition Root | 变薄 |
| Automation order scan handler | `ORDER_SCAN_READ_ONLY` | `{READ_ONLY}` | claimed Run | 保留 |
| Automation settlement handler | `SETTLEMENT_APPLY` | `{APPLY}` | claimed Run | 保留 |
| `run_shadowbot_listing_sync.py` | `LISTING_SYNC_READ_ONLY` | `{READ_ONLY}` | CLI Composition Root | 变薄 |
| commit batch `prepare` | `AUTHORIZED_WRITE_PREPARE` | `{APPLY}` | CLI Composition Root | 保留 |
| commit batch `publish` | `AUTHORIZED_WRITE_COMMIT` | `{COMMIT}` | CLI Composition Root | 保留 |
| commit batch `production-run` | `AUTHORIZED_WRITE_COMPOSITE_COMMIT` | `{COMMIT}` | CLI Composition Root | 兼容 |
| commit batch `import-result` | `LEGACY_RESULT_IMPORT` | internal/admin | CLI Repository | 退役候选 |
| executor `start` | `LEGACY_AUTHORIZED_WRITE_COMMIT` | 当前 `{COMMIT}` | CLI Repository + Runner | 不接入新主线 |
| executor `import-result` | `LEGACY_RESULT_IMPORT` | internal/admin | CLI Repository | 退役 |
| executor `poll-yingdao-result` | `LEGACY_OPENAPI_RESULT_IMPORT` | internal/admin | OpenAPI + CLI Repository | 隔离/退役 |
| Queue Service Importer | `RESULT_IMPORT` | internal only | Queue Service Composition Root | v2/v4/v5 唯一正式主线 |
| Queue Service Watchdog | `QUEUE_RECOVERY` | internal only | Queue Service Composition Root | v2/v4/v5 唯一恢复主线 |
| v5 publish | `LISTING_ACTION_COMMIT` | `{COMMIT}` | 正式发布 Composition Root | 保留 |
| unique reconcile | `UNKNOWN_RECONCILE` | `{RECONCILE}` | 既有 Operation | 唯一入口 |

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

这些字段保留 Web、CLI、Scheduler 和人工补跑的真实差异。

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
- `requested_at`、当前时钟、HTTP session、trace ID、actor 显示名、`manual_reason` 等审计字段绝不参与业务幂等键或内容指纹；
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

Composition Root
  ├─ 绑定受控 Runtime Repository
  ├─ 绑定部署 platform/current account
  ├─ 绑定 queue/adapter
  └─ 为具体入口注入固定 EntryPointCapability

ShadowBot Worker
  → v2/v4/v5 Result Files → Queue Service Importer
  → ORDER_SCAN Result Files → Automation observation importer
  → Runtime authoritative ledgers
```

统一 Service 不直接操作页面、不直接拼接队列 JSON、不写第二套结果投影、不持有第二状态机。

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
- Queue Service Importer 原子提交 v2/v4/v5 receipt、operation/attempt、Task、Lock、平台事实和 Incident 投影；
- ORDER_SCAN 继续由既有 Automation observation importer 导入，不得被新 Service 吸收；
- 兼容 CLI Importer 不得长期成为第二正式主线；
- 入口层不得在 Importer 之外“补写成功”；
- 超时只能精确重放，不得推测副作用状态；
- UNKNOWN 后禁止第二次不安全写，只允许唯一 RECONCILE。

## 16. 保留与退役清单

### 16.1 保留为正式 Composition Root/薄适配器

- `scripts/run_automation_service.py`；
- `scripts/run_shadowbot_queue_services.py`；
- `scripts/evaluate_business_rules.py`；
- `scripts/run_shadowbot_listing_sync.py`；
- `scripts/run_shadowbot_commit_batch.py prepare/publish/production-run`；
- 主 CLI 的 Runtime 查询、复核和受控 APPLY 命令。

### 16.2 受控运维/诊断

- `run_shadowbot_executor.py check-yingdao-app-params`；
- `reconcile_shadowbot_listing_skus.py`；
- `repair_shadowbot_expired_attempt.py`；
- 部署同步、hash 验证、备份和健康检查脚本。

### 16.3 归档或退役候选

- `run_shadowbot_executor.py start`；
- `run_shadowbot_executor.py import-result`；
- `run_shadowbot_executor.py poll-yingdao-result`；
- `run_shadowbot_commit_batch.py import-result`；
- 旧 Excel `resolve-manual-task` 和 Web `/manual-intervention` 正式处理路径；
- 阶段性 demo、baseline build 和旧证据脚本。

删除前必须完成 CI、Runbook、部署脚本、证据复算和运维引用审计。

## 17. 阶段拆分

### 17.1 13.5-7A：本 PR

交付本文的真实入口清单、子命令级 capability 矩阵、canonical identity、完整优先级、退役清单和开工门禁。零业务代码、零真实平台副作用。

### 17.2 13.5-7B：统一 Application Service

仅在 7A 复审通过后开始：

- 最多一组轻量 Application Service；
- Composition Root 固定绑定 Repository、平台、账号和 capability；
- 薄 Web/CLI/Scheduler/manual adapters；
- capability/mode 服务端白名单；
- capability 级 canonical identity；
- 分层幂等复用；
- 统一非持久化结果投影；
- 小型集成和并发测试。

### 17.3 13.5-7C：旧入口退役

- 删除重复业务判断；
- 兼容入口变薄或归档；
- 收口第二 Importer/旧 Executor；
- 更新 Runbook 和启动脚本；
- 重启、精确重放、跨 18:00/20:00 和人工补跑测试；
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

若实现要求突破预算，停止编码并单独评审。

## 19. P1 阻塞清单

1. 入口盘点按脚本名合并，遗漏不同子命令的副作用边界；
2. 具体入口未绑定固定 capability 和 allowed modes；
3. capability 由请求参数决定；
4. READ_ONLY/DIAGNOSTIC 获得 APPLY/COMMIT；
5. COMMIT 接受未授权、过期或错误平台 Task；
6. RECONCILE 接受非 UNKNOWN Operation 或执行第二次写；
7. 业务身份使用 `requested_at` 或重试时当前时钟；
8. 跨 18:00/20:00 重试形成第二业务事实；
9. 人工补跑伪装成 Scheduler；
10. 外部参数伪造来源、审批或策略字段；
11. 请求可自由选择 Runtime DB 或账号；
12. 一个统一幂等键覆盖 Run/Task/Operation/Attempt；
13. 为同一 v2/v4/v5 合同在 Queue Service 之外保留第二正式 Importer/Watchdog；
14. 旧 Executor 长期保留完整 COMMIT 主线；
15. Web subprocess 调 CLI 或直接写队列 JSON；
16. 调度优先级被复制、压平或绕过；
17. UNKNOWN 被普通补跑或新写覆盖；
18. 结构化结果变成第二持久化状态机；
19. 第二队列、第二 Worker、第二写链；
20. `automatic_emergency_offline` 被提前开启。

后续复审原则上只验证这些冻结问题及修复直接引入的回归。

## 20. 测试矩阵

### 20.1 7A

- 对照真实代码核对所有入口、子命令、函数和副作用；
- 对照 `UI_CHANNEL_PRIORITY`；
- Markdown/链接检查；
- 确认 diff 无业务代码、Schema、生产开关变化。

### 20.2 7B/7C

- 每个具体入口绑定预期 capability；
- capability → allowed modes 全矩阵；
- 旧 CLI 不能通过 `--execution-mode` 升级；
- Web/CLI/Scheduler 使用同一 Service；
- 同业务意图、不同真实来源；
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
- 错误账号与请求级 Runtime DB 注入拒绝；
- 旧入口退役引用审计。

### 20.3 Ready for review

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
- 本文入口盘点与代码一致；
- 所有高风险子命令有独立 capability、mode、Composition Root 和退役结论；
- canonical identity 与跨 18:00/20:00 重试合同通过；
- 单账号与 Runtime DB 边界通过；
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
