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
