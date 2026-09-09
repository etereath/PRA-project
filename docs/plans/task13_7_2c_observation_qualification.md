# Task 13.7-2C — Qualified Observation & Listing READ_ONLY Selective Salvage

## 1. Goal and accepted baseline

本任务以 `main@0b1135787ac33c3c6c96531f874924418924bb8a`、业务合同 §23.3 和 PR #54 最新审核交接为基线。Task 13.7-2A / 2B 已进入当前 `main`，不再保留 workbook authority 作为 DB_AUTHORITY 下的兼容回退。

目标是建立“当前平台观察是否足以作为经营事实”的正式资格判断，并把既有 `SYNC_STATUS` READ_ONLY 链路接入 Automation Run 与不可变 `ProductObservation`。本任务不执行平台写、不关闭 UNKNOWN、不设置或释放 global blocker，也不推断历史写操作的因果。

## 2. Frozen public contract

Qualification 结果固定分为两个互不覆盖的维度：

1. `operating_fact_qualified`：观察的内容、来源、身份、覆盖、时间和当前 Mapping authority 是否足以作为经营事实。
2. `delivery_archive_healthy`：结果文件 ACK、归档及相关证据投递是否健康。

ACK、归档或通知失败本身不得抹除已提交的可信不可变观察；只有该失败导致身份、内容或来源完整性不可验证时，才会令经营事实不合格。

稳定结果至少包含：

```text
schema_version
operating_fact_qualified
fact_reason_codes[]
delivery_archive_healthy
delivery_reason_codes[]
platform_name
account_id
internal_sku
platform_product_identity_digest
authority_mode
authority_generation
mapping_snapshot_sha256
mapping_version
provider
observation_type
source_run_id
observation_batch_id
source_snapshot_id
source_manifest_sha256
source_result_sha256
observation_content_sha256
qualification_sha256
observed_at
scan_completed_at
fresh_until
scope_complete
end_marker_verified
```

`qualification_sha256` 对上述决定性字段做规范化摘要，供 PR #55 在不重写本任务规则的情况下验证引用。

## 3. Candidate selection

- 选择目标平台、账号和 SKU 下最新的合格候选，而不是要求数据库物理上只有一个批次。
- 历史 `FAILED`、`SUPERSEDED`、不完整、过期及 retry/recovery 批次可以共存，不得永久污染后续有效观察。
- retry/recovery 成功产生的新观察可以成为当前候选。
- 同一最新完成时刻若存在内容或来源不同的多个合格候选，必须以 `AMBIGUOUS_CURRENT_CANDIDATES` fail closed。
- SKU 隔离：一个 SKU 不合格不改变其他 SKU 的资格结果。
- Qualification 仅返回事实，不创建 Review、不写 blocker、不改变 Automation 或 execution 状态。

## 4. Identity and authority binding

- 身份必须绑定 `platform_name + account_id + platform_product_identity_digest`，并包含 `internal_sku`、Mapping authority generation/snapshot/version 及证据引用。
- DB_AUTHORITY 下只读取当前 Runtime Mapping authority；缺少配置账号、authority generation 漂移、snapshot digest 漂移或无法唯一解析时 fail closed，禁止 workbook fallback。
- 配置的 `account_id` 只是目标账号，不是活动登录会话证明；本任务不声称已验证活动会话。
- 接受时的 authority/identity binding 写入不可变 observation scope；资格读取时再与当前 Runtime authority 对照。

## 5. Production READ_ONLY path

唯一生产路径为：

```text
Automation Run
→ existing SYNC_STATUS READ_ONLY
→ existing Queue / Worker
→ immutable listing snapshot
→ ProductObservationImporter
→ evidence ACK / Archive
→ Automation outcome
```

- 复用现有 Job/Run/Event、Queue/Worker/Importer、heartbeat、lease、result hash 与 ACK；不增加 scheduler、daemon、queue 或状态机。
- `FULL_MARKET_SCAN` / `PRE_CUTOFF_FULL_SCAN` 只负责产生现有 `LISTING_STATUS_SCAN` 子 Run；子 handler 执行上述链路。
- 不注册任何平台写 handler。历史 UNKNOWN 停放不构成 READ_ONLY blanket gate；实际 UI channel 仍服从既有互斥和租约。
- 不可变观察成功提交后，后续归档失败记录为 delivery/archive 不健康，并使本次 Automation outcome 明确反映交付失败，但不会回滚或否认经营事实。

## 6. PR #55 handoff

PR #55 只能消费本任务返回的结构化结果与引用，并自行验证：

```text
scan_completed_at / observed_at strictly after stopped boundary
+ exact platform/account/product identity
+ provider/freshness/scope/end-marker qualified
+ one unique current qualified candidate
+ immutable source/content/qualification refs
```

这些字段不证明活动登录账号、不证明当前没有 submit responsibility，也不允许反推旧 click 的精确因果；对应判断仍属于 PR #55。

## 7. Legacy selective salvage

| Legacy asset | Decision | Current adaptation |
|---|---|---|
| `listing_scan_quality.py` complete/end-marker/fresh/source checks | ADAPTED | 改为结构化 reason code、账号/权威/身份绑定及双维度结果 |
| `len(batches) == 1` | REJECTED | 改为最新合格候选选择；仅同一时刻冲突候选 fail closed |
| run 必须 `SUCCESS` | REJECTED | 资格依赖不可变事实完整性；交付失败可令 run `PARTIAL` 而不抹除事实 |
| `listing_automation_runtime.py` Automation→Queue→Importer→ACK/Archive | ADAPTED | 对齐当前 claim、Runtime Mapping authority 和现有 SYNC_STATUS API |
| 新 daemon/scheduler/queue/state machine | REJECTED | 复用既有 Automation 与单 Worker 文件队列 |
| “等待下一次定时扫描”作为唯一恢复 | REJECTED | 允许显式 READ_ONLY retry/recovery 产生新候选 |

## 8. Verification scope

定向验证覆盖：

1. complete + end-marker + fresh + exact account/identity/authority → qualified；
2. incomplete、缺尾标、stale、identity/authority mismatch → 对应结构化不合格；
3. ACK/archive failure 不抹除已提交事实，并单独报告 delivery 不健康；
4. 历史失败/retry 不污染最新有效候选；同刻冲突候选 fail closed；
5. SKU 隔离；
6. DB_AUTHORITY 无 workbook fallback；
7. 历史 UNKNOWN 停放期间 READ_ONLY 仍走真实 Automation/Queue/Worker/Importer 接线；
8. 交给 PR #55 的时间、身份和摘要引用可独立复核。

完整 pytest、全仓 Ruff/Mypy、完整冒烟及主动 CI 重跑仍受单独授权门禁约束。

## 9. Explicit non-goals

- 不实现 PR #55 / Queue-OPS 的关闭或释放逻辑；
- 不释放 UNKNOWN，不创建 Controller，不修改 Closing、当日销量或 purchase sequence；
- 不执行真实平台写、部署或实机验收；
- 不把 qualification 直接映射为 Review、global stop 或任何写权限。

## 10. Deliverables

- 当前版 `ListingScanQuality` / observation qualification Service；
- Automation listing READ_ONLY production handler；
- Runtime Mapping authority 与不可变 account/identity binding；
- 结构化 qualification diagnostic 及稳定 PR #55 interface；
- 直接风险对应的定向测试；
- 本文的 legacy `REUSED / ADAPTED / REJECTED` 记录。
