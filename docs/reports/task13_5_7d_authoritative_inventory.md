# 任务 13.5-7D：数据库真实库存实施报告

- 实施日期：2026-08-12
- Review Profile：`R4`
- 分支：`codex/task13-5-7d-authoritative-inventory`
- 基线：`ca1dce5`（7C PR #33 合并提交）
- 范围：真实库存唯一权威、人工调整、销售净差、取消恢复、库存预警与 Web 回读

## 1. 结论

7D 实现 Runtime Schema v17 和统一库存应用边界。数据库库存表示农场当前还有多少花可以
销售；平台库存只表示买家在特定平台最多可购买的数量，两者不再复用字段或相互覆盖。

本分支没有迁移、修复或 bootstrap 真实 Runtime DB，没有写真实工作簿、Queue、Worker 或
平台。真实切换继续等待独立维护窗口、备份/Hash/回读门禁和用户明确授权。

## 2. 复用矩阵

| 能力 | 分类 | 实现 |
| --- | --- | --- |
| `SalesFactSelectionService` | 原样复用 | 库存服务重新选择并验证当前 SKU 销售事实，不复制订单/估算算法 |
| 日结与历史回补 | 参数化复用 | 两条既有流水线在汇总回读后调用同一库存差额服务 |
| Incident / Outbox | 参数化复用 | 阈值越界、重复提醒和恢复沿用 `INVENTORY_ANOMALY`，不建新告警状态机 |
| Runtime SQLite 事务与健康检查 | 原样复用 | 余额、流水和销量基准使用同一 `BEGIN IMMEDIATE`；v17 纳入统一健康检查 |
| 商品工作簿与 SKU | 参数化复用 | 只作为 cutover 冻结输入和商品资料；切换后库存由 Provider 注入 |
| Inventory Provider/Application/Repository | 确需新增 | 最小余额、不可变流水、切换、调整、销量基准和预警策略边界 |

没有新增平台合同、平台动作、全局锁、Incident 状态、Agent Schema 或第二平台实现。

## 3. Schema v17 与唯一权威

v17 新增：

- `inventory_authority_state`：唯一 `REAL_INVENTORY`，只允许 `PRE_CUTOVER / DB_AUTHORITY`；
- `inventory_balances`：每 SKU 非负余额和并发版本；
- `inventory_transactions`：before、有符号 delta、after、来源、actor、交易日和证据；
- `inventory_sales_baselines`：平台 + PRA 交易日 + SKU 的已应用累计销量；
- `inventory_alert_policies`：全局默认或 SKU 覆盖阈值，默认关闭。

库存流水由触发器禁止 UPDATE/DELETE，并约束 `after = before + delta`、after 非负和幂等键
唯一。Schema 初始化只建立 `PRE_CUTOVER`，不会读取工作簿或自动切换权威。

`scripts/bootstrap_authoritative_inventory.py` 默认只读预览；`--apply` 只接受环境配置的
canonical 工作簿与 Runtime DB，同时要求工作簿 SHA-256、完整 SQLite 逻辑快照 SHA-256、
备份目录和 actor。脚本在工作簿独占锁内使用 SQLite Backup API；服务在
`BEGIN IMMEDIATE` 后重验包含 WAL 内容的逻辑快照，确认余额/流水/销售基准三表同时为空，
再从 Runtime 读取唯一有效的版本化运营时间策略，并要求调用方显式绑定据此得出的当前
交易日最新、十分钟内、范围完整、尾部已验证且订单数为 0 的可信 OPEN 订单批次。订单
批次 ID 与内容 Hash 写入每条 `BOOTSTRAP` 流水；当前交易日任一快照曾观察到订单，或批次
不完整/过期、策略或日期错位、不是最新批次、出现多个平台时，都会拒绝切换。提交前继续
完成逐 SKU、总量、流水、
销售水位和工作簿冻结回读。任一失败保持
`PRE_CUTOVER`。服务端精确重放会比较原始 BOOTSTRAP 流水、SKU 数量和请求 Hash；同键异
内容拒绝。

bootstrap 只在“可信空 OPEN 订单快照”能够证明当前交易日累计订单为 0 时切换；不再依据
OPEN/PARTIAL 销售汇总猜测水位。推荐维护窗口是在 18:00 交易日换日完成且首笔新交易尚未
出现时。切换日之后首次完整订单从可审计的 0 水位扣减；水位以前的历史回补只写
`SALES_BASELINE_SYNC`，不改变当前余额或余额版本。新增商品正式链先保存工作簿库存 0 的
元数据，验证商品确实存在，再用
`SKU_INITIALIZATION` 建立零 DB 余额并通过独立“新花入库”流水增加库存。孤儿 SKU 拒绝；
DB 已成为权威后，缺余额不回退 Excel。

