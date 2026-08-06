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
8. Operation 状态转换和人工恢复资格门禁；
9. 三层优先级和唯一 RECONCILE；
10. 7B/7C 开工、验证和停止条件。

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
  人工恢复覆盖 RUNNING/VERIFIED、第二 Importer 或第二写链
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

只读路由 `/dashboard`、`/tasks`、`/notifications`、`/system`、`/health` 分别保持 READ_ONLY/DIAGNOSTIC，不得夹带写分支。

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

## 9. 主 CLI、Automation、Queue 与平台入口

### 9.1 主 CLI

| 子命令 | Capability / mode | 结论 |
|---|---|---|
| `templates` | `WORKBOOK_TEMPLATE_MAINTENANCE` / maintenance | 管理员工具 |
| `validate`、`import-data` | `SOURCE_VALIDATE` / diagnostic | 保留；`import-data` 不等于正式 Importer |
| `preview-tasks` | `TASK_PREVIEW` / DRY_RUN | 统一 Service |
| `generate-tasks` | legacy export / maintenance | 兼容，不进 Runtime 主线 |
| `mock-ai-decision` | mock preview / DRY_RUN | 测试 |
| `simulate-execution` | legacy simulation / maintenance | 归档候选 |
| `list-manual-tasks` | legacy read / READ_ONLY | 只读兼容 |
| `resolve-manual-task` | denied | 保持硬失败 |
| `init-runtime-db` | runtime maintenance | 管理员工具 |
| `health` / `check-runtime-health` | diagnostic | 保留 |
| `generate-runtime-tasks` | `TASK_APPLY` / APPLY | 薄 CLI |
| Runtime/Review list commands | READ_ONLY | 保留 |
| `resolve-review-task` | `REVIEW_APPLY` / APPLY | 薄适配 |
| `expire-review-tasks` | DRY_RUN 或显式 APPLY | 参数化复用 |
| `notification-worker` | internal delivery | 保持独立 Worker |
| `serve-web` | bootstrap | 7B 在此绑定唯一 Web Repository |

### 9.2 Automation Service

- bootstrap、window materialization/claim、settlement、order read-only、Incident、host recovery 均复用现有 Service；
- 当前继续声明 `platform_write_handlers_registered=false`；
- 13.5-7 不得变成“扫描全部 pending Task 后自动 COMMIT”。

### 9.3 Queue Service

`run_shadowbot_queue_services.py` 是正式长驻 Composition Root：

```text
login verification monitor
→ v2/v4/v5 Result Importer
→ Queue Watchdog
→ overdue review reminders
→ Notification Outbox watchdog/delivery
```

- Queue Service 是 v2/v4/v5 正式 Importer/Watchdog 主线；
- ORDER_SCAN 继续由 Automation observation importer 处理；
- Watchdog 只能生成安全恢复或唯一 RECONCILE；
- 普通 Web/Scheduler/兼容 CLI 不得为同一合同建立第二 Importer。

### 9.4 v4 commit batch

- `prepare` → `AUTHORIZED_WRITE_PREPARE / APPLY`；
- `publish` → `AUTHORIZED_WRITE_COMMIT / COMMIT`；
- `production-run` → composite COMMIT，但内部仍保持 prepare→publish 边界；
- `import-result` → legacy internal/admin import，7C 引用审计后由 Queue Service 取代。

### 9.5 legacy generic Executor

`run_shadowbot_executor.py start` 当前真实 accepted modes：

```text
READ_ONLY
COMMIT
RECONCILE
```

`COMMIT` 只是 argparse 默认值，不是当前唯一能力。

7B：

- 新 Application Service 永远不调用 generic `start`；
- 新 Web/Scheduler/CLI 不透传用户 `execution_mode`；
- 正式 READ_ONLY 只走 listing sync/Automation read-only；
- 正式 COMMIT 只走 v4/v5 已授权发布；
- 正式 RECONCILE 只走 existing UNKNOWN Operation → 唯一 reconcile service；
- 不新增 generic executor 调用方。

