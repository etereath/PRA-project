# Mobile Review Token Specification

## 1. 文档目的

本文档用于规划未来“手机端复核入口”的系统边界、`review_token` 机制、路由建议、安全要求和后续实现顺序。

当前项目已经完成：

- SQLite 运行态任务系统
- 运行态人工复核闭环 MVP
- `notification_logs` 接入 review 主流程 MVP
- 运行态运营闭环增强第一版
- AI Agent 接入治理规范第一版

下一阶段优先规划手机端复核入口，但当前阶段：

- 只做文档和接口规划
- 不改代码
- 不接真实通知渠道
- 不做完整移动端 UI
- 不接真实平台 / RPA
- 不引入 AI Agent 自动复核

当前定位也需要明确：

- AI Agent 的实际参与暂时后置
- 优先顺序仍是先把真实平台 / RPA 执行器链路跑通
- 手机端复核入口是对现有 `review_tasks` 的补充，而不是另起一套任务系统

---

## 2. 手机端复核的定位

### 2.1 手机端不是独立任务系统

手机端不是一个新的任务中心，也不是独立于当前 SQLite 运行态系统之外的审批模块。

它只是：

- `review_tasks` 的轻量处理入口
- 通知消息中的安全跳转入口
- 面向“移动场景快速确认/驳回/调整”的轻量化页面

它不负责：

- 直接创建运行态任务
- 直接修改 `tasks`
- 直接写 SQLite
- 绕过现有服务层做状态流转

### 2.2 手机端与现有运行态系统的关系

手机端与现有运行态系统的关系应明确为：

- 手机端可以读取指定 `review_task` 的必要摘要
- 手机端可以提交复核动作
- 所有复核处理仍必须走 `ReviewTaskService`
- 若需要推动 `source_task` 状态，仍必须通过 `RuntimeTaskService`
- 手机端不直接写 SQLite 表
- 手机端与 Web 后台只是“不同入口”，最终落到同一套运行态服务边界

---

## 3. `review_token` 的用途

### 3.1 核心用途

`review_token` 用于从通知消息安全打开指定 `review_task`，让用户可以在手机上直接进入对应复核页面，而不是依赖后台 Session 登录。

它的用途包括：

- 绑定一个具体的 `review_task_id`
- 控制该链接可执行的动作范围
- 限制有效期
- 防止越权访问其他 `review_task`
- 防止重复提交

### 3.2 必备能力

`review_token` 机制应至少支持：

- 绑定 `review_task_id`
- 限制 `allowed_actions`
- 设置 `expires_at`
- 支持 `used_at`
- 支持 `revoked_at`
- 支持 `last_used_at`
- 防止重复提交

### 3.3 不保存明文 token

数据库不应保存明文 token，只保存 `token_hash`。

建议实现方式：

- 生成随机、不可预测的原始 token
- 对原始 token 做 hash 后入库
- 手机端链接中只携带原始 token
- 服务端校验时对入参 token 做同样 hash，再与库中 `token_hash` 匹配

---

## 4. 建议数据结构：`review_tokens`

建议未来预留独立对象或表：`review_tokens`

字段建议：

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
- `note`

字段说明建议：

- `token_id`
  - token 记录主键
- `review_task_id`
  - 绑定的复核任务 ID
- `token_hash`
  - 原始 token 的 hash 值，不保存明文 token
- `token_subject`
  - token 对应的处理主体
  - 初期可先使用 `mobile_reviewer` 或 `operations`
- `allowed_actions`
  - 允许动作集合
  - 可保存 JSON 数组或逗号分隔字符串
- `expires_at`
  - token 到期时间
- `used_at`
  - 首次成功提交复核的时间
- `revoked_at`
  - token 被撤销的时间
- `created_at`
  - token 创建时间
- `created_by`
  - token 创建来源，例如 `system`、`notification_service`
- `last_used_at`
  - 最近一次访问或使用时间
- `note`
  - 备注或生成背景说明

约束建议：

- 一个 token 只能访问绑定的那个 `review_task`
- token 不得跨任务复用
- token 应默认只服务于“手机端复核链接”场景

