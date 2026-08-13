# 项目当前状态总览

本文档用于同步当前仓库的真实项目状态，避免早期规划、阶段计划和当前已实现能力混淆。

## 1. 当前项目定位

PRA 当前定位为：

鲜切花预测性销售决策系统 + 运行态任务运营后台。

PRA 已形成任务中心到蚂蚁花团供应商微信小程序的单平台、多商品、受控 RPA 改价与上下架闭环；任务 13.5 已插入任务 13 与任务 14 之间，用于补齐 18:00 平台交易日/20:00 卖家作业日双时间轴、持续只读扫描、历史订单观察、销售日结、S0–S4 异常治理、任务来源对齐、受控紧急保护和运营 Web 重写。当前 13.5-1 已完成双时间轴、Runtime Schema v14、六级质量约束和日结状态机；13.5-2 已通过 PR #23 合并商品映射编译、扫描 JSON 输入、任务 13 双页快照适配和 v14 商品观察导入；13.5-3 已通过 PR #24 合并独立 Automation Service 的计划窗口、租约、合并、补跑、父子 run、心跳和健康控制面。13.5-4 已实现订单只读合同、蚂蚁花团 Adapter、平台无关 Importer、v6 ShadowBot 只读队列边界和 `FULL_MARKET_SCAN → ORDER_SCAN → ORDER_HISTORY_IMPORT` 接入；当前日按 `OPEN`、历史日按 `CLOSED`，重复订单按指纹多重集合保留。最新审查修复已增加历史目标日期 Run Event 精确冻结、Watchdog/Importer 全链路绑定、`HH:10` 完整扫描、跨 18:00 整批失败和正式 Automation Service 只读 Handler 组合。受控真实页面 READ_ONLY 已完成 2026-07-31、2026-07-30 和 2026-07-22 三日期矩阵，分别读取 3、5、4 条并完成范围/尾部验证、导入归档和零平台写副作用；日期轮已修复“隐藏日期存在于无障碍树却不在视口”的误判，从 7 月 30 日到 7 月 22 日的向上滚动以及从 7 月 22 日回到 7 月 31 日的向下滚动、确认和精确日期回读均已通过。2026-07-10 又完成 20 条历史订单 READ_ONLY：订单调用方点击新捕获的 `订单管理_容器` 右边缘空白带，共享助手发送 2 次 `END` 后验证“没有更多了”，再由 `HOME` 恢复首卡；`scroll_progress_verified=true`，订单列表真实滚动门禁已关闭。复用优先整改保留商品和订单共用的参数化列表物化助手，并以 `scroll_count / scroll_progress_verified` 独立记录滚动证据。真实 Runtime DB 因既有 `NEEDS_RECONCILIATION` 安全门禁保持未迁移、未写入订单事实，因此仍不承诺生产级无人值守写操作，也未扩展到第二平台。系统核心职责是：

13.5-6 已完成编码前计划评议、6A-0/6A-1 本地实现以及 6B 的 Runtime Schema v16
极简策略和零副作用影子判定。v15 只增加 Incident 出现次数、扩展类别和一张 append-only
Event 表；v16 只增加一张 `emergency_offline_policies`，数据库和 Decimal 解释器共同固定
`emergency_ratio=0.80`。策略先以未批准草稿创建，批准后不可原地修改，只允许版本替换和
退休；同平台最多一个有效版本。影子服务原样复用 Review、Outbox `sent_at`、完整
`ONLINE_PULSE`、映射/在线/价格事实和共享写锁，并像人工复核链一样从权威商品工作簿
一致性回读 `base_cost` 与内容哈希。只有 `P <= base_cost × 0.80` 的 S4 进入 allowlist；
S3 永不自动。成本缺失/非法或来源不可追溯时复用 `MASTER_DATA` Incident fail closed。
6B 专项为 `33 passed`，受影响回归为 `107 passed`；完整 pytest 为
`1064 passed, 3 skipped, 97 subtests passed`，隔离系统冒烟为 `16 passed, 0 failed`。
6B 本身保持零平台副作用；后续 6C 已在一次性 v16 数据库完成一次明确授权的真实写验收，
验收结束后 `automatic_emergency_offline=false` 已恢复。

13.5-6C 已完成本地代码与合成链：专用授权在同一事务追加 Automation Event、创建唯一
`SYSTEM_EMERGENCY SET_OFFLINE` Task 并把 Incident 转为 `AUTO_PROTECTING`；通用
Repository 仍拒绝该来源。现有 v5 合同版本、SET_OFFLINE 执行链、operation/attempt、共享
写锁、phase、Importer 和 UNKNOWN/RECONCILE 均原样复用。主控端在写账本前重验可撤销
事实；Worker 在唯一定位并核对确认弹窗后，以同一个 Runtime DB 的短
`BEGIN IMMEDIATE` 在线性化边界内再次重验，通过后才记录 `ACTION_INTENT_RECORDED` 和
点击确认。失败先取消弹窗且不记录写意图。成功导入后 Incident 回到
`WAITING_HUMAN`，不自动重新上架。验收前受影响组合为
`127 passed, 3 subtests passed`；修正真实默认装配后授权与 shadow 专项为 `37 passed`，
完整 pytest 为 `1079 passed, 3 skipped, 97 subtests passed`，系统冒烟为
`16 passed, 0 failed`。
Worker 文件已在正常停止、应用列表核对和逐文件哈希一致后同步到真实 `test2`。2026-08-03
对 `AISHA-D-50-Z` 完成唯一一次真实 `SYSTEM_EMERGENCY SET_OFFLINE`：两次完整在线观察、
初始飞书通知后的下一有效 Pulse、S4 阈值、待处理 Review、策略和写入禁止条件均通过；v5
结果为 `VERIFIED`，最终在线列表回读不再包含目标商品，Importer/ACK/Archive 完整通过，
无 RECONCILE。Task 为 `success`，Incident 回到 `WAITING_HUMAN`，Review 保持 `pending`，
没有自动重新上架。验收控制器已在 `finally` 中恢复开关为 false，活动写锁和队列文件均为
0；长期 Worker 保持新鲜 `RUNNING`，原 Queue Service 已恢复绑定原 Runtime DB。该结论只
表示单 SKU 受控实机通过，不表示生产开关已常驻启用。

13.5-6A-1 已完成本地实现和当前安全前提允许的真实 R4 验收。当前新增 Incident 可信检测、精确重放冲突、开放去重、
`RESOLVED` 重开、`CLOSED` 后新建、核心状态转换、ACK、严重度和恢复事件的原子服务；
S3/S4 价格初始 Review、Token、Outbox、兼容通知日志、`REVIEW_RECORDED` 事件和
`WAITING_HUMAN` 状态已在同一事务内提交。Token 仍只保存 hash，飞书 Worker 可在投递
时通过既有 secret 在内存中重建链接并复用预创建 Token。等待只从初始 Outbox 的真实
`sent_at` 开始；失败或 UNKNOWN 投递阻断无人介入推断。S4 只允许一条五分钟中途提醒，
ACK、复核结果或恢复会抑制提醒；恢复和任务结果继续使用同一 Outbox 的稳定业务键。
decision-first Mobile Review 已在原 GET/POST 和单事务入口增加 Incident 分支：“改价到”
按权威工作簿最新 `base_cost` 校验后创建 MANUAL v4 任务，“立即下架”创建 MANUAL v5
任务，“我来处理”不创建平台任务；原 source-task 复核保持回归。完整 Pulse 资格已复用
Automation Run、`MERGED_RUN` 和商品观察事实，只接受通知送达后开始、已完成导入、范围
完整、尾部确认、同平台/交易日/SKU、`VERIFIED` 且仍在线的第二观察；失败或不完整只
延后。新增 Incident/Review 专项为 `33 passed`，当前受影响回归为
`295 passed, 36 subtests passed`；Worker/队列/Automation 组合专项为 `122 passed`；完整
pytest 为 `1031 passed, 3 skipped, 97 subtests passed`，隔离系统冒烟为
`16 passed, 0 failed`。
通知同步、中点提醒和 Pulse 资格已通过显式 `--enable-incident-monitoring` 接入既有
Automation Service 的一分钟维护 Handler，继续复用计划窗口、租约、Run/Event 和
Outbox，默认入口不自动启用，Run 明确保持零平台写。Worker 请求级 stale 恢复继续复用
Queue Watchdog；宿主级恢复已收敛为唯一 `ShadowBotWorkerRecoveryCoordinator` 和严格 JSON
Windows helper，并接入同一个 Incident Automation Handler。活动 working、待导入 result、
写副作用未知、未保存编辑内容和未核实进程路径都会 fail closed；每个 Incident 出现次数只
领取一轮动作，必须在后续周期看到新鲜 `RUNNING` heartbeat 才记为恢复成功。该能力需要
`--enable-incident-monitoring --enable-worker-recovery` 与独立环境开关同时成立，默认关闭。
2026-08-03 已完成真实宿主缺失、核实安装路径重启、唯一 `test2` 语义启动、新鲜
heartbeat、Incident 解决、生命周期更新、空队列正常停止和再次恢复。实机发现并修复
Windows PowerShell 5.1 对无 BOM UTF-8 中文控制常量的错误解释；helper 现为纯 ASCII
源码并以 Unicode 码点匹配 UIA 标签。`Ctrl+Alt+Q` 只在活动 working 正常停止失败时允许，
本轮未伪造该高风险前提，因此该分支不写为实机通过。随后 6C 已另获单 SKU 明确授权并
完成一次受控真实下架；该授权不扩张 6A-1 的宿主恢复权限，也不得复用于其他真实写动作。

2026-08-03 至 2026-08-04 已完成 S0–S4 飞书逐批和手机复核验收。隔离 v16 Outbox 中
14 次真实业务投递均为 `SENT / ACKNOWLEDGED / HTTP 200`，活动待发数为 0；S3/S4 经营
通知、验证码“处理完毕”权威反馈、S4 初始与中点提醒以及“改价到 / 立即下架 / 我来处理”
页面均由用户确认。首次 S4 使用的临时公网域名次日退出导致旧消息 404；已切换到受运维
管理的固定入口、作废旧 Token、重新签发并补发通过。后续真实通知禁止使用随机临时域名，
发送前必须以公网 `/health` 作为部署门禁。手机页面验收未提交经营动作，且未改变
`automatic_emergency_offline=false`。最终受影响组合为 `181 passed, 20 subtests passed`，
完整 pytest 为 `1082 passed, 3 skipped, 97 subtests passed`，隔离系统冒烟为
`16 passed, 0 failed`。详见
[通知与手机复核验收报告](reports/task13_5_6_notification_mobile_review_acceptance_20260804.md)。

