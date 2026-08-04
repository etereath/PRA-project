# Web 前端整体更新计划：PRA 运行态运营后台

> 状态说明：本文保留早期 Web 刷新计划和各阶段落地记录。[GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)
> 是任务 13.5 的宏观权威，[任务 13.5 Web 主控端重写计划](plans/task13_5_web_rewrite_plan.md)
> 是当前本地实施权威；本文中的历史范围、页面排列和阶段编号不得覆盖新的八入口运营
> 信息架构。[2026-07-29 Web 现状独立审计](plans/task13_5_web_current_state_audit_20260729.md)
> 是页面数量、尺寸、视口和 DOM 摘要的快照证据；这些数字不是长期常量。

## 1. 背景与目标

当前 Web 页面起步于 Excel 原型验证阶段，主要围绕“校验 Excel、生成任务、模拟执行、人工介入、查看 SQLite 运行态”展开。

截至当前项目状态，后端已经具备：

- SQLite 运行态任务系统
- 人工复核闭环
- `review_token` 与 Mobile Review MVP
- 飞书 Webhook 真实通知
- 飞书 `post` 富文本消息
- cpolar 真实访问链路

因此，Web 的定位需要从：

`Excel 原型验证页面`

升级为：

`PRA 运行态运营后台`

本次规划目标不是重写技术栈，而是在现有 Python WSGI Web、模板字符串和 CSS 基础上，重构信息架构、导航、页面文案和关键运营交互。

## 2. 定位调整

### 2.1 新定位

Web 后台应服务于日常运营，而不是只服务于开发期验证。

核心职责：

- 查看运行态任务
- 处理人工复核
- 追踪飞书通知与手机端复核状态
- 查看执行日志和状态历史
- 维护 Excel 业务输入
- 检查运行环境配置是否满足真实通知和 Mobile Review 使用要求

### 2.2 保持边界

- 不引入 React / Vue / 前后端分离
- 不改 `ReviewTaskService`
- 不改 `RuntimeTaskService`
- 不改 `NotificationSender`
- 不改 SQLite schema
- 不直接在 Web 中绕过 service 写数据库
- 不展示 token、secret、Webhook 完整敏感值

## 3. 新导航结构

建议主导航改为：

1. 首页总览 Dashboard
2. 任务中心 Tasks
3. 复核中心 Reviews
4. 通知中心 Notifications
5. 执行日志 Execution Logs
6. 业务输入 Business Inputs
7. 系统设置 / 运行检查

导航命名应以中文为主，英文对象名作为辅助说明。

建议页面路径：

- `/dashboard`
- `/tasks`
- `/reviews`
- `/notifications`
- `/execution-logs`
- `/business-inputs`
- `/system`

`/` 路由当前仍承载旧任务生成 POST 逻辑。Phase 1 优先新增 `/dashboard` 作为新首页入口；`GET /` 是否切换到 Dashboard 由实现时根据现有路由风险决定。如存在破坏旧 POST/GET 任务生成流程的风险，先保留旧 `/`，只在导航中提供 `/dashboard`。

现有 `/runtime` 可在过渡期保留，但主导航不再把它作为唯一运行态入口，而是逐步拆分为任务、复核、通知、历史等更清晰的页面。

## 4. 旧页面迁移方案

### 4.1 任务面板

当前定位：Excel 输入校验、任务预览、任务导出。

迁移后定位：

- 移入 `Business Inputs` 或 `Tasks` 的“任务生成”区域
- 保留“校验输入 / 预览任务 / 生成运行态任务”的能力
- 页面文案从“Excel 数据校验页面”改为“基于业务输入生成运行态任务”

建议处理：

- 旧 `/` 任务面板后续可改为 Dashboard，但 Phase 1 不强制修改 `/`，避免破坏现有任务生成 POST 逻辑
- 原任务生成表单迁移到 `/business-inputs` 的“任务生成”卡片
- 保留生成 Excel 任务文件的兼容出口，但标注为“验收/人工核对导出”

### 4.2 Excel 表格管理

当前定位：Excel 表格编辑页。

迁移后定位：

- 改名为 `业务输入 Business Inputs`
- 继续维护商品、规则、预测、产能、冷库等 Excel 输入
- 明确 Excel 仍是当前主数据和业务输入来源
- 页面不再暗示 Excel 是最终运行态事实来源

保留表格：

- 商品主表
- 价格规则表
- 上下架规则表
- 产量预测表
- 价格预测表
- 包装产能计划表
- 冷库状态表

### 4.3 执行回写

当前定位：模拟执行 Excel 任务并回写。

迁移后定位：

- 拆为 `Execution Logs`
- 当前只展示 mock 执行和执行日志
- 未来承接真实 RPA / 平台 API 执行日志

建议处理：

- 原“执行回写”入口改名为“执行日志 / Mock 执行”
- Excel 回写能力标注为兼容验证工具
- 新页面优先展示 SQLite `execution_logs`

### 4.4 人工介入

当前定位：旧 Excel 人工介入工作台。

迁移后定位：

- 降级为旧 Excel 只读兼容页
- 从主处理路径移除
- 只保留历史查看与迁移说明

必须明确：

- 不再允许通过旧 Excel 人工介入链路正式处理任务
- 新的人工复核统一进入 SQLite `review_tasks`
- 主导航应指向 `Reviews`

### 4.5 SQLite 运行态

当前定位：一个聚合页展示 tasks、review_tasks、notification_logs、history。

迁移后定位：

- 拆分为 `Tasks`
- 拆分为 `Reviews`
- 拆分为 `Notifications`
- 拆分状态历史到任务详情页
- 执行日志进入 `Execution Logs`

过渡策略：

- `/runtime` 保留为兼容入口
- 页面顶部提示“运行态页面已拆分为任务中心、复核中心、通知中心”
- 第一阶段不强制重定向旧路径，避免打断当前已验证流程
- 后续新页面稳定后，再评估是否将旧路径重定向到新入口
- 后续主导航不再突出 `/runtime`

## 5. 页面规划

### 5.1 Dashboard 首页总览

目标：让运营人员打开后台后立即知道系统今天是否健康。

长期可扩展指标：

- pending 任务数
- expired 任务数
- pending 复核数
- 即将超时复核数
- 飞书通知成功/失败数

Dashboard 第一版指标收敛为最小集合：

- pending review 数
- 即将超时 review 数
- failed notification 数
- pending task 数
- expired task/review 数

后续增强再考虑：

- 最近一次任务生成时间
- 最近一次通知发送结果
- Mobile Review 链路状态摘要
- cpolar 访问链路状态
- 当前或默认 `trade_date` 的完整运营视图

建议操作：

- 跳转到待处理复核
- 跳转到失败通知
- 跳转到运行检查
- 跳转到业务输入生成任务

状态高亮：

- `pending`：蓝色或琥珀色
- `failed`：红色
- `expired`：灰红色
- 即将超时：橙色
- 正常成功：绿色

### 5.2 Tasks 任务中心

数据来源：SQLite `tasks`

列表字段：

