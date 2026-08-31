# 任务 13.5-7G 统一任务执行协调与运营队列闭环计划

更新时间：2026-09-01

Review Profile：R4

状态：Draft，编码前合同评审
基线：GitHub `main` 提交 `31cfbc2`（PR #37 合并点）

本计划 PR 只承载故障证据与合同。故障审查发生在 `codex/task13-5-7f-runtime-master-data`
工作树 `d30e3d7` 及其当时未提交整改上；`main@31cfbc2` 尚不包含全部被审查代码。7G-1
开始编码前必须先把仍属 7F 的合法实现单独审查并合并到最新 `main`，然后重新执行字段级
复用审计。不得从脏工作树复制业务代码，也不得在缺少实际故障路径的旧基线上提前重构。

## 1. 任务定位

13.5-7G 是 7F 真实运营验收后新增的独立整改任务。它不继续堆叠局部 Web 修复，而是解决
Runtime Task、执行授权、Review/RECONCILE、ShadowBot Queue/Worker/Importer 和 Automation
之间缺少统一生命周期所有权的问题。

故障证据以
[Automation 与任务队列故障分析报告](../reports/task13_5_7f_automation_queue_failure_analysis_20260831.md)
为准。当前 `/management/queue` 只能聚合显示 Task、文件 Queue、Operation/Write Lock 和结果，
尚不是能够推进业务任务的队列控制面。

本计划冻结以下结论：

1. Runtime Task 继续表达“要做什么”，不直接承担全部执行阶段；
2. 新增唯一的持久化派发语义，表达“本次准备如何执行、当前停在哪里、下一步是什么”；
3. `TaskExecutionCoordinator` 是平台写任务从授权到终态的唯一顶层编排者；
4. Web、Automation 和未来 Agent 只能提交明确 Task/Intent，不能各建一套执行状态机；
5. v4/v5、写锁、`UNKNOWN`、唯一 `RECONCILE`、文件 Queue、Worker、Importer 和 Outbox 原样复用；
6. 禁止增加扫描全部普通 `pending` 并自动执行的平台写 Worker；
7. 7G 不能削弱既有 fail-closed 门禁，也不能借重构执行未经授权的真实平台动作。

## 2. 目标与非目标

### 2.1 必须达到的目标

- 每个非终态平台写 Task 都能回答：当前阶段、阻塞原因、下一步、授权是否有效；
- Web 最终确认后即产生持久化执行意图，HTTP 请求或 Web 重启不会丢失恢复点；
- 写锁或 Review 解除后，受影响的明确任务会被重新评估；
- 授权已过期时只能重新预览和确认，不得自动沿用旧授权；
- Queue/Worker/Importer 故障不会造成重复平台写，也不会让 Task 永久停在无语义 `pending`；
- Web“当前任务”成为运营工作中心，而不是多个账本的只读拼接；
- Automation 的平台写 Task 进入同一协调边界；只读 Job/Run 保持现有直接 Handler 模型；
- 顶层 Task 状态与 `task_status_history` 由统一入口推进；
- 系统页能区分“进程存活”和“业务链实际可执行”。

### 2.2 明确不做

- 不实现第二平台；
- 不新增平台动作或 ShadowBot Queue 协议；
- 不重写 v4 改价或 v5 上下架页面执行逻辑；
- 不自动执行所有普通 `pending` Task；
- 不开放 Agent 自动定价或自动审批；
- 不改变订单、日结、库存权威和 18:00/20:00 业务定义；
- 不放宽 `SYSTEM_EMERGENCY` 的策略、开关、Review、二次观察或授权门禁；
- 不在普通 Web Route 中运行常驻循环、直接操作 Queue 或启动 Worker；
- 不以增加提示文字代替持久化状态和恢复机制。

## 3. 权威业务链

```text
任务来源
├─ Web 人工操作
├─ Automation 确定性规则
└─ 未来 AgentIntent
        ↓
权威 Task Application Service
        ↓
Runtime Task：保存“要做什么”
        ↓
Dispatch Attempt：保存“本次如何执行”
        ↓
TaskExecutionCoordinator
├─ 授权与最新事实重检
├─ 阻塞、解除和过期
├─ 优先级与精确 Task 选择
└─ 派发与结果收口
        ↓
既有 v4 改价 / v5 上下架 Pipeline
        ↓
ShadowBot Queue → Worker → Result Importer → Archive
        ↓
Task / History / Operation / Review / Outbox 一致终态
```

