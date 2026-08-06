# 任务 13.5-7：控制服务、脚本和任务来源对齐审查计划

- 计划日期：2026-08-06
- 状态：编码前审查草案
- Review Profile：R4（架构级审查）
- 目标 PR：`task13.5-7-control-plane-alignment`
- 关联父任务：GitHub Issue #20
- 基线：PR #28 已合并，主分支合并提交 `418c605f4ab4434eee422eb0217de3cfe64b01b0`

## 1. 计划定位

任务 13.5-7 不增加新的平台页面动作，也不建设新的执行器、队列、Importer 或数据库账本。
本阶段的职责是将目前分散在 Web、CLI、Automation Scheduler 和人工脚本中的正式业务入口
收口到同一组 Application Service，使相同业务意图无论从哪个入口触发，都获得一致的：

- 平台与账号身份；
- 双时间轴；
- 运行模式；
- 任务来源；
- 审批与策略字段；
- 幂等身份；
- 调度优先级；
- 事务边界；
- 错误语义；
- 恢复与审计结果。

本任务为后续 13.5-8 Web 架构拆分和 13.5-9 运营 UI 重写提供稳定服务层。
Web 路由、CLI 参数解析和 Scheduler Handler 应成为薄适配器，不再各自持有业务判断。

## 2. 权威依据

本计划以下列资料为权威：

1. GitHub Issue #20 中的 `T13.5-7：控制服务、脚本和任务来源对齐`；
2. `docs/plans/task13_5_operational_closed_loop_and_web_rewrite.md`；
3. `docs/pra_review_risk_and_complexity_governance.md`；
4. `AGENTS.md` 中的复用优先开发门禁；
5. `docs/project_current_status.md`；
6. 已合并的任务 12、13、13.5-1 至 13.5-6 合同、测试和实机证据。

若历史脚本行为与现行正式合同冲突，以现行 Application Service、Runtime Schema、v4/v5、
Automation、Review、Importer、写锁和唯一 RECONCILE 为准；不得为了兼容临时脚本而改写
已经验收的安全边界。

## 3. 审查等级

```text
Review Profile: R4
真实平台写操作: 不新增；间接连接既有 v4/v5 写入口
涉及平台: 当前单平台，公共服务继续保持平台无关
涉及账号: 当前单账号运行约束；身份字段不得在适配器层丢失
连接权威模块:
  Web / CLI / Automation Scheduler / Application Service /
  Task / Review / Automation Run / Batch / Operation / Attempt /
  Write Lock / Importer / Outbox / Incident
数据库迁移: 预计否
新增平台动作类型: 0
新增 Worker / 队列 / Importer: 0
```

### 3.1 为什么是 R4

本阶段会改变真实业务入口与权威服务之间的关系，并连接多个已经独立验收的权威模块。
虽然不新增平台副作用，但若入口收口错误，可能造成：

- Web、CLI、Scheduler 对同一输入产生不同业务结果；
- 脚本绕过正式服务直接创建任务或队列文件；
- 重复创建 Task、Review、Run、Outbox、Batch 或 Operation；
- 人工补跑被伪装成计划运行；
- 自动运行被错误标记为人工来源；
- UNKNOWN 被普通补跑覆盖；
- 已合并的人工任务、SYSTEM_EMERGENCY、普通任务和 UI 扫描优先级被绕过；
- 清理临时入口时破坏现有恢复和运维路径。

因此本任务必须在编码前完成设计审查，冻结部署假设、故障模型、非目标、复杂度预算和
第一轮阻塞清单。

## 4. 当前部署假设

本阶段按以下当前事实设计，不提前引入分布式复杂度：

- 单一 PRA Runtime SQLite；
- 单一 Automation Service 实例租约；
- 单一长期 ShadowBot Worker；
- 当前只接入一个真实平台和一个受控账号；
- Web、CLI、Scheduler 可以作为不同进程访问同一 Runtime DB；
- 真实写操作继续通过既有 v4/v5 文件队列；
- UNKNOWN 继续只进入唯一只读 RECONCILE；
- `automatic_emergency_offline=false` 继续是默认生产边界；
- 真实 Runtime DB 的迁移和既有健康问题仍是独立生产门禁。

