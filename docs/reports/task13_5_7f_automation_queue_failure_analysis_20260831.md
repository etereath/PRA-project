# Task 13.5-7F Automation 与任务队列故障分析报告

## 1. 文档信息

- 报告日期：2026-08-31
- 审查对象：Task 13.5-7F 当前工作树中的 Automation、Runtime Task、执行授权、ShadowBot Queue/Worker/Importer、人工复核、通知和运营 Web 任务队列
- 工作树：`codex/task13-5-7f-runtime-master-data`
- 审查工作树 HEAD：`d30e3d7`，并包含当时尚未提交的 7F 实机验收整改；文中行号均以该审查
  快照为准，不表示 PR 基线 `main` 已包含全部相关代码
- 审查性质：只读架构与运行事实审查
- 风险等级：R4（跨控制面、跨进程、涉及真实平台写入链路）
- 当前结论：**任务队列尚不具备可交付的运营闭环，不能按“已可用任务中心”验收。**

本次审查没有修改 Runtime DB、ShadowBot 队列、Automation Job、Worker、平台状态或真实业务任务；本报告也不授权任何真实平台写操作。

本报告随后由独立 7G 计划 PR 从 `main@31cfbc2` 纳入。该动作只保存故障证据，不把审查工作树
中的未提交业务实现带入计划 PR。7G 实现开始前必须先确认被审查的 7F 代码已经合并到新的
`main`，或在 7G-1 字段级审计中明确列出仍缺失的代码，不能按本报告行号盲目修改旧基线。

## 2. 执行摘要

当前系统并非缺少某一个按钮、某一条重试语句或某一项通知，而是缺少一个对任务完整生命周期负责的**统一任务执行协调器**。

现有实现已经分别具备：

- Automation Job/Run/Event、租约和时间窗口；
- Runtime Task 与 Review 持久化；
- 精确 Task ID 的执行授权；
- v4 改价和 v5 上下架执行链；
- ShadowBot 文件队列、Worker、Importer、Watchdog 与 Archive；
- 写锁、`UNKNOWN`、唯一 `RECONCILE` 和人工确认；
- 通知 Outbox；
- Web 创建任务、发送执行、任务队列展示、取消和人工确认入口。

但是这些能力之间没有一个组件持续负责：

1. 新建任务何时可以发送；
2. 被阻塞任务由谁保存阻塞原因；
3. 阻塞解除后由谁唤醒；
4. 授权过期后由谁要求重新确认；
5. Queue/Worker/Importer 失败后由谁恢复或终止；
6. 最终由谁保证任务离开“当前任务”并进入明确终态。

因此当前 Web 的“当前任务队列”实质上是多个状态源的只读拼接页面，不是一个能推进任务的队列控制面。系统在单个组件内部通常能安全地拒绝、停住或保留证据，但业务任务会停留在组件边界之间，形成“安全但不可运营”的状态。

### 2.1 根因一句话

**Runtime Task、执行授权、文件队列和人工复核分别拥有局部状态，但没有任何持久化组件拥有端到端的任务推进责任。**

### 2.2 当前最主要的用户影响

- 人工任务创建成功后，发送失败会遗留为 `pending`，但没有后台服务重新处理；
- 旧平台操作阻塞解除后，后续任务不会自动恢复，也没有清晰的 Web 重发入口；
- 过期任务可能继续显示为“待发送”，直到某条偶然调用过期清理的业务路径被执行；
- 页面展示的“当前阶段”来自不同账本的推断，无法可靠回答任务下一步由谁处理；
- Automation、Queue Service、Worker 和 Web 分开存活，单个进程正常不代表业务闭环正常；
- 通知可能成功送达，但复核完成、锁释放、任务续跑之间没有闭环；
- 局部失败容易扩大为同平台同动作整组任务失败；
- 重启 Web 会丢失内存中的预览/确认上下文。

## 3. 审查范围与边界

### 3.1 已审查范围

- `AutomationService` 的调度、Handler 注册、Job/Run 与平台写边界；
- Runtime Task 状态机、过期和取消；
- Web 人工任务创建、预览、确认和立即提交；
- `ExecutionAuthorizationApplicationService` 的准备、复核、锁检查与投递；
- v4 改价和 v5 上下架 Pipeline；
- ShadowBot 文件 Queue、Worker、Result Importer、Watchdog；
- 人工复核、平台实际状态确认、写锁释放；
- 任务队列 Read Model 与操作入口；
- 进程启动方式和后台生命周期；
- 相关测试的覆盖结构；
- 2026-08-31 审查时点的 Runtime DB、Queue 和心跳快照。