2026-08-04 的 PR #28 完整实现审查已按七项统一顺序完成本地整改：事件时间单调性、正式人工
抢占与入口续期、Incident 人工任务/SYSTEM_EMERGENCY 对普通任务的全链路插队、Worker
最终 Review 栅栏、商品工作簿锁内二次校验、人工任务结果回投和 FINAL 真实 subject 范围
匹配。整改继续复用现有 v4/v5、Automation、Review/Token/Outbox、Importer、写锁和唯一
RECONCILE，没有新增表、全局锁、平台动作或合同版本，也没有再次操作真实平台。生产开关
仍为 `automatic_emergency_offline=false`。最终本地完整 pytest 为
`1116 passed, 3 skipped, 97 subtests passed`，隔离系统冒烟为 `16 passed, 0 failed`。

2026-08-05 的最新复审整改已关闭剩余两项：已持久化紧急请求支持人工 Review 在最终点击前
抢占并由原 Importer 收敛 `NOT_APPLIED`、Worker 先越过最终栅栏时后到 Review 只记录人工事实
且不创建第二个写任务；迟到严重度事件只追加 requested severity 审计，不倒退 Incident 主
投影。两个相反竞态顺序均使用独立 SQLite 连接覆盖 request persistence → Review/Worker →
result → Importer。整改没有新增表、全局锁、状态枚举、合同版本或平台动作；生产开关仍为
`automatic_emergency_offline=false`。完整 pytest 为
`1122 passed, 3 skipped, 97 subtests passed`，隔离系统冒烟为 `16 passed, 0 failed`。仓库内
Worker/fence 源码已更新但尚未同步真实影刀宿主，本轮没有执行真实平台动作；后续部署必须
重新完成正常停止、同步、哈希核对与长期 Worker 恢复门禁。

- 从 Excel 读取业务输入。
- 根据规则和预测输入生成运行态任务。
- 用 SQLite 保存运行态事实。
- 通过 Web 后台、Mobile Review 和飞书通知完成运营复核闭环。
- 为后续生产级真实平台调度、真实 RPA 和 AI Agent 预留边界。

任务 13.5 的宏观权威是 [GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)；
[对齐评估](plans/task13_5_issue20_alignment_review.md)记录了其与本地计划的裁决，
[本地实施计划](plans/task13_5_operational_closed_loop_and_web_rewrite.md)和
[Web 重写计划](plans/task13_5_web_rewrite_plan.md)负责仓库内的具体落地；
[13.5-0 开工基线](plans/task13_5_0_kickoff_baseline.md)和
[Web 独立审计](plans/task13_5_web_current_state_audit_20260729.md)记录正式开工证据。
[13.5-1 冻结合同](plans/task13_5_1_quality_and_settlement_contract_review.md)已经冻结
双时间轴、六级质量、唯一 FINAL 日结状态机和 v14 兼容边界；
[v14 实施报告](reports/task13_5_1_runtime_schema_v14.md)记录本地代码与临时数据库
验收结果，[迁移手册](runtime_schema_v14_migration.md)负责后续真实库升级。
[13.5-4 订单历史只读观察合同](plans/task13_5_4_order_history_observation_contract.md)
冻结无稳定订单 ID 时的批次内多重集合语义、数据最小化、能力降级和 v6 零写边界。
[订单页首轮无副作用探索报告](reports/task13_5_4_order_page_exploration_20260731.md)
记录 2026-07-31 的页面事实、字段边界、候选步长和后续权威裁决。
[13.5-4 实施报告](reports/task13_5_4_order_observation.md)记录代码、测试、迁移纠正和
受控实机验收状态。

## 2. 当前已完成能力

### 2.1 业务输入

- Excel 仍是商品、规则、预测、产能、冷库等业务输入来源。
- Web 的 `Business Inputs` 页面已经从单纯 Excel 表格入口升级为业务输入入口。
- 商品资料与库存录入已提供日常表单：选择已有品种、通过“新增品种”弹窗维护新品种代码、补充公共库存、编辑商品基础资料，并保存回 `products.xlsx`。
- 价格规则管理已提供日常表单：新增、编辑、查看价格规则，并保存回 `price_rules.xlsx`。
- 价格规则适用范围已升级为 `variety_filter / grade_filter / platform_filter` 三维筛选；旧 `scope_type / scope_value` 已废弃。定价规则由旧的多条叠加改为单条胜出，按 `priority` 和具体度选择，冲突规则不会被静默随机选择。
- 上下架规则管理已提供日常表单：新增、编辑、查看上下架规则，并保存回 `listing_rules.xlsx`。
- 上下架规则已升级为 `variety_filter / grade_filter / platform_filter / stock_threshold / listing_strategy` 结构；旧 `condition_type / condition_value / action` 不再作为主路径。
- 包装产能计划已提供日常表单：按业务日期维护基础包装产能、临时工人数、单人临时工产能、确认包装能力和启用状态，并保存回 `capacity_plans.xlsx`。
- `capacity_plans.xlsx` 中的 `confirmed_packing_capacity_qty` 是 CapacityRuleEvaluator 的最终判断口径；如果为空，系统按“基础产能 + 临时工人数 × 单人临时工产能”计算。
- 冷库状态已提供日常表单：按业务日期维护冷库总容量、当前占用、预计入库、预计出库、预计占用、剩余容量、预警阈值和启用状态，并保存回 `cold_storage_status.xlsx`。
- `cold_storage_status.xlsx` 中的 `projected_occupied_qty` 和 `remaining_capacity_qty` 是 ColdStorageEvaluator 的判断口径；页面会按“当前占用 + 预计入库 - 预计出库”和“总容量 - 预计占用”默认计算，也允许运营人员人工确认。
- 旧 `/tables` 仍保留为高级兼容入口，适合批量维护和排障。
- `platform_mappings.xlsx` 现可同时保存 WEB 平台登记记录和严格的商品身份映射；
  商品映射可编译为带源工作簿 SHA-256 的不可变 JSON。
- 当前不迁移 Excel 主数据到 SQLite。

### 2.2 SQLite 运行态事实来源

SQLite 当前保存以下运行态表：

- `tasks`
- `review_tasks`
- `notification_logs`
- `execution_logs`
- `task_status_history`
- `review_tokens`
- `script_runs`
- `script_run_items`
- `shadowbot_operations`
- `shadowbot_execution_attempts`
- `shadowbot_side_effect_checkpoints`
- `retry_authorizations`
- `notification_outbox`
- `notification_delivery_attempts`
- `listing_status`
- `shadowbot_commit_batches`
- `shadowbot_commit_batch_items`
- `shadowbot_write_locks`
- `shadowbot_commit_result_receipts`
- `shadowbot_batch_registry`
- `shadowbot_listing_action_batches`
- `shadowbot_listing_action_batch_items`
- `shadowbot_listing_result_receipts`
- `listing_sync_snapshots`
- `listing_sync_snapshot_items`
- `listing_anomaly_cases`
- `product_observation_batches`
- `product_observation_items`
- `order_observation_batches`
- `order_observation_items`

SQLite 只承接运行态任务系统，不替代 Excel 主数据。

13.5-2 的商品观察导入器只向 v14 不可变观察表追加事实。`ONLINE_PULSE`
缺席不推断下架，也不改写 `listing_status`；任务 13 的完整双页快照可以适配为
`LISTING_STATUS_SCAN` 商品子结果。

13.5-3 的 Automation Service 使用稳定逻辑窗口、`lease_owner + lease_version +
lease_expires_at` fencing、邻近扫描合并和有界补跑。独立 CLI 默认保持
`SCHEDULER_ONLY`；13.5-4 增加显式 `--enable-order-read-only` 模式，只注册完整扫描
订单子 run 派生与 `ORDER_SCAN` 只读 Handler，不注册平台写 Handler。默认模式只记录
到期账本，不启动 ShadowBot、不投递平台请求，也不伪造扫描成功。合同见
[13.5-3 Automation Service 合同](plans/task13_5_3_automation_service_contract.md)。

13.5-4 已实现蚂蚁花团订单只读 Adapter、平台无关订单输入与 Importer、合成 fixture、
v6 ShadowBot 请求/结果和既有文件队列传输，并通过 Automation 父子 run 接入
`FULL_MARKET_SCAN → ORDER_SCAN → ORDER_HISTORY_IMPORT`。当前交易日保存为
`OPEN`，历史交易日保存为 `CLOSED`；完整有数据页要求验证滚动和“没有更多了”，
可信空页要求“暂无订单”。平台订单 ID 和买家 PII 被合同拒绝，相同指纹的真实重复
订单用 `occurrence_no` 保留。页面数量固定为 `order_qty`；执行端用页面单价乘数量
计算 `order_transaction_amount`，不单独定位合计金额元素。取消推导留到 13.5-5。
目标交易日通过唯一 `ORDER_SCAN_TARGET_SELECTED` Run Event 冻结并由 Watchdog、
Worker 请求和 Importer 精确校验；每小时完整扫描对齐 `HH:10`，跨越 18:00 的订单
批次整批失败。真实 Runtime DB 仍未写入订单事实，
受控实机验收已在一次性 v14 Runtime DB 上通过：当前交易日 `OPEN` 的可信空页和
完整有数据页均已验证，最新有数据页为 `scope_complete=true`、
`end_marker_verified=true`，结果已导入归档且平台写操作为 0；批次仅因隔离验收使用
空 Mapping 集合而标记 `PARTIAL / UNMAPPED`。2026-07-31 进一步完成当前日及两个
历史日期的 3/5/4 条真实页面矩阵；从 7 月 30 日到 7 月 22 日的全自动日期轮滚动已经
通过。7 月 10 日进一步完成 20 条历史订单读取，点击 `订单管理_容器` 右边缘后以 2 次
`END` 验证尾部并由 `HOME` 恢复首卡，订单列表实际滚动门禁已通过。随后订单字段读取
改为一次枚举安全等级锚点，再按列表全局 index `2/3/5/6/7`、步长 `9` 只读取允许
字段；同一 7 月 10 日的 20 条实机回归将首末 `observed_at` 跨度由约 29 秒降至 10 秒，
范围、尾部、Importer、归档和零平台写副作用仍全部成立。
通用队列 Watchdog 已识别 v6 `ORDER_SCAN` Run 绑定，旧版 Result Importer 不再抢占
订单结果，超时恢复保持 v6 零写语义。最终合并门禁又让常驻 Watchdog、验收
Automation Run、唯一目标日期事件和订单 Importer 共用同一个一次性 v14 Runtime DB；
Watchdog 输出精确匹配的 `READY_REQUEST_VALIDATED` 后，Worker、Importer 和 Archive
完整通过，20 条历史订单写入隔离 DB，活动队列清空且平台写操作为 0。验收后原真实 DB
队列服务按原参数恢复，真实 Runtime DB 仍未写入订单事实。

