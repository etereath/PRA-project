# 任务 13.5-6C 专用授权与受控下架实施报告

## 1. 当前状态

本阶段的代码门禁、合成集成、Worker 部署和单 SKU 真实写验收均已完成。专用授权、发布前
撤销重验、v5 点击前竞态栅栏、Importer 状态协作和 Archive 已在同一个一次性 Runtime
Schema v16 数据库中闭环。2026-08-03 对 `AISHA-D-50-Z` 唯一执行一次真实
`SYSTEM_EMERGENCY SET_OFFLINE`，结果为 `VERIFIED`；最终在线列表回读已找不到目标商品。

验收只在受控窗口临时启用独立开关，结果导入后已在控制器 `finally` 中恢复为
`automatic_emergency_offline=false`。Task 为 `success`，Incident 回到
`WAITING_HUMAN`，Review 仍为 `pending`；没有自动重新上架，也没有触发 RECONCILE。

## 2. 编码前复用矩阵

| 能力 | 分类 | 处理 |
| --- | --- | --- |
| v16 策略与 Decimal 解释器 | 原样复用 | 授权调用 6B 同一解释器，不重算第二套 S4 阈值 |
| S4 Review、Outbox `sent_at`、下一完整 Pulse | 原样复用 | 使用 6A-1 资格入口与现有事实，不复制扫描、日期或通知判断 |
| Automation Run/Event | 参数化复用 | 在合格 Pulse Run 上追加稳定 `EMERGENCY_OFFLINE_AUTHORIZED` Event；不新增授权表 |
| Task 表、状态机和底层同连接插入器 | 参数化复用 | 专用服务在同一事务创建唯一 `SYSTEM_EMERGENCY SET_OFFLINE`；通用 Repository 继续拒绝该来源 |
| v5 SET_OFFLINE proposal/request/phase/result | 参数化复用 | 不新增合同版本或 Executor；仅为系统紧急任务附加可验证授权引用 |
| v13 共享写锁、Automation UI 租约、UNKNOWN/RECONCILE | 原样复用 | 发布和点击前均读取既有状态；不增加锁或恢复路径 |
| v5 Importer、receipt、ACK、Archive | 原样复用 | 沿用既有结果投影，增加 Incident 结果事件/状态协作，不复制导入器 |
| 功能开关权威 | 参数化复用 | 使用既有 `automation_jobs` 的专用 `SYSTEM_EMERGENCY_SET_OFFLINE` 作业配置；策略与开关保持独立，默认不存在或为 false |
| 平台最终点击前竞态栅栏 | 抽取公共能力 | 仅系统紧急下架在确认点击前对同一 Runtime DB 获取短 `BEGIN IMMEDIATE`，原子重验并建立人工写入与平台点击的先后顺序 |
| 专用授权 application service | 确需新增 | 复杂度预算允许的唯一新控制入口；新表、动作、Executor、全局锁均为 0 |

## 3. 点击前竞态语义

只在 `SYSTEM_EMERGENCY SET_OFFLINE` 使用以下栅栏：

1. Worker 在已唯一定位商品并核对下架确认弹窗后、记录 `ACTION_INTENT_RECORDED` 前，
   对请求绑定的 Runtime DB 执行短 `BEGIN IMMEDIATE`。
2. 同一事务重读授权 Event、任务来源/状态/载荷、Incident、Review、策略、开关、人工冲突、
   Automation UI 租约、共享写锁、UNKNOWN/RECONCILE 和到期时间。
3. 任一门禁失效时取消确认弹窗并返回确定的 `NOT_APPLIED`，不得记录点击意图。
4. 全部门禁有效时，在事务仍持有的短时间内记录 Action Intent 并执行一次确认点击，随后立即
   结束事务。

因此：

- 人工 Review 或人工任务先取得数据库写事务时，Worker 随后必然读到并停止；
- Worker 先取得栅栏时，人工结果会等待到点击事务结束，其事实时间明确晚于 Action Intent；
- 不使用轮询间隔、队列缩短或概率性“最后一次查询”代替竞态门禁。

该栅栏不是新增业务全局锁，不覆盖普通人工 v5；它只把现有 SQLite 写事务能力用于单个
紧急任务的一次最终安全重验。

## 4. 明确禁止

- 不允许普通 Repository、Web、CLI 或测试夹具直接创建 `SYSTEM_EMERGENCY`。
- 不把功能开关写入策略表，也不允许请求载荷单方面宣称开关开启。
- 不在授权后因价格上涨重新计算或撤销已经冻结的第二次观察判断。
- 不跳过 Review、人工任务、写锁、UNKNOWN、RECONCILE、身份和在线状态门禁。
- 不自动重新上架。

## 5. 已实现入口

- `app/services/emergency_offline_authorization.py`：唯一专用授权服务；在一个
  `BEGIN IMMEDIATE` 中追加稳定 Automation Event、创建唯一
  `SYSTEM_EMERGENCY SET_OFFLINE` Task，并把 Incident 转为 `AUTO_PROTECTING`。
