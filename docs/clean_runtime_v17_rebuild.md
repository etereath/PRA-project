# 干净重建 Runtime Schema v17 与真实库存切换

## 1. 适用范围

本流程只适用于当前真实 Runtime DB 主要由测试时期数据构成、无需把历史任务和事件迁入
正式运行库的情况。旧库会完整归档用于追溯；新库只从现有正式商品工作簿接收 SKU、商品
资料和一次性库存余额，并继续复用现有 v17 Schema、订单空快照门禁和库存 bootstrap。

本流程不会修复或删除旧库中的记录，也不会把旧任务、订单、Incident、通知或执行日志复制
到新库。平台商品映射继续由工作簿保存，原有 `DISABLED / UNMAPPED / AMBIGUOUS /
VERIFIED` 状态不得在切换时自动改变。

## 2. 已查明的旧库孤立事件

当前旧库的 1 条外键违规不是正常 Automation 事务产生。2026-07-31 的一次历史验收制品
清理直接删除了 `T1354-ACCEPT-FULL` 对应的 Automation Run 和 Job，却没有先删除
`automation_run_events`，并且该直接 SQLite 连接没有启用 `PRAGMA foreign_keys=ON`。因此事件
`AUTO-EVENT-1205ba4fd0464ad08d629529081a42f6` 保留了对已删除 Run 的引用。

这属于测试清理缺陷。正式流程不推断平台或业务事实，不在旧库原地补写父记录，也不把该
孤立事件迁入新库；完整旧库归档仍保留追溯证据。

## 3. 固定复用边界

| 能力 | 处理方式 |
| --- | --- |
| Runtime Schema v17 | 原样调用 `SQLiteRuntimeRepository.init_schema()` 创建候选库 |
| 商品和初始库存 | 原样读取 `products.xlsx`；逐 SKU 与总量回读 |
| 平台映射 | 原样归档并校验 `platform_mappings.xlsx`，不改变状态 |
| 当前交易日门禁 | 原样复用最新、十分钟内、可信完整且为空的 `OPEN` 订单观察批次 |
| 库存切换 | 原样复用 `InventoryApplicationService.bootstrap()` |
| 订单读取 | 原样复用 `ORDER_SCAN → ORDER_HISTORY_IMPORT` 只读链 |
| 激活与回滚 | 新增最小编排；双逻辑快照哈希、固定确认文本、最终归档和回读 |

不得改写 v17 Schema、复制旧库历史事实或为本次切换新增平行库存服务。

## 4. 维护窗口前预览与准备

所有命令使用 PowerShell 7，且先设置 `PYTHONIOENCODING=utf-8`。以下变量必须替换为本机
固定路径；切换工作目录必须位于 Runtime DB 之外并且为空。

先运行只读预览。预览不会创建工作目录，会输出旧库逻辑快照、工作簿哈希、SKU 数、库存
合计、映射状态计数和旧库健康摘要：

```powershell
python scripts/clean_runtime_cutover.py prepare `
  --source-runtime-db $env:PRA_RUNTIME_DB `
  --products $env:PRA_PRODUCTS_WORKBOOK `
  --platform-mappings $env:PRA_PLATFORM_MAPPINGS_WORKBOOK `
  --workspace-dir D:\PRA_Runtime\cutover\runtime-v17-clean
```

人工核对后，把预览输出的三个哈希原样带入 `--apply`：

```powershell
python scripts/clean_runtime_cutover.py prepare `
  --source-runtime-db $env:PRA_RUNTIME_DB `
  --products $env:PRA_PRODUCTS_WORKBOOK `
  --platform-mappings $env:PRA_PLATFORM_MAPPINGS_WORKBOOK `
  --workspace-dir D:\PRA_Runtime\cutover\runtime-v17-clean `
  --expected-source-snapshot-sha256 sha256:<旧库逻辑快照> `
  --expected-products-sha256 sha256:<商品工作簿哈希> `
  --expected-platform-mappings-sha256 sha256:<映射工作簿哈希> `
  --apply
```

准备成功后，工作目录包含：

