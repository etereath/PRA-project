# 任务 10：生产备份、恢复与发布基线

本 Runbook 对应第五阶段任务 10，基线为 Runtime Schema v6。它把发布版本、运行态 SQLite、Excel 业务输入和非秘密运维配置放进一个可校验的备份目录，并提供恢复与回滚入口。

## 交付内容

- `scripts/release_backup.py manifest` 生成不含秘密值的发布清单，记录 Git commit、wheel SHA-256、Runtime Schema 版本和配置项名称。
- `scripts/release_backup.py backup` 使用 SQLite backup API 复制运行态数据库；Excel 输入和运维配置按角色复制到备份目录。
- 备份先写入唯一临时目录，完成 SHA-256、SQLite integrity check、Schema health、外键和关键表行数校验后，再一次性改名发布；`latest.json` 只在验证成功后更新。
- `scripts/release_backup.py verify` 可独立回读 `manifest.json`、`release-manifest.json`、`SHA256SUMS.txt` 和数据库健康结果。
- `restore` 默认拒绝覆盖已有目标；`rollback` 是显式覆盖操作，二者都会先验证完整备份，再把数据库暂存、校验后替换。

关键逻辑表至少包含 Outbox、Review、ShadowBot operation/attempt 和 execution log；备份清单同时记录这些表的行数，恢复后必须一致。清单只保存配置项名称，不保存任何环境变量值、token、密码或 webhook 内容。

## 发布前备份

先停止会写入运行态数据库的 PRA/ShadowBot 服务，并关闭可能占用 Excel 输入文件的 Excel 进程。示例：

```powershell
python -m build
python scripts/verify_packaging.py --dist-dir dist
python scripts/verify_core_wheel_install.py --dist-dir dist
python scripts/release_backup.py backup `
  --runtime-db data/runtime/pra_runtime.sqlite3 `
  --backup-dir backups `
  --wheel dist/pra_mvp-0.1.0-py3-none-any.whl `
  --input data/samples/products.xlsx `
  --input data/samples/price_rules.xlsx `
  --input data/samples/listing_rules.xlsx `
  --config shadowbot/test2/shadowbot_worker_config.example.json
python scripts/release_backup.py verify --backup backups/<backup-id>
```

`--input` 和 `--config` 可重复使用，也支持 `备份文件名=源文件路径`。不要把 `.env`、密码文件、真实凭据配置或含秘密值的文件作为 `--config` 输入。

## 恢复与上一版本回滚

恢复到新目录时不需要 `--force`：

```powershell
python scripts/release_backup.py restore `
  --backup backups/<backup-id> `
  --runtime-db data/runtime/restore/pra_runtime.sqlite3 `
  --input-dir data/runtime/restore/inputs `
  --config-dir data/runtime/restore/config
```

回滚前先停止服务、确认目标备份的 Git commit 和 wheel SHA-256，再执行显式回滚：

```powershell
python scripts/release_backup.py rollback `
  --backup backups/<previous-backup-id> `
  --runtime-db data/runtime/pra_runtime.sqlite3 `
  --input-dir data/production/inputs `
  --config-dir data/production/config
python scripts/release_backup.py verify --backup backups/<previous-backup-id>
pra-mvp health --runtime-db data/runtime/pra_runtime.sqlite3
python scripts/run_system_smoke_tests.py --temporary-db
```

`rollback` 只恢复已验证的文件和数据库，不自动切换 Git 分支、不自动安装 wheel，也不自动启动服务；完成 health/smoke 后再按当前部署手册启动。若 Windows 报文件被占用，先关闭 PRA、ShadowBot 和 Excel，再重试。

## 验收标准

- 发布清单可回读，包含 Git commit、wheel SHA-256、Schema v6 和配置项名称，且不含秘密值。
- 备份目录含 `manifest.json`、`release-manifest.json`、`SHA256SUMS.txt`、运行态数据库及选定输入/配置文件。
- 备份中断或校验失败时，不删除、不覆盖最后一个有效备份，`latest.json` 保持原值。
- 复制数据库通过 Schema health、SQLite integrity、外键检查和关键逻辑表行数比对。
- 至少完成一次“备份 → 恢复 → health/smoke → 回滚”演练。

## 本次验证记录（2026-07-17）

- `python -m pytest -q tests/test_release_backup.py`：3 passed。
- 完整 `python -m pytest -q tests`：428 passed、3 skipped、63 subtests passed。
- 相关回归集：37 passed、3 skipped、4 subtests passed。
- wheel boundary、sdist boundary、secret scan：全部 PASS；隔离 wheel 安装、Runtime Schema v6 init/health：PASS。
- 使用隔离的 v6 临时数据库完成 CLI `backup → verify → restore → rollback`：全部 PASS，SQLite integrity 为 `ok`、外键违规为 0、关键逻辑表行数一致。
- `run_system_smoke_tests.py --temporary-db`：16 项通过；Linux Core：280 passed、3 skipped、6 deselected；Windows Core fixture：PASS。
- 工作区现有 `data/runtime/pra_runtime.sqlite3` 是旧 v5 且 `journal_mode=delete`，工具按安全策略拒绝将它作为生产备份源；完成 v6 迁移并停止占用服务后再执行生产备份。
