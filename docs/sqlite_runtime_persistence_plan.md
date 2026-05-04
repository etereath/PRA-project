# SQLite 运行态持久化开发计划

## 1. 目标

本阶段目标是让系统从“Excel 原型”进入“可运营任务系统”。

Excel 继续作为商品、规则、预测等输入来源；SQLite 只承接运行态数据，包括任务、执行日志、人工复核、通知记录和状态历史。

当前阶段仍然不接真实销售平台、不接真实 RPA、不接真实通知渠道、不接手机端，也不做完整权限系统。

## 2. 架构边界

- 默认数据库路径：`data/runtime/pra_runtime.sqlite3`
- 数据库访问：使用 Python 标准库 `sqlite3`
- 建表 SQL：集中放在运行态 repository 层
- 业务写入：必须通过 service 层完成
- Excel 主数据：本阶段不迁移

写操作边界：

- CLI 和 Web 不直接执行业务写入 SQL。
- repository 层只负责数据读写。
- service 层负责状态流转、幂等去重和业务约束。
- `ReviewTaskService` 只负责复核任务本身。
- 复核处理后如果需要影响源任务，必须调用 `RuntimeTaskService` 完成任务状态变更和历史写入。

## 3. 运行态数据表

### 3.1 `runtime_schema_migrations`

用于记录运行态数据库 schema 版本。

| 字段 | 中文名 | 说明 |
| --- | --- | --- |
| `schema_version` | 架构版本 | 当前至少写入 `1` |
| `applied_at` | 应用时间 | 初始化或迁移应用时间 |
| `note` | 说明 | 迁移说明 |

### 3.2 `tasks`

运行态任务表，是平台执行任务、运营提醒任务和人工复核前置任务的统一入口。

| 字段 | 中文名 | 说明 |
| --- | --- | --- |
| `task_id` | 任务 ID | 唯一任务标识 |
| `trade_date` | 交易日 | 核心业务维度 |
| `scope_type` | 作用范围类型 | `global / forecast_group / sku / platform / task` |
| `scope_key` | 作用范围对象 | 具体作用对象 |
| `dedupe_key` | 幂等去重键 | 用于避免重复生成相同未终态任务 |
| `internal_sku` | 内部 SKU | 非平台动作任务允许为空 |
| `platform_name` | 平台名称 | 非平台动作任务允许为空 |
| `action_type` | 任务类型 | 如 `update_price`、`capacity_warning` |
| `priority` | 优先级 | 数字越小越先处理 |
| `task_status` | 任务状态 | 见状态流转规则 |
| `created_at` | 创建时间 | 任务创建时间 |
| `scheduled_at` | 计划执行时间 | 可为空 |
| `expires_at` | 过期时间 | 可为空 |
| `target_price` | 目标价格 | 改价任务使用，其他任务允许为空 |
| `target_status` | 目标状态 | 上下架任务使用，其他任务允许为空 |
| `pricing_source` | 定价来源 | 价格相关任务使用 |
| `decision_trace_json` | 决策追踪 JSON | 保存任务生成原因 |
| `result_message` | 结果信息 | 执行结果或人工说明 |
| `required_by` | 处理截止时间 | 人工处理或运营提醒使用 |
| `updated_at` | 更新时间 | 最近更新时间 |

约束：

- `task_id` 唯一。
- `dedupe_key` 使用 partial unique index，只约束未终态任务。
- 终态任务不阻止未来重新生成同类任务。

### 3.3 `review_tasks`

人工复核任务表，支持全局级、预测组级、SKU 级和平台级复核。

