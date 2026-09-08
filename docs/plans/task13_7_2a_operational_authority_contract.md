# Task 13.7-2A — Operational Authority & Continuity Contract

## 1. Goal

在继续 Runtime Master Data、Observation Qualification 与 Queue Continuity 实现前，冻结 PRA 当前经营 authority、最小 blocker 作用域、cutover 责任与 `a3485af` 遗产复用边界。

本任务是 **contract / responsibility gate**，不实施平台写、不切换真实 Runtime authority、不恢复旧 13.5-7F/7G 架构。

基线：`main@52f5b212a41f7a0b8d1584b6d00a037236f485e5`，旧遗产只作为候选材料读取：`a3485af6890c15c7e6590ee8000f3d04c27ec3d1`。

## 2. 已冻结的产品原则

1. PRA 第一目标是安全、持续销售经营；历史证据、Review、UNKNOWN 和旧 Task 只服务于避免真实副作用、恢复和解释，不得为了账本整齐扩大当前经营阻断。
2. blocker 采用最小作用域：`Task → SKU+Action → SKU All Writes → Platform/Account Write Queue → Cross-platform System`。
3. 单 SKU UNKNOWN、Review、mapping 缺失、历史 pending、单次 READ_ONLY 失败、单 continuation 失败默认不是 Global Queue Blocker。
4. Global Queue Blocker 白名单仅允许 GQB-1～GQB-4：
   - GQB-1 Platform-level Critical Business Risk；
   - GQB-2 Observation Blindness；
   - GQB-3 Critical Control Plane Failure；
   - GQB-4 Side-effect / Identity Integrity Failure。
5. Global blocker 默认不能关闭 READ_ONLY、Recovery Calibration、唯一 RECONCILE、Health/diagnostics 与必要人工恢复。
6. UNKNOWN 在 RECONCILE 仍无法证明历史副作用后，可由 stopped boundary 之后的 qualified current observation 结束旧 one-shot business responsibility；历史 side-effect 仍可保持 UNKNOWN。`current != old target` 时旧决定终止，不自动继续旧改价，释放当前 write blocker，后续按当前事实创建新决定。
7. Human decision 与 final platform authorization 保持分离；`Task → explicit authorization → durable continuation → Coordinator → existing v4/v5/Queue/Worker/Importer` 是当前执行基线。

## 3. Authority Matrix 冻结结论

完整矩阵以[业务合同 §23](../business_contract.md)为唯一业务定义，[目标职责 §1.1～1.2](../rebaseline/task13_6_target_responsibility_and_gap_matrix.md)定义实现 owner。冻结摘要如下：

| 对象 | current → target authority | 版本/identity 与 blocker 边界 |
|---|---|---|
| Product Master | `products.xlsx` → 2B 显式 cutover 后 Runtime DB | 版本化 snapshot；新 SKU 不自动建立 0 inventory，缺失为 `NOT_INITIALIZED`；默认最多 SKU 全动作 |
| Platform Product Mapping | `platform_mappings.xlsx` 编译结果 → 2B 显式 cutover 后 Runtime DB | 显式 platform/account/canonical product identity/SKU/status/半开有效期；默认只影响相关 identity/SKU/action |
| Price / Listing Rules | 本阶段继续各自 workbook authority | 绑定规范化规则集 digest；未来迁 DB 另做 cutover，不随 2B 机械迁移 |
| Physical Inventory | 继续既有 Runtime DB ledger | Product Master 不复制余额；只阻断真实依赖库存的动作，不阻断 UPDATE_PRICE |
| Platform Observation | immutable evidence + 可失效的 current qualified projection | Operating fact qualification 与 Evidence delivery/archive health 分维度输出；单 identity 或归档问题默认局部隔离 |
| Human one-shot Decision | 最新有效 Intent/Task；授权和平台事实分离 | 外部事实可使旧决定 stale/terminal；默认 Task 或 SKU+action |
| ShadowBot identity JSON | Executor 本地定位配置 | cutover 后从 Runtime mapping generation 生成/同步为 derived locator，绑定 generation/digest；部署只补 UI-only 字段，不成为业务 authority/fallback |

Configured target `account_id` 必须由负责人/受控部署配置显式赋值，不能从 platform 名、profile、窗口标题、登录显示文本或 applet URI 推断；Adapter/Executor/登录链另行提供 active session/account binding evidence。真实写前二者必须匹配，并与 internal SKU、platform product identity、authority/mapping generation 和 locator digest 一起绑定 authorization/execution evidence。无法证明或 mismatch 时按 GQB-4 阻止受影响账号全部新写，保留安全 READ_ONLY identity calibration/diagnostics，不记录凭据且不影响无关账号。`platform_product_identity` 使用 adapter 定义的版本化 canonical JSON 与 SHA-256 digest；当前蚂蚁无稳定 ID 时可用规范化商品名+等级，online/waiting 页面位置和 UI selector 不进入公共身份。