- `task_id`：任务 ID
- `trade_date`：交易日
- `scope_type`：作用范围类型
- `scope_key`：作用范围对象
- `internal_sku`：内部 SKU
- `platform_name`：平台名称
- `action_type`：任务类型
- `task_status`：任务状态
- `target_price`：目标价格
- `target_status`：目标状态
- `pricing_source`：定价来源
- `scheduled_at`：计划执行时间
- `expires_at`：过期时间
- `required_by`：处理截止时间
- `created_at`：创建时间

详情字段：

- `dedupe_key`
- `decision_trace_json` 摘要
- `result_message`
- 关联复核任务
- 关联通知记录
- 状态历史 `task_status_history`
- 执行日志 `execution_logs`

筛选：

- `trade_date`
- `task_status`
- `action_type`
- `scope_type`
- `scope_key`

操作：

- 查看详情
- 查看状态历史
- 查看关联复核
- 查看关联通知
- 后续可预留“重新生成同类任务”入口，但当前不实现

### 5.3 Reviews 复核中心

数据来源：SQLite `review_tasks`

列表字段：

- `review_task_id`：复核任务 ID
- `trade_date`：交易日
- `review_type`：复核类型
- `review_status`：复核状态
- `scope_type`：作用范围类型
- `scope_key`：作用范围对象
- `source_task_id`：来源任务 ID
- `reason`：原因
- `required_by`：处理截止时间
- `resolved_by`：处理人
- `resolved_at`：处理时间
- `updated_at`：更新时间

详情字段：

- `review_payload_json` 关键摘要
- `resolution_payload_json` 关键摘要
- `resolution_note`
- 源任务当前状态
- 关联通知记录
- 关联 review token 摘要

操作：

- `approved`
- `rejected`
- `adjusted`
- `cancelled`
- 查看源任务
- 查看通知
- 查看手机端复核入口状态

Phase 1 约束：

- `/reviews` 先以只读列表为主
- Phase 1 不重做复核处理表单
- pending review 的处理入口可以链接到现有已验证的 `/runtime` 处理入口
- 正式把复核处理动作迁入 `Reviews` 留到 Phase 3

约束：

- 仅 `pending` 可处理
- actor 来自 Web session
- 不允许前端自由填写 actor 作为审计身份
- 处理仍必须调用 `ReviewTaskService`

### 5.4 Notifications 通知中心

数据来源：SQLite `notification_logs`

列表字段：

- `notification_id`：通知 ID
- `related_review_task_id`：关联复核任务 ID
- `related_task_id`：关联任务 ID
- `recipient_type`：接收人类型
- `recipient`：接收人
- `channel`：通知渠道
- `send_status`：发送状态
- `sent_at`：发送时间
- `created_at`：创建时间
- `message`：通知摘要
- `error_message`：错误信息

详情字段：

- 完整通知日志字段
- 关联复核任务摘要
- 关联任务摘要
- 飞书消息类型摘要，例如 `post / text`
- 是否创建 mobile review URL 的摘要

筛选：

- `related_review_task_id`
- `send_status`
- `channel`

操作：

- 查看关联复核
- 查看关联任务
- 后续可预留“重新发送通知”，当前不实现

安全要求：

- 不展示完整 `token=...` 链接
- 不展示 `FEISHU_WEBHOOK_URL`
- 不展示 `FEISHU_WEBHOOK_SECRET`
- 不展示 `REVIEW_TOKEN_SECRET`

### 5.5 Execution Logs 执行日志

数据来源：SQLite `execution_logs`，兼容展示旧 Excel mock 执行结果。

列表字段：

- `log_id`：日志 ID
- `task_id`：任务 ID
- `executor_name`：执行器名称
- `start_time`：开始时间
- `end_time`：结束时间
- `success_flag`：是否成功
- `error_code`：错误代码
- `error_message`：错误信息
- `created_at`：创建时间

详情字段：

- `raw_output` 摘要
- `ai_model_version`
- `ai_summary`
- 关联任务
- 关联状态历史

操作：

- 查看关联任务
- 查看原始输出摘要
- 未来预留真实 RPA / API 执行结果接入

### 5.6 Business Inputs 业务输入

数据来源：Excel 工作簿。

页面模块：

- Excel 表格管理
- 数据校验
- 任务预览
- 生成运行态任务
- 导出 Excel 任务文件用于人工核对

展示字段：

- 表格名称
- 文件路径
- 最后加载行数
- 校验错误
- 字段中文名
- 字段英文键名

操作：

- 加载表格
- 保存表格
- 校验业务输入
- 预览任务
- 生成运行态任务
- 导出任务 Excel

文案重点：

- Excel 是当前业务输入来源
- SQLite 是运行态任务事实来源
- 不再把 Excel 人工介入作为正式处理入口

### 5.7 系统设置 / 运行检查

数据来源：环境变量、运行态检查脚本、基础健康检查。

需要区分两类检查：

- 配置检查：环境变量是否存在、格式是否合理、是否仍是占位值
- 连通性检查：真实访问或真实发送是否成功，例如飞书 Webhook、cpolar 外网访问、Mobile Review 链接访问

第一版建议只做配置检查，避免把未经真实请求验证的状态误展示为“健康”。

展示内容：

- `DEFAULT_NOTIFICATION_CHANNEL`
- `FEISHU_MESSAGE_TYPE`
- `MOBILE_REVIEW_BASE_URL` 是否已配置
- `REVIEW_TOKEN_SECRET` 是否已配置，不展示值
- `RUNTIME_ADMIN_USER` 是否已配置
- `RUNTIME_ADMIN_PASSWORD` 是否已配置，不展示值
- `FEISHU_WEBHOOK_URL` 是否已配置，不展示完整值
- `FEISHU_WEBHOOK_SECRET` 是否已配置，不展示值
- runtime DB 路径
- schema 版本
- `/health` 本地状态

操作：

- 运行环境检查
- 查看配置缺失项
- 查看当前通知模式
- 后续再增加真实连通性检查，例如测试飞书 Webhook 或 cpolar 外网 URL

安全要求：

- 所有 secret 只显示“已配置 / 未配置”
- URL 只显示脱敏摘要，例如域名和末尾 4 位
- 不允许在 Web 表单里编辑真实 secret

## 6. 视觉与交互优化

### 6.0 登录与公网访问边界

当前系统已通过 cpolar 暴露 Mobile Review 链路，因此运行态后台页面不得在公网未登录可见。

Phase 1 新增运行态页面应要求 Web Session 登录：

- `/dashboard`
- `/tasks`
- `/reviews`
- `/notifications`
- `/execution-logs`
- `/system`

`/business-inputs` 如涉及 Excel 编辑、校验、预览或生成任务，也必须登录。若 Phase 1 只提供静态说明页，可以先只显示说明；一旦接入编辑/生成操作，必须套用登录保护。

登录身份沿用当前 `/runtime` Session 机制，不新增权限系统。

### 6.1 页面布局

- 顶部标题缩小，避免占据过多纵向空间
- 使用固定顶部导航或清晰横向导航
- Dashboard 使用卡片式摘要
- 列表页使用筛选区 + 表格 + 详情区
- 详情页优先展示摘要，再展开 JSON

### 6.2 状态视觉

建议状态 badge：