人工操作在页面上仍保持“预览 → 一次最终确认 → 立即开始处理”。后端必须先把 Runtime Task、
逐项目标值、授权事实和初始 Dispatch Attempt 原子持久化，再立即唤醒 Coordinator；不能再依赖
同一个 HTTP 请求从创建一直同步执行到 Queue 发布完成。

## 4. 状态合同

### 4.1 Runtime Task 状态

Runtime Task 继续使用现有高层业务结果：

- `pending`：业务意图有效但尚未进入最终业务结果；
- `running`：至少一个有效 Dispatch Attempt 已进入执行链；
- `success`；
- `failed`；
- `manual_review`；
- `cancelled`；
- `expired`；
- `skipped`。

`pending` 不再用于解释具体卡点。卡点必须从当前有效 Dispatch Attempt 读取。

### 4.2 Dispatch Attempt 阶段

建议冻结为：

| 阶段 | 含义 | 允许的下一步 |
| --- | --- | --- |
| `AWAITING_CONFIRMATION` | 任务已预览但尚无最终授权 | 确认或取消 |
| `READY` | 授权和当前事实有效 | 派发 |
| `BLOCKED` | 存在明确 blocker | 等待事件后重新评估、取消或过期 |
| `QUEUED` | 请求已原子发布到文件 Queue | Worker 领取 |
| `RUNNING` | Worker 已领取 | 等待结果 |
| `RESULT_PENDING` | 结果已落盘 | Importer 导入 |
| `NEEDS_REVIEW` | 结果未知或需人工判断 | Review/RECONCILE |
| `SUCCEEDED` | 本次派发成功 | 终态 |
| `FAILED` | 本次派发确定失败 | 重新授权新 Attempt 或终止 Task |
| `CANCELLED` | 派发前或安全停止后取消 | 终态 |
| `EXPIRED` | 授权/任务截止时间已过 | 重新预览生成新 Attempt |

阶段名称和是否需要独立 `AWAITING_CONFIRMATION` 行由 7G-1 Schema 评审最终确认，但必须保留
“业务 Task 状态”和“派发阶段”两个维度，不能继续扩张 `pending` 的含义。

### 4.3 当前有效 Attempt

- 一个 Task 同一时刻最多有一个非终态 Dispatch Attempt；
- 重新确认、确定失败后的重试或过期后重建必须创建 successor Attempt，不覆写旧证据；
- successor 必须记录 `supersedes_dispatch_attempt_id`；
- 已进入 `QUEUED/RUNNING/RESULT_PENDING/NEEDS_REVIEW` 的 Attempt 不得被普通重试替代；
- `UNKNOWN` 只能通过唯一 RECONCILE 或人工确认收口，不能自动再写平台。

## 5. 最小持久化合同

7G-1 编码前必须先对现有 `tasks`、`task_status_history`、`retry_authorizations`、
`shadowbot_commit_batches/items`、`shadowbot_listing_action_*`、`shadowbot_operations`、
`shadowbot_write_locks` 和 `execution_attempts` 做字段级复用审计。

如果现有结构不能表达以下信息，允许新增最小的 `task_dispatch_attempts` 及 append-only 事件/阻塞
结构；不得复制已有平台执行账本：

- `dispatch_attempt_id`；
- 精确 `task_id` 与可选父批次；
- `stage` 与乐观并发版本；
- 来源类型和来源引用；
- 授权主体、摘要、创建时间和过期时间；
- `blocker_type`、`blocker_ref`、首次/最近阻塞时间；
- v4/v5 准备批次、Queue Request、Operation 和 Execution Attempt 引用；
- successor/supersedes 关系；
- 失败码、终态原因和完整时间轴。

Schema 预算：

- 新增平台合同：0；
- 新增文件 Queue：0；
- 新增全局锁：0；
- 新增平台动作：0；
- 新增协调持久化表：目标 1～3 张，必须由字段级复用审计证明；
- 不得为了少建表把 blocker/event JSON 塞入 Task 备注或覆盖旧状态。

## 6. Blocker 与恢复合同

### 6.1 Blocker 分类

至少支持：

