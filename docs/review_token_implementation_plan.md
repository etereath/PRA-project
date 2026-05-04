# Review Token Implementation Progress

## 1. 文档目的

本文档用于同步 `review_token` 相关能力在当前仓库中的真实落地进度，避免“计划状态”和“已实现状态”混写。

当前范围以“运行态闭环可用”为目标，重点覆盖：

- `review_tokens` 数据模型与 schema 迁移
- `ReviewTokenService` 的创建、校验、使用记录、撤销
- Mobile Review MVP（详情访问 + 复核提交）
- 与运行态复核服务、通知主流程的衔接边界
- Feishu Webhook 通知 sender 的 MVP 状态

当前仍保持以下边界不变：

- 不接除飞书自定义机器人 Webhook 以外的真实通知渠道
- 不接真实平台 / RPA
- 不引入 AI Agent 自动复核
- 不做完整权限系统

---

## 2. 当前已实现（已落地）

### 2.1 SQLite schema 已升级到 v2

- `runtime_schema_migrations` 作为迁移历史表使用。
- 新库初始化会记录迁移历史到 `schema_version=2`（包含 `v1 -> v2` 演进记录）。
- 旧库（已在 v1）执行 `init_schema()` 后可增量迁移到 v2，不破坏原数据。
- 已新增 `review_tokens` 表和相关索引：
  - `token_hash` 唯一约束
  - `review_task_id` 索引
  - `expires_at` 索引

### 2.2 `review_tokens` 表结构已落地

已实现字段：

- `token_id`
- `review_task_id`
- `token_hash`
- `token_subject`
- `allowed_actions`
- `expires_at`
- `used_at`
- `revoked_at`
- `created_at`
- `created_by`
- `last_used_at`
- `note`（允许为空）

核心约束：

- 数据库只保存 `token_hash`，不保存 raw token
- 一个 token 只绑定一个 `review_task`
- 一个 `review_task` 可以创建多个 token（用于轮转/重发）

### 2.3 `ReviewTokenService` 核心能力已实现

已实现接口：

- `create_token(...)`
- `validate_token(review_task_id, raw_token, action=None)`
- `record_detail_access(...)`
- `record_resolve_usage(...)`
- `revoke_token(...)`
- `revoke_tokens_for_review_task(...)`
- `build_mobile_review_url(...)`

已实现规则：

- 仅允许 `review_status=pending` 的复核任务创建 token
- `REVIEW_TOKEN_SECRET` 缺失时拒绝创建
- `token_hash = HMAC-SHA256(raw_token, REVIEW_TOKEN_SECRET)`
- 默认过期时间：
  - `expires_at = min(review_task.required_by, now + 24h)`
  - `required_by` 为空时：`expires_at = now + 24h`
- `action` 校验支持两种模式：
  - `action=None`：详情访问校验
  - `action=...`：复核提交校验

### 2.4 Mobile Review MVP 已接入 Web

已实现路由：

- `GET /mobile/review/{review_task_id}?token=...`
- `POST /mobile/review/{review_task_id}/resolve`

已实现行为：

- GET：
  - 调用 token 校验（详情模式）
  - 成功后仅更新 `last_used_at`
  - 返回必要摘要，不展示完整敏感 payload
- POST：
  - 校验 token + action
  - 调用 `ReviewTaskService.resolve_review_task(...)`
  - `actor/resolved_by = token_subject`
  - `actor_source = mobile_review_token`
  - 成功后写入 `used_at`，并采用 `303 See Other`（PRG）

### 2.5 安全与幂等关键点已落地

- `used_at` 只在 POST resolve 成功后写入；GET 不会写入 `used_at`。
- 重复提交防护：
  - `review_task` 非 `pending` 拒绝
  - `token.used_at` 非空拒绝
- token 失效场景（无效/过期/撤销/已使用/review 非 pending）统一对手机页返回：
  - `链接已失效或无权访问该复核任务`
- `resolution_payload_json` 最小校验已实现：
  - 允许为空
  - 非空必须是 JSON object
  - 最大 4KB
  - 页面输出进行转义