- `pending`：蓝色或琥珀色
- `running`：蓝色
- `success`：绿色
- `failed`：红色
- `manual_review`：紫色或橙色
- `expired`：深红或灰红
- `cancelled`：灰色
- `skipped`：灰色

复核状态：

- `pending`：高亮
- `approved`：绿色
- `rejected`：红色
- `adjusted`：橙色
- `expired`：灰红
- `cancelled`：灰色

通知状态：

- `success`：绿色
- `failed`：红色
- `pending`：蓝色或灰色

### 6.3 超时与风险提示

高亮规则：

- `required_by < now` 且仍为 `pending`：过期风险
- `required_by` 距离当前小于 2 小时：即将超时
- `send_status=failed`：通知失败
- `task_status=failed`：执行失败
- `review_status=pending` 且 `source_task_id` 存在：需要运营关注

### 6.4 Mobile Review 状态展示

在复核详情页展示：

- 是否存在关联通知
- 通知是否发送成功
- 是否已生成 mobile review URL 的摘要
- 是否已有 token 摘要
- token 是否已使用
- token 是否已撤销
- token 是否过期

安全要求：

- 不展示 raw token
- 不展示完整 mobile review URL
- 可展示“链接已创建 / 已使用 / 已失效”

### 6.5 空数据状态提示

Phase 1 基础列表必须给出明确下一步提示，而不是只显示空表格：

- tasks 空：提示“暂无运行态任务，请前往业务输入生成运行态任务。”
- reviews 空：提示“当前没有待复核任务。”
- notifications 空：提示“生成 pending review_task 后会自动创建通知记录。”
- execution logs 空：提示“当前未执行 mock/RPA，暂无执行日志。”

## 7. 前端文案更新

需要替换或删除的旧阶段文案：

- “当前页面用于校验 Excel 数据...”
- “快速验证整条业务链路...”
- “任务面板”
- “执行回写”
- “人工介入工作台”
- “SQLite 运行态查看”

建议新文案：

- “PRA 运行态运营后台”
- “查看和处理运行态任务、人工复核、飞书通知和手机端复核链路。”
- “Excel 仍作为业务输入来源；SQLite 保存运行态任务事实。”
- “所有人工复核统一进入复核中心处理。”
- “飞书只负责通知，审批通过 Mobile Review 页面完成。”
- “旧 Excel 人工介入入口仅保留只读兼容。”

## 8. 字段中文名规则

所有新增页面必须继续遵守项目规则：

- 英文字段名保持不变
- Web 表头和说明使用中文名
- 文档同步说明字段中文名
- 新增字段必须补充 `FIELD_LABELS`

本次前端刷新涉及的重点字段：

- `task_id`
- `trade_date`
- `scope_type`
- `scope_key`
- `action_type`
- `task_status`
- `review_task_id`
- `review_type`
- `review_status`
- `notification_id`
- `related_review_task_id`
- `send_status`
- `sent_at`
- `history_id`
- `from_status`
- `to_status`
- `changed_by`
- `changed_at`
- `log_id`
- `executor_name`
- `success_flag`
- `token_subject`
- `allowed_actions`
- `used_at`
- `revoked_at`

## 9. 分阶段实施计划

### Phase 1：导航和页面结构重构

目标：

- 建立新的主导航
- 将 `/runtime` 拆分为更清晰的页面入口
- 保留旧路径兼容
- 只做新导航、页面入口、基础列表和旧入口提示
- 复杂详情页、状态时间线、跨页面联动跳转放到后续 Phase
- 优先新增 `/dashboard`，不强制改动 `/` 路由；如改动 `GET /` 有破坏旧任务生成流程的风险，先保留旧 `/`
- 新运行态页面默认要求 Web Session 登录，避免 cpolar 公网暴露运行态数据
- `/reviews` 第一版只读列表为主，pending review 处理入口链接到已验证的 `/runtime`

交付：

- 新导航
- Dashboard 空壳或基础摘要
- Tasks / Reviews / Notifications 基础列表页面
- Execution Logs 基础入口和空状态
- System 配置检查入口
- `/manual-intervention` 标注为旧只读兼容
- `/runtime` 标注为旧聚合兼容入口
- 旧 `/runtime`、`/tables`、`/execution`、`/manual-intervention` 顶部提示新入口
- 第一阶段暂不强制重定向旧路径

验收：

- 原有 Web 功能不回退
- 主导航不再以 Excel 原型为中心
- 旧入口仍可访问但不再作为主处理入口
- 第一阶段不要求完成复杂详情页、状态时间线或深度联动跳转
- 未登录访问 `/dashboard`、`/tasks`、`/reviews`、`/notifications`、`/execution-logs`、`/system` 时不会暴露运行态数据
- 空列表页面显示明确下一步提示
- Phase 1 完成后同步更新本文档的当前进度，避免规划与实现状态脱节

### Phase 2：首页 Dashboard

目标：

- 提供运营总览
- 快速暴露待处理、失败、过期和通知状态

交付：

- pending review 数
- 即将超时 review 数
- failed notification 数
- pending task 数
- expired task/review 数
- 即将超时提示
- 后续增强项暂不做：最近任务生成时间、Mobile Review 链路状态、cpolar 连通性

验收：

- 可一眼看到 pending / failed / expired 状态
- 可跳转到对应筛选列表
- 不展示任何 secret 或 raw token
- 不把未实际验证的外部链路显示为健康状态

### Phase 3：复核中心增强

目标：

- 将人工复核作为 Web 运营主流程
- 增强 review 详情和处理体验

交付：

- 复核列表筛选
- 复核详情页
- 处理动作按钮
- 源任务状态展示
- 关联通知展示
- review token 摘要展示

验收：

- pending 复核可处理
- 非 pending 不显示处理按钮
- Web 处理仍走 session actor
- 重复提交被拒绝

### Phase 4：通知中心增强

目标：

- 让运营人员能追踪飞书通知是否成功
- 明确通知与复核任务、手机端链接的关系

交付：

- 通知列表筛选
- 通知详情页
- 关联 review/task 跳转
- 飞书 `post / text` 摘要展示
- 失败原因高亮

验收：

- 能看到 failed 通知
- 能回到对应 review_task
- 不泄露完整 URL、token、Webhook、secret

### Phase 5：业务输入和旧页面整理

目标：

- 收口 Excel 原型页面
- 保留业务输入能力
- 弱化旧人工介入链路

交付：

- Excel 表格管理改为 Business Inputs
- 任务生成能力迁移到业务输入页
- 执行回写改为 Execution Logs / Mock 执行
- 旧人工介入只读兼容说明

验收：

- 运营路径清晰：业务输入 -> 生成任务 -> 复核 -> 通知 -> 执行日志
- Excel 不再被描述为运行态事实来源
- 旧 Excel 人工处理入口不能执行正式处理

## 10. 测试建议

### 页面路由

- `/dashboard` 或 `/` 正常加载
- `/tasks` 正常加载
- `/reviews` 正常加载
- `/notifications` 正常加载
- `/execution-logs` 正常加载
- `/business-inputs` 正常加载
- `/system` 正常加载
- 旧 `/runtime`、`/tables`、`/execution`、`/manual-intervention` 仍可兼容访问
- 第一阶段旧路径不强制重定向，只显示新入口提示
- 如 `/` 仍保留旧任务生成页，必须继续支持原有 GET/POST 任务生成流程
- `/dashboard` 必须作为新首页入口存在