- `app/emergency_offline_fence.py`：主控端与 Worker 共用的授权绑定、证据哈希和可撤销事实
  重验器；不包含平台选择器或第二套动作执行逻辑。
- `app/services/shadowbot_listing_action_pipeline.py`：原 v5 proposal/persist/importer 的最小
  扩展。紧急任务必须单商品、正式运行、不可混批；授权 Review 仅作为等待人工结果的来源，
  其他 Review、页面异常、人工任务和共享写锁继续正常阻断。
- `app/services/shadowbot_listing_action_contract.py`：v5 版本不变，只为紧急 COMMIT 附加
  `emergency_authorization`；SYNC_STATUS、RECONCILE、普通动作不允许滥用该字段。
- `shadowbot/test2/vertical_slice_read_price.py`：复用原 SET_OFFLINE 的唯一定位、确认弹窗、
  `ACTION_INTENT_RECORDED`、确认点击和回读；只在紧急请求中于确认点击前持有短数据库事务。
- `scripts/sync_shadowbot_test2.py` 与 `scripts/verify_shadowbot_deployment.py`：把同一份公共
  fence 模块同步并校验到影刀宿主，避免主控端和 Worker 各自维护判断副本。

## 6. 合成验收结论

合成 Runtime DB 已覆盖：

1. 完整 Pulse、两次已接受在线观察、已送达飞书初始通知、待处理 Review、有效 v16 策略和
   临时开启的测试开关共同形成授权；
2. 授权 Event、Task、Incident 状态任一点故障均整体回滚，精确重放返回原事实；
3. 完整商品快照导入后，紧急 Task 通过原 v5 proposal，建立原 operation、attempt 和共享
   `ACTIVE` 写锁；Worker v5 合同接受同一授权绑定；
4. 开关关闭或 Review 返回发生在 proposal 后、持久化或点击前时，重验失败且不新增写账本；
5. `VERIFIED` 结果由原 Importer 原子投影，Task 为 `success`，Incident 回到
   `WAITING_HUMAN`，Review 继续等待人工处理，事件明确 `automatic_reonline_allowed=false`；
6. 普通 v5 SET_ONLINE/SET_OFFLINE、SYNC_STATUS、UNKNOWN/RECONCILE 合同保持回归。

验收前专项 `tests/test_emergency_offline_authorization.py` 为 `12 passed`；授权、v5 合同、
Worker 静态点击顺序、动作管线、队列和部署受影响组合为 `127 passed, 3 subtests passed`。
验收前完整 pytest 为 `1077 passed, 3 skipped, 97 subtests passed`；系统冒烟为
`16 passed, 0 failed`；Worker/队列/部署补充组合为 `49 passed, 3 subtests passed`。
2026-08-03 部署准备已正常停止空闲 Worker，从影刀应用列表恢复客户端，精确选择 `test2`
并同步 7 个受控文件；二次 `--check` 全部为 `CURRENT`，部署校验通过。新增
`emergency_offline_fence.py` SHA-256 为
`2a2cc0a83b28a497a9a12c803d67f26a1427b9942690c08c111f4fb104d119d8`。长期 Worker 已
恢复为新鲜 `RUNNING`，生命周期记录一致，队列为空且无 `stop.signal`。真实验收期间发现
并修复两处仅在默认服务装配中暴露的问题：授权影子结果允许开关开启后的零阻塞状态；影子
服务默认使用具备 Pulse 资格入口的 `IncidentNotificationService`，而非只负责创建 Review
的服务。新增回归后授权与 shadow 专项为 `37 passed`；验收后完整 pytest 为
`1079 passed, 3 skipped, 97 subtests passed`，隔离系统冒烟为 `16 passed, 0 failed`。

## 7. 验收后的生产门禁

- 当前开关继续保持 `automatic_emergency_offline=false`。本次临时开启只存在于一次性 v16
  验收数据库，不能据此宣称生产无人值守紧急下架已常驻启用。
- 本次外部同步和重启已经通过生命周期门禁；后续再次修改 Worker 文件时仍须重新正常停止，
  不得用本次状态替代下一次核对。
- 本次授权只覆盖 `AISHA-D-50-Z` 的一次 `SET_OFFLINE`，不得复用到其他 SKU、再次下架或
  重新上架。后续正式启用仍需管理员显式配置，并继续逐次满足 S4、Review、下一完整 Pulse、
  人工冲突、写锁和 Runtime 健康门禁。
- 原生产 Runtime DB 的既有健康问题不因一次性验收数据库通过而消失；本次只证明同库
  Watchdog、Worker、Importer 和 Archive 的 13.5-6C 链路通过。

## 8. 2026-08-03 单 SKU 真实验收结果

用户授权对 `AISHA-D-50-Z` 执行一次受控真实紧急下架，并允许验收窗口内临时开启开关。
验收使用独立一次性 Runtime Schema v16 数据库，且把常驻 Queue Service 临时切换到同一
数据库，保证 Watchdog、Worker、Importer 与 Archive 读取同一事实源。