## 4. `a3485af` Selective Salvage Boundary

| `a3485af` 候选 | 结论 | 当前采用边界 |
|---|---|---|
| `RuntimeMasterDataRepository` read/compiler/snapshot/version | ADAPT | 复用职责与接口思路；按当前 main 的 additive schema、account identity 与 Product/Inventory 分离重写 |
| `MasterDataManagementService` expected_version/idempotency/source digest/operator pattern | ADAPT | 保留并发与重放保护；删除 create-product 自动初始化 0 inventory，写前后读取及 actor/source 继续显式 |
| `clean_runtime_cutover.py` preview/hash/backup/candidate verify/confirm/rollback guard | ADAPT | 复用步骤；只做 additive import/switch，不替换或清空 canonical Runtime |
| `listing_scan_quality.py` run/batch/scope/end-marker/freshness/source/mapping checks | ADAPT | 扩成 account-aware 公共 contract；按唯一 current qualified candidate 选取合法 retry，不以数据库物理 batch 数量判失败 |
| `listing_automation_runtime.py` Automation→READ_ONLY→Queue/Worker→evidence→ACK/Archive | ADAPT | 拆分 Operating fact trust 与 Evidence delivery/archive health；局部 ACK/archive 失败不否定仍可验证的 immutable fact，接当前 Queue/Importer 与 2B mapping snapshot |
| Master Data / Task Queue Read Model、Query、Presenter/UI | ADAPT | 复用展示资产；Current Queue 与 History 分开，UI 不成为责任 owner |
| structured blocker context / notification presentation | ADAPT | 使用 2A category/scope/evidence/allowed-ops/release contract；重写 blanket policy |
| `review_display.py` 展示边界翻译 reason/code 模式 | REUSE | 只在 Presenter 翻译；原始 reason/code 仍保留，不进入业务判定 |
| per-item ManualTask values | DEFER | 作为未来批量 Human Sales Control 候选，不进入 2A～2D |
| 整体 7F/7G commit / 架构 | REJECT | 禁止整体 cherry-pick；当前 #47～#50 execution baseline 保持有效 |
| 旧 `v18` Product/Mapping migration | REJECT | v18 已被 continuation 占用；2B 只能在当前 latest 后新增 migration |
| 缺少 `account_id` 的 mapping identity | REJECT | 2B 必须使用显式 account-aware identity |
| blank candidate 整库 replacement | REJECT | 不删除 Task/history/continuation/Review/observation/UNKNOWN/RM1/inventory evidence |
| Task create 后自动 prepare+submit | REJECT | PENDING 仍由 Human/Web 显式授权 |
| generic `PENDING/FAILED` batch cancel predicate | REJECT | 必须依据副作用边界、owner 与具体 action 判断 |
| 人工 `TARGET_APPLIED/TARGET_NOT_APPLIED` 作为标准机器收口 | REJECT | 仅保留正式人工 fallback；标准自动路径使用唯一 RECONCILE + qualified current observation，且不改写历史因果 |
| 任意 pending Review blanket write block | REJECT | 复用 `blocked_actions` 的 action-scoped 语义；malformed context fail closed 仍取可信最小范围 |
| Inventory authority/balance 对无关 action 的 blanket gate | REJECT | UPDATE_PRICE 与其他无库存依赖动作不得被其阻断；Exposure/SET_ONLINE 的具体整改留后续切片 |
| 旧 Settlement / 20:00 seller day authority | REJECT | 已被现行业务合同 supersede |
| 固定 20 扎安全余量 | DEFER | 未经独立业务裁决不进入规则或硬门禁 |

## 5. Schema / Cutover 约束

1. 不复用旧 migration number；在当前 main 最新 schema 之后 additive migration。
2. 不删除或重建 #47～#50 的 Task/history/continuation/Review/observation/UNKNOWN/RM1 evidence。
3. 新 master-data cutover 固定为：`backup → additive schema → import → shadow compare → 枚举全部正式 Runtime consumers → explicit authority switch → 全部正式 consumers 同 gate 切读 → old source 退为受控例外`。
4. 2B 必须把 Product/Mapping direct reader 逐项标为 `CUTOVER` 或 `OFFLINE/IMPORT/EXPORT/DIAGNOSTIC EXCEPTION`；当前已知类别包括 Web/Manual Task、Authorization、Automation/Observation、Order mapping、Task generation，以及 Queue/Executor/Importer、Emergency、Workflow/business-rule evaluation 的实际依赖。切换后正式路径不得依赖旧 workbook 环境变量或隐式 fallback。
5. 一个 platform/account 的 mapping 或 observation 问题默认不传播到其他 account/platform。
6. rollback 必须恢复全部正式 consumers 到一个 authority；cutover 后若已有新的 authoritative mutation 或依赖新 mapping 的平台副作用，则禁止静默回到旧 authority，只能 forward correction 或负责人显式维护/re-cutover。

