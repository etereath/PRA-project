# 任务 13.5-6A-1 Incident 应用服务阶段报告

## 1. 当前结论

13.5-6A-1 已完成 Incident 应用服务、人工复核、通知和 Worker 恢复入口的本地实现，
并完成当前安全前提允许的真实 R4 验收。

已完成的边界是：

- v15 Incident 可信检测、开放去重、出现次数和 append-only 事件的单事务写入；
- 精确重放不增加次数，同一 `event_key` 异内容稳定冲突；
- `RESOLVED` 后再次发生重开原 Incident，`CLOSED` 后再次发生创建新 Incident；
- 核心状态转换、ACK 事件、严重度变化和恢复记录；
- S3/S4 价格 Incident 的初始 Review、Token、Outbox、兼容通知日志、`REVIEW_RECORDED`
  事件及 `WAITING_HUMAN` 状态在同一个事务内提交；
- 原有通知 Worker 可在真正投递时复用预创建 Token，不新增第二个 Token。
- decision-first Mobile Review 已沿用同一 GET/POST、Token hash、一次性消费和 PRG；
- `改价到` 创建 `MANUAL` v4 `UPDATE_PRICE` 任务，`立即下架` 创建 `MANUAL` v5
  `SET_OFFLINE` 任务，`我来处理` 不创建平台任务；
- 人工改价在提交事务前从权威商品工作簿重新读取 `base_cost`，并要求目标价不低于成本；
  当前可信平台价格则在同一 SQLite 事务内从 `listing_status` 重读，作为旧价门禁。
- 初始等待只从初始 Outbox 明确 `SENT` 的 `sent_at` 开始；最终失败或
  `UNKNOWN_DELIVERY` 不允许推断“无人处理”；
- S4 在等待过半时至多创建一条中途提醒，ACK、复核结果或条件恢复都会抑制提醒；
- 恢复、Worker 恢复失败和任务成功/失败/UNKNOWN 继续使用同一 Outbox 和稳定业务键；
- 完整 `ONLINE_PULSE` 资格只读取既有 Automation Run、`MERGED_RUN` 和已导入商品观察：
  通知送达前已开始的扫描、非完整批次、未映射、离线或复核已有结果都只延后；
- 独立 Pulse 与由 `FULL_MARKET_SCAN → LISTING_STATUS_SCAN` 覆盖的 Pulse 使用同一资格
  语义；本阶段只返回只读资格事实，不计算 S4 价格策略，不创建自动任务。
- 通知同步、中点提醒和 Pulse 资格已接入既有 Automation Service 的一分钟维护 Handler；
  复用既有计划窗口、租约、Run/Event 和 output manifest，连续运行不重复提醒。
- Worker 请求级恢复复用现有 `ShadowBotQueueWatchdog`，宿主级恢复收敛为唯一
  `ShadowBotWorkerRecoveryCoordinator`，由同一个 Incident Automation Handler 在既有租约
  内调用；活动 working、待导入 result、写副作用未知和未保存编辑内容均先行阻断。
- Windows 宿主入口使用严格 JSON stdin/stdout 和 UI Automation 语义定位，不使用固定坐标；
  只有已核实安装路径的 `ShadowBot.Shell.exe` 可以进入重启链。每个 Incident 出现次数只
  允许领取一轮恢复动作，成功/失败均写稳定 Incident Event 和既有通知 Outbox。

后续阶段与条件验收边界：

- Review 与既有 v5 `SUBMIT_INTENT_RECORDED` 的竞态只针对 13.5-6C 的
  `SYSTEM_EMERGENCY` 无人值守任务。按冻结计划第 15、17.3 节，它属于 6C 的专用授权与
  平台点击前撤销重验，不是 6A-1 的未完成代码；6A-1 不得为此提前创建自动任务控制面。
- Worker `Ctrl+Alt+Q` 只允许在真实活动 `working` 已卡死、Watchdog 已保留 phase 且正常
  停止无效时执行。本轮没有这一真实前提，因此没有人为制造 COMMIT 或平台执行风险；
  该条件分支已有合成测试，但仍明确标记为“未完成实机故障注入”，待真实故障或独立获批的
  无平台副作用演练环境出现时补验。

Worker 恢复入口默认不可执行宿主动作。只有同时传入
`--enable-incident-monitoring --enable-worker-recovery`，并显式设置
`PRA_ENABLE_SHADOWBOT_HOST_RECOVERY=true`，才会使用仓库内唯一 Windows helper；自定义
helper 只能通过 `SHADOWBOT_HOST_HELPER_COMMAND_JSON` 的绝对命令数组接入。启动或重启
请求发出后不直接宣称成功，后续 Automation 周期必须看到新鲜 `RUNNING` heartbeat，才会
更新生命周期记录、解决 Incident 并发送“Worker 已恢复”。

