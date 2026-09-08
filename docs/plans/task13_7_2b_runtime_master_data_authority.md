# Task 13.7-2B — Runtime Master Data Authority Selective Salvage

## 1. Goal

在 Task 13.7-2A Authority Contract 接受后，把 Product Master 与 Platform Product Mapping 从运行期 Excel 依赖迁入当前 Runtime DB，并选择性复用旧遗产 `a3485af6890c15c7e6590ee8000f3d04c27ec3d1` 中已经成熟的 Repository / Service / version / idempotency / snapshot / cutover 资产。

本任务不是恢复旧 7F/7G，也不整体 cherry-pick。当前执行/恢复基线始终以 `main` 上 #47～#50 为准。

## 2. Dependency Gate

开始实现前必须读取并服从 Task 13.7-2A 最终接受版本。若 2A 尚未冻结以下内容，只允许做只读探索和兼容性分析，不得先写 Schema：

- Product Master current authority；
- Mapping identity（至少 `platform_name + account_id + platform_product_identity + internal_sku`）；
- 新 SKU 与 inventory 未初始化的语义；
- Price Rules / Listing Rules 是否仍保持 workbook authority；
- authority cutover / rollback 规则。

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
- ShadowBot `product_identity_mapping.json` 继续只作为执行定位配置与 hash gate，不成为业务 Product/Mapping authority。

### 4.3 Cutover

不得使用旧遗产“空白 candidate 整库替换 canonical Runtime”的方式。

推荐流程：

```text
backup current Runtime
→ additive schema migration
→ import Product/Mapping into current-schema candidate copy
→ validate hashes / mapping compile / identity uniqueness
→ shadow compare with current Excel-derived behavior
→ explicit owner cutover
→ Web + Manual Task + Authorization + Automation + Observation 同 gate 切读
→ Excel runtime reads disabled
```

允许使用 current Runtime 的离线副本做 dry-run；最终不得丢失 #47～#50 已有 Task/history/Review/continuation/UNKNOWN/observation/RM1 evidence。

## 5. Runtime Consumers to Migrate

至少审计并迁移以下正式消费者，禁止遗漏后形成 split-brain：

- Operations Web 商品/映射展示；
- ManualTaskApplicationService 的 product/mapping scope 与 preview；
- ExecutionAuthorization 的 product cost / mapping revalidation；
- Product Observation / Order Observation mapping compiler；
- Automation listing/order handlers；
- emergency protection 中需要的 base cost snapshot；
- ShadowBot READ_ONLY target 生成中由 Product Master 提供的业务身份。

每个消费者迁移后应能证明正式运行路径不再读取两个业务 workbook。

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
4. Product/Mapping Web 写入 expected_version 冲突 fail safely；
5. VERIFIED / UNMAPPED / AMBIGUOUS / DISABLED 解析与当前 compiler 一致；
6. current consumers 全部切到 Runtime authority 后，修改 Excel 不再悄悄改变运行结果；
7. shadow compare 能证明 cutover 前后相同输入得到相同 Product/Mapping 业务解释；
8. rollback 只切 authority/read path，不删除新 evidence；
9. Windows/Linux Core CI green。

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