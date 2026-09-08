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

## 3. Authority Matrix 必须冻结

至少对下列对象明确：current authority、允许写入口、运行期读入口、历史/导入源、版本/identity、允许产生的 blocker scope。

| 对象 | 需要冻结的核心问题 |
|---|---|
| Product Master / internal SKU | Runtime DB 是否为 current authority；新增/停售/基础成本/品种等级如何版本化 |
| Platform Product Mapping | 必须包含 `platform_name + account_id + internal_sku + platform_product_identity`；mapping status 与有效期语义 |
| Price Rules | 当前 workbook 是否继续作为 runtime rule authority；若未来迁 DB，单独 cutover |
| Listing Rules | 同上；不得与本轮 Master Data 迁移机械绑定 |
| Physical Inventory | 继续由既有 DB inventory ledger authority；不得复制到 Product Master |
| Platform Observation | immutable evidence、current projection、qualified observation 的角色分离 |
| Human one-shot Decision | 当前经营决定，不是平台事实；外部变化可使其 stale/terminal |
| ShadowBot identity mapping JSON | 平台执行定位配置，不得变成 Product/Mapping 业务 authority |

### 默认方向

- `products.xlsx` / `platform_mappings.xlsx` 不应继续作为正式运行期 Product/Mapping authority；可以保留一次性 bootstrap、批量导入/导出或受控维护辅助角色。
- Runtime DB Product/Mapping authority 的实现允许复用 `a3485af` 的 Repository/Service/version/idempotency/snapshot 思路，但新 Schema 必须按当前 main 与多平台 `account_id` 重新设计。
- `price_rules.xlsx` / `listing_rules.xlsx` 本任务只冻结角色，不默认迁入 SQLite；是否数据库化若会扩大施工，拆成独立后续任务。

## 4. `a3485af` Selective Salvage Boundary

### 允许作为高价值候选复用

- `RuntimeMasterDataRepository` 的读取、编译、snapshot/version 思路；
- `MasterDataManagementService` 的 expected_version、idempotency、source digest、operator write pattern；
- `clean_runtime_cutover.py` 的 preview/hash/backup/candidate verification/explicit confirmation/rollback-guard 方法；
- `listing_scan_quality.py` 的 run/batch/scope/end-marker/freshness/source-snapshot/mapping 一致性 qualification；
- `listing_automation_runtime.py` 的既有 Automation→READ_ONLY→Queue/Worker→immutable observation→ACK/Archive 接线思路；
- Task Queue / Master Data 的 Read Model、Query、Presenter/UI 资产；
- structured blocker context 与 notification presentation；
- `review_display.py` 仅在展示边界翻译原始 reason/code 的模式；
- per-item ManualTask values 作为未来批量 Human Sales Control 候选。

### 明确不得原样恢复

- 整体 cherry-pick 7F/7G；
- 旧 `v18 = product_catalog/platform_product_mappings` 迁移编号；当前 v18 已被 execution continuation 占用；
- 旧 mapping 缺少 `account_id` 的 identity 结构；
- clean-runtime 直接以空白 candidate 整库替换当前 canonical Runtime；
- Human Task create 后自动 prepare+submit；
- generic `PENDING/FAILED` batch cancel predicate；
- 人工直接声明 `TARGET_APPLIED/TARGET_NOT_APPLIED` 并把它作为当前标准机器收口；
- 同 SKU+platform 任意 pending Review 的 blanket write block；
- 对 UPDATE_PRICE 等无关动作的 Inventory `DB_AUTHORITY` / balance blanket gate；
- 旧 Settlement / 20:00 seller day authority；
- 未重新经过业务裁决的固定 20 扎安全余量。

## 5. Schema / Cutover 约束

1. 不复用旧 migration number；在当前 main 最新 schema 之后 additive migration。
2. 不删除或重建 #47～#50 的 Task/history/continuation/Review/observation/UNKNOWN/RM1 evidence。
3. 新 master-data cutover 推荐：`backup → additive schema → import → shadow compare → explicit authority switch → Web/Automation/Authorization 同 gate 切读 → old source 退为 import/history`。
4. 不允许 Web 读 SQLite、Authorization 读 Excel、Automation 再读另一份 source 的长期 split-brain。
5. 一个 platform/account 的 mapping 或 observation 问题默认不传播到其他 account/platform。

## 6. 本任务必须回答的 Open Decisions

1. Product Master 新增 SKU 时，库存未初始化应表示 `unknown/not_initialized` 还是自动建立 0 balance；不得仅因数据库整齐默认 0=真实库存。
2. Price Rules / Listing Rules 本阶段继续 workbook authority 还是计划迁 DB；若继续 workbook，如何做 version/digest/cutover 绑定。
3. Mapping 的 `account_id` 来源、唯一键与 platform_product_identity 形状。
4. qualified observation 的公共 contract：quality/freshness/scope/identity/source refs 输出什么；谁决定是否构成 blocker。
5. Global Queue Blocker Proposal 的 developer workflow 落点。

## 7. Deliverables

- 更新 `docs/business_contract.md`：Operational Continuity、Global Queue Blocker 白名单、UNKNOWN 当前经营责任、authority 分层。
- 更新现役 target responsibility/gap matrix：authority owner、cutover、最小 blocker 作用域、后续 Task 依赖。
- 必要时更新 developer workflow/governance：新增第五类 global blocker 必须先提交 Proposal。
- 给 13.7-2B/2C/2D 写出明确可执行输入，禁止提前实现未冻结的 open decision。

## 8. Acceptance

- Authority Matrix 无双权威/隐式 fallback；
- Product/Mapping、Rule、Inventory、Observation、Human Decision、ShadowBot identity 的角色不混淆；
- #51 的 Queue-OPS-01/02R/03/04 能根据合同判断最小 blocker scope；
- `a3485af` 每项遗产有 `REUSE / ADAPT / REJECT / DEFER` 结论；
- 不新建平行 Coordinator、Queue、Review 状态机或配置中心；
- 不执行真实平台写、不修改真实 Runtime authority。

## 9. Downstream

- Task 13.7-2B：Runtime Master Data Authority selective salvage。
- Task 13.7-2C：Qualified Observation & Listing READ_ONLY selective salvage。
- Task 13.7-2D：Queue Operational Continuity，以 2A 合同为准并复用 2B/2C 的 authority/qualification。
- Exposure/Inventory 解耦（原 Queue-OPS-05）不放入本轮，随 Exposure/SET_ONLINE 后续切片处理。