### 数据展示

- tasks 列表字段完整
- review_tasks 列表字段完整
- notification_logs 列表字段完整
- task_status_history 可从任务详情查看
- execution_logs 可从任务详情或执行日志页查看
- review_tokens 只展示摘要，不展示 raw token
- tasks/reviews/notifications/execution logs 为空时有明确下一步提示

### 操作

- Phase 1 `/reviews` 不重做复核表单，只提供只读列表和跳转到 `/runtime` 的处理入口
- 未登录不能处理 review
- 非 pending review 不显示处理按钮
- 通知失败能在通知中心高亮
- 即将超时复核能高亮

### 登录保护

- 未登录访问 `/dashboard`、`/tasks`、`/reviews`、`/notifications`、`/execution-logs`、`/system` 不应展示运行态数据
- `/business-inputs` 如提供编辑/生成动作，也必须登录
- 登录机制沿用当前 Web Session，不新增完整权限系统

### 安全

- 页面不展示 `REVIEW_TOKEN_SECRET`
- 页面不展示 `RUNTIME_ADMIN_PASSWORD`
- 页面不展示 `FEISHU_WEBHOOK_SECRET`
- 页面不展示完整 `FEISHU_WEBHOOK_URL`
- 页面不展示完整 `mobile_review_url`
- 页面不展示 `token=`

## 11. 当前阶段暂不实现事项

- 不引入 React / Vue
- 不做前后端分离
- 不改业务 service
- 不接真实平台
- 不接真实 RPA
- 不新增完整权限系统
- 不做手机端完整 UI 重设计
- 不新增 SQLite schema
- 不迁移 Excel 主数据

## 12. 推荐验收标准

本轮 Web 刷新完成后，应满足：

- 运营人员能从首页看到系统当前状态
- 复核任务有独立中心页，而不是藏在 `/runtime`
- 飞书通知与 mobile review 链路状态可追踪
- Excel 页面被重新定位为业务输入，不再是系统中心
- 旧人工介入链路只读兼容，不再承担正式处理
- 所有新增 Web 字段都有中文名
- 不泄露任何 token、secret、Webhook、密码

## 13. 当前进度

截至本次同步，Web 前端刷新 Phase 1、Phase 2、Phase 3、Phase 4 与任务中心增强已落地。

任务中心增强已完成范围：

- `/tasks` 已从基础任务列表升级为运行态任务追踪入口。
- `/tasks` 已支持 `task_status / trade_date / action_type / scope_type / scope_key` 只读筛选；非法日期等筛选值不会导致 500。
- 任务列表已补齐 `task_id / trade_date / scope_type / scope_key / internal_sku / platform_name / action_type / task_status / target_price / target_status / pricing_source / required_by / created_at` 等字段。
- `/tasks?task_id=...` 已支持任务详情展示；不存在的 `task_id` 会显示“未找到对应任务”。
- 任务详情已展示 `dedupe_key / scheduled_at / expires_at / result_message / decision_trace_json`，其中 JSON 采用摘要 + 折叠截断展示。
- 任务详情已展示该任务的 `task_status_history`。
- 任务详情已展示 `source_task_id = task_id` 的关联 `review_tasks`，不强行关联全局级或独立复核。
- 任务详情已展示 `related_task_id = task_id` 的直接通知，并额外展示通过关联复核找到的通知，且标注“直接关联 / 通过复核关联”。
- 任务详情已展示 `task_id` 对应的 `execution_logs` 摘要；`raw_output` 默认折叠截断、HTML 转义并脱敏。
- 页面继续避免展示完整 `mobile_review_url`、`token=`、secret、webhook 和完整 runtime DB 本地路径。

Phase 4 已完成范围：

- `/notifications` 已从基础列表升级为通知排障与追踪入口。
- 通知列表已补齐 `notification_id / related_review_task_id / related_task_id / recipient_type / recipient / channel / send_status / sent_at / created_at / message / error_message` 等字段。
- `/notifications` 已支持 `send_status`、`related_review_task_id`、`channel` 三类只读筛选。
- `/notifications?notification_id=...` 已支持通知详情展示；不存在的 `notification_id` 会显示“未找到对应通知”，不会导致 500。
- 通知详情已展示关联 `review_task` 摘要，并提供跳转到 `/reviews?review_task_id=...`。
- 通知详情已展示关联 `task` 摘要；当前任务中心已支持完整任务详情入口。
- `channel=feishu` 的详情页已展示当前 `FEISHU_MESSAGE_TYPE` 配置摘要，并明确该值不是逐条历史通知的持久化字段。
- 列表页 `error_message` 已做长度限制；详情页可展示更长内容，但超长内容会截断并提示。
- `notification_logs.message` 与 `error_message` 在 Web 展示前统一经过通知文本脱敏，至少处理 `token=...`、Mobile Review 链接和飞书 webhook URL。
- `/runtime` 的通知聚合详情继续保留为兼容入口。

Phase 3 已完成范围：

- `/reviews` 已从只读列表升级为 Web 人工复核主入口。
- `/reviews?review_task_id=...` 已支持复核详情展示，包含复核基础字段、源任务状态、关联通知、状态历史和 review token 摘要。
- `/reviews` 已支持 pending 复核处理，动作限制为 `approved / rejected / adjusted / cancelled`；`expired` 仍只允许由 `expire-review-tasks` 超时流程触发。
- Web 复核处理不暴露 `reviewer_code`，`actor / resolved_by` 来自 Web Session 用户。
- 复核处理仍通过 `ReviewTaskService` 与 `RuntimeTaskService` 完成，源任务只有在当前为 `manual_review` 时才按既有映射自动流转。
- 成功处理后使用 POST-Redirect-GET 回到复核详情页，刷新不会重复提交；非 pending 复核不显示处理表单，重复提交会被拒绝。
- `resolution_payload_json` 已与 Mobile Review 保持一致的最小校验：允许为空，非空必须是 JSON object，最大 4KB，页面回显转义。
- 详情页 JSON 展示已采用“关键摘要 + 折叠 JSON”方式，长内容会截断，避免大 JSON 撑爆页面。
- review token 摘要只展示 `token_id / token_subject / allowed_actions / expires_at / used_at / revoked_at / last_used_at`，不展示 raw token 或 `token_hash`。
- 页面继续避免展示完整 `mobile_review_url`、`token=`、secret、webhook 和完整 runtime DB 本地路径。

Phase 2 已完成范围：

- `/dashboard` 已从基础入口升级为最小运营总览
- 已展示 5 个核心指标：pending review、即将超时 review、failed notification、pending task、expired task/review
- 即将超时 review 判断为 `review_status=pending`、`required_by` 不为空，且 `now <= required_by <= now + 2h`；已过期 pending review 不计入即将超时
- expired task/review 卡片已显示合并总数和拆分数，并提供过期任务、过期复核两个入口
- `/tasks` Phase 2 曾支持 `task_status` 最小筛选；当前已由任务中心增强扩展为任务追踪入口
- `/reviews` 已支持 `review_status` 和 `due=soon` 最小筛选
- `/notifications` Phase 2 曾支持 `send_status` 最小筛选；当前已由 Phase 4 扩展为通知排障与追踪入口
- Dashboard 和运行态页面继续要求 Web Session 登录，未登录不展示运行态数据
- 页面不展示完整 runtime DB 路径、secret、webhook、raw token 或 `token=`

