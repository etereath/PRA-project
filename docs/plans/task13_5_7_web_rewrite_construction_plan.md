# 任务 13.5-7：运营 Web 实际业务重基线施工计划

- 计划日期：2026-08-12
- Review Profile：R4
- 当前状态：7A、7B、7C 已分别由 PR #31、#32、#33 合并；7D 在独立分支实现与评审
- 7D 基线：`origin/main` 合并提交 `ca1dce5`
- 7D 分支：`codex/task13-5-7d-authoritative-inventory`
- 恢复检查点：`checkpoint/pre-task13-5-7-web-rewrite-20260807`
- 产品权威：[运营 Web 实际业务重基线计划](task13_5_web_rewrite_plan.md)
- 宏观权威：[GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)

## 1. 替代关系与施工目标

本计划完整取代 Draft PR #30 中按八个一级入口编排的施工路线。PR #30 保留为历史记录，
不能继续作为编码批准依据。新的开发目标是一个直接服务家庭农场日常运营的 Web，而不是
旧 Web 页面、CLI 或数据库表的浏览器复制品。

完成后必须同时满足：

1. 桌面和手机统一使用“今日、数据库、业务管理、系统”四个一级入口；
2. 旧八入口中不产生独立价值的页面、Route、Presenter 和测试全部删除；
3. 读取、人工操作、定时业务、未来 Agent、平台执行和开发恢复分别走唯一正式通道；
4. 任务创建、人工复核、真实库存、自动化配置和受控平台执行在 Web 内形成日常闭环；
5. Task、Review、Incident、Automation、Settlement、Queue、Worker、Importer 和唯一
   RECONCILE 继续复用，不为 Web 建平行状态机；
6. Web 重启不影响 Automation、Queue Service、Worker 或 Importer；
7. 普通 GET 对 Runtime DB、工作簿、Queue 和平台保持零写；
8. 旧 Web 不兼容保留，必要外部协议保持连续；
9. 当前任务不提前实现 Agent、第二平台、买家端价格、复购采集或花材质量录入。

## 2. 开工门禁

7B 编码前必须完成：

- 新重基线 PR 评审并合并，旧 PR #30 标记为 superseded 后关闭；
- Issue #20、`AGENTS.md`、实施总计划、Web 计划、项目状态和文档索引同步为四入口；
- 三份静态样板和新增系统样板通过桌面/手机视觉评审；
- 当前代码与目标页面建立“原样复用 / 参数化复用 / 抽取公共能力 / 确需新增”矩阵；
- 真实库存、自动销售扣减、库存预警和 Automation 可配置性在各自编码 PR 前完成 R4
  合同与最小 Schema 评审；
- 记录准确的合并后 main SHA，不从旧 PR 分支直接开始生产代码；
- 确认当前真实 Runtime DB 已知完整性问题只作为维护事项，不授权本任务顺手修复。

若任一 PR 发现需要第二套任务状态机、第二条平台执行链、Agent Schema、第二平台适配或
绕过既有写锁/Importer 的路径，立即停工回到设计评审。

## 3. 当前工程基线

### 3.1 旧 Web 已确认问题

- `app/web.py` 同时承担路由、HTML、Presenter、数据读取和写操作，继续拆补会保留旧耦合；
- 请求可以通过 query/Session 选择 `runtime_db`，与单一 Composition Root 冲突；
- 真实 Runtime DB 的只读 GET 会调用 `init_schema()`，已知 1 条外键违规会令页面返回
  500；实测文件时间和大小未变化，但这不授权推断或修复数据来源；
- 当前仓库没有 CSP、`X-Content-Type-Options`、frame 和 Referrer Policy 等统一安全
  Header；7B 必须新增并测试，不能写成“保留现有”；
- 本地 HTTP 若沿用生产 Secure Cookie，登录不会形成可用会话；
- `scripts/start_local.ps1` 随 Web 启停 Queue Service，违反后台组件独立生命周期；
- 旧 POST 包含 `start_shadowbot_reconcile`、`confirm_shadowbot_manual_handled` 和
  `save_listing_status`；前两项只能迁到正式恢复/人工处置，后者直接写投影必须删除；
- tasks/reviews/notifications Repository 缺少 limit/offset，旧 Web 先全量加载再内存分页；
  execution logs 只有 limit 无 offset；
- `BusinessRuleRunner` 与 `generate_runtime_tasks_from_sources` 是两条不同历史链，不能用
  “每日任务生成”一个名称宣称全部迁移。

### 3.2 可直接复用的后台资产