---

## 5. token 生命周期

### 5.1 创建

当 `review_task` 新生成并进入 `pending` 后，可以为其生成手机端复核链接所需 token。

创建时建议记录：

- `review_task_id`
- `token_subject`
- `allowed_actions`
- `expires_at`
- `created_at`
- `created_by`

### 5.2 使用

建议手机端访问路由：

`GET /mobile/review/{review_task_id}?token=...`

用户通过通知消息点击链接后，页面先做 token 校验，再展示该复核任务的必要摘要。

### 5.3 校验

校验时至少应满足：

- `token_hash` 匹配
- token 未过期
- token 未撤销
- `review_task` 仍为 `pending`
- 请求动作在 `allowed_actions` 内
- token 与 `review_task_id` 完全匹配

### 5.4 完成

成功提交复核后：

- `ReviewTaskService` 完成正式复核处理
- 记录 `used_at` 或 `last_used_at`
- 写入 `review_tasks`
- 如需推动 `source_task`，通过 `RuntimeTaskService`
- 写入相应 `task_status_history`

### 5.5 失效条件

以下情况 token 应视为不可用：

- `review_task` 已处理
- token 已过期
- token 已撤销
- 请求动作不在 `allowed_actions`
- token 与 `review_task_id` 不匹配

---

## 6. `allowed_actions` 默认规则

### 6.1 默认动作集

手机端默认建议允许：

- `approved`
- `rejected`
- `adjusted`
- `cancelled`

不允许：

- `expired`

`expired` 只能由系统超时处理逻辑触发，不应由手机端用户手动触发。

### 6.2 按 `review_type` 的后续限制

未来可按不同 `review_type` 限制动作集。

例如：

- `below_break_even_review`
  - 可允许：`approved / rejected / adjusted`
- `labor_required`
  - 可允许：`approved / rejected / adjusted`
- `cold_storage_warning`
  - 未来可允许：`acknowledged / cancelled`

如果当前系统还没有 `acknowledged` 状态，则：

- 可以暂时映射为 `approved`
- 或先只作为后续扩展预留，不在 MVP 中开放

---

## 7. 手机端页面建议

### 7.1 路由建议

建议路由：

- `GET /mobile/review/{review_task_id}`
- `POST /mobile/review/{review_task_id}/resolve`

### 7.2 页面展示内容

手机端页面建议展示：

- `review_type`
- `trade_date`
- `scope_type`
- `scope_key`
- `reason`
- `required_by`
- `review_payload_json` 的关键摘要
- 关联 `source_task` 当前状态
- 已发送通知记录摘要
- 可执行动作按钮

注意：

- 页面只展示必要摘要
- 不展示完整敏感 payload
- 不展示不必要的内部执行细节

### 7.3 提交字段

建议提交字段：

- `token`
- `action`
- `resolution_note`
- `resolution_payload_json`

### 7.4 `actor` 规则

手机端 `actor` 规则应明确为：

- `actor` 不来自前端自由填写
- `actor` 来自 `token_subject`
- `actor_source = mobile_review_token`

也就是说，手机端提交时不能依赖用户在表单里自己填写“我是谁”，而应由 token 绑定身份决定审计主体。

---

## 8. 安全要求

### 8.1 token 安全要求

`review_token` 应满足：

- token 必须随机、不可预测
- 数据库只保存 `token_hash`
- token 应有有效期
- token 只能绑定一个 `review_task`
- token 不能越权访问其他 `review_task`

### 8.2 提交流程安全要求

手机端提交时必须满足：

- 已处理 `review_task` 不能重复提交
- 所有提交仍经过 `ReviewTaskService`
- 若需要推动源任务，仍通过 `RuntimeTaskService`
- 成功提交后采用 POST-Redirect-GET，避免刷新重复提交

### 8.3 数据最小暴露原则

移动端页面不要展示完整敏感 payload，只展示必要摘要。

不应直接暴露的信息包括但不限于：

- 账号
- 密码
- 平台 token
- 客户隐私
- 资金信息
- 完整内部执行上下文

