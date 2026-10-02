# Task 13.7-2D — Queue Operational Continuity Lite

## 1. Goal

在已合并 #54、#58（S1）和 #59（S2）的基础上，本任务只解决两个仍存在的经营连续性问题：

1. 新的有效人工销售决定必须先被记录，不能因为同 SKU / 平台存在旧开放 Task 就在创建阶段 blanket 拒绝。
2. 已经结束当前经营责任的历史对象必须退出 Current Queue / blocker 语义，保留完整历史但不继续占据当前经营资格。

一句话目标：

> 任何新的有效人工销售决定都必须能够先保存；旧执行只在其真实副作用责任范围内阻止新执行，旧责任正式结束后新决定自然恢复资格；历史对象继续留存，但不继续拥有当前经营 blocker 权力。

本任务不重新设计 UNKNOWN、Authorization、Queue 或 Review。S1/S2 已完成的减法作为现役基线复用。

## 2. 开工基线

开始编码前：

1. 读取当前根 AGENTS.md 与 docs/owner_product_boundary.md。
2. 将本 PR 分支同步到最新 main。规划冻结时 main 为 9da3392b4a8cc65f6847a58c016f18a1db14da15（PR #59 merge）。
3. 以同步后的实际 main 为代码真值，不从本计划恢复旧实现。
4. 保持 PR Draft；不 merge、不部署、不执行真实平台写。

Task Type：Integration / Bugfix
Review Profile：R3

本轮不改变 authority、Runtime Schema、Queue protocol、Worker protocol 或平台事实定义。

## 3. 已完成能力，不重复施工

以下旧 #55 内容已经由 #54 / S1 / S2 完成或收缩，本 PR 只做直接回归：

- Qualified Observation 已进入正式 current fact。
- UNKNOWN 可在唯一 RECONCILE 后停放，同时 ordinary READ_ONLY 可以继续。
- S1 已提供 stopped UNKNOWN 的安全人工零写终止。
- S2 已将 UPDATE_PRICE 的 Review blocker 收窄到相关 Task / same SKU + same action。
- S2 已解除纯 UPDATE_PRICE 对无关 Inventory maintenance 的依赖。
- S2 已删除正常路径 resolution_only continuation。
- S2 已把 authorization business identity 与执行期事实重新验证分离。
- S2 已停止静默修改旧 one-shot 的 expected_old_price。

不要为这些能力再创建第二套实现。

## 4. Scope A — 2D-01 New Human Decision Admission / Supersession

### 4.1 当前已证实问题

当前 ManualTaskApplicationService._preview_on_connection() 仍会把同 SKU + platform 的开放 UPDATE_PRICE / SET_ONLINE / SET_OFFLINE 投影成 open_identities，并对非价格动作直接加入“该商品与平台已有开放任务”。

因此仍可能出现：

旧 UPDATE_PRICE pending
→ 管理者形成新的 SET_OFFLINE
→ 新决定在创建 Task 前被拒绝

这违反现役合同“新有效 Intent / Task 先记录，旧 Task 只影响 scheduling / execution”的原则。

### 4.2 目标行为

new Human Decision
→ persist new Task first
→ evaluate old responsibility

旧工作尚未跨副作用边界：

old pending + no published/active side-effect responsibility
→ old Task cancelled/superseded
→ new Task remains PENDING
→ zero old platform write

旧工作已经跨副作用边界：

old QUEUED / RUNNING / UNKNOWN / RECONCILE / active write responsibility
→ preserve old execution/history
→ persist new Task
→ new Task waits behind narrow predecessor/write responsibility
→ after old responsibility closes, new Task can be prepared normally

禁止：

- 删除或假装取消已经可能产生副作用的旧 execution。
- 因旧 execution 未结束而拒绝记录新的经营决定。
- 自动执行新 Task。
- 自动把新 Task 的目标写回旧 Task。
- 为此新建 Intent 表、dependency 表或 blocker 表。

### 4.3 优先复用

优先复用：