### 2.6 通知主流程与飞书 Webhook sender 已实现（MVP）

- `NotificationSenderFactory` 当前支持：
  - `channel=mock` -> `MockNotificationSender`
  - `channel=feishu` -> `FeishuWebhookNotificationSender`
  - 未知 channel -> failed sender，并写入可读错误摘要
- `FeishuWebhookNotificationSender` 使用飞书自定义机器人 Webhook 发送真实群通知。
- 飞书 sender 通过环境变量配置：
  - `FEISHU_WEBHOOK_URL`
  - `FEISHU_WEBHOOK_SECRET`（可选，启用签名）
  - `DEFAULT_NOTIFICATION_CHANNEL=feishu`
  - `MOBILE_REVIEW_BASE_URL`
  - `REVIEW_TOKEN_SECRET`
- 飞书通知创建时会生成 `mobile_review_url`，完整 raw token URL 只进入实际飞书发送 payload。
- `notification_logs.message` 只保存简短摘要和 `mobile_review_url_created=true/false`，不会持久化完整 `token=` 链接。
- 如果 `REVIEW_TOKEN_SECRET` 缺失导致 mobile review URL 创建失败，本次飞书通知会记录 `send_status=failed`，不会发送缺少处理链接的半成品通知。
- MVP 使用飞书 `text` 消息；后续可以升级为 `post` 富文本消息，以便更好展示字段和手机复核链接。
- 当前不实现限流队列或复杂重试；发送失败记录 `failed`，消息体保持简短，避免超过 webhook 请求体限制。

---

## 3. 当前测试覆盖（已通过）

已覆盖：

- schema v1 -> v2 迁移
- 新库初始化到最新 schema
- token 不保存明文
- token 创建与校验成功/失败场景
- `used_at` 防重复提交
- `revoked_at` 失效
- `review_task` 非 pending 时 token 无效
- `REVIEW_TOKEN_SECRET` 缺失报错
- Mobile GET 详情访问仅更新 `last_used_at`
- Mobile POST `approved/rejected/adjusted/cancelled` 提交
- `expired` 不允许由 mobile token 提交
- Web 已处理后 mobile 再提交失败
- 审计链路 `actor_source=mobile_review_token`
- 飞书 sender 选择、缺少 webhook URL 失败、固定 timestamp/secret 签名
- 飞书发送成功、飞书业务失败、HTTP 异常失败
- 飞书主流程外发 payload 包含完整 `mobile_review_url`
- `notification_logs.message` 不持久化完整 raw token URL
- token URL 创建失败时飞书通知记录 failed 且不发送

---

## 4. 当前尚未完成（后续事项）

以下仍为后续增强，不属于当前已落地范围：

- 完整手机端 UI/UX（当前为 MVP 表单页）
- 飞书 `post` 富文本消息、交互卡片、按钮审批、回调接口、OAuth、应用机器人
- 企业微信 / Bark 等其他真实通知渠道
- 通知限流队列、复杂重试队列、长期外部响应审计 schema
- 短链/一次性 code 机制（降低 query token 暴露风险）
- 细粒度 `review_type -> allowed_actions` 动作矩阵
- token 与通知的更完整解耦编排（例如专用 token 发行策略与轮转策略）
- 完整权限与身份系统

---

## 5. 下一步建议（按当前进度）

建议下一阶段从“可用 MVP”推进到“可运营增强”，顺序如下：

1. 完善 mobile 页面体验（更清晰的摘要、错误页、状态页）。
2. 细化 `review_type` 动作矩阵，减少误操作空间。
3. 将飞书通知从 `text` 消息升级为 `post` 富文本消息，改善手机端链接展示。
4. 引入短链/一次性 code 或 POST 交换流程，降低 token 泄露面。
5. 结合运营策略，增加 token 轮转、批量撤销、通知限流和重试治理策略。

---

## 6. 当前阶段暂不实现事项

- 不接真实平台 / RPA
- 不引入 AI Agent 自动复核
- 不做完整权限系统
- 不迁移 Excel 主数据
