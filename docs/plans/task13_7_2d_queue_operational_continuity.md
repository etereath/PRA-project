# Task 13.7-2D — Queue Operational Continuity

## 1. Goal

实现 Issue #51 已冻结的 Queue Operational Continuity 原则，使旧 Task、Review、UNKNOWN、历史失败和局部数据异常只阻断能够证明存在真实事故风险的最小经营范围，并复用 13.7-1 已验证的 Human Authorization / durable continuation / Coordinator / v4-v5 / Queue / Worker / Importer / RECONCILE 基线。

本任务依赖 Task 13.7-2A 的 authority/blocker contract，并优先复用 Task 13.7-2B 的 Runtime Product/Mapping authority、Task 13.7-2C 的 qualified observation interface。

## 2. Dependency Gate

- 2A 未接受：只允许现状审计/测试设计，不先改 global blocker policy。
- 2B 未完成：Queue-OPS-01/03/04 可在不绑定新 mapping Schema 的部分先做，但最终 identity gate 必须接 current authority。
- 2C 未完成：Queue-OPS-02R 只能做接口/fixture，不能用临时扫描 stub 伪造 stage completion。

## 3. Scope

### Queue-OPS-01 — New Human Decision Must Survive Old Open Task

当前旧逻辑仍可能出现：同 SKU+platform 有开放 `update_price/set_online/set_offline` Task → 拒绝新的非价格 Human decision。

目标：

```text
new Human decision / Task recorded first
        ↓
old work has not crossed side-effect boundary
        → supersede / cancel old responsibility
old work has crossed side-effect boundary
        → keep new decision, wait for narrow closure
        → re-evaluate against current platform fact
```

要求：

- “已有开放 Task”只能是 scheduling condition，不是 blanket decision rejection；
- 风险降低动作（尤其 SET_OFFLINE）不得仅因无副作用历史 pending UPDATE_PRICE 被拒绝；
- 已提交/ACTIVE/UNKNOWN 的旧执行仍按副作用边界收口，不允许新决定导致第二次不安全写。

### Queue-OPS-02R — Qualified Observation Auto-Closure

保留现有唯一 RECONCILE。RECONCILE 无法确定历史副作用后，普通 READ_ONLY 继续产生当前事实。

当同时满足：

- 原 execution 已有可证明 stopped boundary；
- 无 STARTING/RUNNING attempt；
- 无 submit 正在发生；
- qualified observation 明确晚于 stopped boundary；
- platform/account/SKU identity 唯一匹配；
- observation 满足 2C qualification；
- 无其他真实活动写责任；

允许自动结束旧 one-shot responsibility。

Owner 已冻结：

```text
new qualified READ_ONLY current != old target
→ old one-shot decision = terminal / stale
→ do not retry / do not continue old write
→ historical side-effect remains UNKNOWN
→ release current write blocker
→ future decision is newly created and normally authorized
```

`current == target` 也可以结束旧 responsibility，只表示当前目标满足，不反推历史 click 因果。

禁止把普通 READ_ONLY 直接改写成旧 operation `VERIFIED/NOT_APPLIED` 历史事实。

### Queue-OPS-03 — Review Must Be Action-scoped

统一使用现有 `blocked_actions` / review context/action gate 思路：

- Review 只阻断它声明的 action；
- malformed/scope 不可信可以 fail closed，但只扩大到能证明必要的最小范围；
- 不得“同 SKU+platform 任意 pending Review → 三种销售写全挡”；
- 不新增平行 Review 状态机。

### Queue-OPS-04 — Current Queue vs History

Current Queue 只包含仍承担当前经营责任的对象。

- expired / cancelled / safely terminated / completed Task 保留完整 history，但退出 current work surface；
- 历史 pending 若已超过 TTL 或已具备安全终止证据，应通过正式 service 收口；
- 不允许直接 SQL 清队列；
- Operations Web 可选择性复用 `a3485af` 的 TaskQueueReadModel/Query/Presenter/UI，但 current responsibility 由当前 main 领域状态决定。

## 4. Global Queue Blocker Whitelist

未经新的 Owner/Reviewer 裁决，只允许：

1. GQB-1 Platform-level Critical Business Risk；
2. GQB-2 Observation Blindness；
3. GQB-3 Critical Control Plane Failure；
4. GQB-4 Side-effect / Identity Integrity Failure。

### Global block 默认仍允许