13.5-5 已实现只读销售事实链：继续使用 v14 的 `sales_estimate_segments`、交易日日结、
版本、事件和输入表，没有新增表、字段、锁、平台合同、结算状态或真实平台动作。估算
区间只有在库存调整覆盖被明确证明时才计算；既有上架读回、结构化人工确认和未决写结果
参与资格判断，没有 PRA 写记录不能反推没有人工修改。完整 CLOSED 订单优先于扫描估算，
部分订单与估算只并列保留、不拼成一个总数；取消由相邻完整快照的多重集合减少派生，
不写伪订单，也不重复扣减销量。估算 segment 的 v2 身份已包含规范化调整与失败证据，
事后补证可追加不可变新版本并由选数器采用当前证据，不再与旧 ID 冲突。20:00 作业生成
平台、品种、等级、SKU 和本地小时桶的
PROVISIONAL；FINAL 必须经过订单完整性、复算、差异分类、输入版本和现有阻断 Incident
的同事务门禁。Automation Service 已接入统一 Settlement Pipeline：一次冻结输入、一次
多范围计算、同事务持久化、新连接回读后派生计划输入、销售管理报告和审计回执；20:05
任务只恢复并校验已保存投影。销售计划直接使用 20:00 边界的
`seller_operation_date`，不建立第三套日期；早期订单严格按发生时间筛选，价格/库存轨迹
已形成有界汇总，完整 manifest 可从 Run Event 回读。早期信号只有20:00后10分钟内的
完整 OPEN 快照且之后无失败/不完整扫描时才允许确认总量或可信零值；20:00前快照只作为
部分领先证据。历史订单回补在同一
事务中刷新全部既有范围、创建新范围并整体回滚故障；历史计划投影只生成 `AUDIT_ONLY`
投影，不成为当前运营计划。“第 N 次购买”采集仍需单独合同修订。线下生产、佣金/退款、联网日历/
天气和市场价格指数只保留后续来源规划，不进入本阶段实现。合同与实施见
[13.5-5 销售估算与日结合同](plans/task13_5_5_sales_estimation_settlement_contract.md)和
[13.5-5 实施报告](reports/task13_5_5_sales_settlement.md)。2026-08-01 追加的
`2026-07-10` 真实订单页 READ_ONLY 验收读取 20 条 CLOSED 订单，2 次页面内滚动后确认
“没有更多了”，总耗时 40 秒；Watchdog、Worker、Importer 与 Archive 在同一个一次性
v14 Runtime DB 中通过，平台写操作为 0。批次 `PARTIAL` 仅源于验收使用空映射，20 条
均为 `UNMAPPED`，不是页面读取失败；主 Runtime DB 队列服务和长期 Worker 已恢复。

当前代码中的 runtime schema 最新版本为 v16。v3 新增自动规则评估运行记录，v4 新增 ShadowBot Executor 账本，v5 新增队列审计字段和 `retry_authorizations`，v6 新增事务型通知 Outbox，v7-v9 建立 `listing_status` 并将业务身份统一为“平台 + 品种 + 等级”，v10 将 `tasks.expected_old_price` 结构化，v11 新增单次请求的 `shadowbot_commit_batches` 和 `shadowbot_commit_batch_items`，v12 新增逐商品操作/尝试身份、活动写锁、观察时间和持久化结果回执，v13 新增公共批次注册表、通用上下架 operation、共享写锁、v5 动作账本、两页快照和页面异常事实表，v14 新增双时间轴、Automation、不可变观察、日结、Incident 和任务来源结构，v15 增加 Incident 出现次数、类别扩展和 append-only 事件流水，v16 增加唯一的版本化极简紧急下架策略表。真实 Runtime DB 是否已升级必须单独核实；`app.runtime_schema.LATEST_RUNTIME_SCHEMA_VERSION` 是代码版本唯一权威来源。

### 2.3 人工复核闭环

已完成 Web 人工复核闭环：

- `/reviews` 是 Web 复核主入口。
- pending review 可处理为 `approved / rejected / adjusted / cancelled`。
- Web 场景下 `actor / resolved_by` 来自 Session 用户。
- 如需推动源任务状态，必须通过 `ReviewTaskService` 内部协作 `RuntimeTaskService` 完成。
- 任务状态变化写入 `task_status_history`。

### 2.4 Review Token / Mobile Review MVP

已完成 Mobile Review MVP：

- `review_tokens` 表已落地。
- `ReviewTokenService` 支持 token 创建、校验、使用记录和撤销。
- Mobile Review 路由已实现：
  - `GET /mobile/review/{review_task_id}?token=...`
  - `POST /mobile/review/{review_task_id}/resolve`
- 手机端复核仍调用 `ReviewTaskService.resolve_review_task(...)`。
- 成功提交后写入 `used_at`，重复提交会被拒绝。

### 2.5 飞书通知

已完成飞书 Webhook Outbox 通知链路：

- `NotificationChannelRegistry` 按持久化 `channel` 绑定 `fake / scripted / feishu`；默认运行态 review 不再直接调用 provider。
- ShadowBot 登录验证码人工介入使用 `ReviewTask + verification_code_intervention Outbox` 单事务创建；执行端自动回读确认登录恢复、超时或停止等待后，同事务完成 Review 并创建唯一结果通知，分别为“验证码处理完毕 / 验证码处理超时 / 验证码等待已取消”。
- 验证码 deadline 强制限制为 120 至 600 秒；Review 完成后会在业务事务中取消尚未发送的旧人工介入通知。
- `FeishuOutboxSender` 使用飞书自定义机器人 Webhook；`notification-worker` CLI 提供一次 Watchdog/Worker 调度入口。
- 新旧 Feishu 适配器复用官方签名实现；仅显式成功码可确认投递，HTTP 5xx 与缺少确认码的有效 JSON 均按不确定投递处理。
- ReviewTask、Outbox 与初始 `notification_logs` 兼容投影原子创建；超时回退状态与 `review_expired` 通知也在同一事务提交。
- 重复业务 Review 仅在完整业务字段、截止时间、payload、平台、渠道、收件人、事件版本和投递策略全部一致时复用已有 Outbox。
- 业务创建路径不自动执行测试 Sender；渠道缺失会保持 `PENDING`。默认 Worker 拒绝测试渠道，CLI 仅在 `DEV_MODE=true` 时允许 `mock / fake / scripted`，生产误配置会非零退出。
- 超时原子事务的三阶段故障矩阵覆盖源 Task 与 TaskStatusHistory，证明业务状态、历史、初始 Outbox、过期 Outbox 和兼容日志整体回滚。
- `FeishuWebhookNotificationSender` 保留为旧通知接口的兼容测试适配器。
- 支持可选签名 `FEISHU_WEBHOOK_SECRET`。
- 默认 `FEISHU_MESSAGE_TYPE=post`，使用飞书富文本消息。
- `FEISHU_MESSAGE_TYPE=text` 可作为纯文本回退。
- 飞书通知中可包含 Mobile Review 处理链接。
- `notification_logs.message` 只保存摘要，不保存完整 `token=` 链接。

### 2.6 cpolar 外网访问链路

已验证通过 cpolar 暴露本地 Web 服务后：

- 飞书消息中的 Mobile Review 链接可在手机打开。
- 手机端可提交复核。
- `review_task / review_token / task_status_history` 可正常写回。
- 重复提交会被拒绝。

### 2.7 Web 运行态运营后台

当前 Web 后台是尚未正式投入使用的运行态 MVP，包含：

- `Dashboard`：运营总览。
- `Tasks`：运行态任务追踪。
- `Reviews`：Web 复核主入口。
- `Notifications`：通知排障与追踪。
- `Execution Logs`：执行日志入口。
- `Business Inputs`：Excel 业务输入入口。
- `System`：配置检查、schema 检查、运行态计数、飞书测试通知。

2026-08-07 已决定不再兼容或渐进维护该页面架构。重写前 `main` 已标记为
`checkpoint/pre-task13-5-7-web-rewrite-20260807`；13.5-7 将建设唯一的新运营 Web，
并把 CLI 中残留的日常正式业务职责迁移到 Web、Automation 和 Queue。开发测试、Mock、
验收、诊断、备份和恢复 CLI 继续保留。

2026-08-12 又依据实际运营使用完成产品重基线：不再把旧 Web 整理出的八个页面当作不可
修改的产品权威，桌面和手机统一冻结为“今日、数据库、业务管理、系统”四个一级入口。
今日承担销售/真实库存/待办/时间轴和业务健康摘要；数据库只读展示业务与项目事实并保留
未来 Agent 销售分析位置；业务管理承担即时任务、独立执行授权、人工复核、固定 Automation
方案、人工新花入库和库存预警；系统只展示组件当前状态、业务影响和受控恢复，详细历史仍
在数据库。旧八入口中没有独立运营价值的 Route、Presenter、模板和测试将直接删除。

本轮同时确认数据库库存代表农场真实可售库存，平台库存只代表特定平台买家可购上限。
真实库存将由人工有符号调整和权威销售事实的幂等差额自动更新，并支持取消恢复、不可变
流水和阈值预警；该能力在 7D 编码前必须单独完成 R4 合同和最小 Schema 评审。创建 Task
与真实平台执行授权保持两个可审计阶段，Web 将提供连续操作，但普通 `PENDING` 仍不得
无人值守执行。

