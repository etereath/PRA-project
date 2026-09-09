# Task 13.7-2B — Runtime Master Data Authority Selective Salvage

## 1. Goal

在 Task 13.7-2A Authority Contract 接受后，把 Product Master 与 Platform Product Mapping 从运行期 Excel 依赖迁入当前 Runtime DB，并选择性复用旧遗产 `a3485af6890c15c7e6590ee8000f3d04c27ec3d1` 中已经成熟的 Repository / Service / version / idempotency / snapshot / cutover 资产。

本任务不是恢复旧 7F/7G，也不整体 cherry-pick。当前执行/恢复基线始终以 `main` 上 #47～#50 为准。

## 2. Dependency Gate 与开工基线

Task 13.7-2A 已由负责人/Reviewer 接受并合入 `main`；本任务的开工基线为
`a2a0b65d355fe35809f874baed6e7624f732f904`。以下已冻结内容是 2B 的实现输入，
不是本任务可重新解释的待决项：

- Product Master current authority；
- Mapping identity（至少 `platform_name + account_id + platform_product_identity + internal_sku`）；
- 新 SKU 与 inventory 未初始化的语义；
- Price Rules / Listing Rules 是否仍保持 workbook authority；
- authority cutover / rollback 规则。

2B 只在临时/测试 Runtime 上实施和验证。本任务不授权真实 Runtime authority
cutover、部署或平台写。

## 3. Legacy Assets to Salvage

重点检查并尽量复用逻辑，不复用旧 migration number：

- `a3485af:app/repositories/master_data_repository.py`
  - RuntimeMasterDataRepository；
  - product/mapping snapshot digest；
  - DB rows → existing mapping compiler；
  - one-time seed pattern。
- `a3485af:app/services/master_data_management.py`
  - expected_version；
  - idempotency/source digest；
  - operator write transaction；
  - mapping VERIFIED / UNMAPPED / AMBIGUOUS / DISABLED 维护。
- `a3485af:scripts/clean_runtime_cutover.py`
  - preview/hash/backup/archive/candidate verification/explicit confirmation/rollback guard 方法。
- `a3485af:app/operations_web/*`
  - Product Master / Product Mapping Read Model、Query、Presenter/UI，可按当前 Web 架构抽取。

## 4. Required Adaptations

### 4.0 Selective-salvage 决定

| `a3485af` 资产 | 决定 | 当前实现 |
|---|---|---|
| `RuntimeMasterDataRepository`、snapshot digest、DB rows → compiler | `ADAPTED` | 按当前 v19 schema、`account_id`、canonical identity、库存 `NOT_INITIALIZED` 和现役 compiler 重写；未复制旧 migration number |
| Management Service 的 expected version、idempotency、source digest、四态 mapping | `ADAPTED` | 保留事务语义，补充 authority generation、append-only event、rollback irreversible boundary；Product 写不创建库存余额 |
| `clean_runtime_cutover.py` 的 preview/hash/confirmation/rollback guard | `ADAPTED` | 新增 `runtime_master_data_cutover.py`，对当前 Runtime 做 additive import、shadow compare 与显式 authority switch，不替换整库 |
| 旧 Operations Web Product/Mapping read model | `ADAPTED` | 现役 Query 通过统一 provider 读取 Product 与 account-scoped Mapping；未恢复旧 Web composition |
| 旧 migration number、空 candidate 整库替换、旧 7F/7G daemon/dispatcher | `REJECTED` | 与当前 v18 continuation、Task/Review/UNKNOWN/恢复账本边界冲突 |
| 旧 Product workbook 库存字段进入 Product Master | `REJECTED` | 库存继续只由 inventory ledger 权威；缺余额显示 `NOT_INITIALIZED` |

本任务没有可逐字复制且无需适配的遗产资产，因此没有标记为 `REUSED` 的条目。

### 4.1 Schema

- 当前 Runtime v18 已用于 `execution_continuations`；新增表必须使用当前 main 之后的新 migration。
- `product_catalog` 不保存真实库存、平台库存或 current sales commitment。
- `platform_product_mappings` 必须适配当前多平台公共身份，至少包含 `platform_name`、`account_id`、`internal_sku`、可审计的 `platform_product_identity` 以及 mapping status/version/source。
- 不允许用 `COALESCE(missing_balance, 0)` 把“库存事实缺失”伪装成“真实库存=0”；具体行为服从 2A 对新 SKU inventory 初始化的裁决。

### 4.2 Runtime Authority

目标运行关系：

```text
Excel / batch import / Web maintenance
              ↓ controlled write
         Runtime DB authority
              ↓
Web / Manual Task / Authorization / Automation / Observation / Order mapping
```

