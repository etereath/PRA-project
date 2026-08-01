# 任务 13.5-6：异常人工闭环与受控紧急保护审查计划

- 计划日期：2026-08-02
- 状态：业务规则已逐项评议；本 PR 只冻结计划，不实现业务代码
- Review Profile：`R4`；13.5-6C 的真实写链额外执行完整 `R3` 门禁
- 当前平台：蚂蚁花团供应商微信小程序；公共核心保持平台无关
- 真实平台写操作：13.5-6A/6B 为否，13.5-6C 仅允许一次受控 `SET_OFFLINE`
- 当前生产开关：`automatic_emergency_offline=false`
- 新增平台动作类型：0
- 基线：`main@36cf2babf148cfabc038416e359c2e56a603cd9e`
- 宏观权威：GitHub Issue #20
- 复用权威：`AGENTS.md`、任务 12/13 最终交接、复用资产清单和复用失败复盘
- 风险治理：`docs/pra_review_risk_and_complexity_governance.md`

## 1. 本阶段结果

13.5-6 不建设新的执行系统。它在现有 v14 Incident 预留、Review、飞书 Outbox、
Automation Run、任务来源和任务 12/13 写链之上，补齐一个适合小型家庭农场的异常闭环：

```text
可信检测
→ 创建或更新 Incident，并追加不可变事件
→ 按业务状态通知和请求复核
→ 人工选择“改价到 / 立即下架 / 我来处理”
→ 或在极端低价仍存在且没有复核结果时进入受控自动保护
→ 复用既有 UPDATE_PRICE / SET_OFFLINE
→ 复用 Worker → Importer → Archive
→ UNKNOWN 只进入唯一 RECONCILE
```

本任务只允许极端低价使用无人值守紧急下架。`WRITE_UNKNOWN`、运行库异常、时间异常等
即使是 S4，也只进入各自的安全恢复路径，不能借严重度直接取得下架授权。

## 2. 子阶段与 Schema 顺序

### 2.1 13.5-6A-0：合同与 Runtime Schema v15

- 冻结异常类别、严重度、核心状态机、指纹、事件类型和 FINAL 阻断语义；
- 为 `operational_incidents` 增加 `occurrence_count`；
- 新增一张 append-only `operational_incident_events`；
- 扩充 Incident 类别 CHECK/Enum；
- 不实现平台写任务，不修改 ShadowBot。

### 2.2 13.5-6A-1：Incident、复核、通知与恢复闭环

- 创建、合并、重开、解决和关闭 Incident；
- 复用 `review_tasks / review_tokens / Mobile Review`；
- 复用 `notification_outbox / notification_delivery_attempts / FeishuOutboxSender`；
- 按异常状态和业务动作提醒，不按 S3/S4 固定频率无限广播；
- Worker 不可用时调用现有完整恢复程序；
- 保持 `automatic_emergency_offline=false`。

### 2.3 13.5-6B：Runtime Schema v16 与影子判定

- 新增一张极简 `emergency_offline_policies`；
- 固定 `base_cost`、`0.80` 紧急比例和 Decimal 计算；
- 复用同一规则解释器执行 shadow/dry-run；
- 不创建业务任务、队列请求或平台副作用。

### 2.4 13.5-6C：专用授权与受控实机

- 仅在极端低价仍存在、没有复核结果且完整等待条件满足时授权；
- 授权证据复用 Automation Run/Event，不新增授权表；
- 同事务创建唯一 `SYSTEM_EMERGENCY` 任务并进入 `AUTO_PROTECTING`；
- 复用 v5 `SET_OFFLINE`、共享写锁、Importer 和唯一 RECONCILE；
- 单测试 SKU、单目标、临时开启开关，验收后立即恢复 `false`；
- 不自动重新上架。

## 3. 明确非目标

- 通用策略表达式、规则优先级、继承或第二套策略引擎；
- 企业级 Incident 指派、工单队列、SLA、评论串、多层审批和复杂权限；
- 新的任务状态、平台动作、Executor、写锁、通知账本或 UNKNOWN 恢复路径；
- S3 自动改价或自动下架；
- `WRITE_UNKNOWN` 自动下架；
- 自动重新上架；
- 第二平台、同平台多账号或多 Worker 自动接管；
- Incident 高级 Web UI；该展示属于 13.5-9；
- 销售预测模型、生产包装冷库 ERP、订单或资金写操作；
- 24/7 正式启用；任务 14/15 承接正式授权与连续观察。

