# 任务 13.5-3：独立 Automation Service 合同

- 冻结日期：2026-07-29
- 父级权威：[GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)
- 前置阶段：13.5-1 Runtime Schema v14、13.5-2 商品映射与扫描输入
- 实施入口：`scripts/run_automation_service.py`
- 业务实现：`app/services/automation.py`
- 账本实现：`app/repositories/automation_repository.py`

## 1. 阶段边界

13.5-3 只建设自动化控制面：

- jobs/runs/events/links；
- 计划窗口、合并、补跑和任务风暴上限；
- 单 run 租约、心跳、超时回收和旧 owner 写回 fencing；
- 单 UI 通道的既有写侧阻断；
- 父子 run 的幂等创建和关联；
- 独立进程锁、进程心跳和健康快照；
- 可注入的应用服务 handler 边界。

本阶段不建设订单页面 Adapter、销售估算、日结算法、S4 自动紧急下架或 Web
运营页面，也不修改 ShadowBot Worker、队列合同、Watchdog 或唯一 RECONCILE。
为形成单 UI 通道，只在既有 v4/v5 写任务取得 Runtime 写锁的事务入口增加活动
Automation UI 租约检查；为封闭自动扫描的事实提交边界，在任务 13 权威
`SYNC_STATUS` Importer 增加不可变输入清单绑定和 Automation claim fencing，
但不改变任务、队列、Worker 或动作状态机合同。真实平台扫描 handler 必须在对应
应用服务具备完整合同和验收后显式注册；不存在 handler 时，调度器只创建
`SCHEDULED` 账本，不伪造成功，也不产生平台副作用。

## 2. 默认作业

| job_id | job_type | 计划 | 优先级 | 补跑 |
| --- | --- | --- | ---: | --- |
| `AUTOMATION-ONLINE-PULSE-10M` | `ONLINE_PULSE` | 每 10 分钟 | 60 | 只保留最新有价值窗口 |
| `AUTOMATION-FULL-MARKET-SCAN-HOURLY` | `FULL_MARKET_SCAN` | 每小时 | 50 | 最多执行最新窗口 |
| `AUTOMATION-PRE-CUTOFF-FULL-SCAN` | `PRE_CUTOFF_FULL_SCAN` | 每日 17:55 | 30 | 超过 2 小时记 `MISSED` |
| `AUTOMATION-POST-CUTOFF-PULSE` | `POST_CUTOFF_PULSE` | 每日 18:05 | 35 | 超过 2 小时记 `MISSED` |
| `AUTOMATION-TRADE-DAY-SETTLEMENT` | `PLATFORM_TRADE_DAY_SETTLEMENT` | 每日 20:00 | 40 | 幂等保留最近 2 个窗口 |
| `AUTOMATION-SALES-PLAN-INPUT` | `SALES_PLAN_INPUT_BUILD` | 每日 20:05 | 55 | 幂等保留最近 2 个窗口 |

另保留两个禁用的 `CHILD_ONLY` 作业：

- `LISTING_STATUS_SCAN`
- `ORDER_SCAN`

它们只能由父 handler 通过 `ensure_child_run(...)` 创建，不能被时间调度器独立触发。
13.5-4 实现订单 Adapter 前，`ORDER_SCAN` 仅是编排边界，不代表订单采集已完成。

## 3. 计划窗口与逻辑幂等

一个计划窗口的逻辑身份为：

```text
job_id + scheduled_for(UTC)
```

其 SHA-256 派生稳定 `run_id`，数据库唯一
`automation_runs.logical_run_key` 继续作为最终并发门禁。重复轮询、进程重启或多个
调度实例同时观察同一窗口时，只能得到同一个 run；相同逻辑身份若对应不同 job 类型、
平台、双日期、阶段、时间策略或计划时间，必须报冲突。

已存在的 `job_id` 不允许原地改变 `job_type`、计划类型、计划表达式或平台；调整这些
静态身份时必须创建新 job。运营仍可修改启停、优先级和非身份配置。
服务启动时必须复核所有默认 job 的静态身份；已存在不代表合法，发现漂移必须失败，
不得静默沿用错误计划。

所有计划时间先归一化为 UTC，再由统一 `OperationalTimeService` 写入：

- `platform_trade_date`
- `seller_operation_date`
- `seller_phase`
- `time_policy_version`

10 分钟与整点窗口按 `Asia/Shanghai` 对齐。每日作业使用当地固定时间，不由脚本、
Web 或 handler 重新计算业务日期。
Automation Service 每轮从 Runtime DB 读取完整 `operational_time_policies` 生效链，
因此跨越策略 `effective_from` 后新 run 使用新版本；既有 run 与其子 run 保留父 run
冻结的双日期、阶段和策略版本。