Phase 1 已完成范围：

- 已新增主导航：`/dashboard`、`/tasks`、`/reviews`、`/notifications`、`/execution-logs`、`/business-inputs`、`/system`
- 已将 Web 主定位更新为 `PRA 运行态运营后台`
- 已保留旧 `/` 任务生成 GET/POST 流程，不强制切换首页
- `/reviews` Phase 1 曾按只读列表落地；当前已由 Phase 3 升级为正式 Web 复核入口，`/runtime` 保留为兼容聚合页
- 新运行态页面已接入 Web Session 登录保护，未登录不展示运行态数据
- `/system` 第一版已按“配置检查”落地，展示环境变量存在性、runtime DB 摘要和 schema 版本，不展示本地完整路径或 secret 明文
- 旧 `/runtime`、`/tables`、`/execution`、`/manual-intervention` 已保留兼容访问，并在页面顶部提示新入口
- tasks / reviews / notifications / execution logs 空状态已补明确下一步提示
- 已补齐相关中文字段名和 Web 测试覆盖

仍留到后续 Phase 的内容：

- 连通性检查、真实健康检查和更完整的系统运维页

## 14. 当前进度同步规则

后续每完成一个 Phase，必须同步更新本文档：

- 将已完成能力从“计划”改为“已落地”
- 标记仍未完成的后续事项
- 如果实现范围与原计划不同，记录差异和原因
- 确认是否新增字段中文名、测试覆盖和安全边界

## 15. 系统检查页增强当前进度

本次已按“系统检查页增强”计划完成 `/system` 只读增强，重点是把原来的环境变量存在性列表升级为运行态配置、数据库、schema、计数和外部连通性边界的分组检查页。

已完成能力：

- `/system` 继续要求 Web Session 登录；未登录时不展示运行态数据。
- 配置检查已按模块分组展示：后台登录、Review Token、通知渠道、飞书配置、Mobile Review。
- 配置状态已使用 `ok / info / warning / error / not_configured` badge 展示。
- `DEFAULT_NOTIFICATION_CHANNEL=mock` 已按 `DEV_MODE` 区分语义：`DEV_MODE=true` 时为本地调试 `info`，`DEV_MODE=false` 时为不会真实通知的 `warning`。
- `DEFAULT_NOTIFICATION_CHANNEL=feishu` 时会继续检查 `FEISHU_WEBHOOK_URL` 和 `MOBILE_REVIEW_BASE_URL`，关键配置缺失时显示 `error`。
- 已复用运行环境检查语义：后台密码长度、Review Token secret 长度、占位值、`FEISHU_MESSAGE_TYPE`、`FEISHU_WEBHOOK_TIMEOUT_SECONDS`、通知 channel 合法值。
- 运行态数据库检查已展示 DB 文件名、是否存在、是否可读、已应用 schema version 列表和最新 schema 要求。
- schema 检查统一引用 `app.runtime_schema.LATEST_RUNTIME_SCHEMA_VERSION`，当前要求为迁移记录连续且精确匹配 v16，并由 health check 验证实际表、列、约束和索引。
- 运行态表计数已分项容错：`tasks / review_tasks / notification_logs / execution_logs / review_tokens / script_runs / script_run_items` 任一表缺失或查询失败时，仅该表显示 `error`，不会导致整个 `/system` 返回 500。
- 运行状态摘要已展示 pending review、failed notification、expired task/review、pending task 和当前通知模式。
- 外部连通性已明确标注为“未验证”：本阶段不自动发送飞书测试消息，不自动探测 cpolar 或 Mobile Review 外网入口。
- 页面继续避免展示完整 runtime DB 本地路径、`REVIEW_TOKEN_SECRET`、`RUNTIME_ADMIN_PASSWORD`、`FEISHU_WEBHOOK_SECRET`、完整 webhook URL、完整 mobile review URL 或 `token=`。
- `/system` 已新增“发送飞书测试通知”按钮，登录后可手动验证 `FeishuWebhookNotificationSender`、飞书 Webhook、签名配置和网络是否可用。
- 飞书测试通知使用 `POST /system/test-feishu-notification`，成功后采用 POST-Redirect-GET，刷新页面不会重复发送。
- 飞书测试通知不创建业务 `review_task`，不创建 `review_token`，不生成 `mobile_review_url`，不写 `task_status_history`，不改变 `tasks / review_tasks` 状态。
- 飞书测试通知结果已按系统测试记录写入 `notification_logs`：`recipient_type=system`、`recipient=system_test`、`related_task_id=null`、`related_review_task_id=null`、`message=PRA system test notification`。
- 测试消息和页面反馈继续脱敏，不展示完整 webhook URL、secret、token、mobile review URL 或 runtime DB 完整路径。

仍留到后续阶段的事项：

- cpolar / Mobile Review 外网访问探测。
- 更完整的运维健康检查和错误修复建议。
- 系统检查结果导出或历史记录。
- 业务输入页更细的视觉打磨、库存录入的批量导入能力，以及未来库存流水系统。

当前下一步优先级：

1. 继续运行 `python scripts/run_system_smoke_tests.py` 与完整单元测试，保持主控流程基线稳定。
2. 对当前代码做一次 Code Review。
3. 根据 Review 结果修复明显风险和测试缺口。
4. 再评估真实 RPA、平台适配器或更完整库存流水系统。

当前不建议直接进入真实 RPA、真实平台或 AI Agent 自动决策。

## 16. Web 易用性与本地化优化 Phase 1 当前进度

本轮已按“运营人员可读化”目标完成第一版展示层优化，范围仅限 Web 页面、飞书通知文案和维护文档，不改变数据库 schema、英文枚举存储值、任务生成规则、复核服务、通知发送主流程或 review token 校验逻辑。

已完成能力：

