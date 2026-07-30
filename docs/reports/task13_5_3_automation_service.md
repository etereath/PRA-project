# 任务 13.5-3：独立 Automation Service 实施报告

- 实施日期：2026-07-29 至 2026-07-30
- 分支：`codex/task13-5-3-automation-service`
- 前置合并：PR #23，merge commit `64772b2`
- 合同：[13.5-3 Automation Service 合同](../plans/task13_5_3_automation_service_contract.md)

## 1. 已实现

- 新增 Automation job/run/event/link 数据模型与事务 Repository。
- 新增稳定逻辑 run key、同窗口并发幂等和 job 类型不可变门禁。
- 新增 10 分钟、每小时、17:55、18:05、20:00、20:05 默认计划。
- 新增休眠错过、最近窗口补跑、日结幂等补跑和每 job 物化上限。
- 新增邻近完整扫描覆盖小扫描的候选与 `MERGED_RUN` 链接。
- 新增按 job priority 领取和 UNKNOWN/RECONCILE、活动写锁的 UI gate。
- 新增 `lease_owner + lease_version + lease_expires_at` 原子领取、续租、超时回收和
  晚到写回 fencing。
- 新增 handler 注册边界、受限失败结果和父子 run 幂等创建。
- 新增 `scripts/run_automation_service.py`、跨平台单实例锁、UTF-8 原子进程心跳和
  `--once` 健康入口。
- PR 评审后补齐每轮旧 `SCHEDULED` 清理、无新窗口的迟到失效和崩溃后合并恢复。
- UI 阻断检查改为与每次领取同一写事务，消除 cycle 级 TOCTOU。
- 子 run 改为父租约 fencing 下的一次事务，并限制父子类型、关系与平台，直接继承
  父 run 冻结时间上下文。
- 领取层增加启用状态与合法父链门禁；默认 job 启动时校验静态身份。
- Runtime 时间策略改为每轮读取完整生效链；进程锁改由 Runtime DB 身份派生，锁后
  异常写 `FAILED` 心跳且执行本机路径脱敏。
- 第二轮评审后将扫描合并改成两阶段覆盖：先建立 `COVERAGE_CANDIDATE`，只有具备
  handler 的启用目标以 `SUCCESS` 完成后才落最终 `MERGED_RUN`；目标部分成功、
  失败、取消、错过或禁用时释放小扫描回退执行。
- 活动 Automation UI run 的租约覆盖 handler 完整执行期；v4/v5 写任务在取得写锁
  的同一事务内反查该租约，Automation 侧继续原子反查活动写锁，形成双向单 UI 通道。
- 商品观察事实导入改为强制携带 Automation claim，并在事实写入同一事务内验证
  owner、version、状态和到期时间；旧 owner 及被回收 owner 不能写入新事实。
- 公开 `claim_run(...)` 与常规领取统一启用状态、父链、覆盖候选和 UI 门禁。
- 子 run 仅在父 run `SUCCESS/PARTIAL` 后可领取；父 run 失败、取消或错过会取消尚未
  开始的子 run。
- 第三轮评审后把覆盖完成条件从“完整扫描父 run 成功”收紧为“商品状态子 run 成功且
  权威业务事实已接受”：候选先绑定父 run，父完成后转交 `LISTING_STATUS_SCAN`
  子 run；只有同一清单的任务 13 `VERIFIED` 双页快照和 v14 `ACCEPTED` 完整观察
  同时存在时才最终 `MERGED`。父任务无商品子任务、商品子任务无事实/失败/部分成功
  均释放小扫描，`ORDER_SCAN` 不影响商品覆盖。
- 覆盖候选和领取防御均使用当前已注册 handler 集合；服务重启后父 handler 或商品
  handler 丢失，会释放旧候选，包括目标仍为租约过期 `RUNNING` 的恢复场景。
- Automation 商品子 run 增加不可变输入清单 SHA-256 绑定。绑定后的任务 13
  `SYNC_STATUS` 导入必须在同一事务内校验合法父子链、平台、时间策略和活动 claim，
  旧 owner 在租约回收后不能写入快照、投影、异常、复核或通知；未绑定清单的人工
  导入继续保持独立。
- 商品观察接收时间改由应用服务注入的可信时钟产生，生产调用方不再能够通过 `now`
  参数影响租约判定。
- 第四轮评审后把事实交易日提升为强门禁：任务 13 快照的扫描、分页面和逐项时间，
  以及 v14 批次与逐项观察，必须全部属于 run 冻结的 `platform_trade_date`；
  `17:55→18:05` 等跨 18:00 扫描在任何权威写入前拒绝，最终覆盖再次复核逐项交易日。
- 输入清单首次绑定只允许真实、平台一致且仍为 `PREPARED` 的 `sync_status` 批次，
  并要求尚无 result ID、结果回执或快照；已完成的人工历史清单不能事后绑定到新 run。
- snapshot 与 v14 观察改用 append-only `requested_scope_json` 中的 snapshot ID、
  manifest、result SHA、来源交易日和标准转换摘要显式关联。Importer 从 Runtime
  重读源快照并重算逐项标准转换，最终覆盖不再依赖 observation batch ID 命名。
