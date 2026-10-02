# Task 13.7-3A — Exposure Semantics / SET_ONLINE Decoupling

Issue: #60

## 1. Goal

修正当前仍存在的 Exposure / Physical Inventory 语义混淆。

业务合同已经明确：

- Platform Exposure 是平台侧目标可售量 / 可购买额度，不是 PRA 实物库存 reservation；
- Exposure 可以高于当前实物供给；
- Exposure 总和高于 Supply 不能单独证明超卖；
- `target_inventory <= real_inventory` 不能作为经营硬规则；
- 当前 Exposure 数值仍由 Human 决定，本阶段不实现自动 Exposure 算法。

本任务只删除错误的 physical-inventory hard gate，并保留所有真实平台写安全边界。

## 2. Current confirmed gaps

### G-01 Manual preview blanket Inventory authority gate

`ManualTaskApplicationService._preview_on_connection()` 当前在 Inventory authority 非 `DB_AUTHORITY` 时直接增加全局错误：

> 库存资料正在维护，暂不能创建任务。

这会错误阻止 UPDATE_PRICE / SET_OFFLINE 等并不需要 physical inventory 的人工销售决定。

### G-02 Manual preview SET_ONLINE hard upper bound

当前：

```text
SET_ONLINE target_inventory > balance.current_qty
→ blocked
```

这是旧 Exposure / Physical Inventory 混淆。

### G-03 Authorization SET_ONLINE / SET_OFFLINE requires inventory balance

`ExecutionAuthorizationApplicationService` 当前对非 UPDATE_PRICE 读取 physical balance，并在缺失时拒绝。

SET_OFFLINE 不应依赖 physical inventory；SET_ONLINE 的 Exposure 决定也不应因 balance 缺失而机械拒绝。

### G-04 Authorization repeats target <= physical hard gate

当前：

```text
SET_ONLINE target_inventory > physical balance
→ authorization conflict
```

必须删除。

## 3. Target semantics by action

### UPDATE_PRICE

不依赖 physical inventory。

继续要求：

- product/mapping identity valid；
- current listing fact valid；
- current online state；
- expected old price match；
- target price >= base cost；
- relevant Review；
- predecessor / active write responsibility；
- authorization expiry；
- read-before → compare → write → read-after。

### SET_OFFLINE

physical inventory authority / balance 不构成 blocker。

继续要求：

- product/mapping identity valid；
- current listing status = online；
- listing status fact fresh；
- relevant Review；
- predecessor / active write responsibility；
- authorization expiry；
- existing v5 / Worker / readback / UNKNOWN / unique RECONCILE safety.

SET_OFFLINE 是风险降低动作；Inventory maintenance/missing balance 不应单独阻止它。

### SET_ONLINE

`target_inventory` 在此业务上下文解释为 **Platform Exposure target**。

允许：

```text
physical inventory = 20
target Exposure = 50
```

只要 Human 明确形成并授权该决定，不能用 `target <= physical` 硬拒绝。

physical inventory 可继续作为 UI/经营参考，不成为 platform Exposure 的机械上限。

SET_ONLINE 仍必须保留：

- valid mapping / product identity；
- current listing status = offline；
- fresh listing status fact；
- valid target price / base-cost floor；
- non-negative target Exposure；
- relevant Review；
- predecessor / active write responsibility；
- authorization expiry；
- existing v5 write safety and readback.

## 4. Evidence / accounting boundary

本任务不新建 Exposure ledger。

复用现有 v5 / operation / attempt / item evidence：

- requested `target_inventory`；
- observed inventory before action；
- observed inventory after detail save / actual inventory；
- operation / attempt identity；
- readback timestamp/result。

成功 SET_ONLINE 后现有 postcheck / listing projection 可以继续记录平台库存/可售量事实。

**Exposure 调整不得写入、重算或校准 physical inventory ledger。**

本任务必须证明：

```text
SET_ONLINE Exposure 20 → 50
≠ physical inventory +30
≠ physical inventory =50
```

## 5. Frozen acceptance cases

### EXP-01 — Exposure may exceed physical inventory

```text
physical inventory = 20
SET_ONLINE target_inventory = 50
→ preview allowed
→ Task created
→ authorization allowed
```

### EXP-02 — Inventory maintenance does not block SET_OFFLINE