| Blocker | 解除证据 | 解除后的确定性动作 |
| --- | --- | --- |
| `WRITE_LOCK_UNKNOWN` | 关联 Operation 已人工/RECONCILE 收口 | 重新校验授权有效期和平台事实 |
| `REVIEW_PENDING` | 关联 Review 已产生结果 | 按 Review 结果继续、替代或取消 |
| `AUTHORIZATION_EXPIRED` | 新预览与新确认 | 创建 successor Attempt |
| `WORKER_UNAVAILABLE` | Worker 恢复程序验证新鲜心跳 | 未发布请求可继续派发 |
| `QUEUE_PUBLISH_FAILED` | 同幂等请求安全写入成功 | 进入 `QUEUED` |
| `RESULT_IMPORT_FAILED` | 同一结果幂等导入成功 | 根据原结果收口，不重写平台 |
| `HANDLER_UNAVAILABLE` | enabled Job 已有有效 Handler | 恢复对应 Run/任务处理 |
| `PLATFORM_GLOBAL_FENCE` | 全局平台 UI 栅栏解除 | 重新评估受影响的明确 Attempt |

扫描质量低或扫描过期对于人工任务属于**最终确认警告**，不是默认硬 blocker。人工确认必须绑定
当时显示的质量摘要；确认后仍要检查写锁、Review、身份、成本、Task 有效期等硬门禁。

### 6.2 唤醒原则

- 不扫描全部 `pending`；
- blocker 建立时必须保存明确 `dispatch_attempt_id + blocker_ref`；
- Review、Operation、Worker、Queue 或 Handler 状态变化后发布内部领域事件；
- Coordinator 只重新评估被该事件明确引用的 Attempt；
- 服务重启时可以扫描非终态 Dispatch Attempt 做恢复，但只能恢复其已持久化阶段，不得把所有
  Runtime `pending` 解释为执行授权；
- 授权过期后只能进入 `EXPIRED/AWAITING_CONFIRMATION`，不能自动执行。

## 7. 来源通道合同

### 7.1 Web 人工任务

1. 按品种、等级、平台展开逐项目标；
2. 预览列表允许逐项填写价格或平台目标库存；
3. 低扫描质量只在最终确认弹窗警告；
4. 最终确认事务写 Task、授权事实与 Dispatch Attempt；
5. 每个 SKU/平台/动作形成独立可阻塞 Attempt，批次只作父分组；
6. 确认成功后立即请求 Coordinator 处理，但响应不依赖平台执行完成；
7. 页面跳转到工作中心并持续展示真实阶段。

数据库库存表示实际仍可销售的花材，平台目标库存只表示买家在该平台最多可购买的数量；两者
不是一一对应关系。上架目标库存可以高于数据库库存，各平台目标库存也不得相加后直接判断
超售。Coordinator 只能复用库存销售扣减、安全余量和既有业务准入规则，不能新增“平台目标
库存不得超过真实库存”的错误硬门禁。

### 7.2 Automation

- READ_ONLY 扫描、订单读取、日结等继续使用现有 Job/Run/Handler，不绕入平台写协调器；
- 普通规则生成的改价/上下架 Task 只形成运营待办，不能自动派发；
- 只有独立版本化政策明确授权的平台写任务才可创建 Dispatch Attempt；
- 当前唯一可能的自动平台写仍是已完成全部 13.5-6 门禁且管理员显式启用后的
  `SYSTEM_EMERGENCY`；默认保持 `automatic_emergency_offline=false`；
- Automation 必须提交明确 Task ID、Run ID、政策版本和授权引用。

### 7.3 未来 Agent

Agent 继续遵守项目级 Agent Gateway：只提交结构化 `AgentIntent`，由确定性服务形成 Task/Review。
Agent 不直接创建 Dispatch Attempt、不调用 Coordinator、不拼 Queue JSON、不调用 v4/v5，也不能
伪造 `SYSTEM_EMERGENCY`。

### 7.4 CLI

CLI 只保留开发测试、诊断、备份和受控恢复入口。生产恢复 CLI 必须调用同一 Coordinator 或
Repository 事务，不能成为第二个派发状态机。

## 8. 优先级与批次隔离

建议固定优先级：

1. 人工复核产生的紧急改价/下架；
2. 复核超时且政策允许的紧急保护；
3. 普通人工平台任务；
4. 明确授权的自动平台任务；
5. 只读扫描和后台维护。

插队只影响尚未取得平台副作用栅栏的请求，不能绕过：

- 同商品活动写锁；
- `UNKNOWN/RECONCILE`；
- 精确 Task 授权；
- 平台和商品身份唯一性；
- Worker 单窗口严格串行。

批量任务必须拆为逐项 Dispatch Attempt。一个 SKU 的事实或写锁问题默认只阻塞该项；只有真实
平台全局栅栏、单 Worker 活动请求或同平台无法并行的执行窗口才能阻塞其他项。