| 能力 | 复用结论 |
| --- | --- |
| Runtime Task、来源、历史 | 原样复用；增加窄查询和人工编排调用方 |
| Review、Incident、Outbox | 原样复用原子处置与通知反馈 |
| Automation Job/Run/Event、租约、父子 Run | 原样复用；配置能力单独扩展 |
| Product/Order Observation、Mapping | 原样复用事实和质量；不补采购买序号 |
| Settlement、Sales Estimate、Plan Input | 原样复用交易日、版本、质量和输入 manifest |
| v4/v5 Task、Queue、Worker、Importer | 原样复用唯一平台执行链 |
| 写锁、UNKNOWN、RECONCILE | 原样复用安全状态和恢复入口 |
| 飞书、Mobile Review | 保持外部协议，重写 Presenter 与状态页 |
| 备份、诊断、Worker 生命周期脚本 | 由受控维护 Service 调用，不复制到 Route |
| 四类业务输入 Service | 复用校验/应用/持久化，不提供通用工作簿编辑 |

### 3.3 静态样板

当前 7A 样板位于 `docs/prototypes/`：

- `task13_5_7_operations_web_sample.html`：今日；
- `task13_5_7_database_sample.html`：数据库与销售分析；
- `task13_5_7_business_management_sample.html`：创建任务、复核、方案和库存；
- `task13_5_7_system_sample.html`：状态、通知、数据维护和权限边界。

样板永久保持 `DEMO`、零写和无真实数据连接；它们是交互与信息架构合同，不是生产模板
源。生产模板必须使用 Read Model，不能复制样板的示例经营值。

## 4. 信息架构与路由

### 4.1 一级和二级路由

| 路由 | 页面 | 写权限 |
| --- | --- | --- |
| `/` | 303 到 `/today` | 无 |
| `/today` | 今日 | 只读 |
| `/database` | 数据库默认业务数据 | 只读 |
| `/database/project` | 项目运行数据 | 只读 |
| `/database/sales-analysis` | 确定性销售分析与未来 Agent 展示位置 | 只读 |
| `/database/dictionary` | 字段说明 | 只读 |
| `/database/quality` | 质量与新鲜度 | 只读 |
| `/management` | 业务管理默认创建任务 | 受控写 |
| `/management/reviews` | 人工复核 | 受控写 |
| `/management/automation` | 固定 Automation 方案 | 受控写 |
| `/system` | 当前运行状态 | 管理员只读/受控维护 |
| `/system/notifications` | 通知通路测试 | 管理员受控写 |
| `/system/data` | Runtime DB 与备份 | 管理员受控维护 |
| `/system/diagnostics` | 高级诊断 | 管理员只读 |
| `/health` | 外部健康协议 | 只读 |
| `/login` | 登录 | 认证写 |
| `/logout` | 退出 | POST |
| GET `/mobile/review/{review_task_id}?token=...` | 飞书手机复核 | Token 限定只读 |
| POST `/mobile/review/{review_task_id}/resolve` | 提交复核结果 | Token 限定原子写 |

详情统一使用一个稳定页面：商品/销售/结算/Run/执行事实属于数据库，当前任务/Review/方案
属于业务管理，系统故障属于系统。URL 可携带 PRA 交易日、品种、等级、平台、商品、状态和
来源筛选，不携带 Runtime DB、工作簿、Queue 路径或凭证。

### 4.2 通知抽屉

四入口共享通知抽屉，角标计算未解决 Review/Incident/系统影响，不新增“未读”业务状态。
阅读消息不改变业务状态；完整通知历史跳数据库。业务通知跳业务管理，数据质量跳数据库，
组件故障跳系统。

## 5. 页面—事实—能力对齐矩阵

| 页面能力 | 权威事实/服务 | 复用方式 | 本任务缺口 |
| --- | --- | --- | --- |
| 今日销量/金额/均价 | OrderObservation、SalesFactSelection、Settlement | 原样事实 + Read Model | 首页窄查询和质量 Presenter |
| 当前数据库库存 | 产品主数据、未来库存账本 | 现有主数据起点 | 余额、流水、销售差额扣减 |
| 品种/等级/时段 | OrderObservation、TradeDay Summary | 纯查询组合 | 服务端聚合和分页 |
| 今日待办 | Review、Incident、Mapping、Health | 原样事实 | 中文统一 Presenter |
| 今日时间轴 | Automation Event、Settlement、Task、Execution | 纯查询组合 | 跨来源有界查询 |
| 业务数据集 | Observation、Mapping、Settlement、Inventory | 原样事实 | 统一分页、字段定义 |
| 项目数据集 | Task、Review、Run、Incident、Execution、Outbox | 原样事实 | Repository limit/offset |
| 销售分析 | Settlement、Order、Estimate | 纯计算/查询 | 独立 Read Model；Agent 内容未来 |
| 即时创建任务 | Task、Mapping、Rules | 复用 Task Service | 人工范围编排和逐项预览 |
| 加/降价 | 最新价格、UPDATE_PRICE | 参数化复用 | 服务端绝对价转换和冲突校验 |
| 上架平台库存 | SET_ONLINE target inventory | 原样复用字段 | 与真实库存语义/上限校验 |
| 提交执行 | 既有 Queue/Worker/Importer | 原样执行链 | 独立授权、批次重检和 Web 调用方 |
| 人工复核 | Review/Incident 原子服务 | 原样复用 | Web/手机 Presenter 与 PRG |
| Automation 管理 | Job/Run/Scheduler | 原样运行时 | 固定方案版本化有限配置 |
| 库存预警 | 真实库存、Incident/Outbox | 复用提醒链 | 阈值配置、越界/恢复状态 |
| Worker 恢复 | 生命周期记录、heartbeat、队列事实 | 参数化封装现有脚本 | 受控维护 Service |
| 运行状态 | Health、heartbeat、租约、备份状态 | 组合查询 | 状态聚合，不复制历史 |
| 权限 | 现有认证会话 | 保留认证 | 集中 capability 授权 |