```text
inventory authority != DB_AUTHORITY
current listing = online
→ SET_OFFLINE preview/create allowed
→ authorization allowed
```

### EXP-03 — Missing inventory balance does not block SET_ONLINE decision

```text
no physical balance row
current listing = offline
valid mapping/status/price
→ Human SET_ONLINE decision can be created and authorized
```

### EXP-04 — Real write safety remains

Must still reject relevant failures such as:

- mapping/identity invalid；
- wrong current listing state；
- stale listing status；
- target price below base cost；
- relevant pending Review；
- unresolved predecessor；
- same-SKU active write lock；
- expired authorization。

### EXP-05 — Existing Exposure readback evidence remains

Successful SET_ONLINE continues to preserve requested target, before/after/readback and operation/attempt evidence.

Do not rebuild this pipeline.

### EXP-06 — Physical ledger isolation

SET_ONLINE Exposure change must not mutate physical inventory balance/transactions.

### EXP-07 — UNKNOWN / RECONCILE regression

Existing v5 / Worker / Importer / readback / UNKNOWN / unique RECONCILE behavior remains unchanged.

## 6. Expected change surface

Prefer:

- `app/services/manual_task_orchestration.py`
- `app/services/execution_authorization.py`
- existing tests around manual task creation / authorization / listing action integration

Optional, only if wording is directly misleading:

- Operations Web presentation text for “平台目标库存” → “平台目标可售量 / Exposure”

Do not touch unless a concrete need is proven:

- runtime schema
- Queue protocol
- Worker protocol
- listing action pipeline
- inventory ledger implementation
- qualification
- new persistence tables/services

## 7. Explicit non-goals

Do not implement:

- Exposure table / ledger；
- Exposure allocator；
- automatic Exposure target calculation；
- Current Sales Commitment；
- CurrentTradeDaySalesObservation；
- Supply / Carryover；
- Daily Closing；
- Observation Health；
- physical inventory accounting cutover；
- multi-platform allocation；
- second platform；
- S3 migration retirement；
- WeChat mini-program；
- Agent Sales Controller；
- deployment or real platform action.

## 8. Complexity rule

Before adding any new field/table/state/service/framework, state the current accident that cannot be solved by existing Task / authorization / v5 / operation / attempt / readback / inventory ledger.

“Future multi-platform” or “future enterprise use” is not sufficient justification.

## 9. Verification budget

Development default:

- targeted tests for EXP-01～EXP-07 and direct regressions only；
- reuse existing fixtures wherever possible；
- do not proactively run full pytest, full smoke, all-repo static checks, or manually rerun CI without authorization under AGENTS.md.

Automatic PR CI after push may be read as Merge Gate evidence.

## 10. Review handoff

Submit for review with:

- fixed Head SHA；
- actual changed files；
- mapping from EXP-01～07 to production paths and concrete test functions/assertions；
- confirmation that no new persistent field/state/table/service was added, or explicit justification if that expectation changed；
- targeted verification results；
- automatic CI URL/jobs if triggered；
- explicit note that deployment/real platform/real Exposure write were not performed.

Reviewer should limit first review to 3A semantics and direct regressions; do not expand into Commitment, Closing, Supply, Health, S3, or second-platform work.

## 11. 实现与定向验证交接（2026-10-03）

基线为 `main@23190a1eb76cc59f2f7942e1b2f32d872fcfe721`，计划分支起点 `06423a26338b7eb807cc3daea7c59fa04338f9bb` 已包含该 main。保持 Draft；本地实现没有触发新的自动 CI，待推送后按实际 Head 读取，不借用上游结果。

生产修改仅三个文件：

- `app/services/manual_task_orchestration.py`：删除 authority/balance 硬门禁与 Exposure 不得大于实物库存的上限。保留可选实物库存展示，但不把参考数量/版本纳入决定预览摘要；请求的目标可售量仍被摘要绑定。
- `app/services/execution_authorization.py`：删除授权中的同类硬门禁，保留原映射、Review、predecessor、锁、有效期、授权身份和正式 v5 发布/回读流程。`target_inventory` 继续是现有平台协议字段，不新建业务字段。
- `app/operations_web/presenters.py`：将人工输入项改称“平台目标可售量”，对应错误提示同步，不建设新 UI 或 Exposure 分配能力。

