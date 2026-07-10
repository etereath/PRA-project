# SQLite 运行态持久化进度说明

## 1. 文档目的

本文档用于说明 SQLite 运行态持久化这一阶段在当前仓库中的真实落地情况。

它不再只是“计划文档”，而是：

- 当前已实现能力说明
- 当前未完成事项说明
- 下一步建议说明

当前结论：

- SQLite 运行态持久化已经进入“第一版已落地实现”阶段
- 不是纯计划状态
- 也还没有到“完整运营后台”阶段

可以把它理解为：

`运行态数据库骨架 + CLI 闭环 + 运行态人工复核闭环 MVP 已完成`

---

## 2. 已实现部分

### 2.1 运行态数据库已落地

当前代码已经新增 SQLite 运行态持久化层，默认数据库路径为：

`data/runtime/pra_runtime.sqlite3`

实现方式：

- 使用 Python 标准库 `sqlite3`
- 不引入 ORM
- 不迁移 Excel 主数据

当前已实现建表与版本初始化：

- `runtime_schema_migrations`
- `tasks`
- `review_tasks`
- `execution_logs`
- `notification_logs`
- `task_status_history`
- `review_tokens`
- `script_runs`
- `script_run_items`

并且初始化时会写入迁移历史：

- `schema_version = 1`
- `schema_version = 2`
- `schema_version = 3`

当前最新 runtime schema 要求为 v3。

### 2.2 `tasks` 表已实现

当前 `tasks` 运行态表已经支持以下核心字段：

- `task_id`
- `trade_date`
- `scope_type`
- `scope_key`
- `dedupe_key`
- `internal_sku`
- `platform_name`
- `action_type`
- `priority`
- `task_status`
- `created_at`
- `scheduled_at`
- `expires_at`
- `target_price`
- `target_status`
- `pricing_source`
- `decision_trace_json`
- `result_message`
- `required_by`
- `updated_at`

当前已落实的关键约束：

- `task_id` 唯一
- `dedupe_key` 使用 partial unique index
- partial unique index 只约束未终态任务
- 已终态任务不会阻止未来重新生成同类任务

这意味着运行态任务幂等去重已经不是纸面设计，而是已进入真实实现。

### 2.3 `review_tasks` 表已实现

当前已实现独立的人工复核任务表，支持：

- `trade_date`
- `scope_type`
- `scope_key`
- `dedupe_key`
- `source_task_id`
- `review_type`
- `review_status`
- `internal_sku`
- `platform_name`
- `reason`
- `review_payload_json`
- `resolution_payload_json`
- `required_by`
- `created_at`
- `updated_at`
- `resolved_by`
- `resolved_at`
- `resolution_note`

当前已支持：

- 全局级复核任务
- 非 SKU 级复核任务
- 来源任务为空的结构预留

### 2.4 `notification_logs` 表已实现

当前通知记录表结构已经实现，支持：

- `notification_id`
- `related_task_id`
- `related_review_task_id`
- `recipient_type`
- `recipient`
- `channel`
- `sent_at`
- `send_status`
- `dedupe_key`
- `message`
- `error_message`
- `created_at`

当前已落实：

- `channel` 只表示渠道
- 接收对象通过 `recipient_type + recipient` 表达
- `dedupe_key` 可用于通知去重

### 2.5 `task_status_history` 已实现

当前状态历史表已实现，支持：

- `history_id`
- `task_id`
- `from_status`
- `to_status`
- `changed_by`
- `changed_at`
- `reason`
- `metadata_json`

当前已经通过统一状态变更服务写入历史，而不是只停留在设计层。

### 2.6 状态枚举与流转规则已落地

当前 `TaskStatus` 已调整为运行态任务使用的状态集合：

- `pending`
- `running`
- `success`
- `failed`
- `skipped`
- `manual_review`
- `cancelled`
- `expired`

当前 `ReviewTaskStatus` 也已经独立实现：

- `pending`
- `approved`
- `rejected`
- `adjusted`
- `expired`
- `cancelled`

当前已实现服务层合法流转校验：

- 任务状态流转由 `RuntimeTaskService` 约束
- 复核状态流转由 `ReviewTaskService` 约束

### 2.7 Service 边界已实现

当前已经按计划拆出运行态服务边界：

- `RuntimeTaskService`
- `ReviewTaskService`
- `NotificationLogService`
- `ExecutionRuntimeService`

当前已落实的边界规则：

- CLI 不直接写业务 SQL
- Web 不直接写业务 SQL
- repository 只负责数据读写
- service 负责状态流转、幂等和业务约束
- `ReviewTaskService` 不绕过 `RuntimeTaskService` 直接改任务状态

### 2.8 CLI 第一阶段已基本完成

当前 CLI 已实现以下运行态命令：