### 5.1 当前页面禁止承诺

- 购买次数、复购订单或复购买家率；
- 买家客户端实时售价；
- 花材质量评价；
- Agent 预测或建议；
- 第二平台和多平台库存分配；
- 佣金、退款、曝光或外部市场指数；
- 无算法依据的数据完整度百分比；
- 任意脚本/Cron、SQL 或 Queue 编辑器。

这些能力只在文档记录未来位置。页面不放空按钮、假值或禁用占位。

## 6. 核心业务合同

### 6.1 数据库真实库存

7D 编码前冻结最小库存合同。至少表达：

- `inventory_before`、有符号 `inventory_delta`、`inventory_after`；
- 来源、原因、操作人、发生/记录时间；
- 人工入库、人工修正、销售扣减、取消恢复、估算替换和对账修正；
- `seller_operation_date`、支持观察/订单/取消引用、幂等键；
- 当前余额与不可变流水同事务；
- 不允许负库存；并发版本变化时拒绝并要求重新预览。

人工表单默认来源/原因为“新花入库”，只输入商品、调整值、来源和原因。调整前/后只作为
服务端回读提示。现有 v14 若不能表达不可变库存流水和已应用销量基准，允许最小 Schema
迁移；不得把该事实塞进备注、Review 文本或平台库存字段。

#### 6.1.1 唯一权威切换

切换合同固定为：

```text
7D cutover 前
products.xlsx.current_stock = 历史业务库存来源

7D cutover 时
products.xlsx.current_stock 的冻结快照
→ 每个 internal_sku 仅一次 bootstrap
→ DB 期初余额 + 不可变 BOOTSTRAP 流水
→ 校验每 SKU、总量、幂等键和回读

7D cutover 后
DB inventory balance / ledger = 唯一真实库存权威
products.xlsx.current_stock = 只读历史快照，不再参与业务判断或写入
```

切换必须在受控维护窗口执行：先备份工作簿与 Runtime DB，冻结产品写入，验证 SKU 唯一、
数量非负且目标 DB 尚未 bootstrap，再以确定性幂等键写入每 SKU 期初流水。全部回读一致后
才切换库存 Provider；任一项失败整体保持切换前权威，不能留下部分 SKU 已切、部分仍读
Excel 的状态。`product_inventory_input.py` 必须拆开商品资料/成本/是否销售与库存调整：
补货、盘点、损耗和对账只调用 DB Inventory Application Service，不再修改工作簿库存。
新 SKU 先建立商品资料和零 DB 余额，再以独立、可重放的“新花入库”事务增加库存；第二步
失败不得把工作簿 `current_stock` 当补偿权威。

TaskGeneration、ListingDecision、`SET_ONLINE.target_inventory` 上限、库存预警、今日页和
销售计划统一依赖同一库存 Provider/Service。切换后禁止 Excel/DB 双写；普通代码回滚也
不能把已经过期的工作簿库存恢复成业务权威。只有在尚无任何切换后流水时，管理员才可在
备份/回读门禁下整体恢复切换前的工作簿与 DB；已有切换后流水时只能前向修复。

#### 6.1.2 销售事实写库存准入

数据库可展示的事实不自动等于允许改变库存。首版固定矩阵为：