本阶段不建设：

- 分布式消息总线；
- 外部锁服务；
- 跨机器自动接管；
- 跨平台原子事务；
- 第二平台适配器；
- 第二 Runtime DB 写入主线。

## 5. 故障模型

设计和测试至少覆盖以下故障：

1. Web 重复 POST 或浏览器重试；
2. CLI 重复运行相同命令；
3. Scheduler 租约重放或进程重启；
4. 人工补跑与计划运行同时触发；
5. 相同业务意图从不同入口同时到达；
6. Application Service 在写入部分事实后异常；
7. Runtime DB 被另一个进程持有写锁；
8. UI 通道被扫描、人工任务、紧急任务或 UNKNOWN 占用；
9. 入口读取到过期 Task、Review、Run 或平台事实；
10. CLI/Web 进程工作目录不同；
11. 环境变量缺失或指向错误 Runtime DB；
12. 旧兼容脚本仍被运维人员调用；
13. 适配器将业务错误吞掉并错误返回成功；
14. 入口在请求提交后崩溃，调用方重试；
15. 任务来源、操作者或补跑原因不完整。

## 6. 非目标

本任务明确不包含：

- Web 目录拆分、模板迁移和 Presenter/ViewModel 建设；
- 八个一级入口的视觉和交互重写；
- 新增平台动作或修改 ShadowBot 页面定位；
- 新增 Automation Run 状态；
- 新增 Task 或 Review 状态；
- 新增数据库表或 Schema v17；
- 自动扫描所有普通 pending Task 并发起 COMMIT；
- 放开 `automatic_emergency_offline`；
- 第二平台、第二账号并行执行；
- 生产 ERP、包装、冷库、配送或财务流程；
- 通过 Web subprocess 调用 CLI；
- 为人工脚本建立第二文件队列。

若实施过程中发现必须突破上述边界，应停止编码，单独提交设计变更评审。

## 7. 复用优先盘点门禁

编码前必须完成入口盘点，并将每项归入以下五类之一：

```text
1. 原样复用
2. 参数化复用
3. 抽取公共能力
4. 确需新增
5. 归档或删除
```

盘点至少包含：

| 入口 | 当前文件/函数 | 调用服务 | Runtime DB 来源 | 默认模式 | 可创建业务事实 | 可触发平台写 | 目标归类 |
|---|---|---|---|---|---|---|---|
| Web 手动入口 | 待盘点 | 待盘点 | 待盘点 | 待盘点 | 待盘点 | 待盘点 | 待评审 |
| CLI 业务入口 | 待盘点 | 待盘点 | 待盘点 | 待盘点 | 待盘点 | 待盘点 | 待评审 |
| Automation Handler | 待盘点 | 待盘点 | 待盘点 | 自动 | 是/否 | 是/否 | 待评审 |
| 人工补跑脚本 | 待盘点 | 待盘点 | 待盘点 | 显式 | 是/否 | 是/否 | 待评审 |
| 诊断/验收脚本 | 待盘点 | 待盘点 | 隔离/真实 | 诊断 | 否 | 受控 | 待评审 |

不得以“现有脚本较短”或“重新实现更容易”为理由建立平行控制面。

## 8. 目标架构

```text
Web Route / CLI Adapter / Scheduler Handler / Manual Rerun Adapter
                         ↓
             Unified Application Service Boundary
                         ↓
  ┌─────────────────────────────────────────────────────┐
  │ Operational Time / Identity / Mode / Idempotency    │
  │ Task Source / Approval / Priority / Transaction     │
  │ Runtime Task / Review / Automation / Incident       │
  └─────────────────────────────────────────────────────┘
                         ↓
 Existing v4/v5 / Batch / Operation / Attempt / Lock / Importer
                         ↓
                ShadowBot File Queue / Worker
```

### 8.1 Application Service 约束

允许建立一组按业务能力组织的 Application Service 或一个轻量外观层，但不得建立新的
业务账本。服务必须：