- `init-runtime-db`
- `generate-runtime-tasks`
- `list-tasks`
- `show-task-history`
- `list-review-tasks`
- `resolve-review-task`

这意味着“先用 CLI 跑通运行态闭环”的目标已经基本完成。

### 2.9 Web 已完成运行态人工复核闭环 MVP

当前 Web 运行态页面已经不再只是只读查看，而是具备最小可用的人工复核处理闭环，支持：

- Session 登录后进入运行态复核处理
- 查看运行态任务、人工复核任务、通知记录
- 查看 `source_task_id` 对应的 `task_status_history`
- 处理 `pending` 状态的复核任务
- 支持 `approved / rejected / adjusted / cancelled`
- 处理成功后使用 POST-Redirect-GET，避免浏览器刷新造成重复提交

当前这部分已落地的关键约束包括：

- `resolved_by` 来自 `session_user`
- Web 不直接写 `tasks`
- 复核写入统一经过 `ReviewTaskService`
- 若 `source_task_id` 不为空，只能通过 `RuntimeTaskService` 推动源任务状态
- 仅允许 `pending` 复核任务被处理，已处理任务不能重复覆盖

当前最小安全机制也已落地为：

- `RUNTIME_ADMIN_USER`，默认 `admin`
- `RUNTIME_ADMIN_PASSWORD` 仅通过环境变量或本地未跟踪配置提供
- `DEV_MODE=true` 时允许共享口令 fallback，但仅用于本地调试，不作为正式方案

### 2.10 中文字段名已同步补齐

当前运行态新增字段已经补充中文字段名映射，供以下场景使用：

- 文档说明
- Web 页面
- 人工查看

当前项目继续遵守：

- 英文字段名不变
- 中文字段名只用于展示和说明

### 2.11 自动规则评估运行记录已实现

runtime schema v3 已新增：

- `script_runs`
- `script_run_items`

用途：

- 记录自动规则 evaluator 的每次运行。
- 区分 `dry-run / apply`。
- 保存 proposal 预览、落库结果、跳过原因和错误摘要。
- 在 Web 任务中心的“脚本状态”分页展示运行记录。

当前已落地的关键约束：

- `dry-run` 可以写入 `script_runs / script_run_items`，但绝不能写入业务 `tasks / review_tasks / notification_logs`。
- `apply` 才会通过现有 service 写入业务任务、复核和通知。
- apply 前基于 `proposal.dedupe_key` 做幂等检查，已有业务结果时记录 `skipped` 和 `skip_reason`。
- evaluator 不直接写 SQLite 业务表，不直接发送飞书，不绕过运行态 service。

---

## 3. 已完成验证

当前已完成的验证包括：

- 单元测试通过
- SQLite schema 初始化测试通过
- partial unique index 幂等去重测试通过
- 状态流转与状态历史写入测试通过
- review task 处理后驱动源任务状态更新测试通过
- notification log 去重测试通过
- CLI 冒烟验证通过
- Web 运行态登录测试通过
- Web 复核处理与 PRG 重定向测试通过
- Web 重复提交拦截测试通过

当前可以确认：

- 初始化数据库可用
- 生成运行态任务可用
- 重复生成不会重复插入未终态任务
- 可列出运行态任务
- 可列出人工复核任务
- 可处理人工复核任务
- 可通过 Web 登录后处理人工复核任务
- 可查看源任务状态历史

---

## 4. 尚未完全实现的部分

### 4.1 通知系统已接入 review 主流程 MVP，但仍未形成真实渠道闭环

当前 `notification_logs` 已经不再只是“孤立记录层”。

当前已落地：

- `review_task` 新生成并进入 `pending` 时，会自动创建对应的 initial `notification_log`
- 默认路由支持环境变量配置，未配置时回退到 `role / operations / mock`
- `channel=mock` 时按“模拟发送成功”写入 `send_status=success` 与 `sent_at`
- Web 运行态页面可查看通知记录，并可在 review 详情中看到关联通知

当前仍未完成：

- 真实外部通知渠道接入
- 不同 review_type / risk_level 的复杂路由策略
- 通知重试、回执与失败恢复闭环

### 4.2 Web 还不是完整运行态后台

当前 Web 已实现最小可用的运行态人工复核闭环，但还没有做到：

- 运行态任务筛选与搜索
- 状态历史的完整可视化浏览与过滤
- 通知记录管理页
- 复核结果驱动的新任务再生成闭环
- 面向手机端的 review token 页面与接口

### 4.3 旧 Excel 人工介入链路仍然保留

当前项目里仍存在旧的 Excel 人工介入流程。

这不是错误，而是过渡状态。

当前真实情况是：

- SQLite 运行态复核链路已经有了
- 旧 Excel 人工处理链路也还在

后续需要进一步统一为“运行态优先”的单一路径。

### 4.4 执行层仍然是模拟执行

当前运行态已经能保存执行日志，但执行本身仍然是模拟执行，不是真实平台执行。