7A 评审进一步冻结了实施边界：7D 把 `products.xlsx.current_stock` 按 SKU 仅一次 bootstrap
到 Runtime DB，之后 DB 余额/流水成为唯一真实库存权威，所有库存消费者改读同一 Provider，
不保留 Excel/DB 双写。库存自动变化只接受完整 CLOSED 订单的累计销量净差，以及订单不可用
时合格 `SCAN_ESTIMATED_HIGH` 的正向扣减；部分/OPEN、中低质量估算和不可用事实零写，
取消只通过最新完整订单累计销量的负差恢复，不能额外加回取消量。Web 提交执行必须由
Service 层把认证主体、明确 task IDs 和重检 digest 绑定到既有 v4/v5 发布链；Automation
每类 Job 只开放明确字段和范围，18:00/20:00 不得单独漂移；系统 Route 不作为脚本 Runner。

项目级控制面已经同时冻结：人工运营走 Web，定时业务走 Automation，未来智能调用走
Agent Gateway，平台执行走 Queue/Worker/Importer，开发测试与恢复走 CLI。未来 Agent
读取只能经 Agent Query Adapter 调用权威 Query Service/Read Model；唯一写入口是 Agent
Task Adapter 接收结构化 `AgentIntent`，再由既有权威服务和确定性规则决定拒绝、Review、
Runtime Task 或 Outbox/通知。Agent 不能直接写 Review/通知，不能抓取 Web、调用 CLI、
直读 SQLite/Excel、拼 Queue JSON、直连平台 Adapter 或伪造 `SYSTEM_EMERGENCY`。
`AgentIntent` / `AgentProposal` 只是逻辑载荷，不批准为 Runtime 表；Agent 来源的改价、
上架、下架必须先经人工 Review 和显式授权。正式 Agent 来源预留为 `AGENT +
agent-run:<stable-run-id>`；当前 Schema 尚未支持，本阶段只冻结合同，不提前实现，也不
冒充 `MANUAL` 或 `AUTOMATION`。任何实际接入属于未来独立 R4，不在 13.5-7B～7F 或
任务 14 内实施。

买家页面可见“第 N 次购买”、买家客户端实时售价、每日人工花材质量“好/中/差”和外部
市场指数继续留到 Agent 阶段；13.5-7 不补采、不推断、不扩 Schema，当前 Web 不展示假值。

13.5-7A、7B、7C 已分别由 PR #31、#32、#33 合并到 `main`。新
`app/operations_web` 已建立应用骨架、单一 Composition Root、有界内存 Session、登录后轮换、
POST 退出、CSRF、集中 capability、安全 Header、统一错误边界和本地打包资源。认证 Session
不会被匿名登录页请求替换或因 preauth 容量回收而淘汰；无可安全回收项时 fail closed。
公共路由固定为 `/ → /today`、`/today`、`/login`、POST `/logout`，不在新 Web 复制旧
`/runtime/login|logout` 别名；登录失败和限流继续进入既有有界安全审计。Runtime DB、
三类工作簿和 Queue 根目录只在启动时固定；请求中的路径覆盖会被拒绝。`/health`、受保护的
四入口骨架和错误提示只使用只读连接，绝不调用 `init_schema()` 或迁移；Runtime 健康异常只
提示另走显式维护，不推断或修复真实数据。

7C 已完成并合并四入口只读事实：今日页接入交易日、六级质量、销量/金额/均价、当前
产品库存资料、待办和时间轴；数据库接入业务事实、项目事实、确定性销售分析、字段说明和
质量新鲜度；业务管理接入当前 Task、Review 和 Automation Run；系统只报告 Runtime DB、
工作簿、Queue 和 Worker 当前状态。默认 25 条后端分页已覆盖 Task、Review、Run、Incident、
Execution、Outbox、订单观察和日结。商品、销售、结算、Run、Execution 详情归数据库，Task
和 Review 详情归业务管理，不保留交叉详情。Mobile Review 已能只读呈现有效、无效、过期和
撤销、错绑及已处理状态，Token 不被消费，`last_used_at` 不变化；有效/已处理 GET 返回
200，过期/撤销返回 410，未知或错绑返回统一 404；`/resolve` 仍不执行写入。可用动作名称
复用正式 Review Policy，不再由新 Web 另建一套标签规则。

当前交易日只接受 Runtime 中唯一有效的版本化运营时间策略；策略读取失败、为空或当前无
唯一有效版本时不再回退代码默认值，也不会查询猜测的交易日。显式历史交易日和不依赖当前
交易日的数据集仍可读。经营事实在来源节奏/新鲜度策略冻结前只显示最近更新时间，不使用
Web 自创的固定 30 分钟 TTL。普通结算目录只显示当前权威版本，旧版本详情明确标记已被
取代；今日“当前可售库存”只汇总 `sale_enabled=true` 的商品。

页面继续不展示购买次数、买家端价格、Agent 建议、人工花材质量或完整度百分比。7D 前库存
明确标为当前产品资料来源，不冒充已经完成的 DB 真实库存账本；库存流水和映射查询未接通时
显示不可用，不填充样板数据。真实 Runtime DB READ_ONLY 已验证：既有 1 条外键违规只使
`/health` 返回 503，其他主要页面可读；主库/侧车、三份业务工作簿、心跳与生命周期文件前后
大小、主文件时间和 SHA-256 不变，且没有 Queue、Worker、Importer 或平台副作用。详细见
[13.5-7C 实施报告](reports/task13_5_7c_read_only_facts.md)。

Web 与后台生命周期已先行拆开：`scripts/start_local.ps1` 不再启动或停止 Queue Service，
后台服务改由 `scripts/start_local_services.ps1` 独立运行。新 Web 目前仍是分阶段施工目标；
旧 Web 默认入口、Presenter 和重复测试的最终切换删除仍严格留在 7F，不能在 7C 冒充完成。
7B 的基础边界见[13.5-7B 实施报告](reports/task13_5_7b_web_foundation.md)。

7D 已由 PR #34 合并到 `main`。Runtime Schema v17
新增库存权威状态、非负余额、append-only 流水、按平台/PRA 交易日/SKU 的已应用销量基准
和默认关闭的库存预警策略；没有新增平台合同、状态机或真实平台动作。统一 Inventory
Provider 在 `PRE_CUTOVER` 保留工作簿历史库存，在 `DB_AUTHORITY` 后只返回 DB 余额且缺
SKU 立即失败。一次性 bootstrap、零余额新 SKU 初始化、人工有符号调整、完整 CLOSED 订单
净差、高质量估算只减不增、订单替换估算和取消恢复均通过同一 Application Service；余额、
流水和销售基准同一 SQLite 事务。TaskGeneration、规则评估、今日/数据库/业务管理、销售计划
输入、20:00 结算和历史订单回补已改用该权威边界；平台观察库存只作为平台可购上限，不能
覆盖真实库存。库存阈值继续复用现有 `INVENTORY_ANOMALY` Incident 与 Outbox，默认关闭，
且不会创建下架或改价任务。

PR #34 评审意见已统一整改：bootstrap 现在固定 canonical 路径，使用
工作簿独占锁、包含 WAL 的 SQLite 逻辑快照、`BEGIN IMMEDIATE` 写 fence 和提交前回读；
余额/流水/销售基准三表必须同时为空。切换水位由 Runtime 当前有效的版本化交易日策略
计算，并必须绑定最新、十分钟内、完整且订单数为 0 的可信 OPEN 订单批次；当前交易日任一
快照曾观察到订单，或无法证明始终为空时保持 `PRE_CUTOVER`。切换日以可审计的 0 作为
起点，历史回补只同步基准，
之后完整订单只应用切换后净差。新增商品已形成“工作簿库存 0
元数据 → 经商品主数据验证的零余额 → 独立入库”正式链。人工来源方向、并发首次库存提醒
去重、新 Web 失败 PRG 和旧 Web canonical authority 门禁也已收紧。

7D 分支只提供受控 cutover 脚本和合成测试，没有迁移或 bootstrap 真实 Runtime DB，没有
修改真实商品工作簿、Queue、Worker 或平台。真实库仍有 7C 记录的既有外键违规；7D 不推断
来源也不授权修复。真实切换必须先另走 Schema 维护、备份、工作簿 Hash/Runtime 逻辑快照、逐 SKU 与
总量回读及用户明确授权。代码回滚只适用于尚无切换后流水的环境；已有流水必须前向修复，
不得让过期工作簿重新成为库存权威。详细见
[13.5-7D 实施报告](reports/task13_5_7d_authoritative_inventory.md)。

7E 已通过 PR #35 合并到 `main`。人工任务按品种、
等级和平台多选展开，支持调整价格到、加/降价、下架和带平台目标库存的上架；预览与创建
重新校验基础成本、映射、平台事实、真实库存和开放任务冲突，创建事务只写 MANUAL Runtime
Task，零 Queue/Worker/平台副作用。真实执行授权独立要求 `SUBMIT_EXECUTION`、明确 Task ID、
短期 digest 和提交前二次校验，并原样复用 v4 改价与 v5 上下架发布链，不新增 Queue 协议。

桌面与手机复核现已共享同一 Repository 原子业务事务；手机只额外校验和消费一次性 Token，
有效 Token 只进入 Review 专用 HttpOnly/SameSite=Strict Cookie，不再渲染到 HTML。Automation
页面只开放固定方案的允许字段和范围，排程变更通过确定性新版 Job 原子停旧启新；child job
不能独立配置，18:00/20:00 相关时间由实际生效的运营时间策略派生，历史补跑按目标交易日
选择历史策略。销售计划输入偏移和下游每日任务生成在同一事务重版本。库存预警继续复用 v17
策略；受控补跑只创建既有 Automation Run，由独立服务执行，Web 不持有租约或运行 Handler。
复核超时和每日任务生成已接入薄 Handler；后者只读取启用的商品、价格规则和上下架规则，
明确走冻结规则路径并将 Task 绑定 Automation Run，包装、冷库和 Mock 平台 evaluator 不进入
生产 Automation。

正式 production 执行不再携带开发确认文本或确认人，认证操作者改由既有 Task 历史审计面
持久化；development 保留原固定确认合同。新旧 Web 的 Mobile Review 继续使用同一组稳定
HTTP 状态映射。