| 字段 | 中文名 | 说明 |
| --- | --- | --- |
| `review_task_id` | 复核任务 ID | 唯一复核任务标识 |
| `trade_date` | 交易日 | 复核对应交易日 |
| `scope_type` | 作用范围类型 | `global / forecast_group / sku / platform / task` |
| `scope_key` | 作用范围对象 | 具体复核对象 |
| `dedupe_key` | 幂等去重键 | 避免重复生成相同待复核任务 |
| `source_task_id` | 来源任务 ID | 可为空 |
| `review_type` | 复核类型 | 如 `labor_required`、`below_break_even_review` |
| `review_status` | 复核状态 | 见复核状态流转规则 |
| `internal_sku` | 内部 SKU | 全局复核允许为空 |
| `platform_name` | 平台名称 | 非平台复核允许为空 |
| `reason` | 原因 | 复核触发原因 |
| `review_payload_json` | 复核上下文 JSON | 给人工查看的上下文 |
| `resolution_payload_json` | 复核结果 JSON | 处理后的结构化结果 |
| `required_by` | 处理截止时间 | 可为空 |
| `created_at` | 创建时间 | 复核任务创建时间 |
| `updated_at` | 更新时间 | 最近更新时间 |
| `resolved_by` | 处理人 | 可为空 |
| `resolved_at` | 处理时间 | 可为空 |
| `resolution_note` | 处理备注 | 可为空 |

### 3.4 `execution_logs`

执行日志表，沿用现有执行日志对象并增加 `created_at`。

### 3.5 `notification_logs`

通知记录表，只保存通知记录，不接真实通知渠道。

| 字段 | 中文名 | 说明 |
| --- | --- | --- |
| `notification_id` | 通知 ID | 唯一通知记录 |
| `related_task_id` | 关联任务 ID | 可为空 |
| `related_review_task_id` | 关联复核任务 ID | 可为空 |
| `recipient_type` | 接收人类型 | 如 `operator`、`role`、`group` |
| `recipient` | 接收人 | 接收对象 |
| `channel` | 通知渠道 | 只表示渠道，不承担接收人含义 |
| `sent_at` | 发送时间 | 可为空 |
| `send_status` | 发送状态 | 如 `pending`、`success`、`failed` |
| `dedupe_key` | 幂等去重键 | 避免重复通知刷屏 |
| `message` | 通知内容 | 通知正文 |
| `error_message` | 错误信息 | 发送失败原因 |
| `created_at` | 创建时间 | 记录创建时间 |

### 3.6 `task_status_history`

任务状态历史表。

| 字段 | 中文名 | 说明 |
| --- | --- | --- |
| `history_id` | 历史 ID | 唯一历史记录 |
| `task_id` | 任务 ID | 关联任务 |
| `from_status` | 变更前状态 | 创建时可为空 |
| `to_status` | 变更后状态 | 目标状态 |
| `changed_by` | 变更人 | 操作人或系统组件 |
| `changed_at` | 变更时间 | 状态变化时间 |
| `reason` | 原因 | 状态变化原因 |
| `metadata_json` | 扩展上下文 JSON | 状态变化额外上下文 |

## 4. 状态流转

任务状态：

- `pending -> running / manual_review / skipped / cancelled / expired`
- `running -> success / failed / manual_review`
- `failed -> pending / cancelled`
- `manual_review -> pending / skipped / cancelled / expired`
- `success / skipped / cancelled / expired` 默认终态，未来如需 reopen 必须显式新增规则。

复核状态：

- `pending -> approved / rejected / adjusted / expired / cancelled`
- 其他复核状态默认终态。

## 5. 幂等策略

- `tasks.dedupe_key` 通过 partial unique index 只约束未终态任务。
- `review_tasks.dedupe_key` 用于避免重复生成相同待复核任务。
- `notification_logs.dedupe_key` 用于避免重复通知刷屏。
- `task_id` 唯一不足以解决重复生成问题，因为多次生成任务时 `task_id` 可能不同。

## 6. CLI 和 Web 优先级

第一阶段优先 CLI：

- `init-runtime-db`
- `generate-runtime-tasks`
- `list-tasks`
- `show-task-history`
- `list-review-tasks`
- `resolve-review-task`

Web 第一阶段只做最小只读查看：

- 任务列表
- 人工复核列表
- 通知记录列表
- 状态历史入口后续再增强

## 7. 暂不实现事项

- 不迁移 Excel 主数据。
- 不接真实通知。
- 不接手机端。
- 不做完整权限系统。
- 不接真实平台。
- 不接真实 RPA。