## 9. Coordinator 职责边界

`TaskExecutionCoordinator` 只负责顶层编排：

- 接受精确 Task/Attempt；
- 调用现有执行授权重检；
- 建立、更新和解除 blocker；
- 检查授权与 Task 截止时间；
- 选择 v4 或 v5；
- 记录 Queue 发布引用；
- 接收 Worker/Importer/Review/RECONCILE 事件；
- 统一推进 Runtime Task 和 History；
- 生成通知与运营下一步。

它不得：

- 包含蚂蚁花团页面选择器；
- 自己实现改价或上下架；
- 绕过 v4/v5 Publisher；
- 直接控制影刀窗口；
- 替代 Automation Scheduler；
- 读取 Web HTML；
- 持有未落库的长期状态。

## 10. Web 工作中心合同

`/management/queue` 调整为统一工作中心。每一项必须直接显示：

- 业务动作和商品范围；
- Runtime Task 状态；
- 当前 Dispatch 阶段；
- 阻塞原因和发生时间；
- 授权截止时间；
- 系统正在等待什么；
- 运营者需要做什么；
- 最近一次执行/复核结果。

只显示当前状态允许的动作：

- 最终确认；
- 重新预览并确认；
- 重新发送安全失败的 Attempt；
- 确认平台实际状态；
- 取消；
- 查看完整历史。

Web 不接受 Queue 路径、脚本、命令、任意 Task JSON 或伪造 actor。飞书跳转的移动复核页面和
桌面 Web 必须调用同一 Review/Coordinator 边界。

## 11. v4/v5 与 Task 状态收口

- v4/v5 继续拥有平台专属准备、身份校验、提交栅栏、Operation 和结果解析；
- v4/v5 返回统一结构化结果给 Coordinator；
- 顶层 Task/Dispatch 状态转换只走统一 Repository/Application Service；
- 每次转换必须写 append-only 事件和 `task_status_history`；
- Pipeline 内现有直接 Task 更新需逐项迁移并证明不会丢失事务原子性；
- 迁移完成前通过兼容适配器保持单一写入口，不允许两套状态推进长期并存。

## 12. 后台生命周期与可用性

Web、Automation、Queue Service、Worker 和通知服务继续独立运行，但新增统一 Readiness 投影：

- 进程心跳；
- enabled Job 是否有注册 Handler；
- Queue Service、Worker、Importer 是否可用；
- 非终态 Attempt 数量；
- 最老阻塞时间；
- 未导入结果、过期授权和 unresolved Operation 数量；
- 当前是否具备 READ_ONLY、人工平台写和自动紧急保护能力。

“进程存活”与“业务链可执行”必须分开显示。Worker 恢复继续复用 AGENTS.md 的既有恢复程序，
不另建启动机制。

## 13. 复用矩阵

| 能力 | 分类 | 7G 处理 |
| --- | --- | --- |
| Runtime Task 与 History | 参数化复用 | 保留业务状态，统一转换入口；不再用 `pending` 表达所有派发卡点 |
| Execution Authorization | 参数化复用 | 保留精确 Task、主体、digest 和重检，将长期恢复点持久化 |
| v4 改价 Pipeline | 原样复用 | 通过适配器接入 Coordinator，不重写平台动作 |
| v5 上下架 Pipeline | 原样复用 | 通过适配器接入 Coordinator，不重写平台动作 |
| Operation/Write Lock/UNKNOWN/RECONCILE | 原样复用 | 作为 blocker 和结果事件来源 |
| ShadowBot 文件 Queue | 原样复用 | 保留 checksum、inbox/working/results/archive |
| Worker/Importer/Watchdog | 原样复用 | 通过持久化 Attempt 引用回收结果 |
| Review 与 Mobile Token | 原样复用 | 处置完成后发出明确协调事件 |
| Notification Outbox | 原样复用 | 阻塞、恢复、过期和失败通知继续走 Outbox |
| Automation Job/Run/Event | 原样复用 | 只读 Handler 不变；平台写 Task 只提交明确 Attempt |
| Dispatch Attempt/Blocker | 确需新增 | 填补跨组件持久化生命周期所有权 |
| TaskExecutionCoordinator | 确需新增 | 唯一顶层任务推进器 |
| Web 工作中心 | 抽取公共能力 | 使用统一 Query/Command Service，不直接拼接恢复逻辑 |
| 统一 Readiness | 抽取公共能力 | 聚合既有心跳和账本，不控制各进程生命周期 |