`serve-web` 已切换到新运营 Web；旧 Excel 生成和 Runtime/Review 写入 CLI 现在要求明确的
测试或管理员恢复开关，不能作为日常旁路。7E 没有迁移或修复真实 Runtime DB，没有启动
Worker、写 Queue、发送通知或执行真实平台动作。完整实现与验收边界见
[13.5-7E 实施报告](reports/task13_5_7e_control_plane.md)。本地专项回归为
`130 passed, 21 subtests passed`，完整 pytest 为
`1280 passed, 3 skipped, 102 subtests passed`，隔离系统冒烟为 `16 passed, 0 failed`；构建、
严格制品审计、secret scan、wheel 隔离安装和 Windows 临时 ShadowBot fixture 均通过。

7F 当前在 `codex/task13-5-7f-cutover-acceptance` 独立分支完成唯一 Web 切换和本地验收。
旧 `app.web`、旧样式和重复测试已删除，制品审计禁止旧模块回流；`serve-web` 只启动新运营
Web。系统入口已分成运行状态、通知通路、数据与备份和高级诊断，`SYSTEM_ADMIN` 与只读
查看权限分离。Worker 恢复、通知测试和备份只提交固定类型化意图，分别复用既有 Incident /
Automation、Notification Outbox 和 release backup；Web Route 不接受脚本、命令或路径，
也不直接启停 Worker 或等待后台执行。

真实 Runtime DB 六个主要 GET 页面已再次验证主库和 WAL 内容零写，既有健康问题仍只使
`/health` 返回 503，7F 没有初始化、迁移或修复真实数据。桌面和 390×844 手机验收通过，
任务动作字段显隐、无平台映射阻断和静态资源发布重验证已修复。完整 pytest 为
`1215 passed, 3 skipped, 82 subtests passed`，系统冒烟为 `16 passed, 0 failed`，制品构建、
审计、隔离安装和 Windows fixture 均通过。真实飞书仍等待独立通知后台新鲜心跳；真实平台
写仍等待用户另行指定商品和批次授权，因此两项不能在本分支冒充已验收。详见
[13.5-7F 实施与验收报告](reports/task13_5_7f_cutover_acceptance.md)。

### 2.8 自动规则评估框架 MVP

已完成轻量自动规则评估框架第一版：

- 新增 `script_runs / script_run_items`，记录 evaluator 运行和 proposal 明细。
- 新增 evaluator / proposal / runner 结构。
- `dry-run` 只写脚本运行记录，不写业务 `tasks / review_tasks / notification_logs`。
- `apply` 通过现有 `RuntimeTaskService / ReviewTaskService / NotificationSender` 链路落成业务任务、复核和通知。
- apply 前基于 `proposal.dedupe_key` 做幂等检查，重复运行不会重复生成 `capacity_warning / labor_required` 复核。
- 新增 CLI：`python scripts/evaluate_business_rules.py`。
- 任务中心新增“脚本状态”分页：`/tasks?task_tab=automation`。
- `CapacityRuleEvaluator` 读取 `harvest_forecasts.xlsx` 和 `capacity_plans.xlsx`，按 `trade_date` 使用启用的包装产能计划，并以 `confirmed_packing_capacity_qty` 判断是否需要产能预警。语义是“预测产量超过确认包装能力”的预警，不是订单需求超过包装能力的预警。
- 已新增保守版 `ListingRuleEvaluator`，语义是“上下架规则建议下架时生成人工复核 proposal”，不会直接生成可执行平台动作。
- 已新增保守版 `ColdStorageEvaluator`，语义是“预计冷库占用超过容量或剩余容量低于阈值时生成冷库预警复核 proposal”，不会直接阻断任务或执行平台动作。

### 2.9 Mock 平台同步实验室

已完成 Mock Platform Sync Lab 第一版，用于在本地验证运行态任务执行和平台状态同步闭环。

已完成能力：

- 新增独立 Mock 平台数据库：`data/runtime/mock_platform.sqlite3`。
- Mock 平台状态不写入 runtime DB，也不回写 `products.xlsx` 公共库存。
- 新增 `MockPlatformExecutorService` 与 CLI：`python scripts/run_mock_platform_executor.py`。
- CLI 支持 `--dry-run / --apply / --init / --reset-sample / --platform / --task-id / --runtime-db / --mock-platform-db`。
- `dry-run` 只预览，不写 `tasks / execution_logs / mock_platform_products`。
- `apply` 通过 `RuntimeTaskService` 推动任务状态，并写入 `execution_logs`。
- Mock 执行器支持 `update_price / set_online / set_offline / sync_status`。
- 平台商品缺失、非法低价等场景会执行失败并写入执行日志。
- 新增 `PlatformSyncEvaluator`，用于对比 PRA 期望状态与 Mock 平台实际状态。
- 当前差异类型包括：`price_mismatch / listing_status_mismatch / stock_mismatch / platform_sync_warning`。
- `PlatformSyncEvaluator` 只生成 review proposal，不自动修复平台状态。
- 任务中心新增只读分页：`/tasks?task_tab=mock_platform`。

边界：

- Mock 平台同步实验室不是接入真实销售平台，也不是影刀真实平台实验室。
- Mock 平台同步实验室不接真实 RPA。
- 不直接发送飞书。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不把平台库存覆盖为 PRA 公共库存。

### 2.10 影刀微信小程序 RPA 单平台闭环

已完成真实桌面微信小程序 `蚂蚁花团供应商` 的单平台、多商品、受控改价闭环。当前定位是“可审查的单平台执行实现”，不是生产级无人值守或多平台调度承诺。最终交接见 [reports/task12_final_handoff_20260723.md](reports/task12_final_handoff_20260723.md)。

当前已验证能力：

- 影刀应用 `test2` 控制桌面端微信小程序 `WeChatAppEx`，`vertical_slice_read_price.py` 支持 `READ_ONLY / COMMIT / RECONCILE`；FILL_PREVIEW 作为历史开发诊断能力保留，不是正式 COMMIT 前置。
- 任务中心正式输入使用内部 SKU、`expected_old_price` 和 `target_price`；SKU 通过 `products.xlsx` 映射为页面商品名称和等级。
- v4 合同一次投递完整多商品 `items` 队列，不包含页面位置、READ_ONLY、FILL_PREVIEW、`listing_status_id` 或快照版本依赖。
- Worker 写操作前主动刷新并结构化读取当前“上架中”页面，按“商品名称 + 等级”匹配全部目标，确认均唯一存在并校验全部旧价。
- 任一旧价不一致时，全批次返回 `OLD_PRICE_CHANGED/NOT_STARTED`，不提交任何商品。
- 全部门禁通过后，按页面实时行号从上到下严格串行执行；页面顺序变化和跳过中间商品均已实机覆盖。
- 第 4 行及以后商品按实际元素边界滚动，不通过点击失败后再滚动试错；非默认滚动位置失败样本和修复后成功样本均已归档。
- 每项提交后独立重新定位并回读，只有平台实际价格等于目标价才记为 `VERIFIED`。
- 结果同时回传完整页面的价格、库存和 `ONLINE` 状态，Importer 校验后更新任务、批次账本、逐商品账本和 `listing_status`。
- `ShadowBotExecutor` 继续管理 operation/attempt、副作用检查点和 UNKNOWN→唯一 RECONCILE；Worker、Importer 和 Watchdog 不得自行创建重复 COMMIT。
- 2026-07-23 审查接续修复将 manifest/request/result 分别升级为 `1.2/1.2/1.1`：逐商品 `operation_id` 在任务载荷不变时保持稳定，逐商品 attempt 随批次执行尝试变化；页面身份只用于 UI 定位，写锁身份固定为“平台 + internal_sku”。
- 终态结果支持幂等重投影：同一份已通过身份和哈希绑定的结果若曾在任务或商品状态投影阶段中断，再次导入只修复数据库投影，不重新发布队列请求或重复执行平台动作。
- Result Importer 先完成严格类型、价格、时间、逐项绑定和计数校验，再在一个 SQLite 事务中写入页面快照、任务、批次/逐项账本、operation/attempt/checkpoint、写锁和技术回执。数据库回执是接受真值，归档 ACK 是可重试投影。
- Worker 的通用异常和 4 MiB 降级结果均保留完整逐商品骨架；Watchdog 只重建完整恢复结果，只有 `ShadowBotExecutor.ensure_reconcile_attempt(...)` 可以创建唯一 RECONCILE。
- `ShadowBotFileQueueRunner` 按 `.ready.json + .sha256` 原子发布请求；常驻 Worker、Result Importer 和 Queue Watchdog 的职责保持分离。
- `test2` 支持长驻监听；生命周期状态记录避免每条任务重复启动/关闭，正常结束使用 `stop.signal → STOPPED → 关闭.flow`。
- 最终暖态四商品实机批次 `BATCH-T12-WARM-FAST-PATH-20260723-01` 为 4/4 `VERIFIED`，总用时 `51.094 秒`。
- READ_ONLY 完整页面结束判定样本 `ATTEMPT-PLATFORM-ENDMARKER-READONLY-20260722-01` 为 1 页、1 次扫描、0 次滚动、`27.445 秒`。

13.5-0 于 2026-07-29 首次只读审计发现生命周期、心跳与部署 hash 不一致；随后已按
停机与编辑器关闭门禁完成 4 个差异文件备份和官方同步，6 个受控部署文件全部
`CURRENT`，部署验证通过。应用从列表重启后 Worker 保持新鲜 `RUNNING`。部署后
`ATTEMPT-POST-DEPLOY-READONLY-20260729-03` 完整扫描“上架中/待上架”，结果、
快照、ACK 与 Runtime DB 回读均为 `VERIFIED`，副作用为 `NOT_STARTED`，队列已
清空。此前两次失败发生在用户确认的特殊页面状态，不定性为已确认选择器漂移。详情见
[13.5-0 开工基线](plans/task13_5_0_kickoff_baseline.md)。