## 4. 休眠、错过、补跑与风暴保护

首次启动只观察每个 job 最近一个已到期窗口，不回填历史全部窗口。已有运行记录后：

- 小扫描：旧窗口记 `MISSED`，只让最新有价值窗口进入 `SCHEDULED`；
- 小时完整扫描：旧窗口记 `MISSED`，最多补跑最新窗口；
- 17:55/18:05 边界扫描：超过两小时不再执行，明确记 `MISSED`；
- 20:00 日结与 20:05 计划输入：幂等保留最近两个到期窗口；
- 每 job 每周期最多物化 16 个窗口；更早窗口压缩为
  `MISSED_WINDOWS_TRUNCATED` 事件并记录数量，避免长时间停机后形成任务风暴。

`MISSED` 保存原计划时间、实际物化时间、迟到秒数和补跑策略。不得把错过运行伪装为
成功，也不得因重启堆积执行所有 10 分钟小扫描。
每轮都必须统一复核全部既有 `SCHEDULED`，即使本轮没有新窗口也要应用
`max_lateness_seconds`；`LATEST_ONLY` 仅留最新 1 个，`IDEMPOTENT` 仅留配置的最近
N 个。进程在创建 run 后、合并前崩溃时，下一轮必须重新扫描既有候选并补建
`MERGED_RUN`。

## 5. 合并与单 UI 通道

同一平台、同一 `platform_trade_date`、计划时间相差不超过 5 分钟的 `ONLINE_PULSE` 与
`FULL_MARKET_SCAN/PRE_CUTOFF_FULL_SCAN` 可以形成覆盖候选。候选成立必须同时满足：

- 小扫描与目标扫描均启用且仍为 `SCHEDULED`；
- 当前 Automation Service 已注册目标扫描 handler；
- 平台、交易日和五分钟时间窗口一致。

候选期只建立从完整扫描父 run 指向小扫描的 `COVERAGE_CANDIDATE` 链接，小扫描仍为
`SCHEDULED`，但暂不领取，也不因迟到被标成 `MISSED`。候选不仅要求父扫描 handler，
还要求当前服务已经注册 `LISTING_STATUS_SCAN` handler；任一能力在重启后丢失时，
既有候选必须释放，即使目标父 run 已处于租约过期的 `RUNNING`。

完整扫描父 run 以 `SUCCESS/PARTIAL` 完成并创建合法 `LISTING_STATUS_CHILD` 后，候选
原子改指向该商品子 run。父 run 完成但没有商品子 run、父 run 失败/取消/错过，或
商品子 run 部分成功/失败/取消时，均释放候选，让小扫描回退执行。`ORDER_SCAN`
子 run 的成功或失败不参与商品覆盖判断。

只有 `LISTING_STATUS_SCAN` 子 run 以 `SUCCESS` 完成，并且同一输入清单已经形成任务
13 的 `VERIFIED` 双页权威快照及与该快照显式绑定的 v14 `ACCEPTED` 完整商品观察事实
后，才把小扫描原子推进为 `MERGED`。显式来源至少包含 snapshot ID、输入 manifest、
result SHA、来源平台交易日、来源映射身份摘要和标准转换摘要；不得依赖
`product-observation-{snapshot_id}` 等主键命名约定。最终判定还必须复核所有观察项
的 `platform_trade_date` 与 run 冻结交易日一致，并复核持久化观察 SKU/映射状态
与 snapshot 冻结身份一致。明确 SKU 必须逐项相等；`UNMAPPED/AMBIGUOUS` 必须冻结
并校验状态和候选 SKU 集合。Importer 只有在跨事实校验通过后才写入
`validated_mapping_identity_sha256`，最终覆盖要求该标记等于重算的来源身份摘要。
任一映射漂移都释放候选，不得覆盖脉冲。通过后建立最终关系：

```text
LISTING_STATUS_SCAN 子 run --MERGED_RUN--> 小扫描 run
```

父/子 handler 缺失、父 job 禁用或未接受权威业务事实时，绝不能让小扫描提前进入
终态。领取入口还必须使用当前可执行 handler 集合重复验证候选，不能依赖上次进程
留下的候选状态。

因此 17:55 截单前扫描绝不能覆盖 18:00 后已属于下一平台交易日的脉冲。

调度优先级从高到低为：

```text
UNKNOWN / RECONCILE
SYSTEM_EMERGENCY SET_OFFLINE（13.5-6 启用前保持禁用）
已正式授权写操作
18:00 边界扫描
20:00 所需作业
小时完整扫描
10 分钟小扫描
```