- 正式运行期 Product/Mapping 不再直接读取 `products.xlsx` / `platform_mappings.xlsx`。
- Excel 可以保留为一次性 bootstrap、批量导入/导出或人工维护辅助，但不能与 Runtime DB 同时成为运行 authority。
- `price_rules.xlsx` / `listing_rules.xlsx` 除非 2A 明确裁决，否则本任务不迁移、不扩张施工。
- ShadowBot `product_identity_mapping.json` 在 cutover 后只能由 Runtime mapping
  generation 生成/同步为 derived locator artifact；部署流程只可补充 UI-only
  locator 字段。artifact 必须绑定 authority generation、mapping digest 和自身
  digest；每个执行目标同时携带 canonical identity JSON/digest，授权复核和执行
  manifest 都按该 digest 绑定，不能退化为显示名+等级 authority。
  canonical identity 必须递归规范化并至少包含 `schema_version`、`identity_type`、
  `components`；legacy workbook 行在 import 时显式转换为该版本结构。
  locator 不能独立维护业务映射、成为 authority 或提供 fallback。
  当前派生文件额外保存 `artifact_payload_sha256`，执行授权同时核对文件字节
  digest；同一账号若无法生成唯一平台/SKU locator，则 fail closed，不猜测目标。

### 4.3 Cutover

不得使用旧遗产“空白 candidate 整库替换 canonical Runtime”的方式。

推荐流程：

```text
backup current Runtime
→ additive schema migration
→ import Product/Mapping into current-schema candidate copy
→ validate hashes / mapping compile / identity uniqueness
→ audited shadow compare with current Excel-derived behavior
→ explicit owner cutover bound to the successful compare receipt
→ 全部正式 Runtime consumers 同 gate 切读
→ Excel runtime reads disabled
```

允许使用 current Runtime 的离线副本做 dry-run；最终不得丢失 #47～#50 已有 Task/history/Review/continuation/UNKNOWN/observation/RM1 evidence。

rollback 只允许让全部正式 consumers 原子恢复到同一个已验证 authority。cutover
后若已经发生新的 authoritative Product/Mapping mutation，或发生依赖新 mapping
的真实平台副作用，禁止静默回退到旧工作簿；此时只能 forward correction，或由
负责人执行显式 maintenance/re-cutover。任何回退都不得删除新 evidence、历史
generation 或 continuation。首次 compare 不匹配时通过 `refresh-preview → refresh`
审计化替换 PRE_CUTOVER candidate；rollback 后旧 compare receipt 自动失效，若
workbook 已变更，必须先 refresh，再用当前 candidate、workbook hash 和 account
bindings 生成新的成功 compare receipt，才能 re-cutover。

## 5. Direct-reader Inventory 与切源处置

下表冻结写 Schema 前完成的 direct-reader inventory。`CUTOVER` 表示必须经同一个
Runtime authority gate 读取 DB snapshot/version；`EXCEPTION` 只允许在正常经营
路径之外显式调用，且不得成为 fallback。

| 当前路径 / consumer | 当前 source | 处置 | 目标与理由 |
|---|---|---|---|
| `app/operations_web/queries.py` 商品、库存选择与系统健康 | `products.xlsx` + Inventory Provider | `CUTOVER` | Product 读 Runtime snapshot；库存仍由 ledger hydrate，缺余额显示 `NOT_INITIALIZED` |
| `app/operations_web/app.py` Manual Task、Authorization、Review composition | Product/Mapping workbook path | `CUTOVER` | 统一注入 Runtime master-data provider，不允许各服务自行选 source |
| `ManualTaskApplicationService` scope/preview/create | `products.xlsx` + `platform_mappings.xlsx` | `CUTOVER` | preview 和 create 绑定同一 authority generation/product digest/mapping digest |
| `ExecutionAuthorizationApplicationService` cost/mapping revalidation | 两个 workbook + locator JSON | `CUTOVER` | 写发布前重读 Runtime generation；locator 仅作绑定该 generation 的派生 artifact |
| `ReviewResolutionApplicationService`、`workflow.resolve_mobile_review`、Emergency authorization/shadow | `products.xlsx` base cost | `CUTOVER` | base-cost snapshot 改读 Runtime Product，并保留 source ref/digest |
| `workflow.py`、`DailyTaskGenerationAutomationHandler`、`business_rule_evaluation.py` | `products.xlsx`；Price/Listing Rule workbook | `CUTOVER` | Product 改读 Runtime；Price/Listing Rule 保持 workbook authority，输入 manifest 分别绑定 digest |
| `order_automation_runtime.py` → Order Observation/Importer | `platform_mappings.xlsx` compiler | `CUTOVER` | provider 改读 account-scoped Runtime mapping snapshot |
| Product Observation / Order Observation / Task generation | 上游传入的 compiled workbook mapping | `CUTOVER` | 消费并持久绑定 Runtime mapping generation/digest，不自行回读 workbook |
| `shadowbot_product_read.py`、`ShadowBotExecutor` READ_ONLY target 生成 | `products.xlsx` | `CUTOVER` | target 来自 Runtime Product/Mapping identity；必须显式 target `account_id` |
| `ShadowBotResultImporter` identity resolution | `products.xlsx` | `CUTOVER` | 导入按请求绑定的 Runtime mapping generation 解析；不得按名称猜测新 authority |
| `shadowbot_commit_batch.py` / Queue / Executor locator 读取 | `product_identity_mapping.json` 或历史 Product workbook | `CUTOVER` | JSON 改为 Runtime generation 派生 artifact并校验绑定；它不是 authority |
| `run_automation_service.py`、`run_shadowbot_queue_services.py` | CLI/env 注入两个 workbook | `CUTOVER` | 正式 service composition 只用 Runtime gate；cutover 后忽略旧 Product/Mapping env |
| `product_inventory_input.py`、`platform_mapping_input.py`、`workbook_repository.py` | workbook 维护 | `OFFLINE/IMPORT/EXPORT EXCEPTION` | 保留 legacy workbook parser 供受控 import/export；正常 Web 写走版本化管理 Service，Product 写不代写 inventory |
| `compile_product_mappings.py`、`bootstrap_authoritative_inventory.py`、`clean_runtime_cutover.py` | 明确传入的离线文件 | `OFFLINE/IMPORT/DIAGNOSTIC EXCEPTION` | 只用于 preview/bootstrap/shadow compare/cutover；命令必须显式 source/hash/confirmation |
| `evaluate_business_rules.py` 与 CLI 的文件校验/预览命令 | 明确传入的 workbook | `OFFLINE/DIAGNOSTIC EXCEPTION` | 不作为 daemon/Web/授权路径；结果不得冒充 cutover 后 Runtime authority |
| `run_e2e_flow_tests.py`、`run_system_smoke_tests.py`、`verify_operations_web_readonly.py` 及测试 fixtures | 临时 workbook | `OFFLINE/DIAGNOSTIC EXCEPTION` | 仅测试/验收夹具；正式运行路径独立证明不读旧 workbook env |