## 6. Open Decisions 已冻结

1. **库存：`NOT_INITIALIZED`。** Product 创建不写库存；`0` 只来自独立、可审计、幂等的库存初始化事实。
2. **Rules：继续 workbook authority。** Price/Listing 评估和 Task 绑定规范化规则集 digest、来源与加载时间；未来 DB 化另行 shadow/cutover。
3. **Mapping：显式 account-aware identity。** 配置的 target `account_id` 与 active session binding evidence 分层；写前必须匹配。唯一性为 platform/account/product identity digest + 有效期，canonical identity 不含 UI selector/page state；ShadowBot locator 从 authority generation 派生并绑定 digest。
4. **Qualification：经营事实资格与证据交付健康分开。** Operating fact 输出 contract/provider/capability version、quality codes、scope/pages/end marker、platform/account/product identity、mapping version、时间/freshness/fresh_until 与 run/attempt/batch/snapshot/manifest/result/immutable-evidence refs/digests；ACK/archive/notification 单列 delivery health。局部 delivery 失败不否定仍可验证的事实；selector 允许历史 retry batch 并只选一个 current qualified candidate。Authorization、Coordinator、Observation Health 各自按动作消费。
5. **GQB Proposal：治理触发、Workflow 第 6 节执行。** 除 GQB-1～4 外或扩大至无共享风险对象时先停工，按模板提交，未经 Owner/Reviewer 接受不得实现。

## 7. Deliverables

- 更新 `docs/business_contract.md`：Operational Continuity、Global Queue Blocker 白名单、UNKNOWN 当前经营责任、authority 分层。
- 更新现役 target responsibility/gap matrix：authority owner、cutover、最小 blocker 作用域、后续 Task 依赖。
- 必要时更新 developer workflow/governance：新增第五类 global blocker 必须先提交 Proposal。
- 给 13.7-2B/2C/2D 写出明确可执行输入，禁止提前实现未冻结的 open decision。

## 8. Acceptance

- Authority Matrix 无双权威/隐式 fallback；
- Product/Mapping、Rule、Inventory、Observation、Human Decision、ShadowBot identity 的角色不混淆；
- Operating fact qualification 不被局部 ACK/archive health 机械否定，证据本身不可验证时仍 fail closed；
- 合法 retry/recovery 可选出唯一 current qualified candidate，冲突候选 fail closed 且不合并；
- Product/Mapping cutover 覆盖全部正式 Runtime consumers并登记离线例外；target account 与 active session binding 分层且写前匹配；
- #51 的 Queue-OPS-01/02R/03/04 能根据合同判断最小 blocker scope；
- `a3485af` 每项遗产有 `REUSE / ADAPT / REJECT / DEFER` 结论；
- 不新建平行 Coordinator、Queue、Review 状态机或配置中心；
- 不执行真实平台写、不修改真实 Runtime authority。

## 9. Downstream

- Task 13.7-2B：在当前 latest schema 后新增 additive migration；Product 与 Mapping 分表/版本化，mapping 必含 configured target `account_id` 与 canonical identity JSON/digest；Product create 不初始化库存；生成/同步绑定 generation/digest 的 ShadowBot locator；枚举全部生产 direct readers，逐项 `CUTOVER` 或登记离线例外，正式路径无 workbook fallback；rollback 遵守不可静默回退边界。
- Task 13.7-2C：建立 account-aware qualification DTO/result；分开 Operating fact trust 与 Evidence delivery/archive health；selector 允许 FAILED/SUPERSEDED/retry/recovery 历史 batch，按 run/attempt/scope/completion/time/identity 选唯一 current candidate。验证“首次失败→retry 成功仍合格”、“两份冲突 current 候选 fail closed”、“事实合格但 ACK/archive 局部失败仍可用”和“交付失败导致 immutable evidence 不可验证则 fail closed”。它不直接写 blocker，也不修改历史 execution result。
- Task 13.7-2D：以本合同实现 Queue-OPS-01/02R/03/04；新决定先记录，Review action-scoped，Current Queue/History 分离；02R 只在 stopped boundary 后消费 2C qualified observation，关闭旧 one-shot 当前责任而保留 historical UNKNOWN；写前核对 target account 与 session binding，mismatch 不发布；运行期 blocker 记录 category/scope/evidence/allowed recovery/release owner。
- Exposure/Inventory 解耦（原 Queue-OPS-05）不放入本轮，随 Exposure/SET_ONLINE 后续切片处理。

2B/2C 可以在 2A 接受后并行；2D 的 identity 最终接 2B，Queue-OPS-02R 最终接 2C。在依赖未到位时只允许接口替身或只读探索，不得以临时 fallback 冻结第二套 authority。
