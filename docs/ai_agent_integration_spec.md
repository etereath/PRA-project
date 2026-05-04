# AI Agent Integration Specification

## 1. 文档目的

本文档用于定义未来 AI Agent 接入当前项目时的系统边界、数据结构、审批流程和审计要求。

当前项目已经完成：

- SQLite 运行态任务系统
- 人工复核闭环 MVP
- `notification_logs` 接入 review 主流程 MVP
- 运行态运营闭环增强第一版

未来 AI Agent 可能承担的职责包括：

- 录入预测数据
- 生成 `recommended_price`
- 生成任务建议
- 触发复核
- 协助执行流程

但当前阶段：

- 只定义接入规范
- 不改代码
- 不接真实 AI Agent
- 不引入真实模型推理链路

---

## 2. 核心定位

### 2.1 AI Agent 是系统内的特殊 actor

AI Agent 不是一个绕过系统的“特殊模块”，而是运行态系统中的一种 actor。

建议统一抽象：

- `actor_type = human`
- `actor_type = ai_agent`
- `actor_type = system`
- `actor_type = rpa_executor`

其中：

- `human`：人工运营、人工复核、人工确认
- `ai_agent`：AI 生成建议、预测、任务草案、复核触发建议
- `system`：定时任务、状态扫描、自动过期、规则校验器
- `rpa_executor`：未来真实执行器、机器人、平台代理执行层

后续所有运行态日志、状态变化、proposal、review、notification 都应能记录 actor 身份及类型，而不是只记录一个自由文本的操作人。

### 2.2 AI Agent 不应绕过现有运行态系统

AI Agent 与现有运行态系统的关系应明确为：

- 可以生成 `review_tasks`
- 可以作为 `notification_logs` 的触发来源
- 可以生成 task proposal
- 可以生成预测数据 proposal
- 不能绕过 `ReviewTaskService`
- 不能绕过 `RuntimeTaskService`
- 不能绕过 `NotificationSender`
- 不能直接写 SQLite 表

AI Agent 的所有动作都必须经过已有运行态服务边界，保证：

- 规则校验一致
- 状态流转一致
- 审计记录一致
- 人工复核边界一致

---

## 3. AI 接入原则

### 3.1 AI 不能直接修改主数据

AI Agent 不应直接修改以下内容：

- Excel 主数据
- 价格规则
- 上下架规则
- 最低价体系
- 禁售状态
- 真实平台状态

AI 生成的内容应先进入：

- `proposal`
- `draft`
- `staging`

而不是直接写入事实表或直接触发高风险执行。

### 3.2 AI 不能直接执行高风险任务

AI Agent 可以提出建议，但不能自己完成高风险动作。

高风险动作必须人工复核，包括但不限于：

1. 低于 `break_even_price` 的价格
2. 低于 `absolute_min_price` 的价格
3. `sale_enabled = false` 时尝试上架
4. 超过 `confirmed_packing_capacity_qty` 的上架量
5. 修改规则、最低价、禁售状态
6. 执行真实平台动作

其中：

- 低于 `absolute_min_price` 的价格应直接禁止，不进入自动执行
- AI 可以生成这类 proposal，但系统必须阻断直接执行路径
- 低于 `absolute_min_price` 的 AI 价格 proposal 应标记为 `blocked`
- 这类 proposal 不应生成可执行 task，也不应生成可被批准后放行执行的 `review_task`
- 如业务上需要留痕，最多只生成“风险说明型 review_task”，用于提示或审计，而不是进入可放行执行路径

### 3.3 AI 不能自己审批自己

AI Agent 不能审批自己生成的高风险 proposal 或高风险任务。

明确要求：

- AI 生成高风险 proposal 后，必须进入 `review_task`
- 审批人必须是 `human`
- 后续若引入多 Agent 协作，也不应允许“同一 agent_run_id 自审”

---

## 4. 建议预留的数据结构

## 4.1 `agent_proposals`

建议未来新增 `agent_proposals`，用于承接 AI 输出但尚未成为正式任务或正式主数据变更的内容。

建议字段：

- `proposal_id`
- `proposal_type`
- `trade_date`
- `scope_type`
- `scope_key`
- `agent_id`
- `agent_version`
- `agent_run_id`
- `input_snapshot_json`
- `proposed_changes_json`
- `validation_result_json`
- `decision_trace_json`
- `confidence`
- `risk_level`
- `status`
- `created_at`
- `reviewed_by`
- `reviewed_at`
- `review_note`
- `applied_task_id`

字段职责说明：

- `proposal_type`：区分预测录入、价格建议、任务建议、规则建议等类型
- `scope_type + scope_key`：保持与当前运行态系统一致的作用范围表达
- `input_snapshot_json`：保存 proposal 生成时的输入快照
- `proposed_changes_json`：保存 AI 输出的结构化建议
- `validation_result_json`：保存 deterministic rule validation 的结构化结果，例如命中规则、是否需要人工复核、是否 blocked、是否可转 `pending task`
- `decision_trace_json`：保存 AI 的推理摘要、规则命中、理由说明
- `confidence`：AI 对建议的置信度
- `risk_level`：系统或 AI 评估的风险等级
- `status`：proposal 生命周期状态
- `applied_task_id`：若 proposal 最终转成正式 task，可回连到任务

### 4.2 proposal 状态建议

当前阶段只定义建议，不要求立即实现。

建议 `agent_proposals.status` 包括：

- `draft`
- `pending_validation`
- `validated`
- `review_required`
- `approved`
- `rejected`
- `blocked`
- `applied`
- `cancelled`
- `expired`

其中：

- `blocked` 表示命中硬性禁止规则，不能通过人工复核合法化

---

