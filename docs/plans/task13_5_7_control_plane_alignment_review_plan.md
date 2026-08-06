# 任务 13.5-7A：控制面入口盘点与合同冻结

- 修订日期：2026-08-06
- 状态：编码前设计审查草案
- Review Profile：R4（架构级审查）
- 当前 PR：#29
- 关联父任务：GitHub Issue #20
- 基线：PR #28 已合并，主分支合并提交 `418c605f4ab4434eee422eb0217de3cfe64b01b0`
- 当前 PR Head 基线：`78522c85f2f22b660cb3c498027e682f01de0688`

## 1. 计划定位

PR #29 直接承担 **13.5-7A：入口盘点、复用矩阵和合同冻结**，不再只是“7A 前置计划”。

本 PR 必须在进入 13.5-7B 编码前完成：

1. Web、CLI、Automation Scheduler、人工补跑、平台发布、平台对账、运维和验收入口的真实盘点；
2. 五类复用矩阵；
3. 目标 Application Service 调用图；
4. 入口能力与允许模式矩阵；
5. 业务意图身份、调用来源身份和任务来源合同；
6. 分层幂等矩阵；
7. 调度优先级、事务和恢复边界；
8. 旧入口退役清单；
9. 结构化结果投影；
10. 13.5-7B/7C 的测试和开工门禁。

本阶段不修改业务代码、Runtime Schema、Web 路由、CLI、Scheduler、v4/v5、ShadowBot、Importer 或生产开关。

## 2. 权威依据

按以下顺序处理冲突：

1. GitHub Issue #20；
2. `AGENTS.md` 的复用优先门禁；
3. 当前生产代码、Runtime Schema、服务和测试；
4. 已合并任务 12、13、13.5-1 至 13.5-6 的合同与实机证据；
5. `docs/pra_review_risk_and_complexity_governance.md`；
6. 本计划。

历史脚本行为与正式服务冲突时，以现行 Task、Review、Automation Run、Batch、Operation、Attempt、写锁、Importer 和唯一 RECONCILE 为准。

## 3. 审查配置

```text
Review Profile: R4
真实平台写操作: 本 PR 无；后续 7B/7C 仅连接既有 v4/v5 写入口
连接权威模块:
  Web / CLI / Automation Scheduler / Runtime Task / Review /
  Automation Run / Incident / Outbox / Batch / Operation /
  Attempt / Write Lock / Importer / ShadowBot File Queue
最坏事故:
  重复平台副作用、覆盖人工操作、错误来源授权、UNKNOWN 后二次写、
  调度优先级绕过、错误 Runtime DB、长期双控制面
人工恢复成本: 高
新增数据库表: 0
新增持久化字段: 0
新增状态: 0
新增锁: 0
新增队列/Worker/Importer: 0
新增全局幂等账本: 0
新增 Application Service 外观层: 最多 1 组
测试范围:
  7A 文档和静态核对；7B/7C 模块、集成、恢复、多平台边界、全量 CI
明确非目标:
  Web UI 重写、第二平台、第二账号并行、Schema v17、自动普通任务 COMMIT
```

## 4. 当前部署约束

本阶段冻结为：

- 单一 PRA Runtime SQLite；
- 单一 Automation Service 实例租约；
- 单一长期 ShadowBot Worker；
- 当前仅一个真实平台和一个受控账号；
- Web、CLI、Scheduler 可为不同进程，但必须由各自进程装配层绑定同一受控 Repository；
- 真实平台写继续只走既有 v4/v5 文件队列；
- UNKNOWN 继续只进入唯一只读 RECONCILE；
- `automatic_emergency_offline=false` 保持生产默认值。

### 4.1 当前账号边界

当前数据库的 Task、Automation Run、Operation、Attempt 和写锁没有完整 `account_id` 持久化维度。
因此本阶段不宣称已经实现多账号账本隔离，也不新增字段补齐。

冻结规则：

- 当前账号由部署配置绑定；
- 外部请求不得自由指定或切换账号；
- 非当前部署账号必须在服务边界被拒绝；
- 真正多账号持久化隔离进入独立 R4 + Schema 评审。