### 3.2 不在本报告结论中的事项

- 不判断真实平台页面当前是否发生 UI 变化；
- 不修改或补录真实库存、价格、上下架状态；
- 不评价订单扫描、日结和销售预测算法本身；
- 不授权恢复已过期任务或重新发送真实任务；
- 不在本报告中冻结最终 Schema 迁移方案；
- 不以某一次 Worker 成功执行证明整个任务生命周期可用。

## 4. 当前系统构成

| 层级 | 当前职责 | 主要实现 | 当前边界 |
|---|---|---|---|
| Automation | 生成时间窗口、Automation Run、父子关系、租约和事件 | `app/services/automation.py`、`app/repositories/automation_repository.py` | 明确声明不执行平台写入 |
| Runtime Task | 保存业务意图和粗粒度任务状态 | `app/services/runtime.py`、`app/repositories/sqlite_runtime_repository.py` | 只有 `pending/running/success/failed/...`，没有派发阶段 |
| Web 人工任务 | 预览、创建 Task，并在同一次请求中尝试发送 | `app/operations_web/app.py`、`app/services/manual_task_orchestration.py` | 创建与发送不是一个可恢复的持久化工作流 |
| 执行授权 | 绑定主体、精确 Task ID、重检摘要和幂等键 | `app/services/execution_authorization.py` | 准备态主要保存在进程内存中 |
| 平台执行 Pipeline | 构建改价或上下架请求并维护平台操作账本 | `app/services/shadowbot_commit_pipeline.py`、`app/services/shadowbot_listing_action_pipeline.py` | v4/v5 分支分别推进部分 Task 状态 |
| 文件 Queue | inbox/working/results/archive 文件交接 | `app/services/shadowbot_queue.py` | 只认识已投递请求，不消费 Runtime `pending` Task |
| Worker | 领取文件请求并操作影刀/平台 | `shadowbot/test2/shadowbot_queue_worker.py` | 不决定哪些 Runtime Task 应进入队列 |
| Importer/Watchdog | 导入结果、归档、发现卡住的文件请求 | `scripts/run_shadowbot_queue_services.py` | 不负责唤醒被锁或被授权阻塞的业务任务 |
| Review/RECONCILE | 人工处理异常、确认平台实际状态、释放写锁 | `app/services/review_resolution.py`、Runtime Repository | 解除旧操作，不调度其后被阻塞的任务 |
| 通知 Outbox | 可靠保存并发送通知 | `app/services/notification_outbox.py` | 通知送达不等于任务已推进 |
| Web 任务队列 | 汇总 Task、文件 Queue、操作锁和执行结果 | `app/operations_web/queries.py`、`presenters.py` | 是 Read Model，不是 Queue Coordinator |

### 4.1 当前实际链路

```text
人工 Web 请求
  └─ 预览（内存）
      └─ 创建 Runtime Task（持久化）
          └─ 同一 HTTP 请求立即调用执行授权（内存准备态）
              ├─ 成功：写入 ShadowBot 文件 Queue → Worker → Importer → Archive
              └─ 失败：Task 保留为 pending，但没有常驻消费者接手

Automation
  └─ Job → Run → Handler
      └─ 只执行已注册的具体业务 Handler
          └─ 不负责扫描普通 pending Task，也不执行普通平台写入

人工确认旧平台操作
  └─ 更新平台事实/Operation/Write Lock/Review
      └─ 释放阻塞
          └─ 没有后续动作重新派发受影响的 pending Task
```

### 4.2 业务期望链路

```text
Task/Intent 创建
  → 持久化派发请求
  → 校验与授权
  → READY 或 BLOCKED
  → 阻塞解除后重新评估
  → QUEUED
  → RUNNING
  → RESULT_PENDING
  → Importer/RECONCILE
  → SUCCESS / FAILED / CANCELLED / EXPIRED
```

当前缺失的是上述链路中持续拥有 `校验 → 阻塞 → 唤醒 → 投递 → 收尾` 的中间协调层。

## 5. 关键代码证据

### 5.1 Automation 明确不承担普通平台写入

`app/services/automation.py:343` 将 Automation 定义为“never performs platform writes”。这是正确的安全边界，但意味着不能把“Automation 服务正在运行”等同于“普通 `pending` 任务会自动进入平台队列”。

Automation 只运行本进程注册的 Handler。数据库中 Job 为 `enabled`，不代表当前进程一定注册了对应 Handler，也不代表该 Job 的业务链实际可执行。

### 5.2 Runtime Task 状态不足以表达派发生命周期

`app/enums.py:52-60` 当前只有：