历史垂直切片、故障注入和运维验证仍作为当前安全边界的基础：
- 已完成文件队列真实 `READ_ONLY -> FILL_PREVIEW -> 后置 READ_ONLY` 验收：实际旧价 `9.80`，预览目标和输入回读均为 `10.30`，取消后列表实际价仍为 `9.80`；请求、结果、phase、数据库、执行日志和共享证据 SHA-256 均通过自动校验。
- 已新增 `scripts/verify_shadowbot_filequeue_acceptance.py`；当时的单商品验收流程已移入 [archive/shadowbot_pre_task12/shadowbot_filequeue_real_machine_acceptance.md](archive/shadowbot_pre_task12/shadowbot_filequeue_real_machine_acceptance.md)。
- 历史开发工具 `scripts/prepare_shadowbot_commit_acceptance.py` 曾要求新鲜 READ_ONLY 和精确确认文本；该要求只用于早期单商品验收，不是当前 v4 正式 COMMIT 前置。
- 已完成文件队列受控 COMMIT：`ATTEMPT-ACCEPT-COMMIT-94538902e3dd` 将 `C级艾莎` 从 `9.80` 修改为 `10.30`，结果为 `SUCCESS/VERIFIED`，42 项请求、结果、phase、数据库、执行日志和双证据 hash 校验全部通过；独立 post-COMMIT READ_ONLY 再次读取为 `10.30`。
- 已完成隔离 UNKNOWN→RECONCILE 恢复验收：真实 NTFS 队列上的 stale `SUBMIT_CLICKED` 由 Watchdog 写出 UNKNOWN，Importer 导入后 Executor 只创建一个确定性 RECONCILE，最终归并为 `NOT_APPLIED`，两个 attempt 均归档且无活动队列残留。
- 已完成执行中副作用前停止验收：在 READ_ONLY `UI_STARTED` phase 后写入 `stop.signal`，流程在安全检查点返回 `FAILED/WORKER_STOP_REQUESTED/NOT_STARTED`，Result Importer 归档后 Worker 转为 `STOPPED`，31 项专项校验通过。
- 实机等待期间发现“请求发布后过期”旧路径只写 quarantine、数据库 attempt 悬挂为 RUNNING；现已修复为可信请求写 `FAILED/REQUEST_EXPIRED/NOT_STARTED` 可导入结果，历史样本也已迁移归档。
- 2026-07-02 首轮 8 小时观察失败：heartbeat 在 Windows `os.replace` 时遇到 `PermissionError [WinError 5]`，未捕获异常导致心跳线程退出，但主 Worker 继续运行；同时固定行元素定位在商品位置变化后漏掉了实际仍在上架的 C级艾莎并误报 `PRODUCT_NOT_FOUND`。现已增加原子写重试、心跳线程自愈/错误字段、Watchdog stale 告警、严格健康检查和动态商品元素枚举。详见 [reports/shadowbot_8h_observation_failure_20260702.md](reports/shadowbot_8h_observation_failure_20260702.md)。
- 动态定位已由真实 READ_ONLY `ATTEMPT-READ-DYNAMIC-20260702-165627` 验证：命中 C级艾莎的实际父级 index `17` 并读取价格 `18.30`，共享证据完整。现场同时发现 Result Importer 会把瞬时 Windows 文件 I/O 错误立即误判为契约无效；现已改为 `RESULT_IO_RETRY_PENDING` 自动重试，确定性隔离会持久化 `.error.json` 原因。
- 第二轮 8 小时观察 T4 暴露 Watchdog 读取 heartbeat 的 Windows 文件竞争：单次 `PermissionError` 曾终止整个 PRA 队列服务，但 Worker 结果完整且恢复导入后验收通过。现已增加队列 JSON 读取重试和 Watchdog 循环异常隔离，失败时输出 `WATCHDOG_INSPECTION_FAILED` 并继续运行，最新相关回归 `83 passed`。
- 2026-07-03 第三轮 8 小时 READ_ONLY 连续运行已通过：固定代码和 run2 数据库下完成 T0/T4/T7:45 三次自动投递与归档，T1/T6 heartbeat 健康，Worker 在约 8 小时后自动写出 `STOPPED/processed=3`；无 heartbeat 错误、quarantine 新增或活动队列残留。详见 [reports/shadowbot_8h_readonly_observation_pass_20260703.md](reports/shadowbot_8h_readonly_observation_pass_20260703.md)。
- 2026-07-04 已完成证据共享目录不可用和共享截图 hash 不一致实机注入：不可用目录返回 `FAILED/EVIDENCE_UPLOAD_FAILED/NOT_STARTED`；篡改共享截图后 PRA 验收器以 `evidence_1_storage_hash` 拒绝，恢复原图后重新通过。详见 [reports/shadowbot_evidence_fault_injection_20260704.md](reports/shadowbot_evidence_fault_injection_20260704.md)。
- 2026-07-04 至 2026-07-06 已完成 UI 状态故障组：登录失效真实 READ_ONLY 返回 `FAILED/LOGIN_REQUIRED/NOT_STARTED`；从已登录首页真实断网后返回 `FAILED/NETWORK_OR_LOAD_ERROR/NOT_STARTED`，恢复网络后 READ_ONLY 成功读取 `C级艾莎` 价格 `12.00` 并校验证据 hash。白屏分类已实现单元测试，但当前平台无稳定、可重复的白屏注入机制，未伪造实机通过。另已将瞬时无效窗口句柄改为三次重试，最终返回明确的 `WINDOW_NOT_AVAILABLE`。详见 [reports/shadowbot_ui_fault_injection_20260704.md](reports/shadowbot_ui_fault_injection_20260704.md)。
- 2026-07-06 提交意图后 stop.signal 核心行为已通过实机验证：监视器在 `SUBMIT_INTENT_RECORDED` 后写入停止信号，COMMIT 未在副作用区退出，继续将 `C级艾莎` 从 `12.00` 修改为 `12.50` 并返回 `SUCCESS/VERIFIED`，前后证据 hash 完整。测试同时发现并修复 phase 原子替换的 Windows 文件共享冲突。Result Importer 已导入并归档该 attempt，SQLite 账本记录为 `SUCCESS/VERIFIED`；历史 `stop.signal` 后续已清除，队列也在后续运行中形成 `STOPPED` 心跳。该次测试因 PowerShell 被人工关闭，未单独保留“同一次 Worker 自然识别停止信号”的 STOPPED 样本，但这不再作为提交意图后副作用保护的未完成项。详见 [reports/shadowbot_post_intent_stop_acceptance_20260706.md](reports/shadowbot_post_intent_stop_acceptance_20260706.md)。
- 2026-07-09 完成真实商品 COMMIT 后 UNKNOWN→RECONCILE 样本：第一轮最新 READ_ONLY 发现旧价从 `12.50` 变为 `8.00` 后安全中止原确认；第二轮用户重新确认 `8.00 -> 13.00`，COMMIT 在 `submit_clicked_at` 后返回 `NEEDS_RECONCILIATION/UNKNOWN/retryable=false`，Importer 导入后 Executor 自动创建唯一 `RECONCILE-57cc1892fb8d24961556`，对账读取 `actual_price=13.00` 并将 operation 归并为 `VERIFIED`。本轮暴露的队列根目录与自动对账共享证据继承问题均已完成代码修复：`--queue-dir` 强制同步两个队列环境变量，Importer 从已校验源请求继承 `evidence_share_dir`、`applet_uri` 和 `window_title`。后续已完成 pytest ACL 排障，相关 4 项定向回归通过。详见 [reports/shadowbot_unknown_reconcile_attempt_20260709.md](reports/shadowbot_unknown_reconcile_attempt_20260709.md)。
- 2026-07-11 已完成商品列表强制刷新真实 READ_ONLY 验收：Worker 在价格读取前即使商品管理列表已存在，仍点击“商品管理”重新拉取数据，并写入 `product_list_refreshes`。`ATTEMPT-REFRESH-READ-20260711-135356` 返回 `READ_COMPLETED/NOT_STARTED`，读取 `C级艾莎` 价格 `9.00`，共享证据、hash、结果导入和归档校验均通过。提交后的 `AFTER_SUBMIT_VERIFY` 刷新路径留待后续受控 COMMIT 或独立对账实机样本验证。详见 [reports/shadowbot_product_list_refresh_readonly_20260711.md](reports/shadowbot_product_list_refresh_readonly_20260711.md)。
- 2026-07-13 已完成 URI 冷启动完整 READ_ONLY 验收：在小程序窗口关闭时，`ATTEMPT-URI-FINAL-READ-20260713-154700` 通过 `weixin://launchapplet/` 启动目标小程序（`URI_LAUNCHED`），随后按“登录检查/恢复 -> 商品管理刷新 -> 读取价格”执行，动态定位到第 `38` 行“艾莎 B级”，读取实际价格 `10.00`。结果为 `READ_COMPLETED/NOT_STARTED`；请求、结果和 phase checksum，instruction/request hash，PRA 执行日志，以及共享证据 SHA-256 均通过验收。该样本不代表业务改价完成。
- `NEEDS_RECONCILIATION` 的唯一自动对账入口位于 `ShadowBotExecutor.ensure_reconcile_attempt(...)`；Worker、Importer 和 Watchdog 都不能自行构造对账请求。
- 已新增 `YingdaoOpenApiJobRunner`，可通过影刀开放 API `JOB运行/启动应用` 启动指定 `robotUuid`，并返回 `shadowbot_run_id=yingdao-job:{jobUuid}`。
- 已新增 `check-yingdao-app-params` 只读预检命令，可在真实启动前确认影刀应用已暴露 `request_json` 入参和 `shadowbot_result_json` 出参。
- 已新增 `scripts/check_shadowbot_readiness.py` 离线就绪检查命令，可在真实启动前检查 runner、runtime DB 和必需环境变量；该命令不启动影刀、不访问影刀 OpenAPI，也不会输出密钥明文。
- 已新增 `poll-yingdao-result` 桥接命令，可通过影刀 `job/query` 读取 `shadowbot_result_json` 出参并回写 PRA `execution_logs`、operation 和 task。
- 当前项目不再使用环境变量 SKU/平台白名单；现有普通 production 入口只接受操作人员明确传入的一个或多个 `--task-id`，再校验这些任务仍为有效 `pending update_price`。`pending` 只是候选状态，不能由调度器自动解释为执行授权。任务 13.5 未来允许的 `SYSTEM_EMERGENCY` 必须走独立版本化策略、二次观察和明确 `SET_OFFLINE` 任务，不改变当前入口事实。明确选择后的批次由 v4 批次/逐项哈希、单 Worker 多商品严格串行、旧价门禁和可对账状态机共同约束。
- 已新增 `scripts/prepare_shadowbot_e2e_chain.py`，可一键准备首条 `update_price` task、approved review、批准载荷 hash，并在显式 `--start` 时调用 Executor 启动 `COMMIT`。
- 已新增 `scripts/run_shadowbot_e2e_local_demo.py`，可在本地 runtime DB 中演练成功、提交前失败、提交后未知再对账三条结果分支，并通过 Web 执行日志查看字段、告警和证据链接。
- 已新增 `scripts/run_shadowbot_executor.py` 桥接脚本，可从已批准 review 启动 ShadowBot 执行尝试，也可导入影刀结果 JSON 回灌 PRA 运行态。