| SalesFactSelection 结果 | 自动库存写入 | 规则 |
| --- | --- | --- |
| `ORDER_COMPLETE` | 允许正/负净差 | 必须是目标范围最新完整 `CLOSED` 批次，SKU 映射 `VERIFIED` 且版本一致 |
| `ORDER_PARTIAL` / `OPEN` | 禁止 | 只展示和进入对账，不以部分订单补扣库存 |
| `SCAN_ESTIMATED_HIGH` | 只允许正向销量差额扣减 | 仅在没有可接受订单事实、segment `estimation_eligible=true` 且证据/映射完整时；估算减少不得自动加回库存 |
| `SCAN_ESTIMATED_MEDIUM` | 禁止 | 可展示或进入销售计划的降权输入，不能写真实库存 |
| `SCAN_ESTIMATED_LOW` | 禁止 | 只作方向性附注 |
| `UNAVAILABLE` | 禁止 | 不伪造零销量或零库存变化 |
| 完整订单替换已应用估算 | 允许净差 | 先核对同 SKU/交易日的已应用基准，只应用订单累计销量与既有基准之差 |
| 取消导致完整订单累计销量下降 | 允许负净差恢复 | 恢复来自新权威累计销量的负差，不单独应用 `cancelled_qty` |

所有允许写入的范围仍必须属于同一 PRA 交易日和商品；记录选择来源、质量、映射版本、
支撑输入和已应用累计销量。统一公式为：

```text
inventory_sales_delta =
  canonical_selected_sold_qty - previously_applied_sold_qty

inventory_delta = -inventory_sales_delta
```

重放只返回原结果；同 ID 异内容冲突拒绝。`cancelled_qty` 只解释相邻完整快照的多重集合
减少，正式销量始终来自当前所选完整 CLOSED 快照，因此不得再额外
`inventory += cancelled_qty`。不完整、失败、日期错位、能力失败、映射不唯一和余额不足
均零写并进入 Review/Incident；不能静默截断或写负库存。

### 6.2 平台库存

平台库存是特定平台买家可购上限。当前单平台的 SET_ONLINE 目标库存不得超过数据库真实
库存；平台观察库存不覆盖真实库存。平台库存变化只有形成权威销售事实后才影响真实库存。

### 6.3 人工任务和执行授权

人工编排服务接收结构化范围与动作，不接收任意 Task JSON。服务端展开有效商品，逐项检查
映射、价格、基础成本、库存、平台状态、冲突和任务来源。预览返回版本化事实引用；创建时
再次检查。

创建 Task 与执行授权分离：

- 创建阶段只持久化 Task/必要 Review，不写 Queue；
- 执行阶段要求 `SUBMIT_EXECUTION` capability、二次确认和最新事实重检；
- 只把本批明确任务交给既有提交服务，不扫描全部 `PENDING`；
- 人工复核产生的改价/下架和紧急任务进入既有高优先级编排；
- 任一 Task 失效、冲突或进入 UNKNOWN 时整批预检停止；
- Queue/Importer 失败沿用现有恢复和唯一 RECONCILE。

`SUBMIT_EXECUTION` 必须由薄的 Execution Authorization Application Service 强制，不是
Route 内的 `if capability`。服务固定为两个调用：

```text
prepare_execution(
  authenticated_principal,
  exact_task_ids,
  idempotency_key
)
→ latest-fact revalidation
→ existing v4 prepare / v5 propose
→ confirmation_digest + exact manifest + expires_at

submit_execution(
  authenticated_principal,
  exact_task_ids,
  confirmation_digest,
  idempotency_key
)
→ capability + identity + digest + latest-fact revalidation
→ existing publisher
```

两次调用都从认证上下文取得 principal 并检查 `SUBMIT_EXECUTION`；`confirmed_by` 只能由
该 principal 派生，表单中的 actor/用户名一律不可信。digest 必须绑定排序后的精确
`task_ids`、逐项动作/目标值、来源 Task 版本和重检事实版本，不能表达“执行所有 pending”。
提交前必须重新验证 Task 仍为可执行状态，以及价格、Mapping、基础成本、真实库存、
Review、优先级、共享写锁、活动 Automation UI 租约、`UNKNOWN` 和唯一 RECONCILE；任一
变化使整批 digest 失效并要求重新预览。

Route 只解析请求、调用上述 Service 并 PRG，不得直接调用 Queue、Runner 或拼 manifest。
Service 只编排既有 v4/v5 prepare/propose/publish，不新增 Operation、Attempt、授权、写锁或
审批状态机。CLI/验收/恢复保留的 publisher 入口必须标为管理员或受控验收边界；日常运营
CLI 必须调用同一授权 Service，不能成为绕过 `SUBMIT_EXECUTION` 的旁路。

### 6.4 固定 Automation 方案

只允许 allowlist 中的 Job 类型。配置至少包含启用状态、允许的频率/offset、业务范围、
阈值、版本、修改人和生效时间。Scheduler 只读取已生效版本；Web 请求不持有长期租约或
运行循环。13.5-3 已冻结 `job_id + schedule` 静态身份，因此频率变化必须创建确定性的新版
Job 并在同一配置切换中停用前版，不能原地改 schedule，也不能依赖
`ensure_default_automation_jobs()` 覆盖运营配置。

首版可配置矩阵固定为：