## 5. AI 生成任务建议的标准流程

AI 生成任务建议时，推荐统一走以下流程：

`AI proposal`
-> `deterministic rule validation`
-> `low risk 可转 pending task`
-> `high risk 转 review_task`
-> `人工确认后再转 pending`

### 5.1 低风险路径

如果 proposal 满足以下条件：

- 没有触发高风险规则
- 未突破最低价、禁售、产能、范围等硬约束
- 能通过确定性规则校验

则可以：

- 先由系统做 deterministic validation
- 再转为正式 `pending task`

### 5.2 高风险路径

如果 proposal 触发高风险条件：

- 不直接转为可执行 task
- 必须转成 `review_task`
- 必须由人工确认
- 确认后再通过 `RuntimeTaskService` 进入正式任务状态

### 5.3 禁止路径

如果 proposal 命中绝对禁止条件，例如：

- 低于 `absolute_min_price`
- 违反 `sale_enabled=false`
- 违反规则边界且不可通过人工确认合法化

则应：

- 优先标记为 `blocked`
- 可附带 `review_task` 说明风险，但该 review_task 只能用于风险提示、人工知情或审计留痕
- 不应生成可被批准后继续执行的 review 流程
- 不进入正式任务执行链路

---

## 6. 审计与追踪要求

所有 AI 动作必须保留可审计记录。

至少应记录：

- `agent_id`
- `agent_version`
- `agent_run_id`
- `model_name`
- `model_version`
- `prompt_version`
- `input_snapshot_json`
- `decision_trace_json`
- `confidence`
- `risk_level`

说明：

- `model_name / model_version / prompt_version` 如适用则记录；若当前 agent 不暴露这些字段，也应保留为空位
- `decision_trace_json` 应保存简洁、结构化、可回看信息，而不是不可控的长文本堆叠
- `input_snapshot_json` 应尽量可复现 proposal 生成上下文
- `input_snapshot_json` 只应保存必要业务上下文，不应写入账号、密码、平台 token、客户隐私、资金信息等敏感数据

### 6.1 批量追踪与回滚

为未来批量回滚和批次审计，应预留：

- `agent_run_id`
- `batch_id`

要求：

- 同一次 AI 运行生成的 `proposals / tasks / review_tasks / notifications`
- 应能追踪到同一个 `agent_run_id`
- 若同一轮运行包含多个分批动作，可再细分 `batch_id`

这会直接影响未来：

- 批量审查
- 批量回滚
- 问题定位
- 责任归因

---

## 7. 身份与安全边界

### 7.1 AI 不应使用人类管理员 session

AI Agent 不应复用人类管理员的 Web session，也不应伪装为普通人工操作人。

未来应采用：

- 独立 agent identity
- service account
- API key

并限制：

- `allowed_actions`
- `allowed_scopes`

例如：

- 只允许生成 proposal
- 只允许生成 review 建议
- 只允许处理低风险 task draft
- 不允许修改规则和最低价

### 7.2 reviewer_code 不是 AI 身份机制

当前运行态系统里存在 `reviewer_code` 过渡字段，但它不应扩展为 AI 身份凭据。

未来 AI 身份与移动端复核身份应分别通过：

- agent identity / service account
- `review_token`

来处理，而不是混用人工复核码。

---

## 8. AI Agent 与现有运行态系统的映射关系

未来 AI 接入时，建议按下述映射关系落地：

### 8.1 可以做的事

- 生成 `agent_proposals`
- 生成预测值草案
- 生成 `recommended_price` 草案
- 生成 task proposal
- 触发 `review_task` 创建建议
- 触发通知来源事件

### 8.2 不能直接做的事

- 不能直接修改 SQLite 主表
- 不能直接修改 Excel 主数据
- 不能直接推进任务状态
- 不能直接关闭人工复核
- 不能直接调用真实平台执行
- 不能绕过 `ReviewTaskService / RuntimeTaskService / NotificationSender`

### 8.3 推荐接入顺序

建议未来分阶段接入：

1. 先接入 AI proposal 生成
2. 再接 deterministic validation
3. 再接 review_task 自动触发
4. 再接低风险 proposal -> pending task
5. 最后才考虑真实执行协助

---

## 9. 对当前运行态系统的改造建议

当前阶段不改代码，但未来实现时建议优先考虑以下对象或字段扩展：

### 9.1 建议补充的统一身份字段

- `actor_type`
- `actor_id`
- `agent_id`
- `agent_version`
- `agent_run_id`
- `batch_id`

### 9.2 建议补充的 proposal 层对象

- `agent_proposals`

### 9.3 建议补充的服务边界

- `AgentProposalService`
- `ProposalValidationService`
- `AgentAuditService`

### 9.4 建议补充的通知来源字段

未来 `notification_logs` 可考虑补充：

- `trigger_source_type`
- `trigger_source_id`
- `agent_run_id`

用于明确通知是由：

- human
- ai_agent
- system
- rpa_executor

中的哪一种来源触发。

---

## 10. 当前阶段结论

当前阶段可以先明确以下原则，作为后续 AI 接入的硬边界：

1. AI Agent 是特殊 actor，不是系统外特权模块
2. AI Agent 先产出 proposal，不直接改主数据，不直接做高风险执行
3. 高风险动作必须人工复核
4. AI 不能自审自己生成的高风险任务
5. 所有 AI 动作必须可追踪、可审计、可批量回溯
6. AI 未来必须通过独立 agent identity 或 service account 接入
7. AI 不得绕过现有运行态服务边界和状态流转规则

这意味着，未来 AI Agent 应被视为：

`运行态系统中的受控建议生成者与流程参与者`

而不是：

`可直接改数据、改价格、改状态、直接执行的平台超级用户`