### 4.2 Runtime DB 边界

`runtime_db_path` 不是普通业务请求字段。

冻结规则：

- Web 请求、表单和普通业务 payload 不得选择任意 Runtime DB；
- CLI 的 `--runtime-db` 只允许作为管理员启动/运维配置；
- 路径必须在 Composition Root 中解析并实例化 Repository；
- Application Service 接收 Repository/Unit of Work，不接收请求来源的路径字符串；
- `/health` 继续只读取进程可信配置；
- 不依赖当前工作目录推导生产数据库。

## 5. 不可破坏的既有资产

以下入口和安全属性必须原样复用或兼容参数化，不得复制平行实现：

| 资产 | 冻结 API/职责 |
|---|---|
| v5 请求和合同校验 | `build_listing_action_request`、`build_listing_action_reconcile_request`、相应 validate 函数 |
| 上下架提案/发布/导入 | `propose_listing_action_batch`、`publish_listing_action_batch`、`import_listing_action_result` |
| 唯一平台对账 | `ensure_listing_action_reconcile_attempt`、Watchdog `_automatic_reconcile_payload` |
| 两页只读同步 | `prepare_listing_sync_batch`、`publish_listing_sync_batch`、`import_listing_sync_result` |
| v4 改价发布 | `prepare_task_commit_batch`、`publish_task_commit_batch`、`import_task_commit_result` |
| 审批和门禁 | 现有 Review/Token/Outbox 原子服务、`evaluate_automation_gate`、`review_block_reasons` |
| 写身份和执行身份 | Batch、Operation、Attempt、`write_identity_key`、`execution_attempt_id` |
| 共享 UI 写锁 | 现有平台/任务写锁和发布事务 |
| Importer | 现有 Result Importer 原子投影、receipt、ACK |
| Worker | 现有领取、请求校验、phase、结果发布和登录人工介入 |
| Incident | 已合并 Incident Event、通知、S4 授权和最终点击栅栏 |

## 6. 当前入口真实盘点

分类：

1. 原样复用；
2. 参数化复用；
3. 抽取公共能力；
4. 确需新增；
5. 归档或删除。

### 6.1 Web 入口

| 入口 | 当前文件/函数 | 当前能力 | 当前事实/副作用 | 7B 目标 |
|---|---|---|---|---|
| WSGI 总入口 | `app/web.py::_application` | 路由、认证、CSRF、路径策略 | 分派各页面 | 原样复用安全门禁；路由变薄 |
| `/health` | `_application` 内固定 Repository | Schema/operational health | 只读 | 原样复用；DIAGNOSTIC |
| `/dashboard`、`/tasks`、`/notifications`、`/execution-logs`、`/system` | 对应 `_handle_*` | 运行投影和配置展示 | 只读为主 | 原样复用查询；READ_ONLY/DIAGNOSTIC |
| `/task-generator` | `_handle_task_generator_page`、workflow 预览/持久化函数 | 预览或创建 Task/Review/通知 | 可产生业务事实，不直接平台写 | 抽取公共能力；DRY_RUN/APPLY |
| `/reviews`、`/runtime` | Review 查询与 `resolve_runtime_review_task` | 人工复核 | 更新 Review/Task；不直接点击平台 | 参数化复用；APPLY |
| `/mobile/review/*` | `get_mobile_review_detail`、`resolve_mobile_review` | Token 复核和 Incident 原子处置 | 可创建/失效任务和授权；不直接平台写 | 原样复用原子事务；APPLY |
| `/business-inputs`、`/tables` | workbook 输入服务 | 商品、规则和经营输入维护 | 写受控工作簿，不写平台 | 13.5-7 不改语义 |
| `/execution` | `simulate_execution_from_tasks` | 旧 Excel 模拟执行 | 仅模拟 | 归档候选/兼容只读，不进入正式控制面 |
| `/manual-intervention` | `list_manual_intervention_tasks` | 旧 Excel 人工介入 | 已只读、拒绝正式 resolve | 归档或保留只读说明 |
| `/system/test-feishu-notification` | Web 通知测试处理器 | 受控测试通知 | 可能发送测试通知 | 运维能力，不进入业务模式矩阵 |