已知正式类别 Web/Manual Task、Authorization、Automation/Product Observation、Order
mapping、Task generation、Queue/Executor/Importer、Emergency、Workflow 和
business-rule evaluation 均已登记。实现中若发现新的正常经营 direct reader，必须先
补入本表并分类，不能通过隐式 fallback 兼容。

## 6. Explicit Non-goals / Rejects

- 不恢复旧 Web 的 Task create → automatic prepare+submit；
- 不修改 current Human final authorization / durable continuation / Coordinator；
- 不引入新的 Queue/dispatcher/daemon；
- 不恢复旧 Settlement / 20:00 seller day；
- 不顺带实现 Exposure allocator；
- 不用 Inventory DB_AUTHORITY/balance 作为 UPDATE_PRICE 等无关动作的 blanket gate；
- 不把 missing inventory balance 自动解释成 0，除非 2A 明确裁决。

## 7. Verification

至少覆盖：

1. additive migration 不破坏现有 v18 continuation 和 #47～#50 账本；
2. one-time import/seed 幂等，source hash/版本可回读；
3. mapping 含 account scope，单账号冲突不污染其他账号；
4. Product/Mapping Web 写入保留认证 capability、actor、idempotency 与 expected_version，冲突 fail safely；
5. VERIFIED / UNMAPPED / AMBIGUOUS / DISABLED 解析与当前 compiler 一致；
6. current consumers 全部切到 Runtime authority 后，修改 Excel 不再悄悄改变运行结果；
7. shadow compare 能证明 cutover 前后相同输入得到相同 Product/Mapping 业务解释，cutover 必须消费当前成功 receipt；
8. compare mismatch 可审计 refresh candidate，rollback 后 workbook 变化不能复用旧 candidate/receipt；
9. rollback 只切 authority/read path，不删除新 evidence；
10. Windows/Linux Core CI green。

本地定向验证只使用临时 Runtime；真实 authority switch、部署、平台写和完整 CI 仍需
分别授权。`runtime_master_data_cutover.py` 的 `--runtime-db` 无默认值，import 必须复用
preview digest；shadow compare 必须提供 actor/idempotency key；cutover 必须重读当前
workbook/account bindings 并提供成功 receipt，cutover/rollback 必须提供固定确认文本。

## 8. Deliverables

- Product/Mapping additive schema + health checks；
- RuntimeMasterDataRepository / Management Service 的当前版实现；
- bootstrap/import + shadow compare + explicit cutover 工具；
- Operations Web 最小维护入口（若 2A 接受）；
- 所有正式 runtime consumers 切读完成；
- 文档更新 authority/cutover/current status；
- 一份 selective-salvage 说明：每个 `a3485af` 资产标记 `REUSED / ADAPTED / REJECTED`。

## 9. Handoff to 13.7-2D

向 Queue Continuity 提供稳定接口：

- current Product identity；
- account-scoped Platform Mapping resolution；
- mapping snapshot/version；
- base cost/source ref（仅相关 action 使用）；
- 不把 Product/Mapping authority 异常自动升级为 global blocker，具体 blocker scope 由 2A/2D policy 决定。
