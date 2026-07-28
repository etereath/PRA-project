# 任务 13.5-1：Runtime Schema v14 实施与验收报告

- 实施日期：2026-07-29
- 基线：`cb3be04af57e8004396d1cb4b2140e7435842762`
- 工作分支：`codex/task13-5-1-contract-review`
- 冻结合同：
  [双时间轴、六级质量与日结状态机](../plans/task13_5_1_quality_and_settlement_contract_review.md)
- 实施状态：本地代码与临时数据库验收通过；尚未迁移真实 Runtime DB

## 1. 本轮交付

### 1.1 双时间轴

新增 `OperationalTimeService` 和版本
`CN_SINGLE_PLATFORM_2026_V1`：

- 时区固定为 `Asia/Shanghai`。
- 18:00 切换 `platform_trade_date`。
- 20:00 切换 `seller_operation_date`。
- `seller_phase` 只允许 `NORMAL_SALES / PEAK_SALES /
  DELIVERY_OVERLAP`。
- 输入必须是 timezone-aware datetime；naive datetime 直接拒绝。
- 旧 `TradeWindowService` 保持不变，继续服务原预测窗口。

### 1.2 Runtime Schema v14

v14 新增：

```text
operational_time_policies
automation_jobs
automation_runs
automation_run_events
automation_run_links
product_observation_batches
product_observation_items
order_observation_batches
order_observation_items
sales_estimate_segments
platform_trade_day_summaries
platform_trade_day_summary_events
platform_trade_day_summary_inputs
operational_incidents
incident_notification_state
```

`tasks` 新增：

```text
origin_type
origin_ref_id
approval_policy
policy_version
platform_trade_date
seller_operation_date
seller_phase
time_policy_version
```

历史任务只回填 `origin_type=LEGACY`；不根据旧 `trade_date` 或
`decision_trace_json` 猜测双日期、阶段或来源。

### 1.3 数据质量和日结

数据库、模型和 Service 共同约束：

- 事实来源只有 `ORDER_OBSERVED / SCAN_ESTIMATED`。
- `UNAVAILABLE` 必须使用 NULL 来源和 NULL 数量、订单数、金额。
- 六级质量值保持冻结合同的精确集合。
- 日结只允许 `PROVISIONAL → OBSERVED → RECONCILED → FINAL`。
- 初版只能从 `PROVISIONAL` 开始；FINAL 后迟到数据创建从
  `OBSERVED` 开始的新版本。
- 一个系列最多一个 `is_current=1`。
- FINAL 内容不可修改；只允许在新版本建立时撤销旧版本 current 标志。
- FINAL 必须为 `ORDER_COMPLETE`，并且不存在显式
  `blocks_finalization=1` 的未解决 Incident。
- 所有转换写入不可变事件，实际输入写入输入关联表。

### 1.4 任务来源安全边界

Schema 预留：

```text
MANUAL
AUTOMATION
SYSTEM_EMERGENCY
LEGACY
```

通用任务 Repository 当前拒绝新建 `LEGACY` 和
`SYSTEM_EMERGENCY`；自动任务必须提供 `origin_ref_id`。
`SYSTEM_EMERGENCY` 仍等待 13.5-6 的专用策略、授权与实机门禁。

## 2. 迁移和兼容

- 初始化和迁移继续在 `BEGIN IMMEDIATE` 中执行。
- v13→v14 先添加兼容列和新表，再记录 schema version 14。
- 迁移失败时新增列、表、策略种子和版本记录整体回滚。
- 重复 `init_schema()` 幂等，不重复创建当前时间策略。
- v4/v5 批次、operation/attempt、写锁、UNKNOWN、receipt、ACK、
  v13 两页快照和异常表保持原语义。
- 精确健康检查覆盖 v14 表、列、索引、外键、冻结枚举、当前唯一索引、
  三个日结触发器和时间策略种子。

真实库迁移必须按
[Runtime Schema v14 迁移运行手册](../runtime_schema_v14_migration.md)
执行；本轮没有修改真实 Runtime DB。

## 3. 验收结果

### 3.1 文件和代码

- Python 源码通过 `py_compile`。
- `git diff --check` 通过。
- 新增代码已进入 wheel 和 sdist。

### 3.2 自动测试

```text
完整 pytest：
708 passed, 3 skipped, 97 subtests passed

系统冒烟：
16 passed, 0 failed

wheel_boundary=PASS
sdist_boundary=PASS
secret_scan=PASS
```

定向用例覆盖：

- 六个时间边界、UTC 输入和 naive datetime 拒绝。
- 新库 v14、v13→v14、v12→最新版本和重复迁移。
- 迁移中途失败的事务回滚和外键恢复。
- LEGACY 历史任务回填且不猜双日期。
- 非法来源/质量组合、UNAVAILABLE 非零和 current 冲突。
- 跳级、回退、直接 FINAL 和 FINAL 内容修改拒绝。
- 幂等转换、Incident 阻断和 FINAL 后迟到数据版本链。

### 3.3 未执行项目

- 未运行 Linux CI；由后续 PR 的 GitHub Actions 验证。
- 未迁移真实 Runtime DB。
- 未启动或同步 ShadowBot Worker。
- 未读取订单页、执行平台扫描或产生真实平台写动作。
- 未实现 Scheduler、订单 Importer、销售估算算法、S4 自动保护或 Web
  重写；这些继续按 13.5-2 至 13.5-10 推进。

## 4. 下一阶段输入

13.5-2 按
[商品映射与扫描输入合同](../plans/task13_5_2_mapping_and_scan_input_contract.md)
实现映射资产、`ONLINE_PULSE` 和 `FULL_MARKET_SCAN` 的商品观察子结果，
并写入 v14 不可变观察表；不得在该阶段实现普通自动写任务或 S4。