Web 不得通过 subprocess 调用任何 CLI，也不得直接拼接 ShadowBot 队列 JSON。

### 6.2 主 CLI

| 命令 | 当前服务 | 当前模式 | 7B 处理 |
|---|---|---|---|
| `validate`、`import-data`、`preview-tasks` | workflow 校验/预览 | READ_ONLY/DRY_RUN | 参数化复用 |
| `generate-tasks` | Excel 任务生成 | 旧文件输出 | 兼容工具，不进入 Runtime 正式主线 |
| `generate-runtime-tasks` | `generate_runtime_tasks_from_sources` | APPLY | 抽取统一业务服务；保留薄 CLI |
| `list-tasks`、`show-task-history`、`list-review-tasks` | Runtime 查询服务 | READ_ONLY | 原样复用查询 |
| `resolve-review-task` | `resolve_runtime_review_task` | APPLY | 薄适配；来源由服务决定 |
| `expire-review-tasks` | `expire_runtime_review_tasks` | 默认 DRY_RUN，显式 APPLY | 参数化复用 |
| `notification-worker` | `NotificationOutboxWorker` | 内部投递 | 保持独立 Worker；不并入业务请求模式 |
| `health` | Repository health | DIAGNOSTIC | 原样复用 |
| `serve-web` | `app.web.serve` | 进程启动 | 保持启动器职责 |
| `resolve-manual-task` | 已弃用 | 禁止 | 删除或继续硬失败 |

### 6.3 Automation Service

| 入口 | 当前文件/函数 | 当前能力 | 平台写 | 7B 结论 |
|---|---|---|---|---|
| Service 启动 | `scripts/run_automation_service.py::main` | 装配 Runtime Repository、Jobs、Handlers、Heartbeat | 否 | 原样复用 Composition Root |
| 调度循环 | `AutomationService.run_cycle` | 窗口物化、租约、claim、handler 调用 | 否 | 原样复用 |
| 结算 handlers | `build_sales_settlement_handlers` | 日结和计划输入 | 否 | 原样复用/参数化 |
| 订单只读 handlers | `build_order_read_only_handlers` | FULL_MARKET_SCAN/ORDER_SCAN READ_ONLY | 否 | 原样复用 |
| Incident handlers | `build_incident_notification_handlers` | 通知、提醒、恢复协调 | 当前不注册平台写 handler | 原样复用 |
| Worker recovery | `build_worker_recovery_coordinator_from_environment` | 受控宿主恢复 | 不等于平台业务写 | 独立运维能力 |

当前 `run_automation_service.py` 明确记录 `platform_write_handlers_registered=false`。13.5-7 不得把普通 Scheduler 扩展为自动扫描全部 pending Task 并 COMMIT。

### 6.4 手工只读与业务规则脚本

| 脚本 | 当前调用 | 当前风险 | 7B 处理 |
|---|---|---|---|
| `scripts/run_shadowbot_listing_sync.py` | `prepare_listing_sync_batch` + `publish_listing_sync_batch` | READ_ONLY 队列请求 | 改为统一只读扫描服务的薄手工触发器 |
| `scripts/evaluate_business_rules.py` | `BusinessRuleRunner.run` | DRY_RUN 或显式 APPLY | 保留薄 CLI；业务意图和来源由统一服务生成 |
| `scripts/run_mock_platform_executor.py` | Mock 平台服务 | 测试数据副作用 | 继续隔离为测试工具 |

### 6.5 真实平台写入口