因此当前代码仍不创建 `SYSTEM_EMERGENCY`，不发布无人值守 ShadowBot 队列，也不启用
自动紧急下架。6A-1 的代码和安全可执行验收边界可以进入评审；条件性
`Ctrl+Alt+Q` 实机故障注入不得被误报为已通过，也不得通过制造真实平台风险来补齐。

## 2. 复用与最小新增

| 能力 | 处理 | 说明 |
| --- | --- | --- |
| Runtime v15 Incident 表 | 原样复用 | 不新增表、不扩 v16 |
| SQLite 连接、事务和外键 | 原样复用 | 继续使用 `SQLiteRuntimeRepository` 的连接策略 |
| Review + Outbox 原子入口 | 参数化复用 | 在既有入口增加可选 Token 和 Incident Review Event |
| Review Token HMAC | 参数化复用 | 继续使用 `REVIEW_TOKEN_SECRET` 和现有 Token hash 校验 |
| 飞书 Outbox Worker | 参数化复用 | 优先复用预创建且未使用、未撤销、未过期的 Incident Token |
| 通知节流投影 | 原样复用 | 使用 `incident_notification_state` 表达首次送达、中点提醒和阻断状态 |
| ONLINE_PULSE 事实 | 原样复用 | 读取既有 Automation Run、`MERGED_RUN` 和完整商品观察，不新建扫描体系 |
| Automation 调度 | 参数化复用 | 显式启用一分钟 Incident 维护作业；沿用既有租约、Run、Event 和 Handler |
| Worker 请求恢复 | 原样复用 | 继续调用现有 Queue Watchdog；不新建第二 Watchdog |
| Worker 健康检查 | 抽取公共能力 | CLI 与恢复控制器共用同一只读健康报告构造器 |
| Worker 宿主恢复 | 最小新增唯一入口 | 单一 Coordinator + Windows helper；默认禁用、每次出现只领取一轮 |
| Mobile Review GET/POST | 参数化复用 | 增加无 source task 的 Incident 分支，旧流程不变 |
| v4 改价、v5 下架 | 参数化复用任务入口 | 只创建现有合同可消费的 MANUAL 任务，不复制队列或 Worker |

新增 `OperationalIncidentRepository` 是 v15 表的首个业务 Repository，并不建立平行
控制面。`IncidentManagementService` 只接受可信调用方已确认的检测事实；它本身不扫描
页面、不判断价格阈值，也不执行恢复或平台动作。

## 3. Incident 事实语义

检测去重身份由以下字段规范化后计算：

- 类别；
- 适用时的平台；
- 自然按交易日隔离时的平台交易日；
- `subject_type / subject_key`；
- 原因。

`event_key` 表示一条可信来源事实。首次检测写 `DETECTED`，同一开放 Incident 的新
检测写 `REDETECTED`；只有新 `event_key` 才增加 `occurrence_count`。ACK 只写
`ACK` 事件，不把 Incident 改为历史 `ACKNOWLEDGED` 状态，也不增加出现次数。

本实现只写冻结后的核心状态：`OPEN / WAITING_HUMAN / AUTO_PROTECTING /
RESOLVED / CLOSED`。历史 `RETRYING / ACKNOWLEDGED` 仍可由 v15 读取，但应用服务
不会创建这些状态。

## 4. Review、Token 和 Outbox 原子性

S3/S4 价格初始 Review 使用现有 `review_tasks`、`review_tokens`、`notification_outbox` 和
`notification_logs`。同一 `BEGIN IMMEDIATE` 中依次完成：

1. 插入 pending Review；
2. 插入允许 `adjusted / approved / rejected` 的 Token；
3. 插入初始 Outbox 和兼容通知日志；
4. 将 Incident 更新为 `WAITING_HUMAN`；
5. 追加 `REVIEW_RECORDED` 事件。

任一步失败均整体回滚。重复创建按 Incident 与出现次数形成稳定 Review 去重身份，回读
原 Review、Token 和 Outbox，不产生第二套业务身份。

数据库仍不保存 raw token 或完整 Mobile Review URL。Incident Token 的 raw 值由
`REVIEW_TOKEN_SECRET + review_task_id + token_id` 通过 HMAC 派生，库内仍只保存原有
HMAC `token_hash`。飞书 Worker 在实际发送前仅在内存中重建 URL，并复用该 Token；
普通 Review 继续沿用原有投递时生成临时 Token 的流程。

初始消息和中途提醒共用一个 Review。初始 Outbox 尚未发送时，通知状态为
`WAITING_INITIAL_DELIVERY`；明确发送后才记录 `decision_window_started_at`，并仅对 S4
安排五分钟后的中点。中途提醒的精确重放返回同一 Outbox；成功送达后总数为两条，不再
继续广播。恢复和任务结果使用各自稳定 `notification_kind + incident + event_key` 身份，
同身份异内容会被 Outbox 幂等冲突拒绝。

## 5. 完整 ONLINE_PULSE 资格

