# 任务 13.5-7D：数据库真实库存合同

- 状态：评审整改已实现，等待 Draft PR 复审；真实 cutover 未执行
- 冻结日期：2026-08-12
- Review Profile：`R4`
- 基线：`ca1dce5`（PR #33 合并后的 `main`）
- 范围：真实库存唯一权威、人工调整、销售差额、取消恢复、库存预警和 Web 回读

## 1. 目标与边界

7D 把农场当前可销售数量从 `products.xlsx.current_stock` 一次性切换到 Runtime DB。切换后
DB 余额和不可变流水是唯一真实库存权威；平台库存仍只表示某个平台买家可购上限，不能
覆盖真实库存。

本阶段不新增平台动作、Task/Review/Incident 状态、第二平台、通用脚本 Runner、Agent
Schema 或预测模型。人工库存调整只改 DB；销售扣减只消费既有结算选择；库存预警只复用
Incident/Outbox，不创建下架任务。

## 2. 复用矩阵

| 能力 | 分类 | 7D 处理 |
| --- | --- | --- |
| `SalesFactSelectionService` | 原样复用 | 不重写订单/估算权威关系、完整 CLOSED 与映射判断 |
| `PlatformTradeDaySummary` 版本链 | 参数化复用 | 只消费当前 `SKU` 汇总和其冻结输入，不把平台总量拆回 SKU |
| `TradeDaySettlementService.refresh_after_order_import()` | 参数化复用 | 回补后调用同一库存差额服务，不另算取消量 |
| `OperationalIncidentRepository` / Outbox | 参数化复用 | 余额不足、准入失败和库存阈值事件沿用既有治理链 |
| Runtime SQLite 事务、重试和健康检查 | 原样复用 | 余额、流水和销量基准在同一 `BEGIN IMMEDIATE` 内更新 |
| 商品工作簿读取与 SKU 规则 | 参数化复用 | 商品资料继续读取；库存只作为 cutover 冻结输入 |
| TaskGeneration / Listing / Pricing | 抽取公共能力 | 在边界处使用统一 Inventory Provider 注入 DB 余额 |
| 余额、流水、销量基准、切换状态、阈值配置 | 确需新增 | v17 最小 Schema；不塞进备注、平台库存或 Review 文本 |

## 3. Runtime Schema v17

只新增五张窄表：

1. `inventory_authority_state`：单例 `REAL_INVENTORY`，状态仅为
   `PRE_CUTOVER / DB_AUTHORITY`，保存工作簿 Hash、完整 SQLite 逻辑快照 Hash、销售水位
   交易日、完成时间、操作人和版本；
2. `inventory_balances`：每个 `internal_sku` 一行非负余额、并发版本、最近流水和更新时间；
3. `inventory_transactions`：不可变流水，保存 before、有符号 delta、after、类型、来源、
   原因、actor、业务日期、证据、幂等键、请求 Hash 和余额版本；
4. `inventory_sales_baselines`：按 `platform + PRA 交易日 + SKU` 保存已应用累计销量、事实
   来源、质量、映射版本、来源引用、请求 Hash 和版本；
5. `inventory_alert_policies`：全局默认或每 SKU 覆盖的启停、阈值、重复提醒间隔和版本。

`inventory_transactions` 禁止 UPDATE/DELETE。约束固定为
`inventory_after = inventory_before + inventory_delta`、after 非负、幂等键唯一。余额更新、
流水插入和销量基准更新必须同事务；任何一步失败整体回滚。

## 4. 唯一权威切换

Schema 迁移只建立 `PRE_CUTOVER` 状态，不读取或改写工作簿，也不自动切换权威。显式
bootstrap 服务执行：

1. 只接受 `PRA_PRODUCTS_WORKBOOK` 与 `PRA_RUNTIME_DB` 固定 canonical 路径；
2. 独占锁定商品工作簿，固定工作簿 SHA-256；通过 SQLite 逻辑 dump 计算包含已提交 WAL
   内容的完整 Runtime 快照 Hash，并以 SQLite Backup API 生成同身份备份；
3. 进入 `BEGIN IMMEDIATE` 后再次比较逻辑快照 Hash，使其他 Automation、Importer 或 Web
   Runtime 写入在切换事务结束前无法插入；