| 脚本/入口 | 当前调用 | 当前约束 | 7B/7C 结论 |
|---|---|---|---|
| `scripts/run_shadowbot_commit_batch.py` | `prepare_task_commit_batch`、`publish_task_commit_batch`、`import_task_commit_result` | 显式 task IDs；v4 gate/账本/队列 | 保留兼容薄适配；不得成为 pending 扫描器 |
| `scripts/run_shadowbot_executor.py start` | `ShadowBotExecutor.start_execution` | 需要 approval、operation、attempt | 旧兼容/管理员入口；不得让外部重建批准 payload |
| `scripts/run_shadowbot_executor.py import-result` | `ShadowBotExecutor.record_result` | 导入既有 attempt 结果 | 由正式 Importer 主线取代；完成引用审计后退役 |
| v5 上下架发布服务 | 既有 propose/publish API | 授权 Task、优先级、Review、写锁、UNKNOWN | 原样复用；不新建入口 |
| SYSTEM_EMERGENCY | 已合并 S4 授权链 | 开关、授权、人工竞态、最终 fence | 原样复用；本阶段不启用 |

### 6.6 RECONCILE 与迁移工具区分

平台唯一 RECONCILE 是：

```text
既有 UNKNOWN / NEEDS_RECONCILIATION Operation
→ ensure_listing_action_reconcile_attempt
→ 唯一只读 RECONCILE request
→ Importer 收敛 VERIFIED / NOT_APPLIED / UNKNOWN
```

`scripts/reconcile_shadowbot_listing_skus.py` 只按可信页面身份修正 `listing_status.internal_sku`，属于管理员受控数据迁移工具，不是平台副作用 RECONCILE，也不得进入业务对账入口。

### 6.7 运维、诊断和验收工具

以下能力不进入统一业务入口：

- `check_runtime_env.py`、`check_shadowbot_readiness.py`、`check_shadowbot_worker_health.py`；
- `release_backup.py`、`sync_shadowbot_test2.py`、`verify_shadowbot_deployment.py`；
- `repair_shadowbot_expired_attempt.py`；
- 所有 `export_task12/13_*`、`verify_task12/13_*`、故障注入和受控实机脚本；
- `build_task11_*`、`freeze_task13_v12_baseline.py`、`run_shadowbot_e2e_local_demo.py` 等归档候选。

它们只能执行运维、证据、迁移或受控测试职责，不能被 Web 或 Scheduler 当作生产业务 Service。

## 7. 目标调用图

```text
Web Route ───────┐
CLI Adapter ─────┼─> ControlPlaneApplicationService（最多一组轻量外观）
Manual Rerun ────┤       ├─ OperationalTimeService
Scheduler Handler┘       ├─ Runtime Task / Review / Incident Services
                          ├─ Automation Service / Repository
                          ├─ v4/v5 Proposal + Publish Services
                          └─ Structured ApplicationResult

Composition Root
  ├─ 绑定受控 Runtime Repository
  ├─ 绑定部署 platform_name/current account
  ├─ 绑定 queue/adapter capability
  └─ 生成 EntryPointCapability

平台结果
  ShadowBot Worker → Result Files → 既有 Importer → Runtime authoritative ledgers
```

统一 Service 不直接操作页面、不直接生成文件名、不直接写结果投影、不持有第二套状态机。

## 8. 业务意图、调用来源和任务来源分离

### 8.1 业务意图身份

`business_intent_identity` 用于判断两个调用是否针对同一业务目标。建议由现有稳定字段派生，不新增表：

```text
capability
platform_name
platform_trade_date
seller_operation_date
scope_type
scope_key
requested_business_action
policy_version
logical_target_reference
```

跨入口只要求业务意图和业务结果等价，不要求调用来源相同。

### 8.2 调用来源身份