资格服务不会用计时器授权。它先校验初始观察与 Incident 的平台、交易日、SKU 和
`VERIFIED` 映射绑定，再从初始通知 `sent_at` 之后查找第一个实际完成并导入的完整
`ONLINE_PULSE`。候选必须同时满足：

- Pulse 本身成功，或已通过既有 `MERGED_RUN` 被成功 `LISTING_STATUS_SCAN` 覆盖；
- 逻辑计划时间晚于 `sent_at`，实际扫描开始时间也晚于 `sent_at`；
- 商品观察批次为 `ACCEPTED`，范围完整且尾部已确认；
- 同平台、同交易日、同 SKU，映射为 `VERIFIED`，价格可读且商品仍在线；
- 第二观察与初始观察 ID 不同，Review 仍为 pending，Incident 仍处于活动状态。

资格达到时间取扫描完成、导入完成和对应 Run 完成时间中的最晚值。下一 Pulse 失败或事实
不完整时返回“等待合格 Pulse”，不会创建 Incident 事件、任务或队列请求。

`run_automation_service.py --enable-incident-monitoring` 才注册 Incident 维护作业和 Handler；
默认运行模式保持不变。每轮读取同平台 pending `emergency_protection` Review，依次同步初始
送达、幂等创建中点提醒并投影 Pulse 资格。只有额外传入 `--enable-worker-recovery` 才注册
恢复 Coordinator；宿主动作还受独立环境开关约束。Run Event 只保存恢复状态和计数，不保存
宿主命令、业务载荷或凭据，并始终明确 `platform_write_performed=false`。

## 6. 测试结果

当前本地结果：

- 新增 Incident 与 Incident Review 专项：`33 passed`；
- Incident Automation Handler 集成：`2 passed`；
- Worker 恢复 Coordinator、严格宿主协议和 Windows 只读检查：`13 passed`；
- Incident、Worker 队列和 Automation 组合专项：`122 passed`；
- v15、FINAL、通知、Runtime、工作流、Web 与旧 Mobile Review 受影响回归：
  `295 passed, 36 subtests passed`；
- 完整 pytest：`1031 passed, 3 skipped, 97 subtests passed`；
- 隔离临时 Runtime DB 系统冒烟：`16 passed, 0 failed`；
- 修改文件静态检查：`ruff --select E9,F,I` 通过；
- 新增 Python 文件 `ruff format --check` 通过。

测试覆盖首次检测、精确重放、同 key 异内容、重复发生、状态重开、关闭后新建、非法
转换、ACK、严重度、恢复、各事务失败注入、S3/S4 Review 原子创建、Review 精确重放、
raw token 不落 Outbox、三个复核动作、成本下限、任务/Token 整体回滚、工作簿成本回读，
初始通知失败阻断、S4 中点提醒、ACK/复核抑制、恢复/任务结果通知、完整 Pulse 资格、
合并覆盖 Pulse、活动 working/result 保护、stale phase Watchdog、`WRITE_UNKNOWN` 分流、
`stop.signal`、一次性宿主动作、20 秒登录等待、新鲜 heartbeat 成功门禁、生命周期原子写，
以及旧通知与 source-task Mobile Review 回归。

本轮尚未运行 Linux/Windows CI。2026-08-03 已完成真实宿主缺失、核实路径重启、唯一
`test2` 启动、新鲜 heartbeat、Incident 解决、生命周期更新、空队列正常停止和再次恢复；
结束时 Worker 保持长期 `RUNNING`。实机同时发现并修复 Windows PowerShell 5.1 对无 BOM
UTF-8 中文常量的错误解释，以及“先选择唯一应用、再调用工具栏唯一运行按钮”的真实 UIA
结构。`Ctrl+Alt+Q` 只适用于活动 working 正常停止失败，本轮没有伪造该危险前提，因此仍
只保留合成测试证据。详见
[Worker 恢复实机验收](task13_5_6a1_worker_recovery_acceptance_20260803.md)。

后续于 2026-08-03 至 2026-08-04 完成真实飞书与手机复核验收：14 次权威 Outbox 投递
全部为 `SENT / ACKNOWLEDGED / HTTP 200`，S3/S4 经营通知、验证码结果反馈、S4 中点提醒
和三个手机复核动作均由用户确认。验收期间发现临时公网域名次日失效导致旧链接 404；已
改用固定入口、作废过期 Token、重新签发并补发成功。详见
[通知与手机复核验收报告](task13_5_6_notification_mobile_review_acceptance_20260804.md)。

## 7. 安全边界

- 未迁移真实 Runtime DB；
- 未读取或写入真实订单、商品或买家数据；
- 仅为 R4 验收重启影刀、启动 Worker、空队列正常停止并再次启动；未同步 `test2` 代码；
- 未创建真实平台任务；
- 真实飞书只在隔离验收数据库中逐批发送；不包含真实订单、买家信息、Webhook、raw token
  或完整 Mobile Review URL；
- `automatic_emergency_offline=false` 保持不变。