- 接收显式调用上下文；
- 验证平台、账号、Runtime DB 和运行模式；
- 在服务层计算双时间轴；
- 生成稳定触发身份和幂等键；
- 决定 `origin_type / origin_ref_id / approval_policy / policy_version`；
- 调用现有 Runtime、Review、Automation、Incident、v4/v5 服务；
- 返回结构化业务结果；
- 不直接操作页面；
- 不直接拼接队列 JSON；
- 不绕过现有事务和恢复路径。

Web、CLI 和 Scheduler 不得各自复制上述判断。

## 9. 入口模式合同

所有入口必须显式声明模式：

```text
READ_ONLY
DRY_RUN
APPLY
COMMIT
RECONCILE
DIAGNOSTIC
```

### 9.1 READ_ONLY

- 可以产生 Automation Run、观察事实、只读批次和审计事件；
- 不得创建平台写 Task；
- 不得获取真实写锁；
- 不得拼接 COMMIT 请求。

### 9.2 DRY_RUN

- 可以写预览运行和预览条目；
- 不得创建 Task、Review 或真实 Outbox；
- 不得触发平台写。

### 9.3 APPLY

- 仅通过正式服务创建业务 Task、Review 和通知意图；
- 不等于自动执行平台写；
- CLI 必须显式传入；Web 第一版不得提供通用 APPLY 按钮。

### 9.4 COMMIT

- 只允许既有正式授权 Task 进入；
- 必须经过 gate、Batch、Operation、Attempt、写锁和 Importer；
- 入口层不得修改来源和审批字段；
- 不得默认启用。

### 9.5 RECONCILE

- 只允许已有 UNKNOWN/NEEDS_RECONCILIATION Operation；
- 继续使用唯一只读 RECONCILE；
- 不得创建第二次平台写。

### 9.6 DIAGNOSTIC

- 只读取健康、配置、队列和账本；
- 不创建正式业务事实；
- 不改变 Task、Review、Run、Lock 或平台状态。

## 10. 调用上下文合同

建议统一调用上下文至少包含：

```text
entrypoint_type
actor_type
actor_ref
trigger_type
trigger_ref_id
platform_name
account_id
runtime_db_path
requested_mode
requested_at
manual_reason
scheduled_for
parent_run_id
```

字段要求：

- `entrypoint_type` 必须区分 Web、CLI、Scheduler、manual rerun 和 diagnostic；
- `actor_ref` 必须可审计，但不得保存敏感凭据；
- 人工补跑必须提供 `manual_reason` 和原计划时间；
- Scheduler 必须绑定 Automation Run；
- Web/CLI 不得伪造 Scheduler Run；
- `runtime_db_path` 必须规范化并验证，不得依赖当前工作目录；
- `account_id` 即使当前只有一个账号也必须保留隔离边界。

## 11. 任务来源合同

### 11.1 Incident 人工处置

```text
origin_type = MANUAL
origin_ref_id = incident-review:<review_task_id>
approval_policy = MOBILE_REVIEW
policy_version = incident-review-v1
```

### 11.2 系统紧急保护

```text
origin_type = SYSTEM_EMERGENCY
origin_ref_id = emergency:<authorization_id>
approval_policy = SYSTEM_EMERGENCY_V1
policy_version = <approved emergency policy version>
```

普通 Repository 和外部入口不得创建或改写该来源。

### 11.3 普通 Automation

```text
origin_type = AUTOMATION
origin_ref_id = automation-run:<run_id>
approval_policy = <业务服务决定>
policy_version = <实际规则版本>
```

### 11.4 Web 人工业务操作

```text
origin_type = MANUAL
origin_ref_id = web:<stable request or actor reference>
```

Web 表单不得直接提供 `origin_type / approval_policy / policy_version` 输入框。

### 11.5 CLI 人工补跑

人工补跑必须显式记录：

- 操作者；
- 原计划时间；
- 补跑原因；
- 是否覆盖正常结果；
- 目标平台和账号；
- 关联原 Run 或业务意图。

不得伪装成正常 Scheduler 运行。

## 12. 单次业务意图与幂等身份

统一身份至少由以下维度构成：