- 主导航已收敛为：`首页总览 / 任务中心 / 复核中心 / 通知中心 / 执行记录 / 业务数据`。
- `/system` 已从主导航隐藏，但旧 `/system` 路由仍可直接访问，并在业务数据页保留低调的“系统维护”入口。
- Dashboard、Tasks、Reviews、Notifications、Execution Logs、Business Inputs、System 的主说明已改为业务用途说明，普通运营页面主文案避免使用 `SQLite / runtime / schema / review_task / notification_log` 等开发者术语。
- 新增统一展示映射 helper，用于任务状态、复核状态、通知状态、任务类型、复核类型、作用范围、通知渠道和接收人类型的中文展示。
- 中文映射基于当前代码中真实存在的枚举值，例如 `update_price / set_online / set_offline / capacity_warning / labor_required` 等；仅改变展示，不改变数据库值。
- 新增统一时间展示函数：timezone-aware datetime 转为 UTC+8 / Asia/Shanghai 后展示；naive datetime 按当前系统既有语义作为本地业务时间展示，避免二次加 8 小时。
- 主要运行态页面时间已统一展示为 `YYYY-MM-DD HH:mm`。
- 任务中心、复核中心、通知中心列表已将 ID 从第一视觉重点降级，优先展示业务日期、任务类型、处理对象、状态、截止时间、原因摘要、通知结果等运营字段；完整 ID 保留在详情页。
- 空状态文案已改为业务提示，例如“当前没有待执行或待处理任务。可以先去业务数据生成任务。”
- 飞书 `post/text` 消息已改为中文标签：`需要处理 / 业务日期 / 处理对象 / 截止时间 / 原因 / 👉 点击处理复核`。
- 飞书通知中的复核类型和作用范围已使用中文映射，时间按 UTC+8 `YYYY-MM-DD HH:mm` 展示。
- `notification_logs.message` 仍只保存简短摘要，不保存完整 token URL 或 `token=`。
- 新增 [web_localization_display_spec.md](web_localization_display_spec.md)，作为统一展示术语表和后续维护依据。

仍需在后续阶段继续打磨：

- 旧兼容页 `/runtime /tables /execution /manual-intervention` 仍保留较多技术字段，当前只作为兼容和排障入口。
- 部分详情页仍会保留英文 ID、英文键名或 JSON 字段，这是为了排障和 Code Review，不作为普通运营主说明。
- 后续可继续优化移动端复核页视觉、状态时间线展示和详情页信息层级。

## 17. Web 业务输入重构 Phase 1：商品资料与库存补充录入当前进度

本轮已将 `/business-inputs` 中的商品主数据维护，从“仅提供 Excel 表格入口”推进为“商品资料与库存录入”日常运营入口。当前实现仍保存回 `products.xlsx`，不迁移 Excel 主数据，不新增 SQLite schema，不改变运行态任务、复核、通知、review token 或任务生成主流程。

已完成能力：

- `/business-inputs` 已新增“商品资料与库存录入”区域，主操作为“补充库存”，而不是“新增商品”。
- `/business-inputs` 已将“录入库存”和“价格规则管理”改为页签式切换，默认进入录入库存页签，避免两个日常维护模块堆叠在同一长页面中。
- 商品列表已按业务字段展示：商品名称/品种、等级、枝长/规格、单位、基础成本、当前库存、是否允许销售、内部 SKU 和编辑入口。
- `sale_enabled` 已在页面显示为“是/否”，成本、库存等数值做了清晰格式化。
- 空商品状态已提示：“当前还没有商品资料。请先录入库存，系统会自动创建商品资料。”
- 录入库存表单已支持品种、等级、枝长/规格、单位、基础成本、本次入库数量、是否允许销售。
- 品种录入已从“主表单直接填写品种代码”调整为“选择已有品种 + 新增品种弹窗”。新增品种时在弹窗中填写新品种名称和品种代码，主表单不再常驻展示“品种代码”输入框。
- 已准备 `platform_mappings.xlsx` 平台映射表，并写入现有平台：寻梦、花伍、珍情、花易宝、蚂蚁、花宝宝。
- “录入库存”表单中已在“新增品种”左侧加入“新增平台”按钮，可维护新的销售平台选项；新增平台不会改变库存归属，初始库存仍是公共库存。
- 等级选项为 `A / B / C / D / E / 0`，默认 `B`；等级 `"0"` 按字符串处理。
- 单位选项为 `扎 / 半扎 / 枝`，默认 `扎`。
- 基础成本默认值为 `6`，页面说明其用于低价风险判断，不直接展示给销售平台。
- 枝长/规格选项为 `跟随等级 / 40 / 45 / 50 / 55 / 60 / 65 / 70`，默认 `跟随等级`；选择“跟随等级”时会按 `A=65 / B=60 / C=55 / D=50 / E=45 / 0=0` 解析为实际枝长并写回 `products.xlsx`。
- 历史数据中如果仍存在 `follow_grade / FG / 跟随等级`，匹配时会按当前等级解析，避免旧数据失配。
- 保存前会对品种、等级、枝长/规格、单位做标准化；例如 `艾莎 ` 与 `艾莎` 可识别为同一品种，`b` 会标准化为 `B`。
- 库存录入逻辑已按 `product_name + grade + stem_length + unit` 匹配同类型商品；平台、成本、是否允许销售和 SKU 不参与匹配。
- 匹配到唯一同类型商品时，系统会保留原 `internal_sku`，累加当前库存，并同步更新 `base_cost` 与 `sale_enabled`；页面提示中会明确说明这一点。
- 匹配不到同类型商品时，系统会新增商品资料并自动生成 SKU。
- 匹配到多条同类型商品时，系统拒绝保存并提示先检查商品主表，不会静默随机更新。
- SKU 生成已采用“维护映射表 + 新增品种时填写品种代码”的策略；未知品种缺少映射且未通过新增品种弹窗填写品种代码时，不会静默生成正式 SKU。
- SKU 规则不包含平台，不使用随机值；若基础 SKU 与不同类型商品冲突，使用稳定后缀避免覆盖。
- 录入库存表单已完成第一轮可用性调整：字段标题加粗、输入框与选择框高度统一、双栏布局按输入框 Y 轴对齐，补充库存按钮与同排输入控件底边对齐。
- 编辑商品表单已支持修改品种、等级、枝长/规格、单位、基础成本、当前库存、是否允许销售。
- 编辑商品时 `internal_sku` 只读展示；修改品种、等级、枝长或单位不会自动改变 SKU，并会提示可能影响后续任务生成。
- 编辑保存时会检查修改后的同类型键是否与其他商品重复；若重复则拒绝保存。
- `/tables` 旧表格编辑入口仍保留为高级兼容入口，适合批量维护和排障。
- 已新增 [product_inventory_input_spec.md](product_inventory_input_spec.md)，记录商品库存录入规则、公共库存边界、SKU 生成和旧入口兼容策略。

仍留到后续阶段的事项：

- 产量预测、价格预测、冷库状态等输入模块尚未重构。
- 当前没有实现完整库存流水系统。
- 当前没有实现平台销售转化记录或平台库存拆分。
- 当前仍不迁移 Excel 主数据，不接真实平台、真实 RPA 或 AI Agent。

## 价格规则适用范围三维筛选重构（最新）

价格规则适用范围已从旧的 `scope_type + scope_value` 单维结构，一次性升级为 `variety_filter + grade_filter + platform_filter` 三维筛选结构。

- `scope_type / scope_value` 在价格规则表中已废弃，不作为 Web 表单或任务生成主路径。
- `*` 表示对应维度不限制，空字符串不能替代 `*`。
- Web 价格规则表单使用“品种 / 等级 / 平台”三个选择框。
- 平台选项优先来自 `platform_mappings.xlsx`，读取失败或为空时回退默认平台列表，并在页面给出可读提示。
- 价格规则匹配由“多条命中规则叠加应用”改为“单条规则胜出”：先按 `priority` 升序，再按具体度降序。
- 具体度为 `variety_filter / grade_filter / platform_filter` 中非 `*` 条件数量。
- 如果同一候选商品/平台命中多条 `priority` 相同、具体度相同的规则，不允许静默随机选择；当前实现会返回明确冲突错误，避免生成不确定价格任务。
- `price_rules_backup_before_scope_refactor.xlsx` 仅用于人工回溯，不进入任务生成、Web 表单或测试主路径。