`invocation_identity` 用于审计谁、从哪里、为什么发起调用：

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
request_fingerprint
```

调用来源必须保留真实差异：Web、CLI、Scheduler 和人工补跑不得互相伪装。

### 8.3 任务来源

| 场景 | origin_type | origin_ref_id |
|---|---|---|
| Scheduler 正常运行 | `AUTOMATION` | `automation-run:<run_id>` |
| Web 人工业务动作 | `MANUAL` | `web:<stable request/actor ref>` |
| CLI 人工业务动作 | `MANUAL` | `cli:<stable request/actor ref>` |
| 人工补跑 | `MANUAL` | `manual-rerun:<new run or request id>` |
| Incident 人工处置 | `MANUAL` | `incident-review:<review_task_id>` |
| 系统紧急保护 | `SYSTEM_EMERGENCY` | `emergency:<authorization_id>` |

人工补跑必须记录原计划时间、原因、操作者和原 Run 链接；不得伪装成正常 Scheduler 运行。

`approval_policy` 和 `policy_version` 由服务端业务能力决定，Web/CLI 不得直接提交或覆盖。

## 9. 入口能力与允许模式白名单

模式不是调用方自由选择的线性权限等级。服务端先根据入口和业务能力确定允许集合，调用方只能在集合内选择更保守行为。

| 入口/能力 | 允许模式 | 禁止事项 |
|---|---|---|
| 健康、配置、账本诊断 | `DIAGNOSTIC` | 创建 Task/Run/Review、修改状态 |
| Dashboard/列表/详情查询 | `READ_ONLY` | 写业务事实 |
| 任务预览、规则预览 | `DRY_RUN` | Task/Review/真实 Outbox |
| 手工只读扫描 | `READ_ONLY` | 写 Task、申请写锁、COMMIT |
| Scheduler 只读扫描 | `READ_ONLY` | 平台写 |
| Scheduler 结算/Incident 维护 | 按 job capability 的 `APPLY` | 任意 COMMIT |
| Web/CLI 创建业务任务或处理复核 | `APPLY` | 自动进入 COMMIT |
| 显式授权 Task 发布 | `COMMIT` | 接受未授权或过期 Task |
| UNKNOWN Operation 对账 | `RECONCILE` | 创建第二次平台写 |
| Importer | 内部结果导入能力，不接受用户模式 | 接受无既有 attempt 的结果 |
| 数据迁移/修复工具 | 管理员 maintenance，独立于业务模式 | 暴露给普通 Web/Scheduler |

硬门禁：

```text
requested_mode in allowed_modes(entrypoint_capability)
```

- READ_ONLY/DIAGNOSTIC 调用方不能申请 APPLY/COMMIT；
- COMMIT 只接受既有、有效、已授权 Task；
- RECONCILE 只接受既有 UNKNOWN/NEEDS_RECONCILIATION Operation；
- 当前普通 Web 不提供通用 COMMIT 按钮；
- Scheduler 不注册普通平台写 handler。

## 10. 分层幂等矩阵

不得用一个“统一幂等键”同时替代所有权威账本，也不新增全局幂等表。

| 层级 | 现有权威身份 | 防止的事故 | 合法新增规则 |
|---|---|---|---|
| 请求精确重放 | 稳定 request/trigger reference + 内容指纹 | 双击、HTTP/CLI 重试 | 同引用同内容返回已有结果 |
| 同引用异内容 | reference + payload fingerprint | 旧引用覆盖新内容 | 明确冲突，拒绝写入 |
| Automation Run | `logical_run_key` | 同一计划窗口重复 Run | 正常窗口唯一；人工补跑可建新 Run 并链接原 Run |
| Task | `dedupe_key` | 重复业务任务 | 同业务事实不重复；人工新意图必须有新来源 |
| Review/Outbox | 现有稳定业务键/唯一约束 | 重复复核和通知 | 按现有原子服务处理 |
| Batch/Operation | `batch_id`、`operation_id`、`write_identity_key` | 重复发布平台写 | 同授权写意图唯一 |
| Attempt | `execution_attempt_id` | 重复领取/回执串用 | 仅按既有状态机创建合法新 attempt |
| RECONCILE | 既有 Operation + 唯一 reconcile attempt | UNKNOWN 后第二条恢复链 | 只允许现有唯一只读对账 |
| Importer | result/receipt/attempt 绑定 | 重复导入或错误结果 | 精确重放幂等；异内容冲突 |

跨入口一致性测试断言：

- 使用同一正式 Service；
- 业务意图相同时不重复创建权威事实；
- 来源审计保持各自真实值；
- 不要求 Web、CLI、Scheduler 的来源字段相同；
- 不要求所有层使用同一个幂等键。

## 11. 调度优先级与 UI 通道

固定业务顺序：

```text
Incident 人工复核任务
→ SYSTEM_EMERGENCY 自动紧急任务
→ 普通业务任务
→ 普通 UI 只读扫描
```

另有恢复门禁：任何已有 UNKNOWN/唯一 RECONCILE 均阻断同一平台/商品的不安全新写。

约束：

- 显式 task ID 不能绕过更高优先级 lane；
- Web、CLI、Scheduler 复用同一选择器和发布事务；
- 人工任务在副作用提交边界前可使自动紧急任务失效；
- Scheduler 和人工补跑不能同时占用同一平台 UI 通道；
- 只读扫描必须服从写任务和恢复链；
- 价格回升本身不撤销已形成的 S4 授权，但最终 fence 仍检查 Review、人工任务、写锁、UNKNOWN、身份、开关和授权有效性。

## 12. 结构化结果投影

统一返回 `ApplicationResult`，但不新增持久化生命周期。

```text
result_code:
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