13.5-3 默认 gate 只读取既有账本：存在 `NEEDS_RECONCILIATION`、`UNKNOWN`
写锁或活动写锁时，不领取任何 UI 扫描 run；非 UI 的结算/计算 handler 可以独立运行。
它不把普通 `pending` 任务解释为执行授权，也不创建、启动或重启 ShadowBot。
阻断检查与 UI run 领取必须位于同一个 `BEGIN IMMEDIATE` 事务，并在每次领取前重查，
避免一次 cycle 内出现检查后写锁状态变化的 TOCTOU。

单 UI 通道是双向门禁，而不是只阻止 Automation 一侧：

- Automation UI run 从领取到 handler 完成期间保持 `RUNNING` 且持有有效租约；
- v4/v5 人工写任务在同一个 `BEGIN IMMEDIATE` 写锁事务内检查活动 Automation UI
  租约，存在活动租约时不得发布写任务或取得活动写锁；
- Automation 领取 UI run 时，在同一事务内反查既有写锁与 UNKNOWN/RECONCILE；
- 另一 Automation 实例也不得在首个 UI handler 持有有效租约时领取第二个 UI run；
- 非 UI 的结算和纯计算 run 不占用 UI 通道。

handler 执行时间可能超过初始租约时，必须通过执行上下文心跳续租；租约过期后才允许
另一实例回收。数据库门禁不能强制终止已经失去租约的外部 UI 动作，因此真实 UI
handler 还必须在关键步骤前续租并在失去租约时协作停止。

## 6. 租约、心跳与重启恢复

领取 run 必须在 `BEGIN IMMEDIATE` 事务中完成：

1. `SCHEDULED → RUNNING`；
2. 写入随机 `lease_owner`；
3. `lease_version + 1`；
4. 写入 `lease_expires_at`；
5. 追加 `RUN_STARTED` 事件。

handler 只能通过本次 `AutomationExecutionContext` 续租。完成写回必须同时匹配：

```text
run_status = RUNNING
lease_owner = 当前 owner
lease_version = 当前 version
lease_expires_at > 写回时间
```

租约超时后，另一实例可原子回收同一个 run，递增 `lease_version` 并追加
`LEASE_RECLAIMED`。旧实例的晚到结果必须被拒绝；当前周期一旦发现租约丢失，立即停止
继续领取，避免同一故障 handler 在一个周期内反复抢占。

任何自动化 handler 或 Importer 新增、替换商品、订单、结算等业务事实时，必须携带
当前 `AutomationRunClaim`，并在写入事实的同一个 `BEGIN IMMEDIATE` 事务内验证：

```text
run_status = RUNNING
lease_owner = 当前 owner
lease_version = 当前 version
lease_expires_at > 当前写入时间
```

当前商品观察导入和自动化绑定的任务 13 权威 `SYNC_STATUS` 导入均已落实这一合同；
后续订单和结算事实入口必须复用同一事务校验函数。Automation
`LISTING_STATUS_SCAN` 子 run 必须先把规范输入清单 SHA-256 不可变绑定到 run；
同一清单只能绑定一个 run。首次绑定必须在对应任务 13 `sync_status` 批次仍精确为
`PREPARED`、平台一致、尚未发布且不存在 result ID、结果回执或快照时，在同一事务内
完成；已经完成的人工历史清单不得事后绑定到新 Automation run。同一 run 的既有相同
绑定可以幂等返回。

Importer 在权威快照、投影、异常、人工复核和通知写入的同一事务内校验绑定、合法
父子链、平台、时间策略、冻结 `platform_trade_date` 和活动 claim。扫描开始、完成、
分页面及逐项观察任一时间跨越 18:00 并落入另一平台交易日时，不得作为该 run 的完整
事实，也不得触发脉冲覆盖。未绑定 Automation run 的人工任务 13 导入路径继续独立
存在，不强制伪造自动化 claim。

完全相同且不会新增或替换事实的幂等重放可以在复核绑定信封后直接返回既有结果；
任何不同内容仍必须持有有效 claim，且同一 Automation run 不得用另一批内容替换
已接收的规范事实。

涉及租约有效期、事实接收时刻等安全判定的当前时间必须来自应用服务注入的可信时钟；
生产调用方不能通过导入参数指定 `now` 来延长或回拨 claim。可信时钟只能在成功取得
`BEGIN IMMEDIATE` 后采样，避免等待 SQLite 写锁期间租约已到期却继续使用锁前旧时间。
领取、续租、完成、父子创建、输入清单绑定及事实导入均遵循这一原则；计划窗口的逻辑
时间仍与安全租约时间分离。

handler 异常只写入受限的稳定错误码：

- `AUTOMATION_HANDLER_TIMEOUT`
- `AUTOMATION_HANDLER_FAILED`