- `pending`
- `running`
- `success`
- `failed`
- `skipped`
- `manual_review`
- `cancelled`
- `expired`

其中 `pending` 同时可能表示：

- 刚创建，尚未发送；
- 发送前校验失败；
- 被旧平台操作阻塞；
- Web 请求异常中断；
- 授权已过期；
- 从未被任何消费者选择；
- 等待人工重新发送。

一个状态承担了过多互不相同的业务语义，页面和后台均无法据此确定下一步。

### 5.3 创建成功与发送成功之间没有持久化协调

`app/operations_web/app.py:1096-1137` 先持久化创建任务，再调用 `_submit_manual_task_groups()`。如果提交失败，错误文案说明任务会保留在“当前任务”，但没有创建持久化的重试/派发请求。

`app/operations_web/app.py:1148-1194` 按 `(platform_name, action_type)` 分组后，在同一个 Web 请求内依次 `prepare_execution()` 和 `submit_execution()`。由此产生两个问题：

1. HTTP 请求结束后没有后台工作继续拥有未提交任务；
2. 同一组中一个商品被锁或事实无效，可能令整组任务无法发送。

### 5.4 执行授权准备态依赖 Web 进程内存

`app/services/execution_authorization.py:175` 的 `_preparations` 为进程内字典。Web 重启后，尚未提交的确认摘要和准备态会丢失。Task 虽然仍在数据库，但“为什么未发送、此前确认过什么、应该如何恢复”没有一个完整的持久化实体承接。

### 5.5 没有普通 pending Task 的常驻消费者

代码审查未发现生产级 `TaskDispatcher`、`QueueCoordinator` 或等价服务。现有常驻 Queue Service 负责：

- Result Importer；
- Queue Watchdog；
- 登录验证监控；
- 复核提醒；
- 通知 Outbox。

它不扫描并推进 Runtime DB 中的普通 `pending` Task。项目规则也禁止无条件“扫描全部 `PENDING` 自动执行”，因此正确修复不是增加一个盲扫脚本，而是增加绑定明确 Task ID、来源、授权和阻塞关系的持久化协调流程。

### 5.6 人工确认只解除旧操作，不唤醒后续任务

`app/repositories/sqlite_runtime_repository.py:6593` 的 `resolve_shadowbot_operation_manually()` 会在事务中：

- 记录人工确认的实际平台状态；
- 更新对应平台事实；
- 解决旧 Operation；
- 释放 `UNKNOWN/REVIEW_BLOCKED` 写锁；
- 处理关联 Review、Token 和通知状态。

该事务没有查找“因该锁被拒绝的后续任务”，也没有创建新的派发请求。因此“平台操作阻塞已解除”只表示安全锁已经释放，不表示后续 Task 已恢复执行。

### 5.7 过期清理不是常驻维护责任

`app/services/runtime.py:184-212` 实现了 `expire_overdue_pending_tasks()`，但当前调用主要附着于部分任务生成或工作流路径，没有由常驻任务协调器持续执行。结果是：只要相关入口没有再次被调用，已超过 `expires_at/required_by` 的普通任务仍可能长期显示为 `pending`。

### 5.8 Web 待发送选择器缺少可执行性过滤

`app/operations_web/queries.py:550-559` 从数据库读取最多 50 个 `pending` 平台任务作为发送选项，未在查询层排除：

- 已过期任务；
- 当前存在未解决写锁的任务；
- 需要重新确认的任务；
- 不兼容的平台/动作组合；
- 已经存在持久化派发尝试的任务。

用户只能在提交时才发现冲突，而页面不能提前说明任务是否“可发送”。

### 5.9 Web 任务队列是拼接视图

`app/operations_web/queries.py:825` 之后的队列 Read Model 同时读取：

- Runtime Task；
- `inbox/working/results` 文件；
- unresolved ShadowBot Operation/Write Lock；
- 已有结果和等待时间。

其中：

- `pending` 来自 Task DB；
- `queued/running/results` 来自文件系统；
- “需确认”来自 Operation/Write Lock；
- 页面阶段由 Read Model 推断。

这些记录没有共同的持久化派发主键和单一状态机，所以页面只能尽量去重和解释，不能可靠推进状态。

### 5.10 v4/v5 Pipeline 分别写 Task 状态

改价链和上下架链在不同 Pipeline 中直接更新部分 `tasks.task_status`。其他服务也存在直接写 Task 状态的路径。顶层 Task 转换责任被分散后，出现以下风险：

- 不同动作的失败语义不一致；
- 部分直接更新没有完整写入 `task_status_history`；
- Queue 文件状态与 Task 状态可能不同步；
- 修复一个 Pipeline 不能修复跨 Pipeline 的统一恢复问题。