## 4. 复用优先门禁

编码前必须按“原样复用 → 参数化复用 → 抽取公共能力 → 确需新增”裁决。下面矩阵是
13.5-6 的开工基线；实施报告必须逐项回填代码入口、回归结果和实机证据。

| 职责 | 既有入口或证据 | 裁决 | 本次最小差异 | 必须回归 |
| --- | --- | --- | --- | --- |
| Incident 主记录与开放指纹唯一性 | v14 `operational_incidents`、`ux_operational_incidents_open_dedupe` | 参数化复用 | 增加 `occurrence_count`，扩充类别；保留现有主键、日期和 FINAL 阻断字段 | v14 迁移、开放去重、日结 FINAL |
| Incident 时间线 | v14 只有主记录，没有逐次可信检测和状态变化账本 | 确需新增 | 一张 append-only `operational_incident_events`；不得同时新增评论、指派或审计表 | 精确重放、事件冲突、事务回滚 |
| 通知节流状态 | `incident_notification_state` | 原样复用 | 使用 `escalation_state / next_notification_at / payload_sha256` 表达提醒阶段 | 到期领取、幂等、关闭停止 |
| 通知意图和投递 | `notification_outbox`、`notification_delivery_attempts`、Feishu sender | 原样复用 | 新增 Incident/Review 通知模板与稳定业务 key | 原子 Outbox、重试、UNKNOWN_DELIVERY |
| 人工复核记录与 Token | `review_tasks`、`review_tokens`、现有 Mobile Review 路由 | 原样复用 | 新增 `emergency_protection` review type、中文动作标签和 payload 校验 | Token、过期、并发、CSRF/PRG |
| Mobile Review 原子提交 | `resolve_mobile_review_atomic(...)` 当前强制已有 `source_task_id` | 参数化复用 | 支持“先复核后建任务”的 Incident Review；保留同一路由、Token 和单事务语义 | 既有 source task 流程 + 无 source task 决策流程 |
| 时间归属 | `OperationalTimeService` | 原样复用 | 无；跨 18:00 使本轮复核和等待证据失效 | 17:59/18:00/20:00 |
| 调度、租约和事实 fencing | `automation_jobs/runs/events/links`、ExecutionContext | 原样复用 | 注册 Incident 评估和提醒 Handler，不新增 Scheduler | 租约、重放、旧 owner fencing |
| Worker 健康和完整恢复 | 现有 Queue Watchdog、生命周期文件、AGENTS 异常恢复链 | 原样复用 | Incident 只调用既有健康/恢复入口并记录结果 | 单实例、活动请求保护、恢复成功/失败 |
| 商品成本 | 商品主数据 `base_cost` 与商品入库门禁 | 原样复用 | 作为最低安全价；不建立第二成本源 | 缺失/非法成本 fail closed |
| 人工改价 | 任务 12 v4 `UPDATE_PRICE` 正式链 | 原样复用 | Review 结果生成明确人工来源任务，目标价不得低于 `base_cost` | v4 基线、旧价门禁、UNKNOWN |
| 人工及自动下架 | 任务 13 v5 `SET_OFFLINE` 正式链 | 原样复用 | 人工结果生成 MANUAL 任务；无结果自动路径生成 SYSTEM_EMERGENCY 任务 | v5 基线、ALREADY_APPLIED、UNKNOWN |
| 写锁、operation/attempt 和发布边界 | v12/v13 共享控制面 | 原样复用 | 无 | 跨动作锁、确定未发布/可能已发布 |
| Result Importer、receipt、ACK 和归档 | 任务 12/13 文件队列闭环 | 原样复用 | 无 | 完整预校验、单事务投影、Archive |
| UNKNOWN 恢复 | `ShadowBotExecutor.ensure_reconcile_attempt(...)` | 原样复用 | 无 | 唯一 RECONCILE、不得自动重试 COMMIT |
| 紧急下架参数 | v14 未有正式策略结构 | 确需新增 | 一张极简、版本化 `emergency_offline_policies` | 版本替换、默认禁用、比例精度 |
| 无人值守授权入口 | 通用 Repository 明确拒绝 `SYSTEM_EMERGENCY` | 确需新增 | 一个专用 application service；授权证据写 Automation Event | 幂等、同事务回滚、来源不可变 |

