# Task 13.7-2C — Qualified Observation & Listing READ_ONLY Selective Salvage

## 1. Goal and accepted baseline

本轮以 `main@616888a073c070989753a9bf74b2b8093fc01af2`、[Owner Product Boundary](../owner_product_boundary.md) §7 和 PR #54 更新后的正文为边界，集中整改 P2-54-01/02/03、R0-54-01/02。#52、#53、#57 已合并；旧 account-aware / active-session / page-identity 审核要求不再适用。DB_AUTHORITY 不回退 workbook authority。

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
source_execution_attempt_id
source_manifest_sha256
source_result_sha256
observation_content_sha256
qualification_sha256
observed_at
scan_completed_at
fresh_until
evaluated_at
scope_complete
end_marker_verified
```

公共 schema 为 `listing-scan-qualification-2.0`。`qualification_sha256` 对结果（含 `evaluated_at`）做规范化摘要，供后续消费者验证引用。公共 scope/result/selector 不含 account/session/page identity 维度；底层旧 `page_identity_key` 仍参与原有内容完整性校验，不代表页面认证。

## 3. Candidate selection

- 选择目标平台和 SKU 下最新的合格候选，而不是要求数据库物理上只有一个批次。
- 历史 `FAILED`、`SUPERSEDED`、不完整、过期及 retry/recovery 批次可以共存，不得永久污染后续有效观察。
- retry/recovery 成功产生的新观察可以成为当前候选。
- 同一最新完成时刻若存在内容或来源不同的多个合格候选，必须以 `AMBIGUOUS_CURRENT_CANDIDATES` fail closed。
- SKU 隔离：一个 SKU 不合格不改变其他 SKU 的资格结果。
- Qualification 仅返回事实，不创建 Review、不写 blocker、不改变 Automation 或 execution 状态。

## 4. Identity and authority binding

- 商品定位为 `platform_name + platform_product_identity → internal_sku`。接受时冻结该 SKU 的 mapping IDs、商品 identity digest 和证据引用，资格读取时与当前有效 VERIFIED mapping 对照。
- 共享 authority generation、全局 snapshot digest、mapping version 用作溯源和内部完整性证据，不以跨时刻全局相等作为 SKU 的资格门禁。无关 Product、其他 SKU mapping 或备注修改不会单独令旧事实失效；目标 SKU 的相关 mapping IDs/identity 变化则 fail closed。
- DB_AUTHORITY 只读取 Runtime Mapping authority；v19 `account_id` 由既有 provider/locator 兼容边界吸收，不成为公共 qualification scope 或操作者新增配置要求，不做删列 migration。
- immutable scope 绑定 `source_execution_attempt_id`。Importer 和 qualification 共用既有 snapshot/batch/receipt 校验，核对 attempt、instruction、manifest、result、源采集完成状态及 canonical content。缺少可验证的 attempt 绑定时结构化拒绝，历史记录不重写。
- `fresh_until = observed_at + max_age`；`scan_completed_at` 只证明覆盖完成。结果带 `evaluated_at`；没有 SKU observation 的源失败诊断不生成虚假的 fresh-until。

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
- 源采集 batch 已 VERIFIED、snapshot/receipt/immutable import 已完整成立时，即使 Automation Run 仍 RUNNING，事实也可 qualified；未完成父流程收尾只在 delivery/lifecycle 维度表达。
- `prepare_listing_sync_batch()` 成功后的 mapping/locator/context/input-manifest/callback/publish 前失败，统一走既有 `fail_listing_sync_batch()` 收口。未发布请求为零 Queue 写、零平台写；fresh retry 使用新的既有 Automation child，不新增恢复状态机。

## 6. 后续消费者边界

本轮只提供结构化结果与引用。#55 按 Owner Product Boundary 保持后置，不在本 PR 实施其 Queue-OPS 或 blocker 逻辑。后续涉及 stopped boundary 的消费者须独立验证：

```text
scan_completed_at / observed_at strictly after stopped boundary
+ exact platform/product identity
+ provider/freshness/scope/end-marker qualified
+ one unique current qualified candidate
+ immutable source/content/qualification refs
```

这些字段不证明当前没有 submit responsibility，也不允许反推旧 click 的精确因果；historical UNKNOWN 保持原状。

## 7. Legacy selective salvage

| Legacy asset | Decision | Current adaptation |
|---|---|---|
| `listing_scan_quality.py` complete/end-marker/fresh/source checks | ADAPTED | SKU observed-at freshness、acquisition attempt/商品身份绑定及双维度结果 |
| `len(batches) == 1` | REJECTED | 改为最新合格候选选择；仅同一时刻冲突候选 fail closed |
| run 必须 `SUCCESS` | REJECTED | 资格依赖不可变事实完整性；交付失败可令 run `PARTIAL` 而不抹除事实 |
| `listing_automation_runtime.py` Automation→Queue→Importer→ACK/Archive | ADAPTED | 对齐当前 claim、Runtime Mapping authority 和现有 SYNC_STATUS API |
| 新 daemon/scheduler/queue/state machine | REJECTED | 复用既有 Automation 与单 Worker 文件队列 |
| “等待下一次定时扫描”作为唯一恢复 | REJECTED | 允许显式 READ_ONLY retry/recovery 产生新候选 |

## 8. Verification scope

定向验证覆盖：

1. acquisition complete + snapshot/receipt/observation attempt 一致 + end-marker + fresh + relevant product identity → qualified；
2. 未完成采集、attempt mismatch、缺尾标、observed-at stale、相关 identity mismatch → 对应结构化不合格；
3. ACK/archive failure 不抹除已提交事实，并单独报告 delivery 不健康；
4. 历史失败/retry 不污染最新有效候选；同刻冲突候选 fail closed；
5. 真实 Runtime Product/Mapping 变更中，无关 SKU/备注不连坐，相关 identity 改变则拒绝旧事实；
6. DB_AUTHORITY 无 workbook fallback；
7. 历史 UNKNOWN 停放期间 READ_ONLY 仍走真实 Automation/Queue/Worker/Importer 接线；
8. PREPARED 后发布前异常 terminal cleanup、零 Queue 写及 fresh retry；公共结果与新 scope 不含 account/session/page identity requirement。

完整 pytest、全仓 Ruff/Mypy、完整冒烟及主动 CI 重跑仍受单独授权门禁约束。

## 9. Explicit non-goals

- 不实现 PR #55 / Queue-OPS 的关闭或释放逻辑；
- 不释放 UNKNOWN，不创建 Controller，不修改 Closing、当日销量或 purchase sequence；
- 不执行真实平台写、部署或实机验收；
- 不把 qualification 直接映射为 Review、global stop 或任何写权限。

## 10. Deliverables

- 当前版 `ListingScanQuality` / observation qualification Service；
- Automation listing READ_ONLY production handler；
- 当前 SKU 的 Runtime Mapping identity 与不可变 source attempt binding；
- 结构化 qualification diagnostic 及可验证引用；
- 直接风险对应的定向测试；
- 本文的 legacy `REUSED / ADAPTED / REJECTED` 记录。
