# Runtime Schema v18 商品与平台映射主数据合同

## 1. 结论

真实运营任务在运行时不得依赖 `products.xlsx` 或 `platform_mappings.xlsx`。两份工作簿只
允许在受控干净重建的准备阶段作为一次性输入，导入后由 Runtime DB 保存商品目录和平台
映射；Web、任务预览、任务创建、执行授权、日常任务生成、订单映射、ShadowBot 只读目标、
结果导入以及桌面/手机紧急复核均只回读同一个 Runtime DB。工作簿后续变化不会改变已经
启用的运行库。

本合同不把库存复制进商品目录。农场真实库存仍只由 v17 的 `inventory_balances` 和不可变
流水负责；平台库存仍是销售平台允许买家购买的上限。

## 2. v18 最小结构

- `product_catalog`：SKU、商品名、等级、规格、单位、基础成本、是否允许销售、备注、来源
  与版本；不保存真实库存或平台库存。
- `platform_product_mappings`：平台注册行和商品映射行，保留 `VERIFIED / UNMAPPED /
  AMBIGUOUS / DISABLED` 等既有状态、有效期和来源版本。
- 新增全局锁：0；新增平台动作：0；新增任务状态：0。

`RuntimeMasterDataRepository` 是运行时唯一读取入口。商品回读会联接
`inventory_balances`，因此页面看到的 `current_stock` 来自数据库库存权威，而不是商品目录
中的重复字段。预览和授权摘要绑定数据库逻辑快照；创建时在同一 SQLite 事务中重新展开，
主数据或库存发生变化时整批拒绝。

## 3. 一次性导入与切换

`clean_runtime_cutover.py prepare` 继续显式接收两份工作簿，原因是旧测试库没有完整的商品
目录和映射表。准备阶段会：

1. 校验 canonical 输入、文件哈希和旧库逻辑快照；
2. 原样归档旧库和两份输入工作簿；
3. 建立空白 v18 候选库；
4. 一次性写入商品目录和平台映射并保存数据库快照摘要；
5. 保持库存为 `PRE_CUTOVER`，等待真实空 `OPEN` 订单门禁和既有库存 bootstrap。

`verify` 与 `activate` 不再接收工作簿参数，只核对候选 Runtime DB 与 UTF-8 切换清单。这样
即使准备后工作簿被移动或 Excel 占用，也不会成为正式运行或激活时的隐藏依赖。

## 4. 控制面边界

- Web Composition Root 不接收商品或平台映射工作簿路径。
- `DAILY_TASK_GENERATION` 从数据库商品快照生成候选任务；商品快照在落库前发生变化时整批
  拒绝。
- `ORDER_SCAN` 从数据库编译平台映射；Queue、Executor 和 Importer 从数据库商品目录构造、
  校验只读目标，不再读取商品工作簿。
- 紧急保护影子判定和授权在数据库事务边界内二次读取基础成本快照。
- 人工创建任务只创建 Runtime Task；真实平台执行仍需单独的精确任务授权。
- ShadowBot 的 `product_identity_mapping.json` 是平台执行端唯一定位配置，不是业务 XLSX，
  继续由既有 v4/v5 合同和文件哈希门禁保护。
- 价格规则和上下架规则工作簿目前仍是规则输入，不是商品主数据；其数据库化属于后续独立
  维护项，不能阻塞人工 Web 任务。
- CLI 可以保留显式的一次性导入、测试和诊断入口，旧工作簿读取助手也只允许服务这些边界；
  它们不得成为 Web、Automation、Queue/Worker/Importer 或紧急保护的正式运行旁路。

## 5. 当前验收边界

本次代码只建立 v18 合同、候选库导入和运行时读取链；没有迁移、初始化或替换 canonical
真实 Runtime DB，也没有启动 Worker 或执行平台写操作。真实切换仍须新的维护窗口授权，
完成备份、真实空订单读取、库存确认、候选库 bootstrap、回读和可回滚激活后，才允许从
Web 创建真实验收任务；具体商品、动作和批次仍需另行授权。