### 4.1 Mobile Review 的已知兼容缺口

当前移动复核的事务在消费 Token 前强制要求 `source_task_id` 和已有任务，适合“任务先
存在、人工决定如何处理”的旧流程。紧急保护是“Incident 先存在，人工选择动作后才生成
改价或下架任务”。因此不得谎称原样可用，也不得另建移动端：

1. 继续使用现有 GET/POST 路由、Token hash、允许动作、过期、一次性消费和 PRG；
2. 为 `review_type=emergency_protection` 增加 decision-first 分支；
3. 在同一个 `BEGIN IMMEDIATE` 中重读 Incident、Review、Token、当前观察和写锁；
4. 条件更新 Review 并消费 Token；
5. `approved/adjusted` 通过既有任务应用服务创建唯一任务；`rejected` 不创建任务；
6. 任一步失败整体回滚，不留下已消费 Token、半个 Review 或孤立任务；
7. 既有依赖 source task 的 Mobile Review 流程不得改变。

### 4.2 禁止重写

- 不复制 v4/v5 proposal、gate、manifest、Worker 点击或 Importer；
- 不直接拼 ShadowBot 队列 JSON；
- 不新建紧急 Worker、第二 Watchdog 或第二恢复控制器；
- 不新建 Incident 通知日志、Review 状态机或授权表；
- 不让 Web、Scheduler、Incident Repository 直接创建 `SYSTEM_EMERGENCY`；
- 不用固定坐标、盲目重试或严重度绕过页面身份和旧状态校验；
- 不在 UNKNOWN 后再次执行写操作；
- 不把通知成功、Review 处理或 Incident 状态当作平台事实。

## 5. 部署假设和最坏事故

当前部署为单机 SQLite、单 Automation Service、单平台单账号、单长期 ShadowBot
Worker。多平台身份字段仍保留，但不为尚不存在的多账号/分布式接管增加控制面。

必须防止：

- 正常商品被错误下架；
- 人工已选择处置，系统仍走无人值守路径；
- 复核通知未送达却被误判“无人处理”；
- 相同检测重放造成 occurrence、Review、通知或任务重复；
- 不同平台、SKU、交易日或 Incident 串用；
- `base_cost` 缺失、非法或使用浮点误差仍判定 S4；
- 不完整扫描、映射失败、跨 18:00 或旧观察仍授权；
- Worker/Importer 失败后重复副作用；
- 真实 Runtime DB 损坏或迁移失败；
- 自动下架后自动重新上架。

失败默认停止提升权限、保留证据并转人工。平台副作用不明只走唯一 RECONCILE。

## 6. Incident 持久化合同

### 6.1 小型农场必要字段

继续使用 v14 主表字段：

```text
incident_id
dedupe_key
category
source_type / source_ref_id
severity
incident_status
blocks_finalization
platform_name
platform_trade_date / seller_operation_date
subject_type / subject_key
title / description
first_detected_at / last_detected_at
resolved_at
created_at / updated_at
```

v15 只增加：

```text
occurrence_count
```

不增加 assignee、部门、SLA、评论、标签、父子工单、复杂审批或多阶段责任人。现有
`title/description` 为兼容字段；运营页面优先展示中文类别、影响和下一动作，不把内部
ID、hash 或 JSON 放在主文案。

### 6.2 不可变事件表

`operational_incident_events` 只保存状态演进和可信证据引用：

```text
event_id
event_key            # UNIQUE，防检测重放
incident_id
event_type
occurred_at
source_type
source_ref_id
from_status
to_status
severity
event_payload_json   # 只保存小型结构化原因、观察/Review/Run/Task 引用
created_at
```

事件 append-only，禁止 UPDATE/DELETE。它不复制 Review resolution、Outbox 投递状态、
Automation Run 或 ShadowBot 账本，只保存对应 ID 和业务事件。

`occurrence_count` 只在新的、可信且 `event_key` 未出现的检测事件写入时增加一次；
Importer 重放、Scheduler 重试、Outbox 重试和同一事实的重复投影都不增加。

### 6.3 指纹

`dedupe_key` 使用稳定业务范围：

```text
category
+ platform_name（适用时）
+ subject_type
+ subject_key
+ platform_trade_date（只在异常天然按交易日隔离时）
+ stable_reason_code（确有多个稳定子原因时）
```