| Job/能力 | 默认 | Web 允许修改 | 安全范围与禁止项 |
| --- | --- | --- | --- |
| `ONLINE_PULSE` | 10 分钟 | 启停、间隔 | 10～30 分钟且为 5 的倍数；平台和“仅上架中”范围固定 |
| `FULL_MARKET_SCAN` | 60 分钟、`:10` 对齐 | 启停、间隔 | 60～180 分钟且为 30 的倍数；分钟 offset 固定为 10，父子范围固定 |
| `PRE_CUTOFF_FULL_SCAN` | 18:00 前 5 分钟 | 启停 | 绝对时间和 -5 分钟 offset 只读，从 `OperationalTimePolicy` 派生 |
| `POST_CUTOFF_PULSE` | 18:00 后 5 分钟 | 启停 | 绝对时间和 +5 分钟 offset 只读，从 `OperationalTimePolicy` 派生 |
| `PLATFORM_TRADE_DAY_SETTLEMENT` | 卖家 20:00 cutoff | 启停、明确交易日幂等补跑 | 不允许编辑绝对时间；只从时间策略计算目标交易日 |
| `SALES_PLAN_INPUT_BUILD` | Settlement 后 5 分钟 | 启停、后置 offset | 5～30 分钟；必须依赖同交易日 Settlement，不接受绝对时间 |
| `LISTING_STATUS_SCAN` / `ORDER_SCAN` | `CHILD_ONLY` | 无 | 不显示独立 schedule/启停；只继承合法父 Run |
| Review 超时维护 | 薄 Handler | 启停、扫描间隔 | 5～30 分钟且为 5 的倍数；Review deadline/Token TTL 只读，不能由 Job 配置改写 |
| 每日任务生成 | 薄 Handler | 启停、Plan Input 后置 offset、来源 allowlist | offset 0～30 分钟；只在同作业日 Plan Input 成功后运行，不接受任意绝对时间/脚本 |
| 真实库存预警 | 库存事务后事件驱动 | 启停、默认阈值、每 SKU 覆盖、重复提醒间隔 | 阈值为 0～9999 的整数；提醒间隔 30～1440 分钟；不开放 Cron，也不创建平台动作 |

若未来修改 18:00/20:00 本身，只能新增并生效版本化 `OperationalTimePolicy`，由所有相关
Job 一起派生，不能在 Automation 页面单改一个 Job。任何超出上述范围、增加 Job 类型、
开放任意 Cron/脚本或改变父子关系的需求都必须另开 R4。

库存预警使用真实库存，支持默认阈值和商品覆盖。第一次从阈值上方降到阈值或以下产生
提醒，持续低库存使用既有重复提醒，恢复到阈值上方解除；不直接创建平台下架动作。

### 6.5 未来 Agent 数据

买家端实时售价窗口只在文档预留，默认关闭的 UI 控件也等 Agent/采集合同落地后再加入。
“第 N 次购买”虽然真实页面可见，但当前 Worker、Importer 和 v14 未保存；保持现状，Agent
阶段再做合同、指纹影响、迁移和 READ_ONLY 验收。每日花材质量“好/中/差”未来从业务管理
录入、数据库保存、销售分析展示，当前不建入口或 Schema。

## 7. 目标应用结构

继续使用 Python WSGI 和服务器端模板，不引入 React/Vue、Node 构建链或新的前后端 API
体系。建议目录：

```text
app/
  operations_web/
    __init__.py
    app.py                 # WSGI app 与中央分发
    composition.py         # 启动时固定依赖和路径
    auth.py                # 登录、Session、capability
    security.py            # CSRF、Header、PRG、错误边界
    presenters/
    read_models/
    routes/
      today.py
      database.py
      management.py
      system.py
      mobile_review.py
    templates/
    static/
```

Route 只做解析、认证/权限、调用 Query/Application Service、Presenter 和响应。业务规则、
Repository、Scheduler、Queue 和外部进程生命周期不得进入模板或 Route。

Composition Root 在启动时固定 Runtime DB、工作簿、Queue、环境和 Service；请求不得覆盖。
Web 启动不调用 Queue Service，`start_local.ps1` 必须拆分后台生命周期。

## 8. 安全和零写

### 8.1 环境与 Cookie

- development：必须显式 `PRA_ENV=development`，本地 HTTP，非 Secure Cookie；
- production：必须 HTTPS 和 Secure Cookie；
- 配置冲突时启动失败并给中文原因；
- Session 使用 HttpOnly、SameSite 和固定生命周期；登录后轮换 Session；退出为 POST；
- 7B 新增 CSP、`X-Content-Type-Options`、frame 和 Referrer Policy，并做测试。

### 8.2 GET 零写

每个 GET 测试前后比较：

- Runtime DB 内容哈希、mtime、size；
- 工作簿内容哈希和 mtime；
- Queue inbox/working/results/archive 清单；
- Outbox、Task、Review、Incident、Run 和审计记录数；
- 平台写动作计数为 0。