- 完整旧测试库 SQLite 归档；
- 商品和平台映射工作簿原样归档；
- 健康、空白、`PRE_CUTOVER` 的 v17 候选库；
- UTF-8 JSON 保留清单，列出正式 SKU、商品字段、初始库存和映射状态。

旧库在准备期间发生任何逻辑变化时，归档回读会失败并删除未完成工作目录。

## 5. 候选库真实只读链与库存 bootstrap

准备阶段不会伪造订单空快照。必须把 Automation Service、订单 Watchdog、Queue/Worker、
Result Importer 和 Archive 全部显式绑定到同一个候选 v17 Runtime DB，再完成一次真实页面
`READ_ONLY` 订单扫描。不得让某一进程仍写旧库。

切换窗口必须满足：

- 当前 PRA 交易日刚开始且尚无销售；
- 最新订单批次为 `OPEN`；
- 批次 `SUCCEEDED / ACCEPTED`，范围完整且尾部标记已验证；
- 批次为空，且当前交易日从未观察到订单；
- 扫描完成时间距 bootstrap 不超过十分钟；
- Watchdog → Worker → Importer → Archive 使用同一候选库并完整结束。

随后继续使用既有 `scripts/bootstrap_authoritative_inventory.py`，把 `PRA_RUNTIME_DB` 临时指向
候选库，传入候选库 bootstrap 前逻辑快照、商品工作簿哈希和真实订单观察批次 ID。不得在
测试中生成的空快照上执行真实切换。

bootstrap 完成后执行：

```powershell
python scripts/clean_runtime_cutover.py verify `
  --manifest D:\PRA_Runtime\cutover\runtime-v17-clean\clean-runtime-cutover-manifest.json `
  --products $env:PRA_PRODUCTS_WORKBOOK `
  --platform-mappings $env:PRA_PLATFORM_MAPPINGS_WORKBOOK
```

只有输出同时满足 Schema v17、`DB_AUTHORITY`、外键违规为 0、SKU 集合完全一致、逐 SKU
库存完全一致和库存合计一致，候选库才可进入激活预览。

## 6. 激活与回滚

激活前停止 Web、Automation、Queue Service、Importer、Watchdog 和任何可能写 Runtime DB
的 CLI；影刀 Worker 若仍长期运行，必须确保队列无活动请求和未导入结果。将
`PRA_RUNTIME_DB` 恢复为正式 canonical 路径后先运行不带 `--apply` 的激活预览。

正式激活还必须同时提供：

- 准备清单中的旧库逻辑快照；
- `verify` 输出的候选库逻辑快照；
- 固定确认文本 `REPLACE_TEST_RUNTIME_WITH_CLEAN_V17`；
- `--apply`。

脚本会再次归档当前旧库，把候选库复制为同盘暂存文件，回读逻辑快照后替换 canonical
Runtime DB，并再次核对健康、Schema、库存权威、逐 SKU 库存和总量。任一步失败时会在同一
调用中恢复被替换的旧库。

只有激活后尚未出现任何非 `BOOTSTRAP` 库存流水时，才允许使用激活记录执行紧急回滚。
回滚必须提供当前 v17 逻辑快照、固定确认文本
`ROLLBACK_TO_ARCHIVED_TEST_RUNTIME` 和 `--apply`。出现人工库存调整、销售扣减或其他新库存
流水后禁止回滚旧测试库，必须走新的备份/恢复决策。

## 7. 激活后验收

1. `PRAGMA integrity_check` 为 `ok`，`PRAGMA foreign_key_check` 为 0 条；
2. `/health` 返回 200，四个一级入口均可读取；
3. Web GET 前后主库逻辑内容不变；
4. SKU 集合、逐 SKU 库存和库存合计与冻结工作簿一致；
5. 平台映射状态与切换前工作簿一致；
6. Web、Automation、Queue、Worker、Importer/Watchdog 独立启动和停止；
7. 手机收到一次真实飞书测试通知；
8. 未取得新的 SKU/批次授权时，不执行任何真实平台写动作。

代码合并、临时目录演练和真实库激活是三个独立结论。没有用户对真实维护窗口的再次明确
授权，不得对 canonical Runtime DB 执行 `prepare --apply`、`activate --apply` 或回滚。