严重度、来源 Run、检测时间、标题、策略版本和通知阶段不进入普通开放 Incident 指纹。
已 `CLOSED` 后相同问题再现创建新 Incident；`RESOLVED` 但尚未关闭时再现，重开原
Incident 并追加事件。

## 7. 核心状态机

新流程只使用五个核心状态：

```text
OPEN
WAITING_HUMAN
AUTO_PROTECTING
RESOLVED
CLOSED
```

允许转换：

```text
OPEN → WAITING_HUMAN | AUTO_PROTECTING | RESOLVED
WAITING_HUMAN → OPEN | RESOLVED
AUTO_PROTECTING → WAITING_HUMAN | RESOLVED
RESOLVED → OPEN | CLOSED
CLOSED → 终结；再现时创建新 Incident
```

`ACK` 是事件，不是主状态。v14 的 `ACKNOWLEDGED` 和 `RETRYING` 仅为历史兼容值；
13.5-6 新业务流不得写入。技术重试由 Automation/Outbox/Worker 自己的既有状态表达。

- `ACK` 只证明有人看到，不等于复核结果或问题解决；
- `RESOLVED` 必须有条件恢复、人工裁决或执行回读证据；
- `CLOSED` 表示该次异常处置结束；
- 严重度变化和重开必须追加事件，不静默覆盖历史。

## 8. 异常类别与业务处理

运营表和通知使用中文名称；英文代码只用于数据库、合同和排障。类别不使用人造编号。
严重度是当前业务风险，不是类别的永久属性；下表给出初始处理和常见升级方向。

| 运营可读类别 | 稳定代码 | 初始处理 | 升级或恢复路径 |
| --- | --- | --- | --- |
| 平台登录异常 | `PLATFORM_LOGIN` | 停止本轮 UI 作业，按既有登录/验证码人工接管 | 持续不可用升 S3；登录恢复后复扫 |
| 平台网络异常 | `PLATFORM_NETWORK` | 保留失败 Run，等待下次只读扫描 | 连续失败或关键时点升 S3 |
| 页面结构异常 | `PAGE_STRUCTURE` | 已知页面复现、零写保存证据，不立即改选择器 | 确认漂移后人工维护 Adapter |
| 扫描不完整 | `SCAN_INCOMPLETE` | 不接受完整事实，不生成写授权 | 下一次完整扫描恢复 |
| Worker 不可用 | `WORKER_UNAVAILABLE` | 立即运行第 10 节完整恢复程序 | 一轮恢复失败转人工并持续报告恢复结果 |
| 队列积压 | `QUEUE_BACKLOG` | 检查活动 working/result、租约和处理速度 | 影响关键作业升 S3，不重复投递 |
| 商品映射异常 | `PRODUCT_MAPPING` | 阻止该商品自动动作，提示修正主数据 | 映射重新验证后解决 |
| 价格异常 | `PRICE_ANOMALY` | 按第 11 节 S1～S4 分级并发起复核 | 仅极端低价 S4 可评估自动保护 |
| 库存异常 | `INVENTORY_ANOMALY` | 保留观察，不把未知变化当销量 | 新完整观察或人工解释后解决 |
| 订单页面不可用 | `ORDER_PAGE_UNAVAILABLE` | 商品扫描事实仍可独立接受 | 影响结算时按范围阻断 FINAL |
| 订单数据不一致 | `ORDER_DATA_INCONSISTENT` | 保留冲突批次，禁止混合或覆盖 | 完整重读/人工对账后解决 |
| 销售估算置信度低 | `SALES_ESTIMATE_LOW_CONFIDENCE` | 报告中标明估算，不伪装订单事实 | 订单回补或合格区间后解决 |
| 通知投递异常 | `NOTIFICATION_FAILURE` | 复用 Outbox 重试和 UNKNOWN_DELIVERY | S4 Review 首次通知未确认时阻止无人值守 |
| 写结果不明 | `WRITE_UNKNOWN` | 保持写锁，创建唯一只读 RECONCILE | S4 但不得触发紧急下架 |
| 自动化服务异常 | `AUTOMATION_SERVICE` | 检查进程锁、心跳、租约和迟到 Run | 恢复后补跑有价值窗口 |
| 运行数据库或存储异常 | `RUNTIME_STORAGE` | fail closed，备份/健康检查/人工恢复 | 不允许任何平台写授权 |
| 队列或结果导入异常 | `QUEUE_IMPORT` | 保留原文件和校验和，按 Importer 既有分类处理 | 可能有副作用时转 WRITE_UNKNOWN |
| 交易日或系统时间异常 | `TRADE_DAY_TIME` | 停止时间敏感运行和授权 | 时钟/策略恢复并重新物化 Run |
| 商品上下架状态异常 | `LISTING_STATE` | 完整两页 SYNC_STATUS，阻止自动覆盖 | 明确位置事实后解决 |
| 成本或经营主数据异常 | `MASTER_DATA` | 阻止价格分级和写动作，要求修复主数据 | `base_cost` 合法并重新验证后解决 |
| 日结处理异常 | `SETTLEMENT_PROCESSING` | 保留当前版本和输入，不伪造 FINAL | 修复后版本化重结算 |
| 人工复核通道异常 | `REVIEW_CHANNEL` | 阻止“无复核结果”自动推断 | Token/Outbox/页面恢复后重新发起 |