```text
trigger_type
trigger_ref_id
platform_name
account_id
platform_trade_date
seller_operation_date
scope_type
scope_key
requested_mode
policy_version
```

同一业务意图从不同入口重复提交时应返回已有结果，不得重复创建：

- Automation Run；
- Task；
- Review；
- Notification Outbox；
- Batch；
- Operation；
- Execution Attempt。

允许同一 Run 的明确人工补跑创建新 Run，但必须通过显式补跑身份链接原 Run，而不是复用或
覆盖原 Run。

## 13. 调度优先级合同

必须保持已合并顺序：

```text
Incident 人工处置任务
→ SYSTEM_EMERGENCY 自动紧急任务
→ 普通业务任务
→ 普通 UI 只读扫描
```

约束：

- 显式 task ID 选择不能绕过高优先级 lane；
- Web、CLI、Scheduler 统一使用相同选择器和发布事务；
- 普通 Automation UI Run 在存在紧急 pending Task 时不得 claim；
- 人工任务在平台副作用提交边界前可以抢占自动紧急任务；
- UNKNOWN/RECONCILE 始终高于普通新任务；
- Scheduler 与人工补跑不得同时占用同一平台账号 UI 通道。

## 14. 结构化结果合同

Application Service 应返回统一业务结果，适配器只负责展示：

```text
CREATED
ALREADY_EXISTS
ALREADY_RUNNING
DEFERRED_BY_HIGHER_PRIORITY
BLOCKED_BY_REVIEW
BLOCKED_BY_UNKNOWN
BLOCKED_BY_UI_CHANNEL
BLOCKED_BY_IDENTITY
READ_ONLY_COMPLETED
DRY_RUN_COMPLETED
FAILED_BEFORE_SIDE_EFFECT
START_UNKNOWN
NEEDS_RECONCILIATION
COMPLETED
```

每个结果至少包含：

- 业务状态；
- 入口类型；
- 稳定触发身份；
- 关联 Run/Task/Review/Batch/Operation ID；
- 是否发生平台副作用；
- 是否允许安全重试；
- 阻断原因或错误代码；
- 下一步处理建议。

Web、CLI 和 Scheduler 不得将同一业务错误分别解释成成功、跳过和失败。

## 15. 事务边界

### 15.1 任务和 Review

需要同时创建 Task、Review、Token、Outbox 或 Incident Event 的路径必须继续使用现有原子
服务，不得在适配器层分步写库。

### 15.2 Automation Run

Scheduler claim、租约、状态迁移和事件必须继续由 Automation Repository/Service 管理。
Web 或 CLI 手工补跑应通过正式补跑服务创建审计链接，不得直接更新 Run 状态。

### 15.3 v4/v5 发布

任务选择、优先级重验、gate、Batch、Operation、Attempt、写锁和 Task `running` 投影必须
继续在既有发布事务内完成。

### 15.4 结果导入

结果收据、operation/attempt、Task、Lock、平台事实、Incident 和 Review 意图继续由既有
Importer 原子提交。入口统一不得在 Importer 之外补写结果。

## 16. 阶段拆分

### 16.1 13.5-7A：入口盘点和合同冻结

**风险：R4，零业务副作用。**

交付：

- 全部入口清单；
- 五类复用矩阵；
- 目标调用图；
- 模式合同；
- 来源矩阵；
- 幂等身份；
- 错误语义；
- 退役清单；
- 测试矩阵。

编码门禁：7A 未通过评审前，不重构正式入口。

### 16.2 13.5-7B：统一 Application Service

**风险：R4；涉及既有写入口的路径叠加 R3。**

交付：

- 统一调用上下文；
- 薄 Web/CLI/Scheduler 适配器；
- 统一只读扫描手动触发；
- 统一任务来源和审批字段；
- 统一幂等和结果模型；
- 统一优先级和 UI 通道门禁；
- 入口一致性和并发测试。

### 16.3 13.5-7C：旧入口退役和重启幂等

**风险：R4。**

交付：

- 旧脚本改为诊断/补跑薄适配器或归档；
- 删除重复业务逻辑；
- 明确兼容入口弃用期限；
- 更新 Runbook、启动脚本和文档；
- 重复提交、重启和补跑恢复测试；
- 系统冒烟和双平台 CI。