---

## 9. 与通知系统的关系

### 9.1 链接生成关系

未来通知系统可以把手机端复核链接放进通知消息里。

建议关系为：

- token 的创建由专门服务负责
- sender 不负责直接拼 token
- sender 只消费“已生成好的 mobile review URL”

### 9.2 当前 mock 阶段

在 mock sender 阶段：

- 可以先只生成链接
- 不要求真实发送
- `notification_logs` 可记录：
  - `mobile_review_url`
  - 或 `link_created = true`

### 9.3 未来真实 sender 复用

后续真实 sender，例如：

- 企业微信
- Bark
- 飞书

都应复用同一套 mobile review URL，而不是各自独立生成 token 或拼接不同格式的审批链接。

---

## 10. 与现有 Web 登录复核的关系

### 10.1 两种入口并存

当前 Web 后台继续使用 Session 登录。

未来手机端则使用 `review_token`。

两者关系是：

- Web 后台：`web_session`
- 手机端：`mobile_review_token`

### 10.2 最终调用边界一致

两种入口最终都应调用：

- `ReviewTaskService.resolve_review_task(...)`

如果需要推动源任务状态，两者都必须通过：

- `RuntimeTaskService`

最终写入的对象也应一致：

- `review_tasks`
- `task_status_history`

### 10.3 审计字段要求

字段语义建议为：

- Web 后台：
  - `resolved_by = session_user`
  - `actor_source = web_session`
- 手机端：
  - `resolved_by = token_subject`
  - `actor_source = mobile_review_token`

这样可以保证不同入口共用一套业务处理逻辑，同时仍能区分审计来源。

---

## 11. 超时与撤销

### 11.1 与 `expire-review-tasks` 的关系

当 `expire-review-tasks` 将某个 `review_task` 过期后：

- 对应 token 应视为不可用

MVP 可以先采用保守策略：

- 校验时若发现 `review_task` 已不是 `pending`
- 直接拒绝提交

也就是说，MVP 不必强制在过期时立刻批量把所有相关 token 写入 `revoked_at`。

### 11.2 可选增强

后续可增强为：

- review 过期时批量更新相关 token 的 `revoked_at`
- Web 人工处理完成时同步撤销同一 `review_task` 的其他 token

### 11.3 并发与重复提交

如果人工已在 Web 端处理完该 `review_task`，则手机 token 再提交必须失败。

手机端不应覆盖：

- `resolved_by`
- `resolved_at`
- `resolution_note`

也不应重新推动已经完成流转的 `source_task`。

---

## 12. 本阶段暂不实现事项

本阶段暂不实现：

- 完整移动端 UI
- 真实通知渠道
- 完整用户权限系统
- 真实平台 / RPA
- AI Agent 自动复核

同时也不改变当前既有边界：

- 不绕过 `ReviewTaskService`
- 不绕过 `RuntimeTaskService`
- 不让手机端直接写 SQLite

---

## 13. 后续实现建议

建议分阶段推进：

### Phase 1：文档和接口规划

- 固化 `review_token` 数据结构
- 固化手机端路由和校验规则
- 固化与通知系统、Web Session 复核的关系

### Phase 2：`review_tokens` 表与 `ReviewTokenService`

- 新增 `review_tokens`
- 实现 token 创建、校验、撤销、记录使用痕迹

### Phase 3：mobile review 只读详情页

- 实现 `GET /mobile/review/{review_task_id}`
- 展示必要摘要和可执行动作

### Phase 4：mobile review resolve 提交

- 实现 `POST /mobile/review/{review_task_id}/resolve`
- 调用 `ReviewTaskService`
- 必要时通过 `RuntimeTaskService` 推动源任务

### Phase 5：notification sender 中加入 `mobile_review_url`

- 将 mobile review URL 纳入通知内容
- 在 `notification_logs` 中保留链接创建记录

### Phase 6：真实通知渠道接入

- 企业微信
- Bark
- 飞书

以上阶段建议顺序保持不变，先固化 token 与服务边界，再做移动端页面和真实通知渠道。