登录凭据错误、日期选择失败、单次通知重试、映射候选、普通 Review 过期等继续作为稳定
reason code 或既有子状态，不为每个技术细节增加类别。

## 9. FINAL 阻断规则

`blocks_finalization` 与 S0～S4 正交。只有异常使某个平台、某个
`platform_trade_date` 的结算输入不可信时，才阻断该范围的
`RECONCILED → FINAL`。

- 前一交易日未 FINAL 不阻止下一交易日创建和结算；
- 同一个持续故障若分别损害两个交易日，可各自形成范围明确的阻断事实；
- 历史缺口不得按零处理，迟到数据使用版本化重结算和 `supersedes`；
- S4 不必然阻断 FINAL，S2/S3 若破坏订单完整性也可以阻断；
- 服务和错误文案不得继续声称“所有 S3/S4 都阻断 FINAL”。

## 10. Worker 不可用的恢复闭环

Worker 不可用时不能只等待或反复告警。必须复用 `AGENTS.md` 和现有运维脚本中的完整
恢复链，不建立第二 Watchdog：

1. 读取生命周期记录，再核对新鲜 heartbeat、`stop.signal`、`inbox/working/results`；
2. 存在活动 working 或未导入 result 时，先保留 phase/结果并完成导入，不启动第二实例；
3. Worker 已停止、队列空且影刀应用列表可定位时，正常启动 `test2`；
4. Worker 停止或心跳失效且影刀主窗口不可定位时，确认无活动请求和未保存编辑内容；
5. 必要时只结束已核实路径的残留 `ShadowBot.Shell.exe`，重启影刀并等待至少 20 秒；
6. 定位应用列表中的 `test2`，启动后以新鲜 `RUNNING` heartbeat 为成功；
7. 若 `stop.signal` 卡住活动请求，保留 phase 后发送 `Ctrl+Alt+Q`；只有主窗口仍不可定位
   才进入进程结束；
8. 更新生命周期记录并追加 Incident 恢复事件。

恢复控制器使用单实例租约，每次 Incident 最多自动执行一轮完整重启。成功后通知
“Worker 已恢复”；失败后通知“自动恢复未成功，请人工处理”，不重复发送原始故障。
若恢复检查发现写副作用不明，立即转 `WRITE_UNKNOWN` 和唯一 RECONCILE。

## 11. 价格异常和 S3/S4 合同

### 11.1 成本与计算

- 最低安全价等于商品主数据 `base_cost`；
- 当前所有可入库商品都必须有合法 `base_cost`；缺失或非法值属于主数据损坏，创建
  `MASTER_DATA` Incident，并停止该商品价格分级；
- 紧急比例固定为 `0.80`；
- 紧急线为 `base_cost × 0.80`；
- 金额与比例使用 Decimal 精确计算，禁止 binary float；
- `base_cost` 是每次判定时重新读取的权威主数据，不复制到策略表作为第二事实源。

在商品已经被价格规则识别为“异常低价”后，按下表分级：

| 当前观察价 `P` 与基础成本 `C` | 处置 |
| --- | --- |
| `P >= C` | 首次可信异常为 S1；连续两次可信观察仍异常升 S2 |
| `0.80 × C < P < C` | S3，立即发起人工复核，绝不无人值守执行 |
| `P <= 0.80 × C` | S4，立即发起人工复核；满足全部门禁后才可能无人值守下架 |

