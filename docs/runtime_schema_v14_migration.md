# Runtime Schema v14 迁移运行手册

## 1. 适用范围

本手册用于把已验证的 PRA Runtime SQLite 从 v13 迁移到 v14。
迁移只处理控制面结构和历史任务来源兼容，不启动扫描、订单采集、Scheduler
或平台写动作。

真实迁移前必须使用已构建并验证的 wheel；不得直接用未提交工作树迁移正式库。

## 2. 前置门禁

1. 停止会写 Runtime DB 的 Web、Queue Service、Importer、Watchdog 和计划中的
   Automation Service。
2. 确认没有活动写锁、未归档结果或正在导入的请求。
3. 记录源库绝对路径、文件 SHA-256、当前 schema version 和 Git/wheel 版本。
4. 使用 SQLite backup API 生成可验证备份，不复制正在写入的裸数据库文件。
5. 在副本上先执行迁移和健康检查；原库保持不变。
6. `SYSTEM_EMERGENCY` 必须保持禁用，不因 v14 出现来源字段而启用。

## 3. 推荐流程

### 3.1 建立并验证备份

```powershell
python scripts/release_backup.py backup `
  --runtime-db <runtime-db> `
  --backup-dir <backup-root> `
  --wheel <verified-wheel> `
  --git-root .

python scripts/release_backup.py verify --backup <published-backup>
```

### 3.2 在副本迁移

```powershell
python scripts/release_backup.py migrate `
  --source-db <runtime-db> `
  --output-db <v14-candidate-db>
```

该命令在副本调用 `SQLiteRuntimeRepository.init_schema()`。不得使用
`--force` 覆盖未确认目标。

### 3.3 验证候选库

必须确认：

```text
schema_versions = 1..14
latest = 14
runtime schema health = healthy
PRAGMA foreign_keys = 1
PRAGMA foreign_key_check = []
PRAGMA integrity_check = ok
```

同时抽查：

- `CN_SINGLE_PLATFORM_2026_V1` 为唯一当前时间策略。
- 历史任务 `origin_type=LEGACY`。
- 历史任务的新双日期、阶段和时间策略字段为 NULL。
- v4/v5 批次、UNKNOWN、写锁、receipt、ACK 和 v13 快照计数不变。
- 没有 `emergency_action_policies`，也没有新建
  `SYSTEM_EMERGENCY` 任务。

### 3.4 切换

1. 保持所有写服务停止。
2. 再次确认候选库健康和源库未发生新写入。
3. 按现有发布流程切换 Runtime DB 指向候选库。
4. 先启动只读 Web 健康页，再逐个恢复原有服务。
5. 运行系统冒烟；Automation Service 尚未在 13.5-1 启动。

## 4. 失败和回滚

迁移事务失败时，候选库必须保持 v13，且不残留 v14 表、列或版本记录。
不要在原库上删除 v14 表来模拟回滚。

切换后需要回滚时：

```powershell
python scripts/release_backup.py rollback `
  --backup <published-backup> `
  --runtime-db <rollback-target-db>
```

回滚结果必须再次执行备份验证、SQLite 完整性检查和原版本健康检查。
出现版本不连续、外键错误、来源猜测回填或历史账本计数变化时，不得启动写服务。

## 5. 本轮状态

截至 2026-07-29，v14 已在临时数据库通过新库、带数据迁移、重复迁移和失败回滚
测试；真实 Runtime DB 尚未迁移。本手册不构成生产迁移已完成的证明。