这些值只是非持久化展示/适配器投影。每个结果同时返回：

- 底层真实领域状态；
- 底层错误码；
- 入口和业务意图引用；
- 关联 Run/Task/Review/Batch/Operation/Attempt IDs；
- 是否发生平台副作用；
- 是否允许安全重试；
- 下一步处理建议。

不得把 `ApplicationResult.result_code` 写成第二套 Task、Run、Operation 或 Attempt 状态机。

## 13. 事务与恢复边界

- Task/Review/Token/Outbox/Incident Event 继续由既有原子服务创建；
- Scheduler claim、lease、Run Event 继续由 Automation Repository/Service 管理；
- v4/v5 的 gate、Batch、Operation、Attempt、写锁和 Task running 投影继续在既有发布事务中完成；
- Importer 继续原子提交 receipt、operation/attempt、Task、Lock、平台事实和 Incident 投影；
- 入口层不得在 Importer 之外“补写成功”；
- 调用方超时只能精确重放，不得推测副作用状态；
- UNKNOWN 后禁止第二次不安全写，只允许唯一 RECONCILE。

## 14. 退役与保留清单

### 14.1 保留为正式薄适配器

- `scripts/run_automation_service.py`；
- `scripts/run_shadowbot_queue_services.py`；
- `scripts/evaluate_business_rules.py`；
- `scripts/run_shadowbot_listing_sync.py`；
- `scripts/run_shadowbot_commit_batch.py` 的显式 task ID 兼容入口；
- 主 CLI 的 Runtime 查询、复核和受控 APPLY 命令。

### 14.2 受控运维/迁移

- `scripts/reconcile_shadowbot_listing_skus.py`；
- `scripts/repair_shadowbot_expired_attempt.py`；
- 部署同步、hash 验证、备份和健康检查脚本。

### 14.3 归档或退役候选

- `scripts/run_shadowbot_executor.py import-result`：正式 Importer 覆盖后退役；
- 旧 Excel `resolve-manual-task` 和 Web `/manual-intervention` 正式处理路径；
- `build_task11_*`、`run_shadowbot_e2e_local_demo.py` 等阶段性脚本。

删除前必须完成 CI、文档、Runbook、证据复算和运维引用审计。

## 15. 阶段拆分

### 15.1 13.5-7A：本 PR

交付：

- 本文真实入口清单；
- 五类复用矩阵；
- 调用图；
- 账号/Runtime DB 约束；
- 来源、模式、幂等和结果合同；
- 退役清单；
- 测试与开工门禁。

零业务代码、零真实平台副作用。

### 15.2 13.5-7B：统一 Application Service

只有 7A 审查通过后才可开始。

交付：

- 最多一组轻量 Application Service 外观；
- 进程装配层绑定 Repository、平台和当前账号；
- 薄 Web/CLI/Scheduler/manual-rerun adapters；
- 入口能力白名单；
- 业务意图/调用来源分离；
- 分层幂等复用；
- 统一结果投影；
- 小型集成和并发测试。

### 15.3 13.5-7C：旧入口退役

交付：

- 删除重复业务判断；
- 兼容入口变薄或归档；
- 更新 Runbook 和启动脚本；
- 重启、精确重放和人工补跑恢复测试；
- 系统冒烟和 Linux/Windows CI。