- tasks / Task history
- decision_trace.predecessor_task_ids
- record_price_supersession()
- unresolved_predecessors()
- operation / attempt / write lock / continuation
- 现有 cancel / expire / supersede service

允许对 app/services/price_decisions.py 做窄扩展，但不要发展成通用 workflow graph。

## 5. Scope B — 2D-02 Current Responsibility vs History

Current Queue / current blocker 只由仍承担当前经营责任的对象组成。

以下终态继续保留完整历史，但不得继续作为当前经营 blocker：

- SUCCESS
- SKIPPED
- CANCELLED
- EXPIRED
- 已具备正式安全终止语义的 terminal failure

当前 Operations Web 已通过 list_tasks(status=PENDING) 形成当前任务列表，因此：

- 不从历史分支引入新的 TaskQueueReadModel 体系。
- 不新增 current_responsibility 字段或 CurrentQueue 表。
- 不通过 UI 过滤掩盖仍为 pending 的数据库对象。
- 需要退出 current responsibility 时，复用正式 Task transition / cancel / expire / supersede。

重点检查 pending Task 已超过 expires_at 时，是否能由已有正式 owner 收口为 EXPIRED 并退出 blocker，而不是只在页面隐藏。

## 6. 只做回归、不新增实现的能力

### AC-03 dependency: S1 closure restores eligibility

证明同一正式 Runtime：

old UNKNOWN
→ S1 zero-write human close
→ historical UNKNOWN preserved
→ old one-shot current responsibility closed
→ previously saved new Task can prepare/authorize normally

如果已经保存的新 Task 可以继续，不要求用户再创建第三个 Task。

### UNKNOWN READ_ONLY

证明单 SKU UNKNOWN / MANUAL_REVIEW 不阻止 ordinary listing READ_ONLY。

不要新增 UNKNOWN auto-closure。

### Review action scope

证明 unrelated Review 不单独阻断 UPDATE_PRICE；现有 same-SKU write responsibility / write lock 仍按真实风险阻断。

### Sibling isolation

SKU-A 的 predecessor / UNKNOWN / Review 不得无理由阻止 SKU-B 的新决定和安全操作。

## 7. 明确取消的旧 #55 施工内容

### 7.1 Queue-OPS-02R 自动 UNKNOWN closure

取消施工。

S1 的“stopped boundary + qualified observation + 任一有权限管理员一次零写终止”已经满足当前家庭农场规模。

只有未来真实使用证明人工处置频率成为明确运营负担时，才重新评估自动 closure。

### 7.2 Global Queue Blocker framework

Issue #51 的最小 blocker 原则继续作为治理约束，但本 PR：

- 不实现 GQB Service。
- 不新增 typed blocker framework。
- 不新增 blocker 状态机。
- 不新增 global blocker persistence。
- 不使用 account_id blocker scope。
- 不实现多平台 blocker propagation。

如果实现中发现 Task/SKU/action 范围无法避免一个具体当前事故，先报告 Boundary Conflict / blocker proposal，不直接扩建。

### 7.3 Structured Blocker Contract

旧计划中的 blocker_type、scope_level、account_id、recovery_actions_allowed、automatic_release_condition、owner 等结构不施工。

如 UI 需要 blocker 原因，优先投影既有 Task / Review / lock / operation 事实，不增加持久模型。

## 8. 近期残留复杂度的处理

### OD-58-RESID-01

PriceExecutionResolutionApplicationService.is_resolved() 仍要求固定 price_execution_human_resolved history。

本 PR 默认不改。只有定向旅程实际证明“责任已正式结束，但该历史格式使新决定继续被 blocker”时，才作为 2D 直接修复；否则留后续 retirement。

### OD-59-RESID-01

execution_profile / queue_root / applet_uri_sha256 仍属于旧 continuation context。

本 PR不改、不扩散，不得升级成环境身份认证、page/session identity 或新 blocker。

### OD-59-COMPAT-01

legacy resolution_only guards 继续保留 pre-S2 envelope 防写兼容。本 PR 不删除。

## 9. 预计生产改动范围

优先限制在：