4. 在同一事务确认 `inventory_balances / inventory_transactions /
   inventory_sales_baselines` 三表同时为空，任一非空立即拒绝；
5. 在同一事务从 Runtime 读取唯一有效的版本化运营时间策略，据此计算
   `bootstrap_sales_watermark_date`；调用方必须显式绑定该交易日最新、十分钟内、
   `OPEN / SUCCEEDED / ACCEPTED`、范围完整、尾部已验证且订单数为 0 的订单观察批次；
   当前交易日任一快照曾观察到订单，或批次不完整/过期、日期或策略错位、不是最新批次、
   出现多个平台时，立即拒绝并保持 `PRE_CUTOVER`；
6. 逐 SKU 写入 `BOOTSTRAP` 流水和余额，并把订单批次 ID 与内容 Hash 写入每条切换流水的
   `supporting_refs`。切换日前 eligible 历史事实只建立累计销量基准；切换日本身以可信
   空订单快照作为明确的 0 水位，不从不完整 OPEN/PARTIAL 汇总猜测基准；
7. 在提交前再次校验工作簿冻结、逐 SKU 余额、流水数量、销售基准数量与权威状态；全部
   通过后才把单例状态切换为 `DB_AUTHORITY` 并提交；
8. 精确重放返回原结果；同幂等键异快照、冻结失效或逻辑快照变化均冲突拒绝。

切换事务失败保持 `PRE_CUTOVER` 且不留部分余额。`DB_AUTHORITY` 后所有消费者若缺少某个
SKU 余额必须失败并要求维护，严禁回退工作簿。工作簿 `current_stock` 不再业务写入；新 SKU
保存商品资料时历史字段写 0，再以 `SKU_INITIALIZATION` 零变化流水建立零 DB 余额，并通过
独立“新花入库”流水增加库存。初始化只允许 DB 已成为权威且该 SKU 尚无余额时执行。
`initialize_sku()` 还必须通过固定商品工作簿回读证明 SKU 已存在；任意字符串不能产生孤儿
余额。正式 `create_authoritative_product_with_inbound()` 链把元数据写入、零余额初始化和
独立入库组合为可精确重放的业务入口；中途失败最多留下工作簿库存 0 或 DB 零余额。

## 5. 人工库存调整

正式输入仅为：`internal_sku`、有符号 `inventory_delta`、`source`、`reason`、
`expected_balance_version` 和 `idempotency_key`。默认来源和原因为“新花入库”。

- `NEW_FLOWER_INBOUND` 只允许 `delta > 0`；`LOSS_ADJUSTMENT` 只允许 `delta < 0`；
  `MANUAL_STOCKTAKE / RECONCILIATION_CORRECTION` 允许双向；零值拒绝；
- before/after 由服务端读取和计算，只作为预览/回读显示；
- after 小于 0 拒绝，不截断为 0；
- 版本不一致返回冲突，要求重新预览；
- actor 只取认证主体，不接受表单用户名；
- 精确重放返回既有流水，同幂等键异内容拒绝。

## 6. 销售事实写库存

只接受当前 `SKU` 汇总，统一计算：

```text
sales_delta = current_selected_sold_qty - previously_applied_sold_qty
inventory_delta = -sales_delta
```

准入固定为：

| 当前权威事实 | 处理 |
| --- | --- |
| 完整 CLOSED `ORDER_COMPLETE` | 允许正/负净差；订单替换估算或取消时可恢复 |
| 合格 `SCAN_ESTIMATED_HIGH` | 仅允许销量正差扣减；估算下降不恢复 |
| `ORDER_PARTIAL`、OPEN | 零写 |
| `SCAN_ESTIMATED_MEDIUM/LOW`、`UNAVAILABLE` | 零写 |
| 映射不唯一、版本错位、非当前汇总、非 SKU 范围 | 零写并报告原因 |

完整订单资格必须回读绑定的订单批次并再次验证 `CLOSED / SUCCEEDED / ACCEPTED / scope
complete / end marker / VERIFIED mapping`；高质量估算必须回读全部绑定 segment，并验证
`estimation_eligible=true`、映射版本一致和累计值匹配。