测试扩展既有 `test_manual_task_orchestration.py` 与 `test_execution_authorization.py` 夹具。新增证明针对“合法 Exposure 被库存误拒绝”和“删除错误门禁时意外破坏真实写安全/账本隔离”这两个实际风险；原测试错误接受数量上限，不能证明新合同。沿用现有 Runtime、扫描结果、v5 publisher、Worker 合同校验和 Importer，不新增测试框架。

| EXP | 生产路径 | 测试与关键断言 |
|---|---|---|
| EXP-01 | Manual preview/create → ExecutionAuthorization → v5 | `test_offline_has_no_price_and_online_exposure_may_exceed_physical_inventory`；`test_human_exposure_authorization_v5_readback_preserves_physical_ledger[present]`：实物 20，目标 50，实际发布请求仍为 50 |
| EXP-02 | Manual + ExecutionAuthorization，库存只作参考 | `test_physical_inventory_changes_do_not_invalidate_sales_preview` 的 maintenance 参数覆盖三动作；`test_inventory_maintenance_and_missing_balance_allow_offline_authorization` 覆盖 SET_OFFLINE 预览、创建及授权提交 |
| EXP-03 | 同一上架链，缺失余额仍允许 Human 决定 | 上架旅程 `[missing]` 与 `[maintenance]` 均完成正式 v5 发布及结果导入；预览在余额删除后仍可用原摘要创建 |
| EXP-04 | 原产品/映射、状态、新鲜度、价格、Review、predecessor、锁与有效期门禁 | `test_online_safety_gates_remain_without_physical_inventory`；既有 mapping/base-cost 与 Review 用例；`test_offline_authorization_safety_remains_without_balance`；上架旅程在真实发布锁存在时拒绝第二个同 SKU 授权；`test_task_execution_payload_is_rechecked_inside_publish_transaction` |
| EXP-05 | v5 publish → Importer → operation/attempt/listing projection | 上架旅程核验请求 target=50、operation target=50、attempt VERIFIED/ended_at、写前=20、写后/actual=50、readback timestamp，以及 Task SUCCESS 和平台投影=50 |
| EXP-06 | 平台事实更新与实物库存隔离 | 上架旅程逐行比较 `inventory_balances`、`inventory_transactions`、`inventory_authority_state` 在发布及导入前后完全相同 |
| EXP-07 | 既有 Worker/v5/UNKNOWN/唯一 RECONCILE | `test_publish_and_import_verified_set_online`、`test_multi_offline_unknown_import_preserves_item_specific_states`（含重复 reconcile 返回 ALREADY_EXISTS）以及 `test_shadowbot_task13_worker_recovery.py` 的三个用例 |

最小旅程首先得到 **1 passed / 1 failed（2.24 秒）**；失败发生于维护夹具设置 PRE_CUTOVER 时未清空 bootstrap 字段，数据库正确拒绝，未改变生产约束。修正夹具后，以下限定验证 **59 passed（24.00 秒）**：

```text
python -m pytest -q tests/test_manual_task_orchestration.py tests/test_execution_authorization.py tests/test_shadowbot_listing_action_pipeline.py::test_publish_and_import_verified_set_online tests/test_shadowbot_listing_action_pipeline.py::test_multi_offline_unknown_import_preserves_item_specific_states tests/test_shadowbot_listing_action_pipeline.py::test_task_execution_payload_is_rechecked_inside_publish_transaction tests/test_shadowbot_task13_worker_recovery.py --tb=short --no-header
```

目标文件 Ruff、严格 UTF-8 回读和 diff 检查通过。未运行本地全量 pytest、完整 smoke、全仓静态检查或主动 CI 重跑；没有新增持久字段、状态、表、Service 或 schema migration，没有修改 v5/Queue/Worker 协议或库存 ledger 实现。

证据边界：正式服务、临时 SQLite、v5 文件请求、Worker 请求校验、结果 Importer 和账本比较是真实代码；平台采集/点击/回读由既有结果夹具代替，没有操作真实浏览器或平台。Worker 中断与唯一 RECONCILE 通过既有定向回归验证，不把合成结果当作实机验收。负责人审查、实际 Head 的 CI、部署与真实 Exposure 调整不由本次本地验证替代。