### 5.11 后台生命周期彼此独立但没有统一有效性检查

- `scripts/start_local.ps1` 只启动 Web；
- `scripts/start_local_services.ps1` 只启动 Queue Service；
- Automation 有独立启动脚本；
- ShadowBot Worker 由影刀应用独立运行。

生命周期解耦本身是正确方向，但系统没有统一 Supervisor 或 Operational Readiness 判定来回答：

- 所有必需服务是否在运行；
- 当前启用 Job 是否有对应 Handler；
- 当前是否有任务卡在组件边界；
- 阻塞解除后是否已经产生后继派发；
- Worker 正常是否真的有可领取请求。

### 5.12 全局 UI 阻塞扩大影响范围

`app/repositories/automation_repository.py:2466` 的活动 UI 阻塞检查会让 `UNKNOWN/RECONCILE` 或活动平台写操作阻止 UI Automation 运行。该策略能够避免并发操作平台，安全上合理；但粒度较粗，一个未解决操作可能暂停所有相关扫描。若 Automation 进程同时未运行，阻塞解除后也不会自然续跑。

### 5.13 异常被转换为通用提示

运营 Web 的 Route/Query 存在大量通用 `except Exception`。它们避免把堆栈暴露给运营者，但许多异常只转化为临时重定向提示或“读取失败”，没有稳定错误码、派发尝试记录和下一步动作，导致问题难以定位，也无法由后台恢复。

## 6. 2026-08-31 运行事实快照

以下数据来自本次整体审查时点的只读检查。它们描述当时状态，不代表文件写入后的持续实时状态。

### 6.1 Runtime Task

| 状态 | 数量 |
|---|---:|
| `cancelled` | 8 |
| `pending` | 2 |
| `success` | 5 |

两条 `pending` 均为人工改价任务，且已超过截止时间：

- `TASK-MANUAL-c042fb5151e956c4e93fcaaa`，商品 `AISHA-D-50-Z`；
- `TASK-MANUAL-4d49fa688eace63d0ce80ae7`，商品 `AISHA-C-55-Z`。

两者创建于 2026-08-31 15:56（Asia/Shanghai），截止时间约为 16:26。审查时：

- 没有执行授权历史；
- 没有对应活动 Queue 文件；
- 仍显示为 `pending`；
- 实际应当被识别为“已过期，需重新预览和确认”，而不是普通“待发送”。

### 6.2 旧平台操作与写锁

旧操作 `OP-f2fc27e5d0c546d2746f258e` 为 `set_online`，在 2026-08-31 18:14 左右经人工确认后解除。

审查时：

- 写锁统计为已释放 3 条；
- 没有活动写锁；
- 上述两条后续改价任务并未因锁释放而自动恢复；
- 锁释放时间晚于任务截止时间。

这正好证明：**解除安全阻塞与推进业务任务是两个不同动作，当前只实现了前者。**

### 6.3 文件 Queue 与后台服务

审查时：

- `inbox/working/results` 没有上述任务的活动文件；
- Worker 心跳为新鲜 `RUNNING`；
- Queue Service 心跳为新鲜 `RUNNING`；
- Web 正在 `127.0.0.1:8765` 提供服务；
- Automation 心跳为 `STOPPED`，原因是此前按用户要求停止定时扫描，属于有意停机，不是本次认定的代码故障。

该快照说明：Worker 和 Queue Service 正常运行并不能处理尚未被投递为 Queue 文件的 `pending` Task。

### 6.4 Automation Run 历史

审查时 Automation Run 历史统计包括：

| 状态 | 数量 |
|---|---:|
| `SUCCESS` | 30 |
| `FAILED` | 15 |
| `MISSED` | 35 |
| `PARTIAL` | 1 |
| `MERGED` | 1 |

其中相当一部分 `MISSED` 与服务停机和 catch-up 窗口有关，不能全部归因于代码缺陷。但该统计也说明，单看“存在成功 Run”不足以证明常驻调度完整可用。

### 6.5 Review 与通知

审查时仍存在 8 条 `listing_location_anomaly` 待复核项，其关联通知已过期。该现象进一步表明 Review、通知和任务执行之间缺少统一的时效性与收尾责任。

## 7. 典型故障链复盘

### 7.1 人工任务创建后被旧操作阻塞