因此当前阶段依然没有实现：

- 真实平台任务消费
- 真实 RPA 执行
- 平台 API 执行闭环

### 4.5 手机端与权限系统仍只是字段预留

当前字段设计已经为后续手机端 review 页面和通知系统预留了空间，但还没有实现：

- 手机端页面
- 账号体系
- 角色权限

---

## 5. 当前阶段判断

如果按阶段划分，当前 SQLite 运行态持久化的真实进度是：

1. 基础表结构：已完成
2. 运行态服务边界：已完成
3. CLI 第一阶段闭环：已完成
4. Web 运行态人工复核闭环 MVP：已完成
5. 通知接入 review 主流程 MVP：已完成
6. 自动规则评估运行记录与任务中心脚本状态页：已完成第一版
7. 完整运行态后台化：未完成
8. 完整运营系统：未完成

因此，这一阶段不应再描述为“纯计划中”，更准确的说法是：

`SQLite 运行态持久化第一版已实现，运行态运营闭环增强第一版也已完成，当前已进入 schema v3 自动规则评估记录阶段，正在从可运行闭环向更完整运营后台扩展。`

---

## 6. 下一步建议

按当前项目进度，运行态运营闭环增强第一版已经完成，所以下一步建议只保留真正未完成的事项：

1. 进一步弱化旧 Excel 人工介入入口，例如移出主导航或仅保留内部兼容入口
2. 扩展运行态 Web 的更多筛选、搜索、批量处理和可视化能力
3. 为真实通知渠道补 sender 实现，例如企业微信、Bark、飞书
4. 基于 evaluator 框架继续规划上下架规则、冷库压力和包装产能等自动规则
5. 为 `review_token` 与手机端 review 页面保留并逐步实现稳定接口
6. 在后续阶段再推进真实平台、真实 RPA、完整权限系统

当前仍然不建议做：

- 迁移 Excel 主数据
- 接真实平台
- 接真实 RPA
- 上完整权限系统

---

## 7. 最新落地能力摘要（运行态运营闭环增强）

截至当前代码状态，以下增强已经落地：

### 7.1 旧 Excel 人工介入链路已收口到“只读兼容”

- `list-manual-tasks` 继续保留，但只用于兼容查询和历史查看
- `resolve-manual-task` 已显式弃用，默认不再执行正式处理，并返回非 0 退出码
- Web `/manual-intervention` 页面已改为只读兼容页，不再提供处理按钮
- 所有新的人工复核、运营提醒、运营确认处理统一走 SQLite `review_tasks` 和 `/runtime`

### 7.2 Web 运行态页已完成第一版高频筛选与详情增强

当前 `/runtime` 已新增最小高频筛选组合：

- tasks：`trade_date + task_status`
- review_tasks：`trade_date + review_status`
- notification_logs：`related_review_task_id + send_status`

当前 Web 还支持：

- 任务状态历史摘要展示，不再默认只显示整块原始 `metadata_json`
- 通知详情查看
- review 详情页查看关联通知

这意味着运行态 Web 已从“最小复核闭环”推进到“可运营查询增强”的第一版。

### 7.3 通知发送接口已抽象

当前通知发送链路已经从内联 `channel=mock` 语义升级为：

- `NotificationSender.send(...)`
- `MockNotificationSender`
- `ReviewNotificationService`
- `NotificationLogService`

其中：

- `MockNotificationSender` 是当前默认实现
- `send(...)` 返回结构化结果：
  - `send_status`
  - `sent_at`
  - `error_message`
  - `provider_message_id`
  - `raw_response_json`
- `MockNotificationSender` 当前返回：
  - `send_status=success`
  - `sent_at=now()`
  - `raw_response_json={"mock": true}`

这使得后续企业微信、Bark、飞书等真实通知渠道可以在不改 Web/CLI 主流程的前提下接入。

### 7.4 `expire-review-tasks` CLI 已实现

当前已新增正式 CLI：

- `expire-review-tasks`

当前行为：

- 默认 `dry-run`
- 只有 `--apply` 才实际写库
- 扫描 `review_status = pending` 且 `required_by < now()` 的 `review_tasks`
- 命中后将 `review_task -> expired`
- 若 `source_task_id` 不为空且源任务当前为 `manual_review`，则通过 `RuntimeTaskService` 将源任务同步置为 `expired`
- 若源任务不是 `manual_review`，则只过期 review_task，并在 summary 中记录

写入的 `task_status_history.metadata_json` 至少包含：

- `review_task_id`
- `review_type`
- `required_by`
- `timeout_at`
- `timeout_reason`
- `timeout_policy`

对 `labor_required / capacity_warning`，还会记录：

- `fallback_to_safe_default=true`
- `confirmed_temp_worker_count=0`
- `confirmed_packing_capacity_qty=250`