S4 严重度本身不授权写操作。当前自动 allowlist 只有“极端低价且商品仍在线”。
`WRITE_UNKNOWN`、数据库异常、时间异常、通知异常等 S4 使用各自处理器，不能创建
`SYSTEM_EMERGENCY SET_OFFLINE`。

### 11.2 S3/S4 人工复核选项

飞书和 Mobile Review 只显示三个业务动作：

1. `改价到（输入值）`：映射为现有 `adjusted`，要求 `target_price >= base_cost`，
   创建明确人工来源的 v4 `UPDATE_PRICE` 任务；
2. `立即下架`：映射为现有 `approved`，创建明确人工来源的 v5 `SET_OFFLINE` 任务；
3. `我来处理`：映射为现有 `rejected`，不创建平台任务，Incident 进入
   `WAITING_HUMAN`。

`cancelled` 仅供系统在异常条件已恢复时取消 pending Review；`expired` 只由系统产生，
表示在有效等待窗口内没有复核结果。任一人工复核结果一旦提交，本轮无人值守路径立即
结束；ACK 不是复核结果。

## 12. 通知和复核时限

通知按“当前需要什么动作”发送，不按严重度固定周期无限重复：

| 场景 | 通知规则 |
| --- | --- |
| S0 | 只记录，不主动通知 |
| S1 | 进入每日摘要 |
| S2 | 首次通知；目标响应时限 2 小时，之后按较长周期汇总 |
| S3 | 立即通知；目标响应时限 30 分钟 |
| S4 极端低价 | 立即发送 Review；等待一个完整 ONLINE_PULSE 周期 |
| `我来处理` | 停止无人值守路径；S4 30 分钟、S3 2 小时后询问处理结果，之后降为较长周期 |
| 写任务已创建 | 停止 Incident 动作提醒，改为通知任务成功、失败或 UNKNOWN |
| 条件恢复 | 发送一次恢复通知并停止后续提醒 |

S4 价格 Review 最多发送两条：初始消息，以及在等待周期过半且既无 ACK 也无复核结果时
发送一次中途提醒。到期后进入人工结果或自动评估，不再每 5 分钟广播。

Review、Token 和初始 Outbox 必须同事务创建。只有 Outbox 明确 `SENT` 才开始复核等待
窗口；最终投递失败或 `UNKNOWN_DELIVERY` 时不得把“没有复核结果”解释为无人介入，
自动保护保持阻断。Outbox 重试只重试投递，不创建新 Review 或新业务提醒身份。

ACK 事件抑制高频中途提醒，但不关闭 Incident；对 S4 来说，ACK 也不算复核结果。

## 13. 一个完整 ONLINE_PULSE 的等待合同

S4 第一次可信观察只创建/更新 Incident、Review 和通知。自动评估所需的第二次观察
必须满足：

1. 是另一个不同的、已完成并成功导入的 `ONLINE_PULSE`；
2. 对应目标商品的观察完整、映射为 `VERIFIED`、价格可读且仍在线；
3. 逻辑计划时间至少到达第一次观察之后的下一个 10 分钟计划槽；
4. 第一次和第二次观察属于同一平台、SKU 和 `platform_trade_date`；
5. 下一次 Pulse 失败、不完整、错过或没有该目标时，只延后评估，不能由计时器授权；
6. 跨越 18:00 时本轮等待和 Review 失效，必须以新交易日观察重新建轮次；
7. 第二次观察时重新读取 `base_cost`、策略版本、开关、Review、写锁和平台事实。

自动评估只需要确认：

1. 紧急情况仍然存在；
2. 没有复核结果返回。

不再维护“确认没有人操作过平台”的广泛猜测。人工介入通过 Review 结果表达；平台事实
是否变化由第二次观察和既有写前读取表达。任何复核结果都停止本轮自动路径。

## 14. 极简紧急下架策略

`emergency_offline_policies` 不是通用规则引擎，只保存小量、版本化的批准参数：

```text
policy_version
platform_name
emergency_ratio       # 当前固定 0.80
approved_by
approved_at
created_at
retired_at
```

固定安全门禁写在 application service 和公共合同中，不转成自由表达式。最低安全价直接
读取商品 `base_cost`，不在策略表重复保存。当前单平台单账号不增加 account scope、
优先级、继承或条件 JSON。