1. Web 预览并确认人工任务；
2. Runtime Task 成功创建为 `pending`；
3. 同一 HTTP 请求尝试执行授权；
4. 发现同商品存在未确认平台操作或写锁；
5. 执行授权安全拒绝，并可能发送“任务队列受阻”通知；
6. Web 告知任务会留在“当前任务”；
7. 系统没有创建持久化 `BLOCKED` 派发尝试，也没有保存应由哪个事件唤醒；
8. 人工确认旧平台操作后锁被释放；
9. 没有组件重新评估第 2 步的 Task；
10. Task 最终过期，但仍可能显示为 `pending`。

**结论：** 执行授权拒绝是正确的；错误在于拒绝后的任务没有进入可恢复的持久化状态。

### 7.2 Worker 正常但任务不执行

1. Worker 心跳正常；
2. Queue Service 心跳正常；
3. Runtime DB 存在 `pending` Task；
4. 文件 Queue 的 inbox 为空；
5. Worker 只能领取 inbox 文件，不读取 Runtime Task；
6. Queue Service 只维护文件队列和结果，不投递 Task；
7. 因此所有进程都“正常”，但业务任务永远不会开始。

**结论：** 这是组件健康与业务闭环健康被混为一谈的典型问题。

### 7.3 人工复核完成但任务队列不继续

1. 平台执行结果进入 `UNKNOWN/REVIEW_BLOCKED`；
2. 飞书或 Web 提供人工确认；
3. 人工确认后 Operation 和 Write Lock 被正确解决；
4. 页面可显示阻塞已经解除；
5. 但后续 Task 没有与该 Operation 建立可唤醒的持久化 blocker 引用；
6. 所以后续任务仍留在原状态。

**结论：** 人工复核闭环只完成了“旧操作事实确认”，没有完成“受影响任务恢复”。

### 7.4 Web 重启导致确认流程断裂

1. 用户完成预览；
2. 预览摘要或执行准备态保存在进程内；
3. Web 因更新、异常或重启退出；
4. Runtime Task 可能已经创建，也可能尚未创建；
5. 重启后内存准备态消失；
6. 数据库缺少完整的 Dispatch Attempt 来说明恢复点。

**结论：** Web 重启不应影响后台的原则尚未完整落实到人工任务的确认与派发阶段。

## 8. 为什么任务队列“几乎没有可用性”

### 8.1 页面名称与实际职责不一致

页面叫“当前任务队列”，用户自然会理解为它负责：

- 查看待办；
- 判断为何未执行；
- 解除阻塞；
- 重新确认；
- 重发；
- 取消；
- 跟踪执行；
- 确认终态。

当前页面实际只完成：

- 聚合展示；
- 对部分 `pending/failed` 任务取消；
- 对 unresolved operation 人工确认平台状态。

它没有重新发送、重新授权、过期任务重建、阻塞任务唤醒和端到端状态推进能力。

### 8.2 “队列”存在三种不同含义

项目同时使用了三种队列概念：

1. Runtime Task 列表：业务意图；
2. ShadowBot 文件 Queue：已经完成授权的执行请求；
3. Web 当前任务队列：前两者再加 Review/Operation 的只读投影。

此前评审多次用“文件 Queue 已通过 Watchdog/Importer/Archive 验收”替代“业务任务队列已通过完整生命周期验收”，造成错误结论。

### 8.3 安全停止被误认为业务处理完成

现有系统在很多危险边界都能 fail closed：

- 扫描质量不足时拒绝；
- 写锁未解除时拒绝；
- 授权过期时拒绝；
- 平台结果未知时进入 RECONCILE；
- Worker/Queue 异常时保留证据。

这些都是应保留的安全能力。但“正确拒绝”之后必须有清晰的持久化状态、责任人、下一步和恢复入口。此前审核主要验证了“不会错误继续执行”，没有验证“停止后能否继续完成业务”。

## 9. 既有审核为何没有发现

### 9.1 此前审核实际覆盖的内容

此前审核主要检查：

- Task 13.5 各阶段计划与 Issue 范围；
- Runtime Schema、外键、幂等和迁移；
- READ_ONLY 扫描、订单观察与零平台副作用；
- 日结、销售估算和 FINAL 门禁；
- Incident、S0-S4、通知与紧急保护；
- v4/v5 平台执行安全、旧值校验、写锁和唯一 RECONCILE；
- ShadowBot 文件 Queue 的校验和、Watchdog、Importer 和 Archive；
- Web 的安全、中文运营文本、四入口、创建任务与取消；
- Automation Job/Run、租约、时间窗、父子关系；
- 单项真实页面 READ_ONLY 或单次授权 COMMIT；
- CI、专项测试和全量 pytest。

这些审核能证明很多组件分别符合合同，但没有证明运营任务能够跨组件走完一生。

### 9.2 漏审的关键问题