认证读取、健康检查和错误页也必须满足。GET 不调用 `init_schema()`；启动时只验证 Schema，
迁移由显式维护命令执行。

### 8.3 写操作

所有 POST 要求认证、capability、CSRF、幂等、版本冲突检查、事务、整体回滚和 PRG。高风险
动作二次确认。数据库异常、Queue 失败或服务失败不能留下半个 Task、库存变动、Review 或
Outbox。

## 9. 顺序施工 PR

### 9.1 7A：重基线计划与样板（本 PR）

范围：

- 四入口 HTML 样板和公共导航；
- 新系统状态样板；
- 本计划、产品计划、Issue #20、AGENTS、总计划、项目状态和文档索引；
- 页面能力分类、未来 Agent 数据边界和新复杂度预算；
- 不修改生产代码、不迁移 Schema、不连接真实 Runtime DB。

验收：UTF-8 回读、HTML/JS 静态检查、链接检查、桌面/手机内置浏览器视觉复核。无需主动
运行完整 pytest；仓库自动 CI 若触发则按实际结果处理。

### 9.2 7B：应用骨架、安全、权限与零写

范围：

1. 创建 `app/operations_web`、Composition Root、错误边界和本地静态资源；
2. 固定 Runtime DB/工作簿/Queue 依赖，删除 request 级 DB 选择；
3. development/production、Cookie、CSRF、安全 Header 和 capability；
4. `/health`、登录/退出和 Mobile Review 协议外壳；
5. GET 永不 init/migrate；已知真实库问题显示维护提示；
6. Web 与 Queue/Worker/Automation 启停解耦；
7. 只使用合成 Runtime DB，不接业务 POST。

门禁：所有 GET 零写、HTTP/HTTPS Cookie 冲突启动失败、权限后端拒绝、资源无 CDN、Windows
打包和 Linux 路径测试通过。

### 9.3 7C：四入口只读事实与详情

范围：

1. Repository 增加 limit/offset 和窄查询；
2. 今日、数据库、销售分析、系统运行状态 Read Model/Presenter；
3. 商品、销售、结算、Task、Review、Run、Execution 的唯一详情页；
4. 通知抽屉和跨页筛选上下文；
5. 空、可信零、不完整、过期、不可用、失败和权限不足状态；
6. Mobile Review 有效/无效/过期/撤销/已处理只读状态；
7. 运营时间策略读取失败、策略为空或无唯一有效版本时，不回退代码默认值，也不查询猜测的
   当前交易日；显式历史交易日和不依赖交易日的数据集继续可读；
8. 经营事实只展示来源更新时间，在来源节奏和新鲜度策略另行冻结前不自创固定分钟 TTL；
9. 普通结算目录只查询当前权威版本，superseded 版本只在稳定详情入口中展示并标记；
10. 今日“当前可售库存”只汇总允许销售的商品，停售商品库存不计入可售总量。

门禁：默认 25 条服务端分页；系统状态不复制数据库历史；页面不出现购买次数、Agent 假
建议、完整度百分比或买家端价格；Mobile Review GET 的有效/已处理为 200、过期/撤销为
410、未知或错绑为统一 404，7E POST 继续复用既有原子写入错误映射；合成事实与真实
Runtime DB READ_ONLY 验收零写。

### 9.4 7D：数据库真实库存与预警

编码前先合并独立 R4 合同或在同一 PR 首个可审查提交冻结：Schema、余额/流水、销售基准、
取消恢复、并发、迁移、回滚和现有库存回填来源；合同必须逐项实现 6.1 的 Excel→DB
唯一权威切换和销售事实写入准入矩阵，不得在编码时重新选择权威。

范围：

1. 最小不可变库存流水和权威余额；
2. 工作簿库存冻结、逐 SKU 幂等 bootstrap、回读和单一 Provider 切换；
3. `product_inventory_input.py` 拆分商品资料与 DB 库存调整，删除 Excel 库存业务写入；
4. 人工有符号调整，默认新花入库；
5. 订单/估算选择后的幂等差额扣减；
6. 订单事实替换估算、取消净差恢复和跨日隔离；
7. 真实库存/平台库存严格字段和 Presenter；
8. 阈值配置、越界提醒、重复提醒和恢复；
9. 数据库库存流水、今日库存和销售计划回读。

门禁：精确重放、同 ID 异内容、并发调整、负库存、部分事实、日期错位、数据库失败整体
回滚；逐项覆盖 6.1.2 的全部质量、估算替换和取消负差分支；证明切换后 Excel 不再写、
所有消费者只读 DB，且 rollback 不恢复过期工作簿权威。不能为了满足预算把流水写入备注
或现有无关字段。