新增实现若与本表冲突，必须先提供不可参数化证据和删除平行实现的计划。

## 14. 子 PR 顺序

### 14.1 7G-1：合同与持久化基础

- 字段级复用审计；
- Dispatch Attempt/Blocker/Event 最小 Schema；
- 状态转换和并发约束；
- Repository 与只读投影；
- 不调用 v4/v5，不写 Queue，不执行真实平台动作。

### 14.2 7G-2：统一 Coordinator

- 接入执行授权；
- 接入 v4/v5 Publisher；
- blocker、解除、过期、successor 和优先级；
- Worker/Importer/Review/RECONCILE 事件收口；
- 迁移顶层 Task 状态写入口。

### 14.3 7G-3：Web 工作中心

- 人工最终确认后原子创建 Task + Attempt；
- 工作中心状态、下一步和操作入口；
- 桌面/手机复核后的统一恢复；
- Web 重启恢复；
- 删除旧拼接式恢复逻辑。

### 14.4 7G-4：Automation、生命周期与综合验收

- 平台写 Automation 明确接入；
- Job/Handler 有效性和统一 Readiness；
- 完整模拟 Runtime 旅程；
- 独立授权的真实 READ_ONLY 和真实平台写验收；
- 文档、运维和最终切换。

不得在 7G-1 未合并前并行编写依赖未冻结 Schema 的 7G-2～7G-4 实现。

## 15. 测试与验收矩阵

### 15.1 自动测试

- 正常创建、确认、派发、Worker、Importer、Archive；
- 写锁阻塞后人工确认已执行/未执行；
- blocker 解除时授权仍有效和已过期两条分支；
- Web 在确认后、Queue 发布前重启；
- Queue 发布失败前后幂等；
- Worker 不可用与恢复；
- Worker 已可能产生副作用后禁止自动重试；
- Importer 重放与失败恢复；
- 批量任务逐项隔离；
- 紧急任务插队及普通任务顺序；
- 取消与并发；
- v4/v5 顶层 Task/History 一致；
- enabled Job 缺 Handler；
- 普通 `pending` 零盲扫；
- 飞书与 Web 复核同一事务和同一恢复结果。

### 15.2 完整旅程门禁

必须在同一个一次性 Runtime DB 和同一个临时 Queue 根目录中完成：

```text
Task
→ Dispatch Attempt
→ BLOCKED
→ Review/人工确认
→ READY
→ Queue
→ Worker
→ Importer
→ Archive
→ Task/History/Operation/Web 一致终态
```

不能用互相独立的 Mock 单测拼接成闭环证据。

### 15.3 真实验收

- 先完成真实 Runtime DB 的 READ_ONLY/零写检查；
- 真实平台写必须由用户另行指定商品、动作和批次授权；
- 每次真实写复用长期 Worker，并遵守 AGENTS.md 生命周期门禁；
- 必须覆盖一次阻塞→人工确认→恢复或重新确认的完整路径；
- `UNKNOWN` 路径不得为了验收而人为重复平台副作用；
- 验收后 Queue 必须导入归档，Task/History/Operation/Write Lock 全部回读一致。

## 16. 迁移、切换与回滚

- 迁移前执行 SQLite 逻辑备份、外键检查和回读；
- 现有 Task、Operation、Write Lock、Queue 和结果账本不删除；
- 对历史 `pending` 任务不自动创建可执行 Attempt；只分类为已过期、需人工重建或仅历史保留；
- 当前活动 Queue/UNKNOWN 必须先完成导入或人工确认，不能在中途切换状态所有者；
- 切换采用“新 Task 进入 Coordinator，旧活动链完成后关闭旧入口”的前向策略；
- 已经产生新 Dispatch 事件后不回退到旧 Web 同步提交模型；故障时停用新派发并前向修复；
- 回滚不得重新启用扫描全部 `pending`、旧 CLI 日常旁路或双状态机。

## 17. 本计划 PR 的完成定义

本 Draft PR 只负责立项和合同评审，完成条件为：

- 故障分析、根因和影响范围得到确认；
- 业务 Task 与 Dispatch Attempt 双层状态得到确认；
- Coordinator 唯一所有权得到确认；
- blocker、唤醒、过期和人工复核语义得到确认；
- Web、Automation、Agent、CLI 通道边界得到确认；
- 复用矩阵、子 PR 顺序和验收门禁得到确认；
- 未修改业务代码、Runtime DB、Queue、Worker 或真实平台。

计划合并后，7G-1 必须从新的最新 `main` 独立起分支编码。