保持边界：不新增 SQLite schema，不迁移 Excel 主数据，不支持 SKU 级价格规则，不新增 `absolute_min_price / break_even_price / target_price` 字段，不接真实平台/RPA/AI Agent。

## 任务中心脚本状态页与自动规则评估框架（最新）

任务中心已新增二级分页：

- `任务状态`：保留现有任务列表、筛选和任务详情。
- `脚本状态`：展示自动规则评估脚本运行记录。

当前脚本状态页路径：

```text
/tasks?task_tab=automation
```

已完成能力：

- runtime schema 已升级到 v3，新增 `script_runs / script_run_items`。
- Web 脚本状态页第一版只读展示，不提供 `apply` 按钮。
- 列表展示脚本运行 ID、脚本名称、说明、运行时间、运行状态、运行模式、生成任务数、生成复核数、生成通知数和错误摘要。
- 详情展示该次运行的 proposal 明细、payload 摘要和 decision_trace 摘要。
- `dry-run` 在 Web 中明确展示为预览模式，避免误认为已经生成业务任务。
- 页面沿用 `/tasks` 登录保护，不展示 secret、token、webhook 或完整 mobile review URL。

业务边界：

- 自动规则评估框架只负责读取业务输入、生成 proposals，并由 runner 在 dry-run/apply 下记录运行结果。
- Evaluator 不直接写 SQLite 业务表。
- apply 必须通过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- Web 第一版只读，不提供 apply；apply 仅由 CLI 显式触发。
- 当前第一版 evaluator 为 `capacity_warning`，表示“预测产量超过确认包装能力”的预警，不代表真实订单需求超过包装能力。
- `capacity_warning` 当前读取 `capacity_plans.xlsx` 中对应业务日期的启用计划，并以 `confirmed_packing_capacity_qty` 作为最终判断口径。

后续可继续规划：

- 上下架规则 evaluator。
- 冷库压力 evaluator。
- 更完整的包装产能 evaluator。
- 如需定时运行，可考虑 APScheduler，但调度只负责触发，业务规则仍留在 evaluator 框架内。

## 18. Web 业务输入重构 Phase 2：价格规则输入表单化当前进度

本轮已将 `/business-inputs` 中的价格规则维护，从“只能进入 Excel 表格编辑”推进为“价格规则管理”日常运营入口。当前实现仍保存回 `price_rules.xlsx`，不迁移 Excel 主数据，不新增 SQLite schema，不改变运行态任务、复核、通知、review token 或任务生成主流程。

已完成能力：

- `/business-inputs` 已新增“价格规则管理”区域。
- 价格规则管理已作为 `/business-inputs` 的独立页签展示，与录入库存页签互斥显示；新增或编辑价格规则后会留在价格规则页签。
- 价格规则列表已按运营字段展示：规则名称、适用范围、价格类型、改价值、最低价、取整规则、是否启用、优先级和编辑入口。
- 新增价格规则表单已支持：规则名称、品种筛选、等级筛选、平台筛选、价格类型、改价值、最低价、取整规则、取整步长、是否启用、优先级和备注。
- 编辑价格规则表单已支持保存已有规则，并保留原 `rule_id`。
- 表单严格兼容当前 `price_rules.xlsx` 字段：`rule_id / rule_name / variety_filter / grade_filter / platform_filter / pricing_method / markup_value / min_price / rounding_rule / rounding_step / active / priority / remark`。
- 当前 `price_rules.xlsx` 没有 `target_price / absolute_min_price / break_even_price / manual_review_required` 字段，因此本阶段不在 Web 表单中凭空新增这些字段。
- `pricing_method` 已使用中文展示：`fixed_markup` 为固定改价，`percentage_markup` 为百分比改价。
- `rounding_rule` 已使用中文展示：不取整、四舍五入、向上取整、向下取整、按步长向上取整。
- 价格规则适用范围已升级为“品种 / 等级 / 平台”三维筛选，页面将 `*` 显示为“不限制”，Excel 中仍保存为 `*`。
- 品种从 `products.xlsx` 提取；等级从 `* / A / B / C / D / E / 0` 中选择；平台从当前平台选项中选择。SKU 级价格规则本阶段不支持。
- 价格规则的平台选项会优先读取 `platform_mappings.xlsx`；若平台表不可用，则回退到默认平台列表。
- `active` 已在页面显示为“是/否”，保存时仍写入现有可读布尔值，保持任务生成兼容。
- 表单校验已覆盖：规则名称不能为空、改价值必须为非 0 数字、最低价不能为负、取整步长规则、平台/等级/价格类型/取整规则必须来自允许值、优先级必须为非负整数。
- `rounding_rule=step` 时，`rounding_step` 必须大于 `0`。
- 保存后页面提示：“保存的是业务输入数据，若要影响任务中心，请重新生成运行态任务。”
- `/tables` 旧表格编辑入口仍保留为高级兼容入口，适合批量维护和排障。
- 已新增 [price_rule_input_spec.md](price_rule_input_spec.md)，记录价格规则表单字段、低价边界、Excel 兼容和旧入口策略。

低价安全边界：

- 当前表单不接 AI Agent，不开放绕过复核的自动决策入口。
- 当前结构仅支持 `min_price`，因此本阶段校验 `min_price >= 0`。
- 如未来新增 `absolute_min_price / break_even_price / target_price`，必须先扩展 Excel 字段、文档和校验规则，再开放表单录入。

仍留到后续阶段的事项：

- 上下架规则输入表单化。
- 产量预测、价格预测、包装产能、冷库状态输入表单化。
- 更完整的低价规则字段，例如 `absolute_min_price / break_even_price`。
- 当前仍不迁移 Excel 主数据，不接真实平台、真实 RPA 或 AI Agent。

## 19. Web 业务输入重构 Phase 3：上下架规则输入表单化当前进度

本轮已将 `/business-inputs` 中的上下架规则维护，从旧 Excel 表格编辑入口推进为“上下架规则管理”日常运营入口。当前实现仍保存回 `listing_rules.xlsx`，不迁移 Excel 主数据，不新增 SQLite schema，不改变运行态任务、复核、通知、review token 主流程。

已完成能力：

- `/business-inputs` 已新增“上下架规则管理”页签。
- 上下架规则列表已按运营字段展示：规则名称、适用范围、规则策略、库存阈值、是否启用、优先级、备注和编辑入口。
- 新增/编辑上下架规则表单已支持：规则名称、品种筛选、等级筛选、平台筛选、规则策略、库存阈值、是否启用、优先级和备注。
- 上下架规则适用范围采用三维筛选：`variety_filter / grade_filter / platform_filter`。
- `*` 在页面显示为“不限制”，Excel 中仍保存为 `*`。
- 平台选项优先来自 `platform_mappings.xlsx`，读取失败或为空时仍回退默认平台列表。
- 策略枚举为：允许上架、禁止上架、库存低于阈值下架、库存高于阈值允许上架。
- 表单校验已覆盖：规则名称、品种、等级、平台、库存阈值、策略、是否启用和优先级。
- 旧字段 `condition_type / condition_value / action` 不再作为 Web 表单和任务生成主路径。
- `/tables` 旧表格编辑入口仍保留为高级兼容入口。

