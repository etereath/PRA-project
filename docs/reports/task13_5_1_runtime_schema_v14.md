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
- `observed_at` 统一归一化为 UTC；等价的不同时区输入产生同一个技术时间。
- 按 UTC `effective_from/effective_to` 左闭右开区间选择唯一策略，Service 和
  Schema 都拒绝区间重叠。
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
- PROVISIONAL 实质变化在单事务内原子修订并整体替换输入 manifest；
  OBSERVED/RECONCILED/FINAL 实质变化创建新的 OBSERVED 版本。每个 manifest 的
  输入行按 `summary_id + input_manifest_sha256` 追加保存，旧 manifest 不删除。
- 一个系列最多一个 `is_current=1`。
- FINAL 的业务身份、版本、指标和审计字段全部不可修改；只允许在新版本建立事务中
  执行 `is_current: 1 → 0`。
- FINAL 必须为 `ORDER_COMPLETE`，并且不存在显式
  `blocks_finalization=1` 的未解决 Incident。
- FINAL 门禁查询、summary UPDATE、输入与事件写入位于同一个
  `BEGIN IMMEDIATE` 事务。
- 所有转换写入不可变事件，实际输入写入输入关联表。
- 商品/订单观察批次与明细、销售估算片段、日结事件和日结输入均由数据库触发器
  拒绝 UPDATE/DELETE；精确重复可幂等返回，身份相同但内容不同必须报冲突。

### 1.4 任务来源安全边界

Schema 预留：

```text
MANUAL
AUTOMATION
SYSTEM_EMERGENCY
LEGACY
```

通用任务 Repository 当前拒绝新建 `LEGACY` 和
`SYSTEM_EMERGENCY`；`MANUAL` 与 `AUTOMATION` 都必须提供 `origin_ref_id`。
`Task` 模型不再提供隐式 `MANUAL` 默认值；规则、预测和 proposal 生成路径显式使用
`AUTOMATION` 并绑定来源运行，人工 Workbook 导入显式使用 `MANUAL`。当前 Web/CLI
没有独立的 Task 构造旁路，后续入口也必须通过模型的必填来源门禁。
`origin_type/origin_ref_id` 创建后由数据库触发器保持不可变，不能把普通任务改写为
另一入口、`LEGACY` 或 `SYSTEM_EMERGENCY`。父 Issue 的 `MANUAL_WEB`、
`AUTOMATION_SCAN` 等细分语义由 `MANUAL/AUTOMATION` 与 `web:/scan:` 等不可变
引用前缀组合表达。
任务 Workbook 导出/导入保留来源和授权字段；缺少这些列的旧 Workbook 只标记为
`LEGACY`，不猜测为人工任务。
`SYSTEM_EMERGENCY` 仍等待 13.5-6 的专用策略、授权与实机门禁。

时间策略版本创建后不可改写或删除；唯一允许的 UPDATE 是把当前版本的
`effective_to` 从 NULL 关闭为合法 UTC 时间。后继版本必须指向被替代版本并从同一
边界相邻开始，已关闭版本不得重新打开。关闭与新增只能通过原子替换入口在同一个
`BEGIN IMMEDIATE` 事务内完成；后继插入失败会回滚旧策略关闭，并发替换最多一个
成功，替换后 health 必须继续通过。

### 1.5 Automation 与 Incident 冻结集合

Automation 权威运行状态为：

```text
SCHEDULED / RUNNING / SUCCESS / PARTIAL / FAILED
MISSED / MERGED / SKIPPED / CANCELLED
```

不再接受 `SUCCEEDED`。Incident 增加父 Issue 冻结的 14 个 `category`，状态精确限制为
`OPEN / RETRYING / WAITING_HUMAN / ACKNOWLEDGED / AUTO_PROTECTING /
RESOLVED / CLOSED`；`resolved_at` 与 `RESOLVED/CLOSED` 双向一致。

## 2. 迁移和兼容

- 初始化和迁移继续在 `BEGIN IMMEDIATE` 中执行。
- v13→v14 先添加兼容列和新表，再记录 schema version 14。
- 迁移失败时新增列、表、策略种子和版本记录整体回滚。
- 重复 `init_schema()` 幂等，不重复创建当前时间策略。
- v4/v5 批次、operation/attempt、写锁、UNKNOWN、receipt、ACK、
  v13 两页快照和异常表保持原语义。
- 精确健康检查覆盖 v14 表、列、索引、外键、冻结枚举、当前唯一索引、
  三个日结触发器、时间策略防重叠/不可变触发器、任务来源不可变触发器和 UTC
  策略种子。

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
744 passed, 3 skipped, 97 subtests passed

系统冒烟：
16 passed, 0 failed

wheel_boundary=PASS
sdist_boundary=PASS
secret_scan=PASS
```

定向用例覆盖：

- 六个时间边界、UTC 输入和 naive datetime 拒绝。
- 策略版本切换、区间重叠拒绝和等价时区输入 UTC 归一化。
- 策略原子替换、后继插入失败整体回滚、并发替换单一成功和替换后 health。
- 新库 v14、v13→v14、v12→最新版本和重复迁移。
- 迁移中途失败的事务回滚和外键恢复。
- LEGACY 历史任务回填且不猜双日期。
- 任务来源创建后不可变，拒绝改写入口、迁移为 LEGACY 或提升为
  SYSTEM_EMERGENCY。
- 非法来源/质量组合、UNAVAILABLE 非零和 current 冲突。
- 跳级、回退、直接 FINAL 和 FINAL 内容修改拒绝。
- FINAL 全业务身份不可变和同事务 Incident 阻断。
- PROVISIONAL 原子修订、OBSERVED/FINAL 新版本及输入 hash 幂等。
- 自动化 `SUCCESS/MERGED/SKIPPED`、Incident 类别/状态和 resolved_at 一致性。

### 3.3 PR 收口与未执行项目

- PR #22 已合并；最终评审修复后的 Linux Core 与 Windows Core GitHub Actions 均通过。
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