## 17. 复杂度预算

```text
新增数据库表: 0
新增数据库字段: 0（除非另行评审证明必要）
新增 Schema 版本: 0
新增 Task 状态: 0
新增 Review 状态: 0
新增 Automation 状态: 0
新增平台动作: 0
新增 Worker: 0
新增文件队列: 0
新增 Importer: 0
新增全局锁: 0
新增执行账本: 0
新增 Application Service 外观层: 最多 1 组
长期保留平行业务入口: 0
```

每新增一个控制机制，必须同时说明可以删除或合并的旧机制。

## 18. 第一轮 P1 阻塞清单

第一轮设计和代码审查冻结以下阻塞项：

1. Web、CLI、Scheduler 对相同输入生成不同业务结果；
2. 任一入口直接写 Task、Review、Run 或队列文件；
3. Web 通过 subprocess 调用 CLI；
4. 建立第二文件队列、第二执行器或第二 Importer；
5. CLI 默认执行 APPLY 或真实写；
6. 手动扫描被错误记录为业务 Task；
7. 自动运行被错误标记为 MANUAL；
8. 人工补跑被伪装成正常 Scheduler 运行；
9. 来源字段可由外部参数伪造；
10. 平台、账号或 Runtime DB 在适配器层丢失；
11. Web 和 CLI 使用不同双时间轴计算；
12. 重复点击、HTTP 重试或 CLI 重跑生成重复业务事实；
13. 服务重启后重新创建同一 Run、Task 或 Review；
14. Scheduler 与人工补跑同时占用同一 UI 通道；
15. 统一 Service 绕过紧急任务调度顺序；
16. 旧脚本仍保留完整业务逻辑，形成第二控制面；
17. 删除旧路径时破坏现有运维、恢复或验收入口；
18. READ_ONLY/DIAGNOSTIC 入口意外获得 COMMIT 能力；
19. 适配器吞掉业务错误，Scheduler 或 Web 误记成功；
20. Web auth、CSRF 或权限边界因入口重构而回退；
21. UNKNOWN/RECONCILE 被普通补跑或新任务覆盖；
22. 不同入口生成不同幂等键；
23. 同一业务意图产生多个 Outbox 通知；
24. 正式路径依赖本机相对路径或当前工作目录；
25. 兼容入口没有明确退役方案，形成长期双路径。

后续复审原则上只验证上述问题及其直接回归。若出现可能误操作平台、覆盖人工操作、重复
副作用、损坏权威账本或造成平台/账号串扰的新问题，可以新增阻塞项。

## 19. 测试矩阵

### 19.1 入口一致性

对同一合成业务意图分别通过 Web Adapter、CLI Adapter 和 Scheduler Handler 调用，断言：

- 使用相同 Application Service；
- 生成相同双时间轴；
- 生成相同来源与策略字段；
- 生成相同幂等身份；
- 返回相同业务状态；
- 不重复创建事实。

### 19.2 模式隔离

覆盖：

- READ_ONLY 不创建写 Task；
- DRY_RUN 不创建 Task/Review/Outbox；
- APPLY 不自动进入 COMMIT；
- COMMIT 必须绑定正式授权 Task；
- RECONCILE 不执行第二次写；
- DIAGNOSTIC 不改变业务状态。

### 19.3 幂等与并发

覆盖：

- Web 双击；
- HTTP 重试；
- CLI 重跑；
- Scheduler 重启；
- Web 与 CLI 同时提交；
- Scheduler 与人工补跑同时提交；
- 一个入口取得 SQLite 锁后另一个入口等待；
- 提交后调用方超时重试；
- 精确重放；
- 相同引用不同内容的冲突。

### 19.4 来源审计

覆盖：

- MANUAL、AUTOMATION、SYSTEM_EMERGENCY 来源不可串用；
- Incident 人工 Task 维持 lane 0；
- SYSTEM_EMERGENCY 维持 lane 1；
- 普通任务维持 lane 2；
- 人工补跑保留操作者和原因；
- 外部入口不能伪造 approval/policy 字段。