自动规则评估衔接：

- 已新增保守版 `ListingRuleEvaluator`。
- `dry-run` 只写 `script_runs / script_run_items`，不写业务任务、复核或通知。
- `apply` 通过现有 `RuntimeTaskService / ReviewTaskService / NotificationSender` 链路落成业务结果。
- 第一版在上下架规则建议下架时生成 `manual_review` proposal，不直接生成可执行平台动作。
- 重复 apply 会基于 `dedupe_key` 跳过，不重复生成同一复核。

保持边界：

- 不接真实平台。
- 不接真实 RPA。
- 不接 AI Agent 自动决策。
- 不删除 `/tables`。
- 不绕过运行态服务和通知服务。
- 不引入 React / Vue。

已新增文档：

- [listing_rule_input_spec.md](listing_rule_input_spec.md)

## 20. Web 业务输入重构 Phase 4：包装产能输入表单化当前进度

本轮已将 `/business-inputs` 中的包装产能计划维护，从旧 Excel 表格编辑入口推进为“包装产能计划”日常运营入口。当前实现仍保存回 `capacity_plans.xlsx`，不迁移 Excel 主数据，不新增 SQLite schema，不改变运行态任务、复核、通知或 review token 主流程。

已完成能力：

- `/business-inputs` 已新增“包装产能计划”页签。
- 包装产能计划列表按运营字段展示：业务日期、基础包装产能、临时工人数、单人临时工产能、确认包装能力、是否启用、备注和编辑入口。
- 新增/编辑表单支持：业务日期、基础包装产能、临时工人数、单人临时工产能、确认包装能力、是否启用和备注。
- `confirmed_packing_capacity_qty` 可由页面按“基础产能 + 临时工人数 × 单人临时工产能”自动填入，也允许人工确认修改。
- 同一业务日期不允许存在多条启用的包装产能计划。
- 表单校验已覆盖：日期合法性、非负产能、非负整数临时工人数、确认包装能力和启用状态。
- `/tables` 旧表格编辑入口仍保留为高级兼容入口。

CapacityRuleEvaluator 衔接：

- `CapacityRuleEvaluator` 继续表示“预测产量超过确认包装能力”的预警，不代表真实订单需求超过包装能力。
- evaluator 会读取 `harvest_forecasts.xlsx` 与 `capacity_plans.xlsx`。
- evaluator 按 `trade_date` 查找启用的包装产能计划，并使用 `confirmed_packing_capacity_qty` 作为最终判断口径。
- 缺少产量预测或缺少对应业务日期启用产能计划时，会生成 skipped/warning item，不写业务任务、复核或通知。
- `dry-run` 只写 `script_runs / script_run_items`，`apply` 才通过现有 service 链路落成业务结果。

保持边界：

- 不新增订单表。
- 不新增 SQLite schema。
- 不迁移 Excel 主数据。
- 不接真实平台。
- 不接真实 RPA。
- 不接 AI Agent 自动决策。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。

已新增文档：

- [capacity_plan_input_spec.md](capacity_plan_input_spec.md)

## 21. Web 业务输入重构 Phase 5：冷库状态输入表单化当前进度

本轮已将 `/business-inputs` 中的冷库状态维护，从旧 Excel 表格编辑入口推进为“冷库状态”日常运营入口。当前实现仍保存回 `cold_storage_status.xlsx`，不迁移 Excel 主数据，不新增 SQLite schema，不改变运行态任务、复核、通知或 review token 主流程。

已完成能力：

- `/business-inputs` 已新增“冷库状态”页签。
- 冷库状态列表按运营字段展示：业务日期、冷库总容量、当前占用量、预计入库量、预计出库量、预计占用量、剩余容量、预警阈值、是否启用、备注和编辑入口。
- 新增/编辑表单支持：业务日期、冷库总容量、当前占用量、预计入库量、预计出库量、预警阈值、预计占用量、剩余容量、是否启用和备注。
- 页面会按“当前占用量 + 预计入库量 - 预计出库量”自动计算预计占用量，并按“冷库总容量 - 预计占用量”自动计算剩余容量，也允许运营人员人工确认修改。
- 同一业务日期不允许存在多条启用的冷库状态。
- 表单校验已覆盖：日期合法性、冷库总容量大于 0、各类数量非负、启用状态和重复启用日期。
- `/tables` 旧表格编辑入口仍保留为高级兼容入口。

ColdStorageEvaluator 衔接：

- `ColdStorageEvaluator` 继续表示“预计冷库占用超过容量或剩余容量低于阈值”的预警，不代表真实订单需求或平台销售数据。
- evaluator 会读取 `cold_storage_status.xlsx`。
- evaluator 按 `trade_date` 查找启用的冷库状态，并使用 `projected_occupied_qty / remaining_capacity_qty / total_capacity_qty / warning_threshold_qty` 作为判断口径。
- `projected_occupied_qty > total_capacity_qty` 时生成 critical 冷库超容复核 proposal。
- `remaining_capacity_qty <= warning_threshold_qty` 时生成 warning 冷库容量预警 proposal。
- 缺少对应业务日期启用冷库状态时，会生成 skipped/warning item，不写业务任务、复核或通知。
- `dry-run` 只写 `script_runs / script_run_items`，`apply` 才通过现有 service 链路落成业务结果。

保持边界：

- 不新增订单表。
- 不新增冷库流水系统。
- 不新增 SQLite schema。
- 不迁移 Excel 主数据。
- 不接真实平台。
- 不接真实 RPA。
- 不接 AI Agent 自动决策。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。

已新增文档：

- [cold_storage_input_spec.md](cold_storage_input_spec.md)

## 22. 任务中心增强：Mock 平台测试台当前进度

本轮已在任务中心补充 Mock 平台测试台入口，用于本地验证“运行态任务 -> 模拟平台执行 -> 执行日志 -> 平台状态同步复核”的闭环。该入口是测试台，不是真实平台管理后台。

已完成能力：

- `/tasks` 增加二级分页“Mock 平台测试台”。
- 路由：`/tasks?task_tab=mock_platform`。
- 页面只读展示 `mock_platform.sqlite3` 中的模拟平台状态。
- 展示字段包括：平台名称、内部 SKU、平台 SKU、商品名称/品种、等级、平台价格、平台上下架状态、平台库存、最后同步时间、最后平台更新时间和最后错误。
- 页面不提供执行按钮；Mock 平台执行仍通过 CLI `scripts/run_mock_platform_executor.py` 完成。
- 页面不展示 secret、token、webhook、完整 mobile review URL 或完整本地路径。

保持边界：

- 不接真实平台。
- 不接真实 RPA。
- 不把 Mock 平台库存写回商品公共库存。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不新增主导航项。

相关文档：

- [mock_platform_sync_lab.md](mock_platform_sync_lab.md)