错误文本限制长度并清除明显的本机绝对路径。Scheduler 不吞掉失败，也不把异常转换为
成功。

禁用的普通 job 不得领取新的 `SCHEDULED`；已经开始、后来被禁用的过期 `RUNNING`
仍允许回收，以便确定性收敛到终态。`CHILD_ONLY` 即使默认禁用，也只在存在受支持父
类型、关系且父 run 已为 `SUCCESS/PARTIAL` 后才允许领取；父 run 仍为 `RUNNING`
时子 run 只能保持 `SCHEDULED`，父 run 失败、取消或错过后，仍处于 `SCHEDULED`
的子 run 必须原子取消。父 handler 创建子 run 时，父租约校验、子
类型/关系/平台约束、继承父 run 冻结时间上下文、幂等创建和链接写入必须在同一事务；
任一环节失败不得留下孤儿子 run。

公开的 `claim_run(...)` 与按优先级领取必须经过同一套启用状态、父链、
`COVERAGE_CANDIDATE` 和单 UI 通道门禁；不得保留可绕过策略的直接领取入口。

## 7. 进程生命周期与健康

正式入口使用跨平台非阻塞文件锁保证同一主机只运行一个 Automation Service 进程。
锁身份由规范化 Runtime DB 路径派生，不由可配置的 heartbeat 路径派生；指向同一
Runtime DB 的两个进程即使使用不同 heartbeat，也必须互斥。
进程心跳独立保存在：

```text
data/runtime/automation_service/heartbeat.json
```

心跳使用 UTF-8、同目录临时文件、`fsync` 和 `os.replace` 原子更新，至少记录：

- schema version；
- `RUNNING / STOPPED`；
- service instance；
- 最近 cycle 时间；
- scheduled/missed/merged/claimed/completed 数量；
- 截断窗口数；
- UI 阻断原因；
- Runtime run 状态计数和过期 `RUNNING` 数量。

`--once` 用于只执行一个调度周期和健康检查。13.5-3 的 CLI 运行模式明确为
`SCHEDULER_ONLY`：只有后续阶段注册经过验收的 handler 后才会领取对应 run。
获取锁后的未处理异常必须原子写入 `FAILED` 心跳，错误文本执行与 handler 相同的路径
脱敏；锁冲突发生在获得所有权前，因此不得覆盖现有实例的活动心跳。

## 8. 验收与安全结论

专项测试必须覆盖：

- 重复轮询幂等；
- 完整扫描候选经商品子任务权威事实覆盖小扫描，订单子任务不影响结果；
- 休眠与 `MISSED`；
- 长时间停机风暴上限；
- 单 owner 领取；
- 租约心跳；
- 租约超时、重启回收和旧 owner fencing；
- handler 异常；
- job 优先级；
- 父子 run 幂等；
- 父租约过期/被回收、子链接回滚、跨平台和策略切换；
- UNKNOWN/RECONCILE 阻断；
- 每次领取的原子 UI gate；
- 首个 UI handler 整个执行期间第二实例无法领取另一 UI run；
- v4/v5 写任务在取得写锁前反向检查活动 Automation UI 租约；
- 业务事实与 Automation claim 的同事务 fencing、过期 owner 与被回收 owner；
- 覆盖候选从父 run 转交商品子 run，只有权威双页快照和对应观察事实均接受后转为
  `MERGED`；无 handler、重启能力丢失、无商品子任务、无事实、部分成功和失败均回退；
- 自动化 `SYNC_STATUS` 清单不可变绑定、同事务 claim fencing、人工导入隔离及
  可信时钟；
- 历史人工清单事后绑定拒绝、`17:55→18:05` 和逐项跨 18:00 拒绝；
- snapshot ID、manifest、result SHA、交易日和标准转换摘要的显式不可变来源链，
  且合法 observation ID 不受命名约定限制；
- snapshot 明确 SKU、`UNMAPPED/AMBIGUOUS` 状态和候选集合的来源冻结；当前
  v14 映射漂移时零写观察事实且最终覆盖回退；
- 安全时钟在取得 `BEGIN IMMEDIATE` 后采样；
- 禁用 job、合法 `CHILD_ONLY` 父链与默认 job 静态漂移；
- 子 run 在父 run 完成前不可领取，父失败时自动取消；
- Runtime 时间策略热加载；
- 单实例进程锁；
- 同 Runtime DB 不同 heartbeat 的锁冲突与心跳保护；
- UTF-8 原子心跳；
- `--once` 入口。

本阶段验收只证明控制面、文件内容和本地 Runtime 测试库正确，不代表真实 Runtime DB
已迁移、ShadowBot 实际扫描成功或生产无人值守已启用。
