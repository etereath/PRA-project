# Web 前端整体更新计划：PRA 运行态运营后台

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

- `/` 或 `/dashboard`
- `/tasks`
- `/reviews`
- `/notifications`
- `/execution-logs`
- `/business-inputs`
- `/system`

现有 `/runtime` 可在过渡期保留，但主导航不再把它作为唯一运行态入口，而是逐步拆分为任务、复核、通知、历史等更清晰的页面。

## 4. 旧页面迁移方案

### 4.1 任务面板

当前定位：Excel 输入校验、任务预览、任务导出。

迁移后定位：

- 移入 `Business Inputs` 或 `Tasks` 的“任务生成”区域
- 保留“校验输入 / 预览任务 / 生成运行态任务”的能力
- 页面文案从“Excel 数据校验页面”改为“基于业务输入生成运行态任务”

建议处理：

- 旧 `/` 任务面板改为 Dashboard
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
- 后续主导航不再突出 `/runtime`

## 5. 页面规划

### 5.1 Dashboard 首页总览

目标：让运营人员打开后台后立即知道系统今天是否健康。

建议展示：

- 今日或当前 `trade_date`
- pending 任务数
- failed 任务数
- expired 任务数
- pending 复核数
- 即将超时复核数
- 飞书通知成功/失败数
- 最近一次任务生成时间
- 最近一次通知发送结果
- Mobile Review 链路状态摘要

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
- `/health` 状态

操作：

- 运行环境检查
- 查看配置缺失项
- 查看当前通知模式

安全要求：

- 所有 secret 只显示“已配置 / 未配置”
- URL 只显示脱敏摘要，例如域名和末尾 4 位
- 不允许在 Web 表单里编辑真实 secret

## 6. 视觉与交互优化

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

交付：

- 新导航
- Dashboard 空壳或基础摘要
- Tasks / Reviews / Notifications 基础页面
- `/manual-intervention` 标注为旧只读兼容
- `/runtime` 标注为旧聚合兼容入口

验收：

- 原有 Web 功能不回退
- 主导航不再以 Excel 原型为中心
- 旧入口仍可访问但不再作为主处理入口

### Phase 2：首页 Dashboard

目标：

- 提供运营总览
- 快速暴露待处理、失败、过期和通知状态

交付：

- 任务状态计数卡片
- 复核状态计数卡片
- 通知状态计数卡片
- 即将超时提示
- 飞书 / Mobile Review 链路状态摘要

验收：

- 可一眼看到 pending / failed / expired 状态
- 可跳转到对应筛选列表
- 不展示任何 secret 或 raw token

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

### 数据展示

- tasks 列表字段完整
- review_tasks 列表字段完整
- notification_logs 列表字段完整
- task_status_history 可从任务详情查看
- execution_logs 可从任务详情或执行日志页查看
- review_tokens 只展示摘要，不展示 raw token

### 操作

- Web session 登录后可处理 pending review
- 未登录不能处理 review
- 非 pending review 不显示处理按钮
- 通知失败能在通知中心高亮
- 即将超时复核能高亮

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