- 只有管理员（当前用户）可以创建、批准、替换和启用策略；
- 运营人员只处理 Review 和任务；
- 已批准策略不可原地修改，新版本替代旧版本；
- 同一平台同一时刻只允许一个已批准、未退休版本；
- `automatic_emergency_offline` 是与策略独立的功能开关，默认 `false`；
- 13.5-6B 只 shadow；13.5-6C 受控实机临时开启，验收后恢复 `false`；
- 不允许测试绕过生产门禁；
- 删除原计划中的“每商品每日次数上限”和“冷却时间”。人工重新上架后若条件仍满足，
  按正常新的 S3/S4 轮次重新检测、复核和处置。

## 15. 无人值守授权与事务边界

不新增授权表。专用服务在 Automation Run 中追加不可变
`EMERGENCY_OFFLINE_AUTHORIZED` 事件，至少绑定：

```text
authorization_id / event_key
incident_id
policy_version
platform_name / internal_sku / platform_trade_date
first_observation_id / second_observation_id
observation content hashes
wait window and completed pulse run id
automatic_emergency_offline flag state
expected current online state and price
action = SET_OFFLINE
expires_at
```

同一个 `authorization_id/event_key` 只能创建一个任务。以下操作位于同一个
`BEGIN IMMEDIATE`：

1. 重读并校验 Incident、pending/expired Review、策略、功能开关和两次观察；
2. 确认没有复核结果、没有活动写锁或未决 UNKNOWN；
3. 写入授权 Automation Event；
4. 通过专用入口创建唯一 `origin_type=SYSTEM_EMERGENCY`、
   `origin_ref_id=emergency:<authorization_id>` 的 `SET_OFFLINE` 任务；
5. Incident 转为 `AUTO_PROTECTING` 并追加事件。

任一步失败整体回滚。通用 Repository 继续拒绝创建或改写 `SYSTEM_EMERGENCY`。
任务发布前继续由 v5 application service 在写锁事务中重验任务、Review、Automation UI
租约和完整载荷 hash；Worker 在最终点击前重新读取并校验商品身份与当前状态。

竞态规则：

- 人工复核结果先于平台最终点击到达时，待执行自动任务失效；
- 平台已经下架时返回 `ALREADY_APPLIED`，不重复点击；
- 平台事实漂移为其他状态时停止覆盖并转人工；
- 最终点击后副作用不明时进入 `WRITE_UNKNOWN`，只允许唯一 RECONCILE；
- 自动下架 `VERIFIED` 后 Incident 进入 `WAITING_HUMAN`，等待人工后续经营处置；
- 不自动重新上架；人工重新上架后按正常 S3/S4 条件重新评估。

## 16. 复杂度预算

```text
Runtime Schema v15：occurrence_count + 1 张 Incident Event 表
Runtime Schema v16：1 张 emergency_offline_policies 表
13.5-6C 新表：0（使用 Automation Event 作为授权证据）
新增 Incident 主状态：0
新增 Review/Token/通知账本：0
新增平台动作类型：0
新增 Executor/点击链：0
新增写锁/全局锁：0
新增 UNKNOWN 恢复：0
新增自动重新上架：0
新增无人值守授权入口：1 个专用 application service
```

Incident 事件表解决“同一异常多次发生、检测重放、状态变化和恢复证据无法仅由主行表达”
这一当前需求，因此是必要复杂度；企业工单字段和通用策略引擎没有当前必要性。超过预算
必须暂停编码并重新评审。

## 17. 测试和验收

开发期间只运行改动模块和直接依赖；每个子 PR Ready for review 前按 R4/R3 集中运行
完整门禁。

### 17.1 v15 与 13.5-6A

- 新库、v14→v15 带数据迁移、重复迁移、失败回滚和 health；
- 新增类别精确集合、非法类别拒绝；
- 新检测增加一次 occurrence；精确重放不增加；同 key 异内容冲突；
- 开放去重、RESOLVED 重开、CLOSED 后新建；
- 核心状态转换和非法转换；ACK 只写事件；
- Incident 主行与 Event 同事务，数据库失败整体回滚；
- FINAL 只按平台和交易日范围阻断，前一日不阻塞下一日；
- Review/Token/Outbox 原子创建；投递失败阻断自动推断；
- 三个复核选项和 `target_price >= base_cost`；
- decision-first Mobile Review 与既有 source-task Review 全回归；
- S4 最多两条 Review 通知、ACK 抑制中途提醒、恢复/任务结果通知；
- Worker 完整恢复成功、失败、活动 working/result、stop.signal 和 WRITE_UNKNOWN 分支；
- 零任务、零队列、零平台副作用。