- 安全时钟在取得 `BEGIN IMMEDIATE` 后才采样；Automation 领取、续租、完成、父子
  创建、清单绑定以及两个事实 Importer 使用同一原则，等待写锁期间到期的 owner
  不能凭锁前旧时间继续写入。
- 第五轮评审后补齐跨事实 SKU 身份门禁：Task 13 snapshot 的明确 SKU，或
  `UNMAPPED/AMBIGUOUS` 状态及候选 SKU 集合，按 item/page 形成不可变来源摘要并
  纳入标准转换摘要。ProductObservation 在事务内把当前映射解析与来源逐项比较，
  SKU、状态或候选集合漂移时整批零写；通过后持久化与来源摘要相等的验证标记。
  最终覆盖要求验证标记匹配，并再次比较持久化观察与 snapshot 身份，不接受仅有
  来源信封但 SKU 已分裂的事实。

## 2. 复用与未改写

- 双日期、卖家阶段和策略版本继续只由 `OperationalTimeService` 计算。
- Schema 继续使用 13.5-1 已冻结的 v14 四张 Automation 表，没有新增迁移。
- 真实平台写动作继续复用 v4/v5、action gate、operation/attempt、共享写锁、
  Importer 和唯一 RECONCILE。
- 本阶段没有修改、同步、启动或停止 ShadowBot Worker，没有投递 READ_ONLY/COMMIT
  队列，也没有迁移或写入真实 Runtime DB。对 v4/v5 的改动仅限写锁事务入口的
  Automation UI 租约反向检查。

## 3. 当前部署边界

CLI 当前明确报告 `SCHEDULER_ONLY`。它可以安全创建到期账本、记录错过/覆盖候选和输出
健康状态，但不会在缺少应用服务 handler 时领取 run 或伪造扫描结果。

后续接入顺序：

1. 13.5-4 注册订单历史只读 Adapter/Importer；
2. 13.5-5 注册销售估算、日结和计划输入 handler；
3. 已有商品扫描执行 Adapter 完成独立验收后，注册 `ONLINE_PULSE` 和商品状态子
   handler；
4. 13.5-6 评审 S4 前，`SYSTEM_EMERGENCY` 仍保持禁用。

`ORDER_SCAN` 子 job 的存在只证明父子编排合同已就绪，不代表订单管理页面已经采集。

## 4. 验收

专项自动化测试覆盖：

- 计划幂等、合并、休眠、错过和任务风暴保护；
- 优先级、单 owner、租约心跳、超时、重启恢复和 fencing；
- handler 异常、父子 run、UNKNOWN 阻断；
- 父候选转交商品子任务、权威快照与观察事实双重接受后合并、订单子任务隔离；
- 重启后 handler 能力丢失、租约过期运行目标和无商品事实的候选回退；
- 事实写入同事务租约 fencing、旧 owner 拒绝和同 run 规范事实保护；
- 自动化输入清单不可变绑定、权威 `SYNC_STATUS` 同事务 fencing 与人工导入隔离；
- 应用服务可信时钟，生产调用方不可指定安全判定时间；
- 跨 18:00 批次与逐项交易日拒绝、最终覆盖交易日纵深复核；
- 已完成人工清单事后绑定拒绝及 PREPARED 首绑原子门禁；
- 任意 observation ID 下的显式 snapshot/manifest/result/交易日/转换摘要来源链，
  以及篡改标准转换拒绝；
- 明确 SKU 漂移、`UNMAPPED→VERIFIED` 和 `AMBIGUOUS` 候选集合漂移零写拒绝，
  来源身份一致时接受，持久化 SKU/映射状态不一致时脉冲不合并；
- `BEGIN IMMEDIATE` 后安全时钟采样；
- Automation UI handler 执行期的跨实例互斥及 v4/v5 写锁反向门禁；
- 公开领取入口门禁与父 run 终态驱动的子 run 领取/取消；
- 单实例锁、UTF-8 原子心跳和正式 CLI。

验收结果：

- `python -m pytest -q tests/test_automation_service.py`：
  `45 passed`；
- 第五轮涉及模块：
  `191 passed`；
- `python -m pytest -q`：
  `850 passed, 3 skipped, 97 subtests passed`；
- 系统冒烟：16 项通过、0 项失败；
- 本次新增/修改 Python 文件 Ruff：PASS；
- `compileall`：PASS；
- wheel/sdist 构建：PASS；
- wheel/sdist 包边界与 secret scan：PASS；
- 仓库外 wheel 安装、核心 import、CLI、Schema v14 与 health：PASS；
- Windows ShadowBot 静态 fixture/hash 漂移门禁：PASS；
- Python/Markdown 中文 UTF-8 显式回读：PASS。

完整仓库级 Ruff 仍会报告 69 项本阶段开始前已存在的告警；本批没有修改这些文件，
也没有用 13.5-3 扩张清理范围。该项与本次新增文件的 Ruff PASS 分开记录。