早期第一次只读请求在 Worker 领取前被仍绑定旧 Runtime DB 的 Queue Service 判为
`ORPHAN_READY_REQUEST` 并隔离；该请求没有进入 `working`，没有打开平台动作链，也没有
平台副作用。保留隔离证据后，Queue Service 被切换到本次 v16 数据库，并以新的 attempt
重新执行既有 Task 13 `SYNC_STATUS`。

首次完整双页只读结果确认该商品上架中，但当时价格不满足 S4，因此系统按合同零写入停止。
用户随后把页面售价调整为 `1.50`；权威商品工作簿的 `base_cost=5.00`，紧急阈值为
`4.00`。验收重新完成两份相互独立、范围完整、尾部确认且映射为 `VERIFIED` 的在线观察，
并保证第二份观察来自初始飞书通知送达后的下一有效 `ONLINE_PULSE`。日期范围曾错误使用
UTC 日历日创建一组 Incident/Review，系统因观察绑定不匹配而阻断；该组事实随后被取消和
解决，按 PRA 交易日 `2026-08-03` 重建后才进入正式授权。

正式控制器先确认 Worker 为新鲜 `RUNNING`、`stop.signal` 不存在、队列为空，再临时启用
专用开关，并在同一 v16 数据库中原子创建唯一
`TASK-EMERGENCY-0da2439c1cac3c3153b57841`。v5 proposal 仅含一个 SKU、仅含
`SET_OFFLINE`，发布 attempt 为
`ATTEMPT-T1356C-EMERGENCY-AISHA-D-50-Z-20260803`。Worker 在最终确认点击前重新验证全部
可撤销事实；结果记录 `action_confirm_clicked=true`、`listing_effect_state=VERIFIED`，最终
在线列表扫描 `target_rows_hydrated=0`。动作总耗时约 `30.2s`，未进入 UNKNOWN 或
RECONCILE。

Importer 在同库投影结果并写入 ACK、报告和 Archive。最终检查确认：Task=`success`、
Incident=`WAITING_HUMAN`、Review=`pending`、活动写锁为 0、活动队列文件为 0、一次性数据库
`PRAGMA foreign_key_check` 无问题，Watchdog 无事件。控制器随后恢复开关为 `false`；原
Queue Service 已重新绑定原 Runtime DB，长期 Worker 保持新鲜 `RUNNING`，生命周期最后
attempt 与本次验收一致。该结果构成 13.5-6C 的单 SKU 真实紧急下架验收通过，但不代表
生产开关已常驻启用。

## 9. 2026-08-04 合并前审查整改

本轮把六项完整实现审查阻塞与运营补充的紧急插队要求按同一安全链整改，未执行新的真实
平台动作：

- Incident 主投影采用事件时间单调门禁；迟到事件保留审计但不倒退当前状态；
- 正式 Mobile Review 可抢占 `AUTO_PROTECTING`，并在一个事务中取消尚未执行的自动任务；
  Review Token 最长入口期与自动评估截止时间分离，失效入口通过原 Review/Outbox 原子续期；
- 任务调度固定为“Incident 人工任务 → SYSTEM_EMERGENCY → 普通任务”，v4、v5、显式选择
  和 Automation UI claim 共用同一优先级判定；
- Worker 最终栅栏复用主控端的 Review context 与 `review_block_reasons`，检查持久化后新增的
  全部阻断 Review，只排除绑定的 emergency Review；
- 自动授权和人工决策在 SQLite 写锁内二次读取权威商品工作簿；文件 hash、SKU 或成本变化
  全部 fail closed，不在最终点击前重新计算 80% 阈值；
- v4/v5 Importer 共用人工 Incident 结果投影：成功解决，失败保持待处理，UNKNOWN/部分完成
  保留原 Review/RECONCILE 路径，事件与通知以稳定键防重；
- FINAL 使用共享 subject/scope matcher，规范化真实 `internal_sku/listing` 合同，并只按已选
  输入依赖向品种、等级和时段传播。

并发测试使用两个独立 SQLite 连接覆盖人工先持锁与授权先持锁；两种顺序都以数据库提交
顺序确定结果。另以外部 `BEGIN IMMEDIATE` 制造等待，并在等待期间修改合成工作簿，验证
人工和自动两条成本路径均整笔失败、零任务残留。全部数据均为合成 fixture；未提交真实
订单、买家信息、平台截图或新的真实写证据。

受影响模块集中回归为 `195 passed`；补充通知窗口、结果投影和竞态用例后相关专项为
`61 passed`。最终本地完整 pytest 为 `1116 passed, 3 skipped, 97 subtests passed`；隔离
系统冒烟为 `16 passed, 0 failed`。本轮没有修改或同步 `shadowbot/test2` 文件，没有停止、
覆盖或重启常驻 Worker，也没有再次执行真实平台写操作。