关键安全结论：

- 提交前失败必须保持 `side_effect_state=NOT_STARTED`。
- 提交点击后、列表复核前失败必须进入 `NEEDS_RECONCILIATION`，`retryable=false`。
- 结果未知后只能执行 `RECONCILE` 只读核对，不允许自动重复 `COMMIT`。
- 当前小程序实际副作用边界按 `INNER_CONFIRM` 管理：价格弹窗“确认”后已经可能产生平台结果，不应依赖独立最终保存按钮。

边界：

- 当前已形成不依赖 OpenAPI 的本地文件队列代码闭环，并完成 READ_ONLY、历史 FILL_PREVIEW、v4 多商品 COMMIT、逐项独立回读、副作用前停止、提交意图后停止、UNKNOWN→RECONCILE 和 8 小时 READ_ONLY 连续运行验收。尚未完成长期告警和证据运维观察，因此仍不是无人值守生产调度承诺。
- 影刀 OpenAPI runner 继续保留为可选能力；申请成本较高，不再作为当前阶段的上线前置条件。
- 当前未形成无人值守生产运行承诺。
- 当前仅验证单平台、单窗口、单 Worker 严格串行多商品路径，不支持跨平台混合批次或多 Worker 并发。
- 任务12交接文档整理后的核心定向回归为 `62 passed in 10.72s`；系统冒烟测试为 16 项通过、0 项失败。完整发布仍需按第 10 节运行更大范围测试。

## 3. 当前主控流程

当前主控流程如下：

1. 运营维护 Excel 业务输入。
2. 系统根据 Excel 输入生成 runtime `tasks`。
3. 高风险、提醒或需要人工确认的内容进入 `review_tasks`。
4. pending `review_task` 创建后触发 `notification_logs`。
5. 飞书通知发送到群，消息中携带 Mobile Review 链接。
6. 运营人员通过 Web `/reviews` 或手机 Mobile Review 处理复核。
7. 复核处理结果写回 `review_tasks`。
8. 如绑定源任务且满足条件，通过 `RuntimeTaskService` 推动 `tasks` 状态。
9. 源任务状态变化写入 `task_status_history`。
10. 操作人员显式传入一个或多个 `--task-id`；ShadowBot 批次管线只读取这些明确选择且发布前仍有效的 `pending update_price` 任务，并生成一个 v4 COMMIT 请求。
11. Worker 完成全页预扫描、旧价门禁、页面顺序编排、严格串行提交和独立回读。
12. Result Importer 校验合同后更新任务、批次账本、逐商品账本、`listing_status` 和 `execution_logs`。

Mock 平台测试流程在当前阶段作为本地验证链路：

1. Mock Platform Executor 读取 runtime `tasks`。
2. `apply` 时通过 `RuntimeTaskService` 推动任务状态，并修改 `mock_platform.sqlite3` 中的模拟平台状态。
3. 执行结果写入 `execution_logs`。
4. `PlatformSyncEvaluator` 对比 PRA 期望状态与 Mock 平台实际状态。
5. 发现差异时生成 review proposal，再由现有复核和通知链路处理。

真实平台价格更新流程：

1. 普通改价由操作人员从任务中心明确选择同一平台的一个或多个 `update_price task_id`；任何阶段都禁止自动扫描并发布全部 pending。
2. PRA 以 `products.xlsx` 将 SKU 唯一映射为页面商品名称和等级。
3. 批次管线创建一个 v4 COMMIT 合同并原子发布一次。
4. ShadowBot 读取当前页面、匹配全部目标并校验全部旧价。
5. 全部门禁通过后按页面实时位置严格串行提交，每项独立回读。
6. Importer 校验并回写运行态；UNKNOWN 只允许进入唯一 RECONCILE。

## 4. 数据流

```text
Excel 业务输入
  -> runtime tasks
  -> review_tasks
  -> notification_logs
  -> Web Review / Mobile Review
  -> task_status_history
  -> execution_logs
```

Mock 平台测试数据流：

```text
runtime tasks
  -> Mock Platform Executor
  -> mock_platform.sqlite3
  -> execution_logs
  -> PlatformSyncEvaluator
  -> review_tasks / notification_logs
```

真实平台价格更新数据流：

```text
products.xlsx + runtime tasks
  -> ShadowBot v4 commit manifest/request
  -> file queue -> test2 Worker -> 微信小程序
  -> item results + full page snapshot
  -> Result Importer
  -> tasks / execution_logs / commit ledgers / listing_status
```

关键边界：

- Excel 是业务输入来源。
- SQLite 是运行态事实来源。
- Web 和 Mobile 只是复核入口，不直接写 SQLite 表。
- 所有复核处理必须走 `ReviewTaskService`。
- 所有源任务状态变化必须走 `RuntimeTaskService`。
- 通知发送必须走 `NotificationSender`。

## 5. Web 后台页面结构

### Dashboard

展示最小运营指标：

- pending review 数。
- 即将超时 review 数。
- failed notification 数。
- pending task 数。
- expired task/review 数。

### Tasks

用于追踪运行态任务：

- 支持任务筛选。
- 支持任务详情。
- 展示状态历史、关联复核、关联通知、执行日志摘要。
- 支持“脚本状态”分页，查看自动规则评估运行记录和 proposal 明细。
- 支持“Mock 平台测试台”分页，只读查看本地模拟平台状态。

### Reviews

Web 复核主入口：

- 支持复核筛选。
- 支持复核详情。
- 支持处理 pending review。
- 展示源任务、通知、状态历史、review token 摘要。

### Notifications

通知排障入口：

- 支持通知筛选。
- 支持通知详情。
- 展示关联 review/task。
- 展示当前飞书消息类型摘要。
- 不展示完整 token URL。

### Execution Logs

执行日志入口：

- 当前以 mock/RPA 执行日志查看为主。
- 尚未接入生产级真实 RPA 调度闭环。

### Business Inputs

业务输入入口：

- 承接商品资料与库存录入，保存回 `products.xlsx`。
- 支持补充公共库存，而不是将初始库存拆分到具体平台。
- 承接价格规则管理，保存回 `price_rules.xlsx`。
- 价格规则按品种、等级、平台三维筛选；平台只用于规则命中，不参与库存匹配或 SKU。
- 承接上下架规则管理，保存回 `listing_rules.xlsx`。
- 上下架规则按品种、等级、平台三维筛选，并通过规则策略和库存阈值决定是否建议上架/下架。
- 保留 Excel 表格管理作为高级兼容入口。
- 承接任务生成入口。

### System

系统检查入口：

- 检查环境变量配置。
- 检查 runtime DB、schema、运行态表计数。
- 展示运行态摘要。
- 手动发送飞书测试通知。
- 不自动探测 cpolar / Mobile Review 外网链路。

## 6. 当前明确未做事项

当前明确不做：

- 不承诺真实销售平台无人值守生产改价；当前 production profile 以有效 pending 任务为执行权威，并保留单 Worker 多商品严格串行、旧价校验和人工可对账边界。
- 不承诺生产级无人值守 RPA；本地文件队列、自动对账和审计闭环代码已完成，但常驻实机样本、告警和长期证据运维仍未达到生产级。
- 当前不接 AI Agent 自动决策；未来接入必须复用已冻结的 Agent Gateway，不得另建直连
  Web、CLI、数据库、Queue 或平台执行器的通道。
- 不引入 React / Vue。
- 不做前后端分离。
- 不迁移 Excel 主数据。
- 不新增完整权限系统。
- 不做飞书交互卡片审批。
- 不做飞书长连接或回调。
- 不做完整移动端 UI。

## 7. 安全边界

不得提交：

