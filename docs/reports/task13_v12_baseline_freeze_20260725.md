# 任务13 Runtime Schema v12 基线冻结报告

## 1. 结论

任务13开发前的 Runtime Schema v12 基线已经冻结。

- 源数据库保持 Runtime Schema v12 健康；
- 已使用 SQLite online backup 创建一致性备份；
- 源快照与备份的 7 张任务12关键账本表行数及规范化摘要完全一致；
- 备份通过 `integrity_check`、`foreign_key_check` 和项目 Runtime Health 检查；
- 完整测试为 `557 passed, 3 skipped, 97 subtests passed`；
- 任务12两份脱敏实机证据均通过重新复算；
- 本阶段没有修改业务数据、任务状态、影刀代码或影刀运行状态。

该备份是后续 v12→v13 迁移测试和回滚校验的迁移前事实基线，不得提交到 Git 仓库。

## 2. Git 和工作区基线

```text
Head SHA: aaca931701603c98a2f2f322b8d5dafe79c0cee9
Branch: codex/task12-final-handoff
```

开始本阶段前已经存在以下用户工作区内容，本阶段未覆盖或修改：

- `data/samples/web_generated_tasks.xlsx`
- `任务12审查问题修复对接文档.docx`
- `任务13_单平台商品上下架与状态对账闭环_交接与实施计划.md`
- `任务13_单平台商品上下架与状态对账闭环_交接与实施计划_修改意见.md`
- `任务13_商品状态定义修改反馈.md`

本阶段新增：

- `scripts/freeze_task13_v12_baseline.py`
- `tests/test_task13_v12_baseline_freeze.py`
- 本报告

## 3. 数据库基线

源数据库：

```text
D:\PRA project\data\runtime\pra_runtime.sqlite3
```

冻结时源文件信息：

```text
文件大小：987136 bytes
最后修改时间：2026-07-23 19:45:07
文件 SHA-256：7f1b88d80546a914be58993d26fa9cc4e5b3014e1fc67d0e1cf1b0233c00d301
Runtime Schema：v12
```

源数据库健康检查：

```text
schema_versions = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
integrity_check = ok
foreign_key_violation_count = 0
journal_mode = wal
synchronous = NORMAL
foreign_keys = 1
```

## 4. 外部一致性备份

备份目录：

```text
D:\PRA_Runtime\backups\task13_v12_baseline_20260725_043352
```

数据库备份：

```text
D:\PRA_Runtime\backups\task13_v12_baseline_20260725_043352\pra_runtime_v12_baseline.sqlite3
SHA-256：9343248e2e122a42b15a897f1a4907616afe0ff06ebdd7f14cb0af2b681f6bee
```

冻结清单：

```text
D:\PRA_Runtime\backups\task13_v12_baseline_20260725_043352\baseline_manifest.json
SHA-256：1640f94fa2422a2928480e8370a5c3c28e1e43192bd0f4292614398087042e54
```

源数据库物理文件和 SQLite online backup 的文件 SHA 不要求相同；迁移基线以一致事务快照产生的逐表规范化摘要相等为准。

## 5. 任务12关键账本摘要

清单只保存字段顺序、主键顺序、行数和规范化 SHA-256，不保存业务行内容。

| 表 | 行数 | 规范化 SHA-256 |
|---|---:|---|
| `shadowbot_operations` | 46 | `74c10ada1caab31743ebf283f1b4ba4d04777385b49d9a366b13c522bbe425f2` |
| `shadowbot_execution_attempts` | 47 | `84f6b8312a333f4952b846290a61000d332e71451b09fbaa6651c7b2c5fb866b` |
| `shadowbot_side_effect_checkpoints` | 38 | `61a0f3b595fecd83489de9762621a3e74f4cd275b98cab53071f207c9f1ccf71` |
| `shadowbot_commit_batches` | 21 | `b82933c11a79004f91477e96aa706d7e623e47b314c7f17ebb433832a41a6f13` |
| `shadowbot_commit_batch_items` | 63 | `1b3a7435c1222abc4f4aa66f325ef3f05f9e65999f34fddabc037f958acb5cd9` |
| `shadowbot_write_locks` | 4 | `8d59caeb33c5738e61791cc4c3926baae4aa0bf8477c3b9e1704b68306f61c93` |
| `shadowbot_commit_result_receipts` | 3 | `bfea580ebffc4b7231d1f9f2938c468410f5da9e44a4394a7416064368807dae` |

> 注意：`shadowbot_commit_batch_items` 的权威完整摘要以外部 `baseline_manifest.json` 为准。本报告中的摘要必须在最终提交前通过冻结清单自动核对，不能手工用于迁移判定。

## 6. 自动测试

冻结工具定向测试：

```text
1 passed in 0.26s
```

完整测试第一次执行时，pytest 已输出：

```text
557 passed, 3 skipped, 97 subtests passed in 119.03s
```

但外层命令在 120 秒上限返回超时码，因此没有把该次执行作为正式通过记录。

提高超时上限后的正式基线结果：

```text
557 passed, 3 skipped, 97 subtests passed in 133.78s
exit_code = 0
```

任务12脱敏证据重新复算：

```json
{
  "ok": true,
  "bundle_count": 2,
  "bundles": [
    "ATTEMPT-0f30900b398045cc",
    "ATTEMPT-52c584afca044d79"
  ]
}
```

## 7. 后续使用规则

1. v13 迁移测试必须复制外部备份后操作，不得直接修改该基线备份。
2. 迁移前后至少重新比较本报告列出的 7 张表。
3. 旧字段必须逐行保持业务等价；v13 新增回填字段单独断言。
4. `approved_payload_hash`、instruction hash、manifest hash、operation/attempt/checkpoint 身份和历史锁状态不得变化。
5. v13 迁移通过后再次运行完整测试和任务12证据复算。
6. 基线备份和 manifest 未验证前，不得标记 v13 迁移完成。
