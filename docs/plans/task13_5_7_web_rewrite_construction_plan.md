# 任务 13.5-7：运营 Web 实际业务重基线施工计划

- 计划日期：2026-08-12
- Review Profile：R4
- 当前状态：7A 计划与静态样板重基线；7B 生产代码尚未开始
- 基线：`origin/main` 当前仍为 `418c605`；旧 Draft PR #30 尚未合并
- 新分支：`codex/task13-5-7-operations-web-rebaseline`
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
| `/mobile/reviews/{token}` | 飞书手机复核 | Token 限定原子写 |

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

销量应用规则：

1. 只接受同一 PRA 交易日和商品的有效事实；
2. 完整订单事实优先，合格估算只在订单不可用时使用；
3. 记录已应用基准，重放只返回原结果；
4. 估算被订单替换时撤销旧基准并只应用净差；
5. 已验证取消恢复对应 `order_qty`；
6. 不完整、失败、日期错位和能力失败零写；
7. 余额不足进入 Review/Incident，不能静默截断或写负数。

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

### 6.4 固定 Automation 方案

只允许 allowlist 中的 Job 类型。配置至少包含启用状态、频率/时间、业务范围、阈值、版本、
修改人和生效时间。Scheduler 只读取已生效版本；Web 请求不持有长期租约或运行循环。

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
6. Mobile Review 有效/无效/过期/已处理只读状态。

门禁：默认 25 条服务端分页；系统状态不复制数据库历史；页面不出现购买次数、Agent 假
建议、完整度百分比或买家端价格；合成事实与真实 Runtime DB READ_ONLY 验收零写。

### 9.4 7D：数据库真实库存与预警

编码前先合并独立 R4 合同或在同一 PR 首个可审查提交冻结：Schema、余额/流水、销售基准、
取消恢复、并发、迁移、回滚和现有库存回填来源。

范围：

1. 最小不可变库存流水和权威余额；
2. 人工有符号调整，默认新花入库；
3. 订单/估算选择后的幂等差额扣减；
4. 订单事实替换估算、取消恢复和跨日隔离；
5. 真实库存/平台库存严格字段和 Presenter；
6. 阈值配置、越界提醒、重复提醒和恢复；
7. 数据库库存流水、今日库存和销售计划回读。

门禁：精确重放、同 ID 异内容、并发调整、负库存、部分事实、日期错位、数据库失败整体
回滚；不能为了满足预算把流水写入备注或现有无关字段。

### 9.5 7E：人工任务、执行授权、复核与 Automation 配置

范围：

1. 品种/等级/平台多选和有效商品展开；
2. 调整价格到、加/降价、下架、上架+平台目标库存；
3. 逐项预览、排除、最低成本、价格新鲜度、映射和冲突校验；
4. 创建 Task 与提交执行两个后台阶段；
5. `SUBMIT_EXECUTION` capability、批次重检、优先队列和既有 v4/v5 提交；
6. Review/Incident Web 与手机原子处置；
7. 固定 Automation 方案版本化配置和 READ_ONLY 补跑；
8. `start_shadowbot_reconcile`、`confirm_shadowbot_manual_handled` 迁到正式服务；
9. 删除 `save_listing_status` 直写投影；
10. 完成逐 CLI 正式归宿矩阵。

门禁：创建任务零平台副作用；普通 `PENDING` 不自动执行；提交执行只处理明确批次；预览后
价格/库存/映射变化必须拒绝；Queue/DB 失败整体回滚；人工复核优先于自动紧急任务；无新
平台动作类型或第二执行链。

### 9.6 7F：系统维护、切换删除与运营验收

范围：

1. Worker 检查并恢复、飞书测试、备份和显式维护 Service；
2. 完成权限隔离和管理员高级诊断；
3. 拆分 `start_local.ps1`，Web 重启不影响后台；
4. 新 Web 切换为唯一入口；
5. 删除旧页面、旧路由、旧 HTML 拼接、兼容层和重复测试；
6. 保留测试/Mock/验收/诊断/备份/恢复 CLI；
7. 桌面、手机、飞书、真实库 READ_ONLY 和单独授权的真实平台批次验收；
8. 更新实施报告、项目状态、索引和任务 14 交接。

真实平台写验收必须由用户明确商品和批次授权；7F 计划本身不授权 COMMIT。

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
| 库存 | 人工调整、差额扣减、估算替换、取消、重放、并发、负库存 |
| 任务 | 多选展开、delta 价格、最低成本、映射、冲突、创建零副作用 |
| 执行授权 | 批次重检、优先级、Queue/Importer、UNKNOWN/RECONCILE |
| Automation | 配置版本、租约、补跑、库存阈值、重复提醒和恢复 |
| 通知/手机 | 有效、失效、过期、已完成、重复点击、处理完毕反馈 |
| 系统 | Worker 恢复状态机、备份/回读、显式维护、Web 独立重启 |
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