### 9.5 7E：人工任务、执行授权、复核与 Automation 配置

实施状态（2026-08-13）：已按
[7E 控制面合同](task13_5_7e_control_plane_contract.md) 完成编码；验收事实和真实副作用边界见
[7E 实施报告](../reports/task13_5_7e_control_plane.md)。

范围：

1. 品种/等级/平台多选和有效商品展开；
2. 调整价格到、加/降价、下架、上架+平台目标库存；
3. 逐项预览、排除、最低成本、价格新鲜度、映射和冲突校验；
4. 创建 Task 与提交执行两个后台阶段；
5. 6.3 的 Execution Authorization Application Service、`SUBMIT_EXECUTION`、精确 task IDs、
   digest、批次重检、优先队列和既有 v4/v5 提交；
6. Review/Incident Web 与手机原子处置；
7. 按 6.4 逐 Job allowlist/上下限实现版本化配置和 READ_ONLY 补跑；
8. `start_shadowbot_reconcile`、`confirm_shadowbot_manual_handled` 迁到正式服务；
9. 删除 `save_listing_status` 直写投影；
10. 完成逐 CLI 正式归宿矩阵。

门禁：创建任务零平台副作用；普通 `PENDING` 不自动执行；提交执行只处理明确批次；预览后
价格/库存/映射变化必须拒绝；伪造 form actor、digest 重放/换批、Route 直调 publisher 和
CLI 日常旁路全部拒绝；关键 Job 时间不能独立漂移，child job 不可编辑；Queue/DB 失败整体
回滚；人工复核优先于自动紧急任务；无新平台动作类型或第二执行链。

### 9.6 7F：系统维护、切换删除与运营验收

范围：

1. Worker 检查并恢复、飞书测试、备份和显式类型化 Maintenance Service；
2. 完成权限隔离和管理员高级诊断；
3. 复核 7B 已拆分的 `start_local.ps1` 和独立后台入口，证明 Web 重启不影响后台；
4. 新 Web 切换为唯一入口；
5. 删除旧页面、旧路由、旧 HTML 拼接、兼容层和重复测试；
6. 保留测试/Mock/验收/诊断/备份/恢复 CLI；
7. 桌面、手机、飞书、真实库 READ_ONLY 和单独授权的真实平台批次验收；
8. 更新实施报告、项目状态、索引和任务 14 交接。

真实平台写验收必须由用户明确商品和批次授权；7F 计划本身不授权 COMMIT。
Route 不得接受脚本名、路径或参数，不得直接 `subprocess.run(...)` 或同步等待长耗时动作；
页面只提交类型化意图并查询既有 heartbeat、生命周期、备份 manifest 或维护状态。没有可
复用异步承载的动作必须停工另开窄 R4，不能顺手建设通用脚本/后台任务 Runner。

实施状态（2026-08-13）：7F 代码、唯一 Web 切换删除、真实库 GET 零写、桌面/手机、本地
完整回归和制品门禁已完成，详见
[7F 实施与验收报告](../reports/task13_5_7f_cutover_acceptance.md)。真实飞书仍等待独立通知
后台具有新鲜心跳后验收；真实平台写仍等待用户另行明确商品和批次授权；GitHub
Linux/Windows CI 由本阶段 Draft PR 执行。

## 10. CLI 正式归宿矩阵

| 当前入口 | 正式归宿 | CLI 保留 |
| --- | --- | --- |
| `preview-tasks` | 业务管理任务预览 | 测试 |
| `generate-runtime-tasks` | Automation + 业务管理受控补跑 | 测试/管理员恢复 |
| `list-tasks`、`show-task-history` | 数据库/业务管理详情 | 诊断 |
| `list-review-tasks`、`resolve-review-task` | 业务管理/Mobile Review | 隔离测试/恢复 |
| `expire-review-tasks --apply` | Automation | 管理员修复 |
| `notification-worker` | 独立通知服务 | 启动/诊断 |
| `serve-web` | 新 Web | 启动 |
| `init-runtime-db`、`health` | 显式维护/系统状态 | 管理员 CLI |
| `templates`、`validate`、`import-data` | 数据维护 | 保留 |
| `generate-tasks` | 旧 Excel 候选链 | 归档或明确拒绝 |
| `mock-ai-decision`、`simulate-execution` | 测试 | 隔离保留 |
| `list-manual-tasks`、`resolve-manual-task` | 旧人工链 | 删除或明确拒绝 |

7E 前必须另列 `scripts/evaluate_business_rules.py` 四类 evaluator：listing 是否被每日任务生成
覆盖；capacity、cold_storage、platform_sync 分别明确延期、诊断保留或废弃。包装/冷库 ERP
不因 Web 迁移扩大到 13.5-7。

## 11. 测试矩阵

