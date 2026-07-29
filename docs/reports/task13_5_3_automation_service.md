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
- 新增邻近完整扫描覆盖小扫描的 `MERGED_RUN` 链接。
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
- 两阶段覆盖候选、目标失败回退、无 handler/禁用目标不合并；
- 事实写入同事务租约 fencing、旧 owner 拒绝和同 run 规范事实保护；
- Automation UI handler 执行期的跨实例互斥及 v4/v5 写锁反向门禁；
- 公开领取入口门禁与父 run 终态驱动的子 run 领取/取消；
- 单实例锁、UTF-8 原子心跳和正式 CLI。

验收结果：

- `python -m pytest -q tests/test_automation_service.py`：
  `35 passed`；
- `python -m pytest -q`：
  `830 passed, 3 skipped, 97 subtests passed`；
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
