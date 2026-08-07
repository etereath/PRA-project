# AI Agent 接入边界规范

> 状态：合同冻结，尚未授权实现
>
> Review Profile：未来实际接入必须独立按 R4 评审
>
> 适用范围：整个 PRA 项目，不限于任务 13.5-7

## 1. 目的与当前边界

未来 Agent 可以成为与 Web、Automation 并列的业务调用方，但不能成为新的业务控制面、
数据库入口或平台执行器。本规范只冻结唯一接入边界，防止后续再次开发直连 Web、CLI、
数据库、Queue 或平台的旁路。

任务 13.5-7 不实现 Agent，不新增 Agent Schema、状态、队列、审批策略或平台动作。任务 14
只负责既有闭环的综合验收，也不承担 Agent 实现。任何实际 Agent 接入都必须另开独立 R4。

## 2. 唯一读取与写入通道

```text
读取：Agent → Agent Query Adapter → 权威 Query Service / Read Model

写入：Agent → Agent Task Adapter → 结构化 AgentIntent
                         ↓
       既有权威 Application/Domain Service 与确定性规则
                         ↓
             拒绝 / Review / Runtime Task / Outbox

执行：有效且已授权的 Runtime Task → v4/v5 Queue → Worker → Importer
```

### 2.1 读取约束

- Agent 只能通过 Agent Query Adapter 调用权威 Query Service 或 Read Model。
- Agent 不得抓取 Web HTML，不得把 SQLite、Excel、Queue 文件或平台页面当作业务接口。
- 查询结果必须保留数据时间、质量、完整性和权限边界；模型解释不能改变事实状态。

### 2.2 写入约束

- Agent 唯一写入口是 Agent Task Adapter，且只允许提交结构化 `AgentIntent`。
- Agent 不得直接调用 Review、Notification、Runtime Repository、CLI、Web Route、平台
  Adapter、ShadowBot 或 COMMIT，也不得自行生成 Queue JSON。
- Review、Runtime Task、Outbox 和通知是否产生，只能由既有权威服务与确定性规则决定。
- Agent 永远不得伪造 `SYSTEM_EMERGENCY`；该来源只属于 13.5-6 的专用授权服务。

本规范中的“Task Application Service”是逻辑边界，不是新模块名称。它统称现有
`RuntimeTaskService`、任务生成、规则校验以及其他承担相关职责的权威
Application/Domain Service；不得据此新增万能 `TaskApplicationService` 或平行状态机。

## 3. `AgentIntent` 是逻辑载荷

`AgentIntent`（或讨论中的 `AgentProposal`）只表示 Agent Task Adapter 边界上的结构化
逻辑载荷，不是已批准的数据库表，也不是可执行任务。

首版固定流程为：

```text
结构化 AgentIntent
→ 身份、范围、参数、业务规则和授权校验
→ 拒绝 / 形成人工 Review / 生成 Runtime Task
```

- 未形成 Task 或 Review 的建议可以直接作为调用结果返回，不要求为了“proposal”概念持久化。
- 当前不批准 `agent_proposals` 表、proposal 状态机或长期 staging 区。
- 如果真实运行以后证明需要跨会话保存未物化建议，必须另开 R4，说明保留期限、清理方式、
  幂等、敏感数据、审计需求和无法复用现有结构的证据，再评审最小 Schema。

## 4. 真实平台副作用与人工授权

任何 Agent 来源的以下任务均不得直接成为可执行 `PENDING`：

```text
AGENT + UPDATE_PRICE
AGENT + SET_ONLINE
AGENT + SET_OFFLINE
→ 人工 Review
→ 显式授权
→ 才可进入既有执行链
```

“低风险可直接 PENDING”只适用于对真实平台零副作用的任务。不得仅凭模型置信度、价格
变化较小、商品数量较少或规则判断为低风险，就跳过真实平台写操作的人工 Review。

未来若希望 Agent 自主改价或执行其他真实平台写操作，必须另开独立 R4，至少评审：

- 版本化审批与撤销策略；
- v4/v5 对 `AGENT` 来源的显式门禁；
- 平台、账号、商品、动作、价格和有效期范围；
- 人工接管、状态漂移、重放、越权和撤销测试；
- 与 `SYSTEM_EMERGENCY` 专用来源的严格隔离。

## 5. 身份与审计

未来正式来源预留为：

- `origin_type=AGENT`；
- `origin_ref_id=agent-run:<stable-run-id>`；
- 版本化审批策略引用。

当前 Schema 尚未支持 `AGENT`，在独立评审和必要的最小迁移完成前，不得把 Agent 冒充为
`MANUAL` 或 `AUTOMATION` 落库。若 Agent 由 Automation 触发，应另保留父
`automation-run:<run_id>` 关联，但业务来源仍是 `AGENT`。

审计目标是让一次意图、Review、任务、执行和结果可追溯，不是给每张表预先添加 Agent
专属字段。实现时优先复用：

- `origin_type` / `origin_ref_id`；
- `changed_by`；
- `resolved_by`；
- 结构化 metadata 或 event payload；
- 当前任务、Review、事件、执行和通知审计链。

只有出现现有字段无法表达的具体追踪缺口，才允许在独立迁移中增加最小字段。

## 6. 未来独立 R4 的评审清单

任何实际 Agent 接入不属于 13.5-7B～7F，也不属于任务 14。未来 R4 至少需要分别审查：

1. `AGENT` 枚举和必要的最小 Schema 迁移；
2. Agent 身份、服务账号、密钥轮换与权限范围；
3. Agent Query Adapter 和 Agent Task Adapter 的生产实现；
4. 人工审批与版本化授权策略；
5. 是否确有未物化 proposal 的持久化需求；
6. 任何自主真实平台写权限及其 v4/v5 门禁；
7. 数据最小化、提示注入、越权、重放、撤销和审计测试。

上述项目必须依据当时的真实业务需求逐项批准。本规范仅固定通道和禁止项，不构成实现、
Schema 或自主权限的预批准。

## 7. 冻结结论

1. Agent 读取只走 Agent Query Adapter。
2. Agent 写入只提交结构化 `AgentIntent` 给 Agent Task Adapter。
3. Review、Task 和 Outbox/通知由既有确定性服务派生，Agent 无直接写能力。
4. `AgentIntent` / `AgentProposal` 是逻辑载荷，不是已批准的 Runtime 表。
5. Agent 来源的真实平台改价、上架和下架必须先经人工 Review 和显式授权。
6. “Task Application Service”只是既有权威服务的逻辑统称，不授权新增万能服务。
7. 审计优先复用现有来源、操作者和事件链，不预先给每张表增加 Agent 字段。
8. Agent 不得伪造 `SYSTEM_EMERGENCY`，不得绕过 v4/v5 Queue、Worker 和 Importer。
9. 任何实际 Agent 接入都是未来独立 R4，不属于 13.5-7B～7F 或任务 14。