此前没有把以下问题作为同一个强制验收场景：

1. 谁拥有 `pending → 可派发`；
2. 谁保存阻塞任务与 blocker 的关系；
3. blocker 消失后谁唤醒任务；
4. 任务已过期后谁改变状态并要求重新确认；
5. Web 重启后确认/派发如何恢复；
6. Queue 结果导入后谁保证 Task、Operation、History 和页面同步终结；
7. 当前任务如何从页面离开并进入历史。

### 9.3 审核方法的三个偏差

1. **把安全停住当作完成处理。** 重点检查 fail closed，却没有检查恢复闭环。
2. **把文件 Queue 当作业务任务队列。** Worker 链验收通过，不代表 Runtime Task 会进入 Worker 链。
3. **以计划合同为主，弱化实际运营旅程。** 页面、Service、脚本分别满足计划条目，但没有从运营者角度连续执行“创建到终态”。

这是审核方法失效，不是一个隐藏得特别深、无法预见的偶发缺陷。

## 10. 测试覆盖评估

相关测试数量很多，且覆盖了大量重要边界。审查时相关文件大致包括：

| 测试领域 | 约有测试数 |
|---|---:|
| Automation Service | 45 |
| Automation Configuration | 13 |
| Execution Authorization | 11 |
| Operations Web Read Models | 23 |
| Operations Web Foundation | 35 |
| ShadowBot Queue | 29 |
| Listing Action Pipeline | 19 |
| Runtime Persistence | 38 |
| Manual Task Orchestration | 14 |

问题不在于完全没有测试，而在于测试以组件和合同为单位：

- Repository 测试证明锁可以释放；
- Authorization 测试证明错误任务会被拒绝；
- Queue 测试证明文件能进入 Worker；
- Importer 测试证明结果可以归档；
- Web 测试证明页面能展示并提交表单。

但缺少同一 Runtime DB、同一组真实 Service、非 Mock 连接下的完整旅程：

```text
创建 Task
→ 被旧 Operation 阻塞
→ 发送通知
→ Web/手机人工确认
→ 锁释放
→ 受影响 Task 进入可恢复状态
→ 过期则重新确认，不盲目执行
→ 投递 Queue
→ Worker
→ Importer
→ Archive
→ Task/History/Operation/Web 全部进入一致终态
```

现有测试中，“人工确认释放 UNKNOWN 锁”的断言允许文件 Queue 保持不变；人工创建/发送测试大量使用 Mock，无法发现跨数据库、文件 Queue 和进程边界的责任空洞。

## 11. 风险与影响评估

| 风险 | 严重度 | 直接影响 | 当前安全属性 |
|---|---|---|---|
| pending 无消费者 | 严重 | 任务创建后永久不执行 | 不会擅自写平台 |
| 阻塞解除不唤醒 | 严重 | 人工处理无业务效果，需重复建任务 | 写锁能正确释放 |
| 过期任务仍待发送 | 严重 | 运营者可能误判任务有效性 | 授权层最终会拒绝过期任务 |
| Web 内存准备态 | 高 | 重启丢失确认上下文 | 已创建 Task 通常仍保留 |
| 队列状态拼接 | 高 | 页面状态难以解释、无法确定下一步 | 原始账本仍可追溯 |
| v4/v5 分散写状态 | 高 | 状态与历史可能不一致 | 各 Pipeline 自身有安全校验 |
| Job enabled 与 Handler 不一致 | 高 | 页面显示启用但实际不执行 | 未注册 Handler 不会误写平台 |
| 多进程无统一就绪判断 | 高 | 服务看似正常，业务链实际中断 | 单进程生命周期相互隔离 |
| 全局 UI blocker | 中 | 单一异常扩大为扫描停顿 | 避免平台并发冲突 |
| 通用异常提示 | 中 | 排错时间长、无法自动恢复 | 不泄露内部堆栈 |

## 12. 应保留的现有能力

整改不应推翻已经验证的安全基础。以下能力应原样复用或通过薄适配复用：

- Automation Job/Run/Event、租约、catch-up 和父子关系；
- 精确 Task ID、主体、幂等键和重检摘要的执行授权；
- v4 改价、v5 上下架的页面动作与旧值校验；
- ShadowBot 文件 Queue 的 checksum、inbox/working/results/archive；
- Worker、Result Importer、Watchdog；
- Operation、Write Lock、`UNKNOWN` 和唯一 `RECONCILE`；
- Review Token 原子消费；
- Notification Outbox；
- Runtime Task 和 `task_status_history` 的业务审计基础；
- Web 的认证、CSRF、权限和 PRG 边界；
- “普通 `PENDING` 不得被无条件扫描并自动执行”的安全约束。

