# Task 13.7-2C — Qualified Observation & Listing READ_ONLY Selective Salvage

## 1. Goal

在 Task 13.7-2A Authority Contract 接受后，把“当前平台观察是否足以作为经营事实”的判断抽成正式、可复用的 qualification 能力，并选择性复用 `a3485af` 中 `listing_scan_quality.py` 与 `listing_automation_runtime.py` 的成熟资产。

本任务只建立 READ_ONLY observation production path 与 qualification contract，不执行平台写，不直接关闭 UNKNOWN、不决定 Queue blocker scope；后者由 Task 13.7-2D 依据 2A policy 完成。

## 2. Dependency Gate

开始实现前必须读取 2A 最终接受版本，至少冻结：

- Platform/account/SKU identity；
- Product/Mapping authority 来源；
- qualified observation 的公共输出字段；
- GQB-2 Observation Blindness 的定义与最小作用域；
- READ_ONLY recovery 在 global blocker 下必须保持可用。

如果 13.7-2B 尚未完成，可用现有 mapping source 做兼容实现，但不得把 workbook source 固化成新公共合同；最终接线必须能切到 2B 的 Runtime Mapping authority。

## 3. Legacy Assets to Salvage

### `a3485af:app/services/listing_scan_quality.py`

可复用的核心检查：

- 最近 due scheduled run；
- run terminal success；
- observation batch identity；
- scope_complete；
- end_marker_verified；
- scan_completed_at freshness；
- source snapshot binding；
- SKU mapping uniqueness/VERIFIED；
- listing projection 与 source snapshot 的一致性。

### `a3485af:app/services/listing_automation_runtime.py`

可复用的生产接线：

```text
Automation Run
→ existing SYNC_STATUS READ_ONLY
→ file Queue / Worker
→ immutable listing snapshot
→ ProductObservationImporter
→ ACK / Archive
→ Automation Run outcome
```

必须继续复用现有 Queue/Worker/Importer、heartbeat、lease、result hash/ACK；不新建第二套扫描 daemon。

## 4. Qualification Contract

Qualification 只回答“这份观察是什么、质量如何、覆盖什么”，不直接决定是否阻断经营。

建议公共结果至少包含：

```text
qualified: bool
platform_name
account_id
internal_sku / scope
observation_type
source_run_id
observation_batch_id
source_snapshot_id
observed_at / scan_completed_at
freshness
scope_complete
end_marker_verified
identity_match
mapping_version / mapping_refs
quality_reason
```

必要时增加 provider capability / mode，但不要提前建设 selector DSL 或配置中心。

### 核心原则

- quality evaluator 输出事实，不自行发明 global blocker；
- 单 SKU qualification 失败默认只影响该 SKU/相关 action；
- 平台级主 Provider 与可信 fallback 均失效，才可能由 2D/Health policy 升级 GQB-2；
- `qualified=false` 不等于 UNKNOWN execution；
- 新 observation 可以比历史 execution 更晚，但不能因此反推旧 click 的精确因果。

## 5. Queue-OPS-02R Handoff

本任务必须让 2D 可以可靠判断：

```text
observation happened after execution stopped boundary
+ identity/scope/source qualified
+ no active submit responsibility
```

并提供结构化 evidence ref/digest，使 2D 能实现：

```text
Historical execution = UNKNOWN
Current qualified platform fact = observed value
Old one-shot business responsibility = CLOSED
Current write eligibility = RELEASED
```

本任务本身不得执行上述 closure。

## 6. Listing Automation Requirements

1. Light/Listing scan 必须是 READ_ONLY，结果声明 `platform_write_performed=false` 或等价 side-effect proof。
2. 使用既有 Automation Run/Job/Event，不能再建独立 scheduler。
3. 使用既有 Queue/Worker/Importer/ACK/Archive，单个 handler 异常不得拖垮其他 Automation/Importer/Watchdog。
4. READ_ONLY 与 write/reconcile 共享 UI channel 时保持串行，但“存在历史 UNKNOWN”不等于“UI 当前被占用”。
5. recovery/read-only 在 GQB 场景下默认仍允许被调度。
6. 结果必须能绑定 account/platform/product identity，未来第二平台不返工公共合同。

## 7. Required Adaptations vs Legacy

- 旧 `len(batches) == 1` 等过度严格条件需按当前 retry/recovery 语义重审；不能因为合法 retry 有多 batch 就永久视为不可用。
- “请等待下一次定时扫描”不能成为固定恢复策略；允许 Recovery Calibration / explicit READ_ONLY 产生合格新事实。
- freshness 由 provider capability/observation contract 决定，不硬编码成所有动作统一 30 分钟。
- mapping identity 增加 `account_id`，接 2B Runtime authority 后不得读旧 workbook。
- 单 SKU observation failure 不直接升平台 S4/GQB-2。

## 8. Explicit Non-goals

- 不实现 Queue-OPS-01/03/04；
- 不自动 release UNKNOWN write lock；
- 不新增 Agent controller；
- 不实现 CurrentTradeDaySalesObservation / Closing / purchase_sequence；
- 不恢复旧 Settlement/Summary authority；
- 不执行真实平台写；
- 不把 qualification 结果直接映射为 Review/global stop。

## 9. Verification

至少覆盖：

1. scheduled listing READ_ONLY 走同一 Automation/Queue/Worker/Importer wiring；
2. complete + end-marker + fresh + identity matched → qualified；
3. incomplete/tail missing/stale/mapping mismatch → structured unqualified，而不是伪成功；
4.合法 retry/recovery 后能选择最新合格 observation，不被旧失败 batch 永久污染；
5.单 SKU unqualified 不阻止其他 SKU qualified；
6. UNKNOWN 停放期间普通 READ_ONLY 仍可产生 observation；
7. observation ref/digest 可被 2D 在 stopped boundary 后验证；
8. GQB 恢复场景下 READ_ONLY 不被 blanket gate 封死；
9. Windows/Linux Core CI green。

## 10. Deliverables

- 当前版 `ObservationQualification` / `ListingScanQuality` Service；
- Automation listing READ_ONLY production handler；
- qualification read model/diagnostic output；
- 与 2B mapping authority 的可切换接线；
- tests/fixtures 选择性吸收 `a3485af:tests/listing_scan_support.py` 等资产；
- 文档说明 legacy `REUSED / ADAPTED / REJECTED`；
- 向 13.7-2D 提供稳定 qualified-observation interface。