### 19.5 事务和恢复

覆盖：

- Task/Review/Outbox 创建失败回滚；
- Run claim 和状态迁移失败回滚；
- 发布前优先级变化；
- 发布前 Review/UNKNOWN/UI 状态变化；
- Importer 精确重放；
- UNKNOWN 后唯一 RECONCILE；
- 进程崩溃后同一意图安全恢复。

### 19.6 平台和账号隔离

即使当前只有一个平台账号，也必须用合成测试证明：

- 平台身份进入幂等键；
- 账号身份进入调用上下文；
- 不同平台/账号的 Task、Run、Lock 不串用；
- 一个平台注册失败不会被公共服务解释成其他平台失败；
- 不创建跨平台混合写批次。

### 19.7 Web 安全回归

若 13.5-7 修改现有 Web POST 入口，必须保持：

- 认证；
- CSRF；
- 权限；
- Token；
- 状态码；
- 重复提交行为；
- 错误信息不泄漏敏感路径和凭据。

## 20. 验证要求

13.5-7A 纯计划 PR：

- Markdown 和链接检查；
- 不要求实机；
- 不得修改业务代码。

13.5-7B/7C 实现 PR：

- 模块测试；
- 受影响集成测试；
- 入口一致性测试；
- 幂等和并发测试；
- 来源和优先级测试；
- UNKNOWN/RECONCILE 回归；
- 系统冒烟；
- 完整 pytest；
- Linux Core；
- Windows Core；
- Secret Scan（若修改配置、日志、通知或凭据读取）；
- `git diff --check`；
- 工作区清洁检查。

若仅收口调用入口且不修改 v4/v5 发布和 ShadowBot 文件，原则上不需要重复真实平台写验收。
若修改 COMMIT 启动、写锁、operation/attempt、Worker 请求或 Importer，则必须重新评估并执行
一次最小受控实机验收。

## 21. 验收标准

完成 13.5-7 必须满足：

1. Web、CLI、Scheduler 使用同一 Application Service；
2. CLI 和 Web 仅保留薄适配逻辑；
3. 正式业务逻辑不依赖 subprocess；
4. 不存在第二业务队列、第二 Worker 或第二 Importer；
5. READ_ONLY、DRY_RUN、APPLY、COMMIT、RECONCILE、DIAGNOSTIC 清晰隔离；
6. 相同业务意图跨入口只产生一个权威结果；
7. 任务来源和审批字段由服务决定且不可伪造；
8. 人工任务、SYSTEM_EMERGENCY、普通任务和 UI 扫描优先级保持不变；
9. 人工补跑有操作者、原因和原 Run 关联；
10. UNKNOWN 不会被普通重跑覆盖；
11. 旧脚本已归档、删除或成为薄适配器；
12. 入口重启和重复提交幂等；
13. 现有 v4/v5、Review、Automation、Incident、Importer、写锁和 RECONCILE 全量回归通过；
14. 文档、启动方式和 Runbook 与正式入口一致；
15. 未提前实施 13.5-8/13.5-9 Web 重构。

## 22. 编码开工门禁

在开始 13.5-7B 业务代码前，必须先完成并通过评审：

- 入口全量盘点；
- 五类复用矩阵；
- 目标 Application Service 调用图；
- 模式合同；
- 来源矩阵；
- 幂等身份；
- 错误结果合同；
- 旧入口退役清单；
- 测试矩阵；
- 对复杂度预算无突破的确认。

本计划 PR 通过只表示允许进入入口盘点和设计冻结，不表示允许修改真实写入口或启用任何
生产平台动作。

## 23. 后续阶段边界

13.5-7 完成后：

- 13.5-8 才进行 `app/webapp/`、路由、模板、Presenter、静态资源和兼容层拆分；
- 13.5-9 才进行八个一级入口、运营信息层级和移动端体验重写；
- 13.5-10 才进行完整交易日连续观察和任务 14 交接。

13.5-7 不应以“方便后续 Web 重写”为理由提前改变页面信息架构或视觉设计。

---

Refs #20