真正需要新增/收口的是这些能力之间的持久化协调，而不是另写一套平台执行器或另建一条 Queue。

## 13. 整改方向

本节是架构整改建议，不是已批准的施工计划。编码前应先冻结合同并进行 R4 评审。

### 13.1 P0：冻结统一任务生命周期合同

需要先明确业务 Task 与派发状态是两个维度。建议至少表达：

- `DRAFT` / 尚未确认；
- `AWAITING_CONFIRMATION` / 等待最终确认；
- `BLOCKED` / 被明确 blocker 阻塞；
- `READY` / 校验和授权有效，可投递；
- `QUEUED` / 已写入文件队列；
- `RUNNING` / Worker 已领取；
- `RESULT_PENDING` / 结果已产生，等待导入；
- `NEEDS_REVIEW` / 需要人工复核；
- `TERMINAL` / 已成功、失败、取消或过期。

不得直接把这些字符串硬塞进现有 `tasks.task_status`；应先评估使用独立 `DispatchAttempt/ExecutionRequest` 是否能减少对既有领域 Task 的破坏。

### 13.2 P0：增加唯一 Task Execution Coordinator

协调器应成为以下动作的唯一编排者：

- 接收明确 Task ID；
- 建立持久化派发尝试；
- 调用既有执行授权；
- 保存 blocker 类型和来源引用；
- 处理过期和重新确认；
- 在明确事件发生后重新评估，而不是轮询全部 pending；
- 调用既有 v4/v5 Pipeline 投递文件 Queue；
- 接收 Importer/RECONCILE 结果；
- 统一写 Task 状态与 History；
- 产生运营通知和下一步动作。

Automation、Web 和未来 Agent Gateway 都只能向该协调边界提交明确意图/Task，不得分别实现一套恢复状态机。

### 13.3 P0：建立持久化 blocker 与唤醒事件

至少需要支持：

- `WRITE_LOCK_UNKNOWN`；
- `REVIEW_PENDING`；
- `SCAN_QUALITY_WARNING`；
- `AUTHORIZATION_EXPIRED`；
- `WORKER_UNAVAILABLE`；
- `QUEUE_PUBLISH_FAILED`；
- `RESULT_IMPORT_FAILED`；
- `HANDLER_UNAVAILABLE`。

每个 blocker 必须记录：

- 被阻塞的派发尝试；
- 来源 Operation/Review/Run/服务状态引用；
- 是否允许自动重新评估；
- 解除时间和解除证据；
- 解除后是继续、重新确认还是终止。

### 13.4 P1：将 Web 队列改为真正的工作中心

页面应直接显示：

- 当前业务状态；
- 当前派发阶段；
- 为什么停住；
- 是否已过期；
- 需要谁做什么；
- 可执行的唯一下一步。

建议动作仅在合法状态出现：

- 重新预览并确认；
- 重新发送；
- 确认平台实际状态；
- 取消；
- 查看历史和证据。

页面不应再用“请打开任务查看”替代状态解释，也不应把已经过期的 Task 展示为普通“待发送”。

### 13.5 P1：统一 v4/v5 顶层状态推进

保留两个平台 Pipeline 的动作差异，但顶层 Task/Dispatch 状态只能由 Coordinator 通过统一 Repository 方法改变，并始终写入 `task_status_history`。Pipeline 返回结构化结果，不再各自决定完整业务终态。

### 13.6 P1：统一后台就绪与生命周期视图

应在系统页增加“有效运行能力”而非只显示进程心跳：

- Web；
- Automation Scheduler；
- 每个 enabled Job 是否存在 Handler；
- Queue Service；
- Worker；
- Importer；
- Notification Sender；
- 当前卡住的 Dispatch 数；
- 最老卡住时长；
- 是否存在未导入结果或过期确认。

这不要求把所有进程合并为一个进程，但应有统一的 Supervisor/Readiness 合同。

### 13.7 P1：补齐端到端真实 Runtime 验收

至少覆盖：

1. 正常创建到归档；
2. 写锁阻塞后人工确认并恢复；
3. 阻塞期间授权过期，解除后必须重新确认；
4. Web 重启后恢复；
5. Queue 写入失败后安全重试；
6. Worker 不可用时明确阻塞并通知；
7. Importer 失败后不重复平台写；
8. `UNKNOWN → 人工确认已执行/未执行` 两条分支；
9. 批量任务中单 SKU 被阻塞时的隔离策略；
10. 取消与紧急任务优先级。

