# 任务13 T13-2 Runtime Schema v13 实施报告

## 1. 阶段结论

2026-07-25 已完成 Runtime Schema v13 的本地实现和 v12→v13 无损迁移验证。

本阶段只修改 PRA 的 SQLite 运行态结构、迁移逻辑、结构健康检查和 v4 批次注册衔接，没有部署 ShadowBot Worker，没有执行真实上架或下架，也没有修改任务13总状态。

T13-2 已具备人工审查条件。进入 T13-3 前仍应由审查方确认 schema、迁移测试和任务12回归结果。

## 2. v13 新增结构

### 2.1 公共批次身份

新增：

```text
shadowbot_batch_registry
```

已有 v4 `shadowbot_commit_batches` 全部按以下规则回填：

```text
batch_type = update_price
contract_version = 4
```

注册表只提供 v4/v5 共用的批次外键，不维护第二套业务状态。

### 2.2 通用 operation

`shadowbot_operations` 已原子重建并新增：

```text
action_type
expected_old_status
target_status
target_inventory
approved_payload_json
operation_result
resolution_status
resolved_by
resolved_at
superseded_by_operation_id
```

现有 v4 operation 回填：

```text
action_type = update_price
expected_old_status = NULL
target_status = NULL
target_inventory = NULL
```

`expected_old_price` 和 `target_price` 已改为可空。数据库动作约束保证：

- `update_price` 必须有旧价和目标价；
- `set_online` 必须有目标价、目标库存和正确状态转换；
- `set_offline` 的价格、库存为空，禁止使用占位值。

### 2.3 共享写锁

`shadowbot_write_locks` 已重建为引用 `shadowbot_batch_registry`，状态扩展为：

```text
ACTIVE
UNKNOWN
REVIEW_BLOCKED
RELEASED
```

迁移逐行保留已有锁状态，不把历史 `UNKNOWN` 转为 `RELEASED`。

### 2.4 平台状态失效字段

`listing_status` 新增：

```text
last_listing_change_at
last_listing_operation_id
online_status_observed_at
online_status_source_type
online_status_source_id
```

T13-2 只建立字段和模型读取能力；点击 phase 到这些字段的投影由后续 Importer/Watchdog 阶段实现。

### 2.5 v5 动作账本

新增：

```text
shadowbot_listing_action_batches
shadowbot_listing_action_batch_items
shadowbot_listing_result_receipts
```

逐商品项同时保存资料修改和上下架两个副作用阶段、操作前后价格库存、点击和回读时间及 operation 结果。

### 2.6 两页快照与异常

新增：

```text
listing_sync_snapshots
listing_sync_snapshot_items
listing_anomaly_cases
```

父快照保存两页扫描起止时间、完成标志和结束标记。商品项保存两页出现次数、位置分类、行身份、价格、库存和观察时间。异常表支持无 SKU 页面商品、多 SKU 映射冲突、开放异常去重和 Review 关联。

## 3. 迁移实现

迁移入口仍是：

```text
SQLiteRuntimeRepository.init_schema()
```

v13 迁移使用单个 `BEGIN IMMEDIATE` 事务：

```text
关闭本迁移连接的外键即时检查
→ 创建并回填公共批次注册表
→ 重建并回填 operation
→ 重建写锁及外键
→ 新增 listing_status 字段
→ 创建 v5 账本、快照和异常表
→ 创建索引
→ 写入 schema_version = 13
→ PRAGMA foreign_key_check
→ PRAGMA integrity_check
→ 提交
→ 恢复本连接 foreign_keys = ON
```

外键检查或完整性检查失败时整体回滚，不留下半迁移状态。重复调用迁移幂等。

## 4. 结构健康检查

`app.runtime_schema.LATEST_RUNTIME_SCHEMA_VERSION` 已更新为 `13`。

健康检查现在验证：

- 迁移记录必须连续为 `1..13`；
- v13 七张新表和全部关键列存在；
- v13 必需索引存在且列顺序正确；
- operation 价格列确实可空；
- operation 动作、公共批次类型和写锁状态枚举精确；
- 写锁批次外键指向公共注册表；
- v5 批次、逐项、receipt、snapshot 和 anomaly 外键完整；
- v4/v5 专用批次均存在公共 registry；
- `PRAGMA foreign_key_check` 无异常。

## 5. 自动化验证

### 5.1 固定带数据 v12 夹具

覆盖：

- v4 VERIFIED 批次；
- v4 UNKNOWN/NEEDS_RECONCILIATION 批次；
- operation、attempt、checkpoint；
- batch、item、receipt；
- `RELEASED` 和 `UNKNOWN` 写锁；
- `approved_payload_hash`；
- instruction、manifest、result 绑定字段。

迁移前后对旧字段生成排序稳定的表级摘要，结果完全一致。新增字段按回填规则单独断言。

### 5.2 失败回滚

向 v12 测试库注入孤儿写锁后执行迁移：

- 迁移被外键门禁拒绝；
- schema 版本仍为 `1..12`；
- operation 仍是 v12 结构；
- 公共注册表和其他 v13 部分结构没有残留。

### 5.3 冻结的真实 v12 备份

对冻结备份的复制件执行迁移，源文件保持不变：

```text
source_schema_version = 12
target_schema_version = 13
commit_batch_count = 21
registry_count = 21
write_locks = RELEASED: 4
foreign_key_violation_count = 0
integrity_check = ok
source_hash_unchanged = true
```

真实备份中没有历史 UNKNOWN 锁，因此 UNKNOWN 保留规则由固定带数据 v12 夹具覆盖，不能把真实备份的四个 RELEASED 样本误写成 UNKNOWN 验证。

### 5.4 最终自动化回归

最终全项目测试：

```text
586 passed, 3 skipped, 97 subtests passed
```

三个 skipped 是测试集既有的条件跳过，不是本阶段新增失败。

系统冒烟测试：

```text
通过 16 项
失败 0 项
schema version exact v13
```

v13 专用测试同时覆盖新库、带数据迁移、幂等、历史 UNKNOWN 锁、动作字段约束、结构健康拒绝和失败事务回滚。

## 6. 代码位置

- `app/runtime_schema.py`
- `app/repositories/sqlite_runtime_repository.py`
- `app/models.py`
- `app/services/shadowbot_commit_pipeline.py`
- `scripts/release_backup.py`
- `tests/test_runtime_schema_v13.py`

## 7. 明确未完成

以下属于后续阶段：

- T13-3 独立 SYNC_STATUS 扫描和原子快照导入；
- snapshot 到 `online_status`、Review 和通知 Outbox 的事务投影；
- v5 Result Importer 和 Watchdog recovery；
- ShadowBot v5 Worker 部署；
- 真实 SET_ONLINE / SET_OFFLINE；
- 任务13状态修改。