## 4. 人工调整、销售净差与预警

新 Web `/management` 使用认证主体、CSRF、并发版本和一次性幂等键提交有符号调整值。
before/after 由服务端计算；来源限于新花入库、人工盘点、损耗和对账修正；零值、负余额、
过期版本、同键异内容和非权威状态均拒绝。数据库“库存调整流水”只读回看不可变记录。
新花入库只接受正数，损耗只接受负数；人工盘点和对账修正允许双向。

销售库存统一使用：

```text
销售差额 = 当前权威累计销量 - 既有已应用累计销量
库存变化 = -销售差额
```

完整 CLOSED 订单允许正负净差；`SCAN_ESTIMATED_HIGH` 只允许销量增加时扣减；中低质量、
OPEN/部分订单和不可用事实零写。完整订单替换估算和取消均只应用累计销量净差，不额外应用
`cancelled_qty`。日期、平台、SKU、映射版本和支撑输入继续由既有选择服务验证。

库存阈值默认关闭；启用后首次越界、到期重复和恢复复用 Incident/Outbox。它不产生改价、
下架或任何平台写任务。并发首次越界使用同一 Incident 时间窗通知键去重，不会生成两条
首次 Outbox。预警发送失败不回滚已经提交的库存事实，但会在结果中明确报告。

## 5. 消费者与 Web

- TaskGeneration、业务规则评估和 Listing 输入由统一 Provider 注入真实库存；
- `SET_ONLINE.target_inventory` 不得超过真实库存；
- 20:00 结算和历史订单回补调用同一库存差额服务；
- 销售计划输入 v3 保存带版本和来源流水的真实库存快照；
- 今日页、商品详情、数据库商品/库存流水和业务管理人工调整回读 DB；
- 平台价格表单独展示“平台可购上限”，不把它命名为真实库存；
- 旧 Web 在 `DB_AUTHORITY` 后拒绝工作簿补库存和库存字段修改。
- 旧 Web 的拒绝门禁固定读取 canonical Runtime，不再信任 request/session Runtime；
- 新 Web 库存失败路径也使用 PRG 和 allowlist 错误码，URL 不包含异常正文或用户输入。

7D 不实现 7E 的任务创建、真实平台执行授权、Review POST 或 Automation 配置。

## 6. 测试与回滚

已覆盖 v17 新库/升级/健康检查/append-only、bootstrap 重放、三表非空、freeze/逻辑快照
冲突、Runtime V2 时间策略、可信空 OPEN 水位、曾有订单而最新快照为空时仍拒绝切换、
切换后首次完整订单
只扣切换后销量、历史订单回补零余额影响、订单替换估算、新 SKU 元数据→零余额
→入库与孤儿拒绝、人工正负调整、来源方向、版本冲突、负库存、完整订单扣减、取消恢复、
估算回落、中低质量/不可用/跨日零写、数据库失败整体回滚、阈值并发首次越界/重复/恢复、
Web CSRF/成功与失败 PRG、canonical 旧 Web 门禁和切换后 Excel 零写。

本地验收结果：

- 本轮库存切换水位、结算、脚本与直接受影响 Web 定向回归：`58 passed`；
- 完整 pytest：`1229 passed, 3 skipped, 97 subtests passed`；
- 隔离系统冒烟：`16 passed, 0 failed`，确认 Schema exact v17；
- wheel/sdist 构建：成功；严格包边界、secret scan、wheel 隔离安装、Windows fixture：通过；
- 静态编译与 `git diff --check`：通过。

长 worktree 路径下首轮完整测试有 4 个既有 ShadowBot 队列用例因 Windows 路径长度创建
临时文件失败；另有一个旧 Web 用例因未声明 canonical 临时 Runtime 而正确触发新门禁，已
补齐测试隔离。随后把相同 worktree 临时映射为短盘符完成整套全绿回归，映射已解除。未修改
队列代码来规避环境证据。

Windows/Linux CI 结论以 Draft PR 检查为准。

未执行真实 cutover 时可直接撤销本 PR。真实切换后若尚无任何后续库存流水，只能在受控
备份/回读门禁下整体恢复；已有后续流水时必须前向修复，不得让过期工作簿恢复为权威。