验收必须使用同一个一次性 Runtime DB，并覆盖 Coordinator → Queue → Worker 模拟/受控实机 → Importer → Archive，不允许用互相独立的 Mock 测试拼接为闭环证据。

## 14. 不建议采用的局部修复

在统一合同冻结前，不建议继续实施以下补丁：

- 单独在 Web 增加一个“重试”按钮直接调用现有提交方法；
- Queue Service 定时扫描所有 `pending` 并自动执行；
- 人工确认后直接查找所有同 SKU Task 并盲目投递；
- 仅增加更多 Task 状态字符串而不建立派发实体；
- 继续在 v4/v5 Pipeline 中分别补状态分支；
- 用页面文案掩盖“锁已解除但任务未恢复”；
- 把 Web 内存字典扩容或延长 TTL 当作持久化恢复；
- 只增加单元测试，不增加完整旅程测试；
- 因当前单平台而把协调逻辑写进蚂蚁花团 Adapter。

这些方案可能暂时修复某一条任务，却会继续增加状态分叉和后续维护成本。

## 15. 建议验收门禁

在重新声明“任务队列可用”前，至少应满足：

- [ ] 每个当前任务都有明确业务状态和派发阶段；
- [ ] 每个非终态任务都有明确下一步或 blocker；
- [ ] blocker 解除会触发确定性重新评估；
- [ ] 已过期任务不会显示为普通待发送，也不会自动写平台；
- [ ] Web 重启不会丢失已确认但未投递的派发上下文；
- [ ] Worker/Queue Service 正常但 inbox 为空时，系统能区分“无任务”和“任务未投递”；
- [ ] 文件 Queue、Operation、Task 与 History 能通过同一派发 ID 追溯；
- [ ] v4/v5 不再独立拥有顶层 Task 终态；
- [ ] enabled Job 与有效 Handler 不一致时系统页明确报警；
- [ ] 普通任务不通过盲扫 `pending` 自动执行；
- [ ] 人工确认“已执行/未执行”后，受影响任务进入正确后继状态；
- [ ] 飞书复核和 Web 复核使用同一协调入口；
- [ ] 队列受阻通知只发送一次且在恢复/终止后闭环；
- [ ] 完整真实 Runtime 生命周期测试通过；
- [ ] 受控真实平台验收单独授权，且不重复副作用。

## 16. 结论

当前故障不是 Worker、Automation、Web 或复核模块中的某一个孤立错误。各组件大多完成了自己的局部合同，真正缺失的是跨组件的任务生命周期所有权。

现状可以概括为：

> 系统能安全地创建任务、安全地拒绝危险执行、安全地操作文件队列、安全地确认未知平台状态，但不能保证同一个业务任务在这些安全边界之间持续向前推进。

因此下一步不应继续逐条修复页面提示或单个复核分支。应先冻结“业务 Task + 持久化派发尝试 + blocker + 唤醒事件 + 统一终态”的协调合同，再以既有执行授权、v4/v5、Queue、Worker、Importer、RECONCILE 和 Outbox 为下层能力完成收口。

在该合同和完整旅程验收完成前，当前任务队列应标记为**诊断/受控操作视图**，不能标记为已完成的运营任务中心。

## 17. 主要证据索引

- `AGENTS.md`：项目控制面、执行授权、Queue/Worker/Importer 和复用约束；
- `app/services/automation.py:343`：Automation 不执行平台写入；
- `app/enums.py:52-60`：当前 Task 状态；
- `app/operations_web/app.py:1096-1194`：人工创建后立即分组提交；
- `app/services/execution_authorization.py:175`：进程内执行准备态；
- `app/services/execution_authorization.py:530` 起：Task 有效性、Review 和写锁校验；
- `app/services/runtime.py:184-212`：过期 pending Task 清理；
- `app/repositories/sqlite_runtime_repository.py:6593` 起：人工确认平台操作和释放写锁；
- `app/repositories/automation_repository.py:2466`：活动 UI 阻塞；
- `app/operations_web/queries.py:550-559`：Web pending Task 选择器；
- `app/operations_web/queries.py:825` 起：任务队列聚合 Read Model；
- `scripts/run_shadowbot_queue_services.py`：Importer/Watchdog/通知等常驻服务；
- `scripts/start_local.ps1`：Web 独立启动；
- `scripts/start_local_services.ps1`：Queue Service 独立启动；
- `docs/plans/task13_5_7_web_rewrite_construction_plan.md:523-531`：现有任务队列只读/取消范围与普通 pending 不自动执行门禁；
- `docs/reports/task13_5_7f_cutover_acceptance.md`：7F 切换和验收历史。