- `.env.local`
- `.env`
- `.env.*`
- `scripts/local_env.ps1`
- `REVIEW_TOKEN_SECRET`
- `RUNTIME_ADMIN_PASSWORD`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_WEBHOOK_SECRET`
- 带 `token=` 的 Mobile Review URL
- `data/runtime/`
- `*.sqlite3`
- `*.db`

展示与持久化要求：

- 不展示 secret。
- 不展示 raw token。
- 不展示完整 webhook。
- 不展示完整 `mobile_review_url`。
- `notification_logs.message` 不应保存 `token=`。
- Web 后台运行态页面需要登录。
- Mobile Review token 只用于指定 `review_task`。

## 8. 后续推荐优先级

任务12审查修复版已通过 PR #18 合并，其正常 COMMIT 与受控
UNKNOWN→唯一 RECONCILE 继续作为稳定基线。任务13的实现、受控实机验收、
脱敏证据、最终本地回归、PR #19 审查修复和 Windows/Linux Core 均已完成。
本轮文档整理不代替审查方执行合并或任务状态变更，也不扩大无人值守真实 RPA。

Code Review 后的高中低风险问题已完成修复，系统冒烟测试、全量单元测试和主控端到端流程测试均已通过。修复详情见 [reports/risk_fix_report_20260610.md](reports/risk_fix_report_20260610.md)。

13.5-0 准备阶段于 2026-07-29 在当前 main 新鲜复跑完整 pytest：
`679 passed, 3 skipped, 97 subtests passed in 144.54s`。该结果证明当前代码回归通过，
不代替后续 v14、Automation、订单、S4、Web 或实机验收。

推荐顺序：

1. 以 [reports/task13_final_handoff_20260727.md](reports/task13_final_handoff_20260727.md) 为任务13审查入口，复核四维状态模型、Runtime Schema v13、v5上下架流水线、运行边界和证据矩阵。
2. 任务13独立 PR #19 的 COMMENT Review 修复、Windows Core 和 Linux Core 已通过；后续合并和任务状态仍由审查方处理。
3. 以 [GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20) 合并后的正文为
   宏观权威，先按[13.5-0 开工基线](plans/task13_5_0_kickoff_baseline.md)完成评审：
   当前 ShadowBot 生命周期、部署 hash 和部署后完整 READ_ONLY 基线已经收敛；六级
   质量矩阵和日结状态机按
   [13.5-1 冻结合同](plans/task13_5_1_quality_and_settlement_contract_review.md)
    已通过门禁。Draft PR #22 已提交 v14 与 13.5-1 业务代码；本轮已按评审修复
    FINAL 并发/不可变性、任务来源、Automation 状态、Incident、时间策略和非 FINAL
    修订合同；复审新增的时间策略版本不可变、观察/审计 append-only 和人工任务来源
    引用门禁也已落地。最终复审新增的时间策略原子替换、并发单一成功、任务来源
    创建后不可变和粗粒度枚举/引用前缀映射也已完成，本地完整回归为
    `744 passed, 3 skipped, 97 subtests passed`。13.5-2 商品映射与扫描输入已通过
    PR #23 合并：评审要求的 run 类型/状态/平台/时间策略绑定、精确页面范围、
    时间/价格/证据校验、同 run 内容幂等、跨 run 接收审计、批次状态矩阵、页面
    顺序规范化和 WEB 平台登记隔离均已修复；显式停用的内置平台不会被默认值补回。
    终态 run 对同 ID 和跨 ID 同内容重放保持一致幂等，只有新内容插入要求
    `RUNNING`，同时仍校验 run 类型、平台和时间策略。
    12 条迁入映射仅保留为 `candidate_internal_sku` 且全部 `DISABLED`，等待运营
    逐条复核。
    修复后本地完整回归为 `790 passed, 3 skipped, 97 subtests passed`，系统冒烟
    16/16、编译、打包、包边界、敏感信息扫描、仓库外 wheel 安装和 Windows
    ShadowBot 夹具门禁均通过。13.5-3 已建立独立 Automation Service 本地实现：
    默认计划、逻辑 run 幂等、租约 fencing、错过/补跑/合并、父子 run、UI 写侧
    阻断、单实例锁与 UTF-8 原子心跳均已有专项测试。PR 评审后进一步补齐了旧
    `SCHEDULED` 每轮清理、崩溃后合并恢复、逐次领取原子 UI gate、受父租约
    fencing 的原子子 run、禁用/子任务领取门禁、Runtime 时间策略热加载，以及按
    Runtime DB 唯一化的进程锁和失败心跳。第二轮评审进一步把扫描合并收紧为
    两阶段覆盖候选，无 handler、禁用、部分成功或失败目标都会让小扫描回退；业务
    事实写入必须在同一事务校验 Automation claim，活动
    Automation UI 租约与 v4/v5 写锁形成双向门禁，公开 `claim_run` 不再绕过策略，
    子 run 只在父 run `SUCCESS/PARTIAL` 后可领取且父失败会取消未开始子 run。
    第三轮评审继续把最终覆盖绑定到 `LISTING_STATUS_SCAN` 子 run 的已接受业务事实：
    完整扫描父 run 完成后候选转交商品子 run，只有同一清单的任务 13 `VERIFIED`
    双页快照和 v14 `ACCEPTED` 完整观察同时存在时才合并；订单子任务不影响该判定。
    重启后任一必要 handler 丢失会释放既有候选。自动化清单采用不可变 SHA-256
    绑定，权威 `SYNC_STATUS` 在写快照、投影、异常、复核和通知的同一事务内执行
    claim fencing，同时保留未绑定批次的人工导入路径；事实接收时间改用应用服务
    可信时钟。
    第四轮评审进一步封闭跨交易日和来源替换：自动化快照、页面及逐项观察必须全部
    匹配 run 冻结的 `platform_trade_date`，跨 18:00 完整扫描在权威写入前拒绝；
    manifest 首次绑定只允许尚未发布、无任何结果事实的 `PREPARED sync_status`
    批次，历史人工完成批次不能事后绑定。v14 观察通过 append-only 来源字段显式绑定
    snapshot、manifest、result SHA、交易日和标准转换摘要，最终覆盖不再依赖批次 ID
    命名；所有租约安全时钟均在取得 `BEGIN IMMEDIATE` 后采样。
    第五轮评审继续封闭 Task 13 与 v14 的 SKU 分裂：明确 SKU，或
    `UNMAPPED/AMBIGUOUS` 状态及候选 SKU 集合，均按 snapshot item/page 冻结并
    纳入来源/转换摘要；当前映射解析发生漂移时观察整批零写，最终覆盖还会复核
    持久化观察 SKU/映射状态与 snapshot 身份。
    第六轮评审进一步把 ProductObservation 的既有事实读取与新事实写入分路：
    终态 run 的幂等重放返回数据库保存的原批次、内容摘要和验收映射版本，不再依赖
    当前全局映射；只有新事实才解析当前映射并要求实时 claim。PR #24 已于
    2026-07-30 合并，合并提交为 `6d46e3f`。真实扫描 handler、Runtime DB 迁移和
    生产部署仍未执行。最终 ProductObservation 专项 `46 passed`，完整回归为
    `852 passed, 3 skipped, 97 subtests passed`；系统冒烟、构建、包边界、
    secret scan、仓库外 wheel 安装、Windows ShadowBot 静态夹具和双平台 CI 门禁
    均通过。下一阶段边界见
    [13.5-4 订单历史只读观察合同](plans/task13_5_4_order_history_observation_contract.md)。
4. 任务13.5通过验收后，任务14只进行多品种、多动作、异常恢复、正式授权和观察版本冻结的综合验收。普通写动作保持明确任务与授权；唯一自动写特例是验收后的版本化 `SYSTEM_EMERGENCY` 紧急下架。
5. 任务12 PR #18 已合并；任务13也已完成 T13-0 页面探索、T13-1 合同、T13-2 Runtime Schema v13、独立两页 SYNC_STATUS、单商品状态往返、正常多商品严格串行上下架、整批预检异常零写、严格串行 UNKNOWN、最终确认点击后的 `UNKNOWN → 唯一自动 RECONCILE → VERIFIED` 和 `UNKNOWN → 唯一自动 RECONCILE → NOT_APPLIED`、`ALREADY_APPLIED` 0 写点击、跨动作共享写锁、phase/result 恢复、Web 运营投影、最终回归、PR #19 COMMENT Review 修复和双平台 CI。仓库内已保存脱敏证据、自然语言报告、数据库回读及 CI 复算入口；本轮文档整理不执行合并或任务状态变更。
6. 继续运行系统冒烟、完整单元测试和 ShadowBot 成功基线测试，任务13.5不得重写已验证 COMMIT 动作链路。
7. 基于自动规则评估框架继续完善上下架、冷库、包装产能等 evaluator，但保持 dry-run/apply 和 service 边界。
8. AI Agent 自动决策应放在真实平台执行和运维边界通过更长期审查后再推进；接入时只能
   使用项目级已冻结的 Agent Query Adapter 和 `AgentIntent` 唯一写通道，并为身份、
   来源、审批、持久化需求和任何真实平台写权限执行独立 R4；任务 14 不承担该实现。
9. 13.5-0 已建立独立分支、黄金基线、脚本/路由盘点、禁止重写点、Web 独立审计、
   验收清单和 main 回滚点；13.5-1、13.5-2 已分别通过 PR #22、#23 合并，后续编码仍按
   [任务12—13复用路径与失败复盘](shadowbot_task12_task13_reusable_lessons.md)
   逐子 PR 建立最小差异和回归门禁。

## 9. 后续可复用资产

完整资产见[任务12—13可复用资产清单](task12_reusable_assets.md)，工程路径和
重复失败门禁见[任务12—13复用路径与失败复盘](shadowbot_task12_task13_reusable_lessons.md)。优先复用：

- v4 单次请求多商品合同、批次/逐项哈希和原有 v12 批次/逐项/技术回执账本；Runtime Schema v13 通过公共批次注册表继续兼容这些历史数据。
- 文件队列原子发布、Worker 租约、phase、Importer、Watchdog 和归档。
- operation/attempt/side-effect 状态机及 UNKNOWN→唯一 RECONCILE。
- SKU→商品名称/等级映射、平台/等级规范化和完整页面快照。
- 全目标唯一匹配、旧价总门禁、页面实时顺序编排和视口边界检查。
- 每项提交后的独立回读和 `listing_status` 新鲜度保护。
- 长驻 `test2` 生命周期记录和 `stop.signal → STOPPED → 关闭.flow` 收尾。
- 成功动作基线、v4 编排、READ_ONLY 快照和状态回写测试。
- v5 单次完整队列、两页父子快照、统一 action gate、跨动作写锁和共享批次终态语义。

## 10. 推荐验收命令

```powershell
python scripts/run_system_smoke_tests.py
python -m unittest discover -s tests
python -m pytest -q tests/test_shadowbot_commit_batch.py tests/test_shadowbot_commit_pipeline.py tests/test_shadowbot_commit_success_baseline.py tests/test_shadowbot_commit_v4_orchestration.py tests/test_shadowbot_readonly_snapshot_baseline.py tests/test_shadowbot_product_read.py tests/test_listing_status.py
```

系统冒烟测试脚本已落地，建议与完整单元测试一起作为后续功能开发前、发布前和回归排查时的基线检查。

pytest 运行环境注意事项：

- 使用系统 Python 执行 `python -m pytest`。
- 当前已确认系统 Python `C:\Users\etere\AppData\Local\Programs\Python\Python314\python.exe` 安装了 `pytest 9.1.1`。
- 不要使用 Codex bundled runtime Python `C:\Users\etere\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` 运行 pytest；该环境未安装 pytest。
- 若在 Codex 沙箱中遇到 pytest 临时目录 `PermissionError: [WinError 5] 拒绝访问`，优先视为沙箱临时目录权限问题，而不是 pytest 未安装。
- 当前本机已为 `etere` 与 `CodexSandboxUsers` 授予 `.pytest_cache` 和 `%TEMP%\pytest-of-etere` 的修改权限；需要可信测试结果时，仍应在普通 Windows 用户环境运行 `py -3.14 -m pytest`，而不是把 Codex 文件沙箱的目录限制当作应用回归失败。