| 层级 | 核心覆盖 |
| --- | --- |
| 编码/静态 | UTF-8、HTML、JS、模板、链接、无外部资源 |
| Query/Presenter | OPEN/CLOSED、质量六级、可信零、部分/失败、分页边界 |
| 安全 | 认证、capability、CSRF、Header、Cookie 环境冲突、路径注入 |
| GET 零写 | DB/工作簿/Queue/Outbox/事实表前后比较 |
| 库存 | Excel→DB bootstrap/单一权威、准入质量矩阵、人工调整、差额扣减、估算替换、取消净差、重放、并发、负库存 |
| 任务 | 多选展开、delta 价格、最低成本、映射、冲突、创建零副作用 |
| 执行授权 | principal、防伪 form actor、精确 task IDs/digest、双重检、优先级、Queue/Importer、UNKNOWN/RECONCILE |
| Automation | 逐 Job 字段/上下限、版本替换、时间策略派生、child 禁配、租约、补跑、库存阈值、重复提醒和恢复 |
| 通知/手机 | 有效、失效、过期、已完成、重复点击、处理完毕反馈 |
| 系统 | Worker 恢复状态机、备份/回读、类型化维护、Route 无脚本/无长等待、Web 独立重启 |
| 视觉 | 桌面、手机、横向表格、弹窗焦点、通知抽屉、中文错误 |
| 真实只读 | 固定 Runtime DB、零写、已知违规只报告、平台副作用为 0 |
| 真实写 | 仅独立授权批次，完整 Queue→Worker→Importer→Archive |

开发 PR 只运行专项和直接依赖测试；Ready for review 前运行受影响集成、完整 pytest、系统
冒烟和 Linux/Windows CI。7A 文档/样板 PR 不主动运行完整 pytest。

## 12. 复杂度预算与停工条件

### 12.1 固定零扩张

- 新平台动作类型：0；
- 新任务/Review/Incident 状态机：0；
- 新平台执行链：0；
- 第二平台：0；
- Agent Schema/队列/自主动作：0；
- 买家端价格、购买序号、花材质量的当前采集：0；
- 任意脚本、Cron、SQL 或 Queue 编辑器：0；
- 旧 Web 兼容层：0。

### 12.2 允许但必须先合同证明

- 真实库存余额/流水和已应用销量基准所需的最小 Schema；
- 固定 Automation 方案版本与库存阈值所需的最小配置结构；
- Repository 后端分页方法；
- 人工任务编排、执行授权、状态聚合和权限 Service；
- 新模板、Presenter、Read Model 和本地静态资源。

### 12.3 立即停工重审

- 直接写平台或 Queue 的 Web Route；
- 请求级 Runtime DB/工作簿/Queue 路径；
- 普通 GET 初始化、迁移或修复；
- 以平台库存覆盖真实库存；
- 订单事实与扫描估算重复扣库存；
- 扫描全部 `PENDING` 自动执行普通任务；
- 为页面方便复制 Task、Review、Incident、Automation 或库存状态；
- 把未来 Agent 数据用假值、推断或临时 JSON 提前上线；
- Web 启停控制 Worker/Queue Service 生命周期；
- 新 Schema 没有迁移、回读、回滚和旧数据来源证明。

## 13. 每个实现 PR 的固定说明

每个 7B～7F PR 必须列出：

1. 对应页面与实际运营场景；
2. 原样复用、参数化复用、公共抽取和新增矩阵；
3. 权威输入、输出、事务和副作用；
4. capability、CSRF、幂等、冲突和回滚；
5. GET 零写或 POST 受控写证据；
6. 数据库、工作簿、Queue、平台和外部通知影响；
7. 测试、Windows/Linux、桌面/手机与真实验收；
8. 删除的旧代码和仍保留的恢复点；
9. 未实现的未来能力；
10. 文档、项目状态和索引更新。

## 14. 完工定义

- 四个一级入口均服务真实业务且没有重复控制面；
- 今日、数据库、业务管理、系统之间的通知、详情和返回路径闭环；
- 销售事实、PRA 交易日、质量、真实/平台库存语义一致；
- 人工入库、销售扣减、取消恢复和库存预警使用同一权威库存；
- 创建任务与执行授权可连续操作但后台可分别审计；
- 普通任务不会无人值守写平台；紧急动作继续遵守 13.5-6；
- Web、Automation、Queue、Worker、Importer 独立运行；
- 账号权限可扩展，系统维护受独立能力保护；
- 旧 Web 页面、兼容路由、直写投影和重复实现已删除；
- 测试 CLI 和恢复检查点保留；
- 真实 Runtime DB GET 零写、真实平台批次按单独授权验收；
- Issue、AGENTS、计划、样板、实现报告、项目状态和索引结论一致；
- 任务 14 接收的是已验收的单 Web 业务闭环，而不是未完成的 UI 清单。