- READ_ONLY Observation；
- Recovery Calibration；
- unique RECONCILE；
- Health / diagnostics；
- necessary human verification；
- 已证明安全的恢复动作。

### 默认不得升级 global 的对象

- 单 SKU UNKNOWN / Review / mapping ambiguity；
- 单 Task failed/expired；
- OLD_PRICE_CHANGED / submit 前 NOT_STARTED；
- historical pending；
- 单 SKU observation incomplete；
- 单次 READ_ONLY failure；
- 单 continuation / Worker request failure；
- 单 SKU Inventory/Exposure 数据异常；
- historical audit unresolved。

## 5. Legacy Assets to Salvage Carefully

可复用：

- `a3485af` Task Queue Read Model / filter / presenter/UI；
- structured `ExecutionQueueBlocked` context/notification pattern；
- Review reason 展示翻译层；
- batch cancellation 的 UI/事务/history 形式；
- per-item task controls（若当前范围需要）。

不得复用旧 policy：

- create Task 后自动 prepare+submit；
- `PENDING/FAILED` status alone 决定可取消；
- Queue 文件无法完整核对就 blanket 禁止所有取消；
- 人工直接声明 TARGET_APPLIED/TARGET_NOT_APPLIED 作为当前标准机器 closure；
- pending Review blanket block；
- Inventory DB_AUTHORITY/balance blanket block。

## 6. Structured Blocker Contract

建议保留 typed blocker/context，但作用域由当前 policy 决定。至少可表达：

```text
blocker_type
scope_level
platform_name
account_id
internal_sku
blocked_actions
source_task / operation / review / observation refs
reason_code
recovery_actions_allowed
automatic_release_condition
owner
```

不要为了这个结构新建独立 blocker 状态机；优先从现有 Task/Review/lock/continuation/Automation/Health 事实投影。

## 7. Global Blocker Proposal Gate

除 GQB-1～4 外，Codex 若准备新增能够阻断无关 SKU/action、整个 platform/account、READ_ONLY 或其他共享范围的 blocker，必须停止施工并先报告：

```text
Trigger
Concrete accident if not blocked
Affected platform/account
Why smaller scope is insufficient
Exactly what is blocked
Recovery/read-only still allowed
Automatic release condition
Human recovery path
Maximum expected blocking duration
Tests proving unrelated work remains available
```

未经明确接受不得实现第五类 global blocker。

## 8. Explicit Non-goals

- 不实现 Queue-OPS-05 Exposure/Inventory 解耦；后续 SET_ONLINE/Exposure 切片单独处理；
- 不重写 Coordinator；
- 不新增第二 Queue、dispatcher、daemon；
- 不改 Current Sales Commitment / Closing / Supply authority；
- 不实现 purchase_sequence；
- 不新增自动 Sales Agent；
- 不用本任务重新设计 Product/Mapping Schema（服从 2B）；
- 不执行未经单独授权的真实平台写。

## 9. Verification

至少证明：

1. 单 SKU UNKNOWN 不阻止其他 SKU Human decision；
2. UNKNOWN 停放期间 ordinary READ_ONLY 可继续；
3. stopped UNKNOWN + 2C qualified observation 能结束旧 one-shot responsibility，historical side-effect 不被改写；
4. `current != target` 时旧决定 terminal，不自动重试，随后新决定可正常预览/授权；
5. Review 只阻断声明的 action；
6. 无副作用旧 pending 不阻止新的风险降低 Human decision；
7. terminal/expired/safely-terminated Task 不再占据 Current Queue；
8. GQB 不阻止明确允许的 Recovery READ_ONLY；
9. 单 continuation/Worker request 异常不拖垮 sibling work / Importer / Watchdog / Review / Outbox；
10. account/platform blocker 默认不传播到另一个 account/platform；
11. UNKNOWN、已提交、active side-effect responsibility 仍保持 fail closed；
12. Windows/Linux Core CI green。

## 10. Deliverables

- Queue-OPS-01/02R/03/04 implementation；
- Current Queue read model/workbench 最小更新；
- structured blocker projection/notification（若确有必要）；
- developer-facing business contract / responsibility / workflow 更新；
- Issue #51 验收矩阵逐项对应测试；
- selective salvage 记录：从 `a3485af` 复用了什么、拒绝了什么；
- 不把 Queue-OPS-05 或后续 Exposure work 偷带入本 PR。