7C 完成 call-site、Runbook、部署和测试引用审计后退役或隔离为测试工具。

## 10. Capability 绑定硬门禁

高风险绑定：

| Entry | Target capability | Allowed mode |
|---|---|---|
| task generator preview | `TASK_PREVIEW` | `{DRY_RUN}` |
| task generator persist | `TASK_APPLY` | `{APPLY}` |
| execution logs query | `EXECUTION_LOG_QUERY` | `{READ_ONLY}` |
| execution logs reconcile | `UNKNOWN_RECONCILE` | `{RECONCILE}` |
| execution logs manual handled | `MANUAL_OPERATION_RECOVERY` | `{APPLY}` admin only |
| hidden `save_listing_status` | `DEBUG_LISTING_STATUS_WRITE` | **production none / denied** |
| listing sync | `LISTING_SYNC_READ_ONLY` | `{READ_ONLY}` |
| commit prepare | `AUTHORIZED_WRITE_PREPARE` | `{APPLY}` |
| commit publish | `AUTHORIZED_WRITE_COMMIT` | `{COMMIT}` |
| v5 listing publish | `LISTING_ACTION_COMMIT` | `{COMMIT}` |
| Queue Service Importer | `RESULT_IMPORT` | internal only |
| Queue Watchdog | `QUEUE_RECOVERY` | internal only |
| generic executor start | `LEGACY_GENERIC_EXECUTION` | **不进入新业务白名单** |

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

本阶段只允许已有业务证据支持的两种来源状态：

```text
FAILED
NEEDS_RECONCILIATION
```

不为“以后可能有用”扩大白名单。

明确禁止：

```text
PENDING
RUNNING
VERIFIED
NOT_APPLIED
RETRY_AUTHORIZED
MANUAL_REVIEW
```

`MANUAL_HANDLED` 不再次执行 transition，只进入幂等重放判定。

若未来确需从其他状态人工接管，必须提供具体业务事故和单独评审，不在 7B 顺手放宽。

### 14.3 并发与资格门禁

人工确认必须在同一数据库事务/CAS 中满足全部条件：

1. Operation 存在且状态在 `{FAILED, NEEDS_RECONCILIATION}`；
2. `lock_owner` 为空；
3. 该 Operation 无 `STARTING`/`RUNNING` 活动 Attempt；
4. 无活动 RECONCILE attempt；
5. 无 ACTIVE retry authorization；
6. 若来源状态为 `NEEDS_RECONCILIATION`，必须提交人工平台核验的稳定 `evidence_type + evidence_hash`；
7. 只有全部条件成立才更新 `MANUAL_HANDLED`；不得先清锁再判断。

非法状态或竞态统一返回 conflict/blocked，Operation、Attempt、Lock、Task 和执行日志均不得被修改。

### 14.4 幂等与异内容冲突

canonical recovery identity：

```text
operation_id
+ action=confirm_manual_handled
+ reason_code
+ evidence_type
+ evidence_hash
```

`actor`、free-text `note`、`requested_at` 仅审计。

- 首次合法确认：原子 transition + 一条成功审计 log；
- 已是 `MANUAL_HANDLED` 且 canonical recovery identity 相同：返回 `ALREADY_HANDLED`，不重复写成功 log；
- 已是 `MANUAL_HANDLED` 但 canonical identity 不同：409 conflict；
- 不新增表/字段；必要的 recovery identity/evidence 可写入现有 execution log `raw_output`。

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

允许投影：`CREATED / ALREADY_EXISTS / ALREADY_RUNNING / DEFERRED_BY_HIGHER_PRIORITY / BLOCKED_BY_REVIEW / BLOCKED_BY_UNKNOWN / BLOCKED_BY_UI_CHANNEL / BLOCKED_BY_IDENTITY / READ_ONLY_COMPLETED / DRY_RUN_COMPLETED / FAILED_BEFORE_SIDE_EFFECT / START_UNKNOWN / NEEDS_RECONCILIATION / COMPLETED`。

