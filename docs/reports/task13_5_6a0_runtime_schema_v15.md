# 任务 13.5-6A-0：Incident Runtime Schema v15 实施报告

- 实施日期：2026-08-02
- Review Profile：`R4`
- 数据库结构版本：Runtime Schema v15
- 平台动作：0；未修改 ShadowBot Worker、队列、Adapter 或真实平台
- 合同权威：GitHub Issue #20 与
  `docs/plans/task13_5_6_incident_and_emergency_protection_review_plan.md`

## 1. 实施结果

本阶段完成 13.5-6A-0 的合同和持久化基础，不提前实现人工复核、通知、策略或自动紧急
下架。v14 的 `operational_incidents` 主键、开放指纹、交易日、subject 和 FINAL 阻断
字段全部保留；v15 只增加 `occurrence_count`，并新增唯一一张 append-only
`operational_incident_events`。

复杂度预算实际结果：

```text
新增数据库表：1（operational_incident_events）
新增主表字段：1（occurrence_count）
新增 Incident 主状态：0
新增 Review/Token/通知表：0
新增授权表：0
新增锁或租约：0
新增平台动作：0
```

## 2. 复用矩阵

| 职责 | 复用结论 | 实际入口 |
| --- | --- | --- |
| Incident 主记录 | 参数化复用 | v14 `operational_incidents` 原字段、开放 dedupe 和状态约束 |
| 迁移与健康检查 | 参数化复用 | `SQLiteRuntimeRepository.init_schema()`、`runtime_schema` 精确健康检查、事务回滚和外键检查 |
| 时间线 | 确需新增 | 单表 `operational_incident_events`，具名唯一 event key 和 append-only trigger |
| FINAL 事务 | 参数化复用 | 现有 `OperationalSummaryRepository` FINAL 同事务门禁，扩展为动态范围匹配 |
| Review、Outbox、Automation、Worker、v4/v5 | 原样保留 | 6A-0 未修改；留待 6A-1/6B/6C 按既有入口接入 |

没有复制 Incident 主表、日结状态机、Scheduler、通知账本、Review 状态机、写锁或
ShadowBot 控制流。

## 3. v15 数据合同

`occurrence_count` 为 `INTEGER NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1)`。v14
已有 Incident 迁移后统一从 1 开始；新可信检测如何与事件原子增加次数属于 6A-1
application service，不在本阶段用数据库 trigger 猜测业务事实。

事件表固定保存：

```text
event_id / event_key / incident_id / event_type
occurred_at / source_type / source_ref_id
from_status / to_status / severity
event_payload_json / created_at
```

事件类型精确限制为 `DETECTED / REDETECTED / STATUS_CHANGED / SEVERITY_CHANGED / ACK /
RECOVERY_RECORDED / REVIEW_RECORDED / TASK_RECORDED`。`event_key` 全局唯一，payload 必须
是合法 JSON；UPDATE 和 DELETE 均由数据库 trigger 拒绝。事件只引用 Review、Run、Task
等既有事实 ID，不复制它们的完整账本。

异常类别在原 v14 集合上增加：自动化服务异常、运行数据库或存储异常、队列或结果导入
异常、交易日或系统时间异常、商品上下架状态异常、成本或经营主数据异常、日结处理异常和
人工复核通道异常。数据库 CHECK、Python Enum 和健康检查使用相同精确集合。

## 4. v14→v15 迁移

SQLite 需要重建 `operational_incidents` 才能扩充类别 CHECK。迁移在关闭外键检查后的
同一个 `BEGIN IMMEDIATE` 内完成以下步骤：

1. 创建 v15 临时主表；
2. 原字段逐列复制，旧行 `occurrence_count=1`；
3. 替换主表并重建开放 dedupe 和状态索引；
4. 创建事件表、索引和 append-only trigger；
5. 写入 schema version 15；
6. 执行 `foreign_key_check` 和 `integrity_check` 后提交；
7. 无论成功或失败都恢复 `PRAGMA foreign_keys=ON`。

`incident_notification_state` 的已有行和外键保持有效。重复执行 `init_schema()` 幂等；任一
v15 DDL 失败时，主表、通知子表和 migration row 整体回滚。

## 5. FINAL 范围门禁

原实现只识别 `source_type='TRADE_DAY_SUMMARY' + source_ref_id=summary_id`。本阶段保留该
历史兼容路径，并在同一 FINAL 写事务内增加动态匹配：

- 同平台、同交易日的 `PLATFORM` Incident 阻断全部范围；
- subject 类型和 key 与 Summary 范围精确相同则阻断；
- Incident 的 `source_ref_id` 被当前不可变 Summary input manifest 选用时，阻断依赖该
  输入的聚合；
- 其他范围、其他交易日或输入不相交的 Incident 不扩大阻断。

因此 Incident 可以早于 Summary 创建。单 SKU 的价格异常不会仅因 S4 严重度阻断可信的
平台订单总额；严重度与 `blocks_finalization` 继续正交。错误文案不再声称所有 S3/S4 都
阻断 FINAL。

## 6. 验证结果

开发专项命令：

```text
pytest -q tests/test_runtime_schema_v15.py \
  tests/test_runtime_schema_v14.py \
  tests/test_trade_day_summary.py
```

结果：`61 passed in 9.77s`。

覆盖新库、v14 带数据迁移、重复迁移、失败回滚、通知子表保留、外键、类别和事件类型精确
集合、非法值拒绝、事件 key 重放、append-only、健康检查、历史 Summary ID 兼容、平台
范围阻断、SKU 价格异常不扩大阻断，以及 Summary 输入依赖阻断。

受影响 Runtime、持久化、Outbox、备份和日结扩展回归结果：
`180 passed, 34 subtests passed in 33.66s`。第一次完整回归发现 Automation Service
启动脚本仍把健康结构硬编码为 v14；该门禁已改为引用唯一权威
`LATEST_RUNTIME_SCHEMA_VERSION`。修复后完整 pytest 为
`983 passed, 3 skipped, 97 subtests passed in 203.53s`。

`python scripts/run_system_smoke_tests.py --temporary-db` 使用操作系统临时数据库和 mock
通知，结果为 `16 passed, 0 failed`。这些结果证明本地文件内容和隔离数据库业务回归正确，
不代表真实 Runtime DB 已迁移，也不代表影刀或真实平台运行成功。

## 7. 后续边界

6A-0 不提供 Incident application service，也不创建 Review、通知或任务。下一步 6A-1
才实现主行与事件同事务、可信新检测增加 occurrence、精确重放、状态转换、ACK 事件、
人工 Review 和通知闭环。`automatic_emergency_offline=false`，当前没有任何真实平台写
授权或副作用。