## 16. 复杂度预算

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

不增加这些结构时，当前事故由现有账本、入口能力白名单、进程装配约束和人工恢复解决。
若实现要求突破预算，必须停止编码并单独评审。

## 17. 第一轮 P1 阻塞清单

1. Web、CLI、Scheduler 各自实现业务判断；
2. 任一入口直接写 Task/Review/Run 或队列 JSON；
3. Web subprocess 调 CLI；
4. READ_ONLY/DIAGNOSTIC 获得 APPLY/COMMIT；
5. COMMIT 接受未授权、过期或错误平台 Task；
6. RECONCILE 接受非 UNKNOWN Operation 或执行第二次写；
7. 人工补跑伪装成 Scheduler；
8. 外部参数伪造来源、审批或策略字段；
9. 请求可自由选择 Runtime DB 或账号；
10. 一个统一幂等键覆盖 Run/Task/Operation/Attempt 的不同语义；
11. 重复点击、重试或重启创建重复权威事实；
12. Scheduler 与人工补跑同时占用同一 UI 通道；
13. 人工任务、SYSTEM_EMERGENCY、普通任务和扫描优先级被绕过；
14. UNKNOWN 被普通补跑或新写覆盖；
15. 旧脚本长期保留完整业务逻辑；
16. 适配器吞掉业务错误并记为成功；
17. 删除旧入口破坏运维、恢复或证据链；
18. 结构化结果变成第二套持久化状态机；
19. 第二队列、第二 Worker、第二 Importer 或第二写链；
20. `automatic_emergency_offline` 被提前开启。

后续复审原则上只验证这些问题及修复直接引入的真实回归。

## 18. 测试矩阵

### 18.1 7A

- Markdown/链接检查；
- 对照真实代码核对入口、函数和分类；
- 确认 PR diff 无业务代码、Schema 和生产开关变更。

### 18.2 7B/7C 模块与集成

- Web/CLI/Scheduler 使用同一 Service；
- 业务意图相同、来源不同的跨入口测试；
- capability → allowed modes 全矩阵；
- Web 双击、HTTP 重试、CLI 重跑、Scheduler 重启；
- 同引用同内容精确重放；
- 同引用异内容冲突；
- Automation `logical_run_key`、Task `dedupe_key`、Operation/Attempt 分层断言；
- 人工补跑新 Run/来源链接；
- Review/Outbox 原子回滚；
- UI lane 优先级和发布前重验；
- UNKNOWN → 唯一 RECONCILE；
- 错误账号和请求级 Runtime DB 注入拒绝；
- Importer 精确重放和异内容冲突；
- 旧入口退役引用审计。

### 18.3 Ready for review

- 完整 pytest；
- 系统冒烟；
- Linux Core；
- Windows Core；
- `git diff --check`；
- 工作区清洁检查；
- 修改配置、日志、通知或凭据读取时执行 Secret Scan。

若 7B/7C 未修改 v4/v5 发布、写锁、Worker 请求或 Importer，不重复真实平台写验收；若修改上述边界，必须重新评估最小受控实机验收。

## 19. 13.5-7B 开工门禁

必须同时满足：

- PR #29 仍为 Draft 并完成设计复审；
- 5 个 P1 和 2 个 P2 已由审查方确认关闭；
- 本文入口盘点与代码一致；
- 账号和 Runtime DB 约束无 Schema 承诺；
- 分层幂等矩阵通过；
- capability/mode 白名单通过；
- 业务意图与调用来源分离通过；
- 退役清单和复杂度预算通过；
- 未修改生产开关或真实平台链路。

计划文档合并只授权开始 7B 代码工作，不授权真实平台动作、结束 Draft 或合并后续实现。

## 20. 后续边界

- 13.5-8 才进行 `app/webapp/`、路由、模板、Presenter 和静态资源拆分；
- 13.5-9 才进行八个运营一级入口、信息层级和移动端体验重写；
- 13.5-10 才进行完整交易日连续观察和任务 14 交接；
- 第二平台和真正多账号支持分别单独评审。

---

Refs #20