它只是非持久化适配器投影，必须同时返回底层领域状态、错误码、canonical intent、invocation audit、关联 Run/Task/Review/Batch/Operation/Attempt IDs、副作用和安全重试建议。不得形成第二生命周期。

## 18. 事务与恢复边界

- Task/Review/Token/Outbox/Incident Event 继续走现有原子服务；
- Automation claim/lease/Run Event 继续走 Automation Repository；
- PREPARED Batch/Operation 属于 APPLY，不等于平台副作用；
- v4/v5 gate、Batch、Operation、Attempt、写锁和 Task running 继续走既有事务；
- Queue Service Importer 是 v2/v4/v5 结果主线；
- ORDER_SCAN 继续由 Automation importer；
- Web 不得在 Importer 之外“补写平台成功”；
- `save_listing_status` 不得成为平台观察替代入口；
- UNKNOWN 后只允许唯一 RECONCILE；
- manual recovery 必须按 §14 CAS 门禁执行。

## 19. 保留、迁移与退役

保留：Automation Service、Queue Service、business rules、listing sync、commit batch prepare/publish、主 CLI Runtime 查询/复核/APPLY、正式 Web query/review/reconcile 能力。

7B 迁移：

- Web Runtime DB 请求级权威 → fixed Composition Root；
- legacy `/` preview/persist → 同一 Application Service；
- execution-log manual recovery → §14 门禁；
- `save_listing_status` → production deny；
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

### 20.2 Manual operation recovery

必须覆盖：

- `FAILED` 合法人工确认；
- `NEEDS_RECONCILIATION` + 有效人工证据合法；
- `NEEDS_RECONCILIATION` 无证据拒绝；
- `PENDING/RUNNING/VERIFIED/NOT_APPLIED/RETRY_AUTHORIZED/MANUAL_REVIEW` 全拒绝；
- 活动 STARTING/RUNNING Attempt 拒绝；
- 活动 RECONCILE 拒绝；
- 非空 lock 拒绝；
- ACTIVE retry authorization 拒绝；
- 同 canonical recovery 重放幂等且不重复 log；
- 异 canonical recovery 返回 conflict；
- 非法请求前后 Operation/Attempt/Task/Lock/日志零变化。

### 20.3 Runtime DB

覆盖 login/session/CSRF/PRG、同路径兼容、异路径拒绝、伪造 Session DB、各运营页同 Repository、旧书签不静默切库。

### 20.4 幂等、恢复与优先级

覆盖 Web 双击、CLI 重跑、Scheduler 重启、同引用精确重放、异内容冲突、18:00/20:00 跨界、PREPARED/COMMIT 分离、Queue Importer 重放、Watchdog、Review/Outbox 原子回滚、完整 UI priority、人工任务压制 emergency、UNKNOWN→唯一 RECONCILE。

### 20.5 Ready

- 完整 pytest；
- 系统冒烟；
- Linux Core；
- Windows Core；
- `git diff --check`；
- 工作区清洁；
- 修改凭据/日志/通知/配置时 Secret Scan。

当前 Head 必须有自己的 CI 结果，不能沿用旧 Head。

## 21. Final Audit / Freeze Gate

7A 最终复审只检查以下四组：

1. **Web mutation inventory**：所有生产/legacy/debug POST 写入口均已分类，尤其 `save_listing_status`；
2. **Operation transition inventory**：所有 Operation mutation 使用既有权威方法，manual recovery 满足 §14；
3. **Control-plane contracts**：capability/mode、Runtime DB、canonical identity、Importer、priority、RECONCILE 均与现有代码一致；
4. **Validation**：当前 Head CI + diff check，通过且无生产代码/Schema/开关变化。

若复审发现其他非 §8.2 安全例外问题，记录为后续技术债，不再重开 7A。

## 22. 13.5-7B 开工门禁

必须同时满足：

- PR #29 保持 Draft 并完成 7A 最终复审；
- 第四轮 2 个 P1 已确认关闭：hidden listing status write + manual recovery eligibility；
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