- app/services/manual_task_orchestration.py
- app/services/price_decisions.py

只有实际断点证明需要时才修改：

- app/services/execution_authorization.py
- app/operations_web/queries.py

原则上本 PR 不应修改：

- app/runtime_schema.py
- app/services/shadowbot_queue.py
- shadowbot/test2/shadowbot_queue_worker.py
- app/shadowbot_contract_primitives.py
- app/services/listing_scan_quality.py

若认为必须触及这些区域，先在 PR 说明：

> 当前 2D 的哪个具体事故无法由现有 Task / predecessor / operation / write-lock / continuation 机制解决？

在得到明确依据前不要扩张。

## 10. 冻结验收情景

### AC-01 — old pending replaced by new risk-reducing decision

old pending UPDATE_PRICE
+ new SET_OFFLINE
→ new Task created
→ replaceable old Task formally cancelled/superseded
→ zero old platform write

### AC-02 — old side-effect responsibility does not reject new decision

old UPDATE_PRICE RUNNING/UNKNOWN
+ new SET_OFFLINE
→ new Task still created
→ old execution/history unchanged
→ new Task cannot execute until relevant old responsibility closes

### AC-03 — closure restores eligibility

old UNKNOWN
→ S1 human zero-write close
→ historical UNKNOWN preserved
→ previously saved new Task can prepare/authorize

### AC-04 — UNKNOWN does not block READ_ONLY

single-SKU UNKNOWN
→ listing READ_ONLY succeeds

### AC-05 — Review stays action-scoped

SET_OFFLINE-only Review
→ UPDATE_PRICE not blocked by that Review alone

### AC-06 — terminal history does not own Current Queue

SKIPPED / CANCELLED / EXPIRED / SUCCESS
→ remains in history
→ absent from current responsibility/blocker

### AC-07 — sibling SKU isolation

SKU-A predecessor/UNKNOWN/Review
→ does not block SKU-B decision

## 11. Verification Budget

开发阶段：

- 只运行修改模块和上述直接旅程。
- 优先扩展现有 test_manual_task_orchestration.py、价格 journey / authorization fixtures。
- 不因本计划重跑 #54/S1/S2 全量测试。
- 不主动运行完整 pytest、完整 smoke、全仓 Ruff 或重复 CI，除非用户明确授权或 AGENTS 的扩大验证门禁被触发。

推送后由 GitHub 自动触发的 Core CI 可读取作为 Merge Gate；不要主动重跑已绿的同 Head CI。

## 12. Explicit Non-goals

本 PR 不做：

- UNKNOWN 自动 closure
- 新 Global Queue Blocker framework
- account_id / session / page identity
- 新表、Schema migration、CurrentQueue 表
- 第二 Queue、dispatcher、daemon
- Queue stop-fence 泛化
- 新 recovery state machine
- Exposure / SET_ONLINE 库存语义整改
- Closing / Supply / Commitment
- Product/Mapping migration retirement
- S3 工程退役
- 微信小程序
- 第二平台实现
- Agent Sales Controller
- 部署或真实平台操作

## 13. Deliverables

1. 2D-01 decision admission / narrow supersession implementation。
2. 2D-02 current responsibility cleanup，仅在现有机制确有断点时做最小修改。
3. AC-01～AC-07 的定向测试 / 直接回归。
4. PR 正文记录实际复用、修改路径、测试结果与未验证项。
5. 若发现范围外结构性问题，记录后停止扩张，不顺手建设新框架。

## 14. Review Handoff

提交审核时给出：

- 固定 Head SHA
- 实际 changed files
- 每个 AC 对应的生产路径和测试函数
- 是否新增任何持久字段/状态/Service（预期为否）
- 定向测试结果
- 自动 CI URL / Windows / Linux 状态（若已触发）
- 明确未执行 merge、deployment、real platform operation
- 未核验真实环境项

Reviewer 首轮围绕 2D-01、2D-02 与 AC-01～AC-07 审查，不重新打开 S1/S2 已关闭设计，也不扩展到 S3。