基准保存的是“已应用累计销量”，不是取消量。订单回补后仍只比较新旧累计销量；不得再把
`cancelled_qty` 单独加回。精确来源重放零变化，同来源身份异内容冲突。余额不足不得产生
负库存或推进基准。

切换水位以前的交易日即使之后收到完整历史订单，也只追加 `SALES_BASELINE_SYNC` 和更新
销售基准，不修改当前余额或余额版本。切换水位交易日只允许在“最新可信空 OPEN 订单
快照”成立时切换，因此 0 是可审计的起点；之后首次完整订单及其后续修订都只应用切换后
累计净差。若当前日已有 OPEN/PARTIAL 销售、订单批次不可信或无法证明为空，切换直接拒绝，
不能把未建立基准的事实留给后续流程从 0 重扣。

## 7. 结算流水线接入

首轮 20:00 结算和历史订单回补都在既有汇总完成后调用同一
`InventorySalesApplicationService`：

```text
Settlement / order backfill
→ 当前 SKU 汇总回读
→ 库存准入与累计差额
→ 余额 + 流水 + 基准原子写入
→ 库存预警评估
→ Sales Plan Input / 人工报告继续使用同一回读结果
```

`PRE_CUTOVER` 时明确返回未启用，不写库存；不能悄悄读取 Excel。库存应用失败使对应
Automation/回补链报告失败，并通过既有 Incident 入口记录，不新建平行错误状态。

## 8. 库存预警

策略默认关闭，避免在运营人员冻结阈值前发明业务数值。管理员可设置全局默认和每 SKU
覆盖：阈值 `0..9999`，重复提醒间隔 `30..1440` 分钟。

- 从阈值上方降到阈值或以下：创建/重开 `INVENTORY_ANOMALY` Incident 并进入 Outbox；
- 持续低库存：只在重复间隔到期且有新库存事务时再次通知；
- 并发首次越界可分别形成 Incident 检测事件，但使用 `Incident + 重复时间窗` 稳定通知键，
  同一时间窗 Outbox 只保留一条通知；
- 恢复到阈值上方：记录恢复并关闭 Incident；
- bootstrap 不触发预警；预警不创建平台下架或改价任务。

## 9. Web 与消费者切换

- 今日页、商品详情、数据库“商品与库存/库存调整流水”和销售计划回读 DB 余额；
- 业务管理提供人工调整表单，输入 delta/source/reason，显示服务端 before/after；
- TaskGeneration、ListingDecision、Pricing 输入在服务边界由统一 Provider 注入 DB 库存；
- `SET_ONLINE.target_inventory` 在 7E 创建/授权时不得超过真实库存；7D 先提供唯一校验服务；
- `products.xlsx.current_stock` 的旧编辑入口立即拒绝库存修改，7F 再删除旧页面代码。
- 旧 Web 的拒绝判断只读取固定 canonical Runtime DB，不接受 request/session Runtime 选择；
- 人工库存调整的业务失败统一 POST → 303 → GET，只在 URL 传 allowlist 错误码，不回显
  原始异常或用户输入。

## 10. 测试与验收

必须覆盖：新库 v17、v16→v17 重复迁移、迁移失败回滚、append-only、bootstrap 精确重放/
冲突/三表非空/freeze 失效/逻辑快照变化、Runtime V2 时间策略、可信空 OPEN 水位、
含销量的 OPEN/PARTIAL 历史后再出现空快照仍拒绝、历史回补、人工正负调整、
来源方向、并发版本、负库存、DB 故障回滚、准入矩阵全部分支、估算替换、取消负差、跨日/
错绑、流水线重放、阈值并发首次越界/重复/恢复、新 SKU 正式链、停售商品库存显示、Excel
切换后零写和平台副作用为 0。

Ready for review 前运行库存专项、受影响集成、完整 pytest、系统冒烟和 Linux/Windows CI；
真实 Runtime cutover 只能在备份、维护窗口、逐 SKU/总量回读和用户明确授权后执行。本合同
和代码本身不授权修改真实 Runtime DB、工作簿、Queue、Worker 或平台。