### 17.2 v16 与 13.5-6B

- 策略默认不可授权、批准后不可原地修改、替代和退休；
- `base_cost` 缺失/非法创建主数据异常并 fail closed；
- Decimal 边界：`C`、`0.80C` 及边界上下最小货币单位；
- 异常低价的 S1/S2/S3/S4 分级；S3 永不自动；
- 只有极端低价 S4 进入自动 allowlist；其他 S4 拒绝；
- 一次观察、同一 Pulse 重放、下一槽未到、失败/不完整 Pulse、跨 18:00 均拒绝；
- Review 有任一结果时拒绝；ACK 单独存在时仍等待 Review；
- feature flag 关闭、策略退休、映射/价格/在线状态不可信、写锁/UNKNOWN 均拒绝；
- shadow 与真实授权使用同一解释器；shadow 零任务、零队列、零平台副作用；
- 不存在次数上限、冷却和自动重新上架逻辑。

### 17.3 13.5-6C

- Automation Event 授权证据完整、幂等和冲突检测；
- 授权 Event、SYSTEM_EMERGENCY task 与 Incident 状态同事务；
- 通用 Repository 不能伪造或事后修改来源；
- 人工结果在任务创建前、创建后和平台点击前到达的竞态；
- 平台已下架 `ALREADY_APPLIED`；状态漂移零覆盖；
- 完整复用 v5 request/phase/result、共享写锁、Importer、receipt、ACK、Archive；
- `UNKNOWN` 不重试，只创建唯一 RECONCILE；
- 成功后 `WAITING_HUMAN`，无自动重新上架；
- 全量 pytest、系统冒烟、Linux/Windows CI；
- 真实 Runtime DB backup/迁移/health；
- 单测试 SKU、单目标、临时开关、受控真实 `SET_OFFLINE`、数据库回读和脱敏证据；
- 验收后开关回到 `false`，Worker/Importer/Archive 状态一致。

## 18. 第一轮代码审查冻结清单

第一轮审查应一次性覆盖：

1. 不同平台、SKU、交易日或类别串案；
2. 检测重放重复增加 occurrence、Review、通知或任务；
3. ACK 被当成复核结果或 RESOLVED；
4. 通知未确认送达却启动等待或自动保护；
5. Decision-first Mobile Review 产生半消费 Token、半 Review 或孤立任务；
6. Worker 不可用只等待、重复启动或覆盖活动请求；
7. `base_cost` 缺失、非法或浮点误差仍判定；
8. S3 或非价格类 S4 取得自动下架授权；
9. 没有第二个完整 Pulse、没有跨计划槽或跨 18:00 仍授权；
10. 已有复核结果仍走无人值守路径；
11. 策略未批准、已退休、开关关闭或观察/映射/写锁不可信仍授权；
12. 相同授权创建多个任务，或通用 Repository 伪造 SYSTEM_EMERGENCY；
13. Web/Scheduler/Incident Service 绕过 v4/v5、gate、写锁或队列；
14. 人工状态漂移被旧证据覆盖；
15. UNKNOWN 自动重试而不是唯一 RECONCILE；
16. 紧急下架后自动重新上架；
17. 前一交易日未 FINAL 无条件阻塞下一交易日；
18. 未完成受控实机就宣称生产启用。

后续复审原则上验证上述问题是否关闭；只有会误操作平台、覆盖人工操作、重复副作用、
损坏关键账本、破坏平台隔离或绕过审批/价格安全边界的新问题才新增阻塞项。

## 19. 完成定义

13.5-6 完成必须同时满足：

- v15 Incident 主记录、事件流水、类别和人工闭环通过 R4；
- 现有 Mobile Review、Outbox、Feishu 和 Worker 恢复链完成参数化复用，没有平行系统；
- v16 极简策略与影子判定通过 R4；
- 专用授权只对极端低价、完整 Pulse、无复核结果的情形生效；
- 人工“改价到 / 立即下架 / 我来处理”完整闭环；
- v4/v5、共享写锁、Importer、Archive 和唯一 RECONCILE 基线不回退；
- 全量回归、系统冒烟、Linux/Windows CI 和受控实机通过；
- 自动重新上架不存在；次数上限和冷却规则不存在；
- 任务 14 正式授权前 `automatic_emergency_offline=false`。

Refs #20
