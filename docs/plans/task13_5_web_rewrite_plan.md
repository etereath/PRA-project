# 任务 13.5-7：运营 Web 替代重写与 CLI 残留业务迁移计划

- 决策日期：2026-08-07
- 当前状态：编码前计划评审
- Review Profile：R4
- 真实平台写操作：否；新 Web 只展示既有平台写事实，不提供普通 COMMIT 入口
- 宏观业务权威：[GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)
- 当前恢复检查点：`checkpoint/pre-task13-5-7-web-rewrite-20260807`
- 检查点提交：`418c605f4ab4434eee422eb0217de3cfe64b01b0`
- 现状证据：[Web 现状独立审计快照](task13_5_web_current_state_audit_20260729.md)

## 1. 本轮重新决策

当前项目尚未正式投入运营，不需要维持新旧两套 Web，也没有旧路由、旧页面或旧登录会话的
生产兼容义务。13.5-7 从本计划起直接承担：

1. 以 Issue #20 冻结的八个一级入口建设唯一运营 Web；
2. 放弃当前 `app/web.py` 的页面、路由分发、HTML 拼接和开发者信息架构；
3. 保留并复用后期已经完善的任务、自动化、执行、日结、Incident 和通知控制面；
4. 将 CLI 中残留的日常正式业务职责迁移到 Web、Automation 或 Queue 正式流程；
5. 保留开发测试、合成 fixture、Mock、验收、诊断、备份和紧急恢复 CLI；
6. 新 Web 验收后删除旧 Web，不建立长期兼容层或双 Web 回退。

原计划中“先统一所有 Web、CLI、脚本和 Automation 入口，再开始 Web”的前置方式作废。
当前缺少的是一个面向运营者的统一人工入口，不是重新设计全部后台控制面。

Issue #20 已于 2026-08-08 同步为当前 13.5-7 路线，原 13.5-8、13.5-9 的 Web 架构拆分
和 UI 重写内容已并入 7B～7F；双时间轴、八个一级入口、扫描父子关系、S0–S4、唯一
RECONCILE、唯一 `FINAL` 和任务 14 边界继续保持不变。

### 1.1 项目级 Agent 唯一通道预留

未来 Agent 是与 Web、Automation 并列的业务调用方，不是 Web 用户，也不是 CLI 包装器。
本任务只冻结接口边界，不实现 Agent，不增加 Schema、状态、队列或平台动作：

```text
Agent 读取：Agent Query Adapter → 权威 Query Service / Read Model
Agent 发起：Agent Task Adapter → 结构化 AgentIntent → 既有权威服务与确定性规则
服务决定：拒绝 / Review / Runtime Task / Outbox
执行链路：有效 Runtime Task → 既有 v4/v5 Queue → Worker → Importer
```

- Agent 不抓取 Web HTML，不直接读取 SQLite、Excel 或本地文件作为经营事实；
- Agent 不调用 CLI、平台 Adapter、ShadowBot，不直接拼 Queue JSON 或发起 COMMIT；
- Agent 只有 `AgentIntent` 一个写入口，不能直接写 Review、通知或 Runtime Task；
- `AgentIntent` / `AgentProposal` 只是逻辑载荷，不是本任务批准的数据库表；未形成 Task
  或 Review 的建议直接返回，不为 proposal 概念预建持久化；
- Agent 来源的 `UPDATE_PRICE`、`SET_ONLINE`、`SET_OFFLINE` 必须先经人工 Review 和
  显式授权，不得直接成为可执行 `PENDING`；
- Agent 永远不能伪造 `SYSTEM_EMERGENCY`，该来源只属于 13.5-6 既有专用授权服务；
- 未来正式来源应使用 `origin_type=AGENT` 与
  `origin_ref_id=agent-run:<stable-run-id>`，并记录版本化审批策略；
- 当前 Schema 尚不支持 `AGENT`。13.5-7 不得把 Agent 冒充为 `MANUAL` 或
  `AUTOMATION`；未来接入必须用独立评审和必要的最小 Schema 迁移完成。

这里的“Task Application Service”只是现有 `RuntimeTaskService`、任务生成、规则校验及
其他权威 Application/Domain Service 的逻辑统称，不授权新增万能服务。任何实际 Agent
接入都是未来独立 R4，不属于 13.5-7B～7F，也不属于任务 14。

该约束同时写入根级 `AGENTS.md`、项目总实施计划、项目当前状态和文档索引，后续开发
不得另建 Agent 直连数据库、Web、CLI、队列或平台的平行路径。

## 2. 为什么选择替代重写

现有 Web 是早期 MVP 持续追加形成的单文件应用。独立审计时 `app/web.py` 已同时承担：

- WSGI 分发；
- 登录、Session 和 CSRF；
- 表单解析；
- Repository 创建；
- 业务 Service 调用；
- HTML 拼接；
- 运营展示；
- 高级技术诊断。

当前页面按数据库表、开发阶段和脚本入口组织，不能满足运营人员快速判断“发生了什么、
影响什么、下一步做什么”。继续在原文件内调整页面，会同时承担旧布局、旧路由和旧调用
方式的兼容成本。

任务 11 至 13.5-6 已经形成更成熟的后台权威模块。新 Web 可以直接围绕这些模块建设，
不需要先把所有历史入口改造成同一种形式。

## 3. 唯一控制面职责

### 3.1 人工运营控制面：新 Web

新 Web 负责：

- 展示今日运营、平台商品、销售、自动化、待处理、任务、业务资料和系统状态；
- 提交人工任务、Review、Incident 处置和允许的只读补跑；
- 展示执行结果、数据质量、新鲜度和下一步；
- 调用现有权威 Application Service；
- 不自行实现任务状态机、写锁、Importer、RECONCILE 或平台 Adapter。

### 3.2 自动业务控制面：Automation

Automation Service 继续负责：

- 到期计划、租约、合并、补跑和运行事件；
- `ONLINE_PULSE`、`FULL_MARKET_SCAN`、订单观察、日结和规则评估；
- Incident 检测和已经冻结的紧急保护编排；
- 不直接点击平台，不替代 Queue Service 或 Importer。

### 3.3 平台执行控制面：Queue、Worker、Importer

继续原样复用：

- v4/v5 正式动作合同；
- Queue 原子发布；
- ShadowBot 长驻 Worker；
- Operation、Attempt 和共享写锁；
- Result Importer、Watchdog、ACK 和归档；
- `UNKNOWN →` 唯一只读 `RECONCILE`。

### 3.4 未来智能调用控制面：Agent Gateway

Agent Gateway 只负责把未来 Agent 的结构化查询和任务意图适配到权威 Query/Application
Service。它与 Web、Automation 共享领域服务，但不共享 Web Session，不复用 Mobile
Review Token，也不形成第二套任务状态机、授权服务或执行队列。Agent 只提交
`AgentIntent`；Review、Runtime Task 和 Outbox/通知由既有确定性服务派生。当前阶段只
冻结边界，不实现该模块。

### 3.5 系统维护和开发控制面：CLI

CLI 只承担：

- 开发测试和合成 fixture；
- Mock 平台；
- 系统冒烟、集成和实机验收；
- 初始化、健康检查、部署、备份和诊断；
- 明确授权的紧急恢复；
- 启动 Web、Automation、Queue 等常驻进程。

CLI 不再作为日常任务生成、复核处理、运营查询或计划维护的唯一入口。

五类控制面长期固定为：人工操作走 Web，定时业务走 Automation，智能调用走 Agent
Gateway，平台执行走 Queue/Worker/Importer，开发测试与恢复走 CLI。它们共享权威应用
服务，不互相抓取界面或绕过服务层。

## 4. CLI 残留业务迁移矩阵

迁移的是正式调用责任，不把 CLI 内的代码复制到 Web。新调用方必须复用当前 Service。

| 当前主 CLI | 目标归属 | 处理结论 |
| --- | --- | --- |
| `preview-tasks` | 新 Web 任务中心 | 迁移为候选任务预览；CLI 保留测试模式 |
| `generate-runtime-tasks` | Automation + 新 Web 受控人工补跑 | 日常生成迁出；CLI 仅测试/管理员恢复 |
| `list-tasks` | 新 Web 任务中心 | 运营查询迁出；CLI 保留诊断查询 |
| `show-task-history` | 新 Web 任务详情 | 运营查询迁出；CLI 保留诊断查询 |
| `list-review-tasks` | 新 Web 待处理 | 运营查询迁出；CLI 保留诊断查询 |
| `resolve-review-task` | 新 Web/手机复核 | 日常处理迁出；CLI 仅隔离测试或紧急恢复 |
| `expire-review-tasks --apply` | Automation | 定时维护迁出；CLI 保留显式管理员修复 |
| `notification-worker` | Queue/独立通知服务 | 保留进程启动与诊断，不作为人工业务操作 |
| `serve-web` | 新 Web | 保留进程启动入口 |
| `init-runtime-db`、`health` | 管理员 CLI | 原样保留 |
| `templates`、`validate`、`import-data` | 开发/数据维护 | 保留 |
| `generate-tasks` | 旧 Excel 链路 | 归档候选，不进入新 Web 正式流程 |
| `mock-ai-decision`、`simulate-execution` | 测试 | 隔离保留 |
| `list-manual-tasks`、`resolve-manual-task` | 旧 Excel 人工链路 | 删除或保持明确拒绝 |

`scripts/` 下的验收、证据导出、故障注入、部署同步和 Mock 工具不因为本任务统一改造。
只有仍被日常运营依赖的正式业务步骤，才需要迁移到 Web、Automation 或 Queue。

## 5. 测试 CLI 保留合同

开发过程中必须能够不经过浏览器独立验证模块。测试 CLI 应支持：

- 单独调用业务规则、Presenter 输入和 Application Service；
- 使用合成 fixture 和临时 Runtime DB；
- 使用 Mock Adapter 验证任务、Review、Incident、日结和 Automation Handler；
- 运行单步、集成、冒烟和回归测试；
- 输出结构化诊断结果；
- 在独立授权下调用既有真实页面验收链路。

测试隔离要求：

- 普通测试命令不得默认连接真实 Runtime DB 或真实 Queue；
- Mock/Test 命令不得通过环境变量静默切换为生产平台写操作；
- 合成数据不得导入正式经营事实；
- 真实 READ_ONLY/COMMIT 验收继续使用现有授权、合同、写锁和 Importer；
- 测试命令名称和帮助文本明确显示 `test`、`mock`、`acceptance` 或 `admin` 属性。

## 6. 技术架构

继续使用 Python WSGI 和服务器端渲染，不在本任务引入 React、Vue、Node 构建链或新的
前后端 API 体系。建议新目录为：

```text
app/
  operations_web/
    application.py
    composition_root.py
    auth.py
    csrf.py
    routing.py
    routes/
      today.py
      platform_products.py
      sales.py
      automation.py
      work_queue.py
      tasks.py
      business_inputs.py
      system.py
    presenters/
    templates/
    static/
```

边界固定为：

- `composition_root.py` 在进程启动时只绑定一个受信 Runtime Repository、配置和权威
  Service；request、query、form 和 Session 均不得选择或覆盖 Runtime DB；
- Route 只解析 HTTP 输入、授权、调用 Service/Presenter 并返回响应；
- Presenter/ViewModel 只转换运营显示语义，不修改状态；
- Template 默认转义，不包含业务状态转换；
- Static 资源必须随 wheel 打包并通过隔离安装验证；
- Web 请求线程不得运行 ShadowBot、长期脚本或轮询 Worker；
- Web 不直接拼 Queue JSON，不调用平台 Adapter，不直接写业务 SQL。

### 6.1 13.5-7A 冻结的安全属性

现有认证代码可以抽取和参数化复用，但下列安全属性是新 Web 的验收合同，不以旧路由或
旧 HTML 为兼容对象：

- 登录表单在认证前也必须校验 CSRF；登录路由可改名，但不能删除该保护；
- 登录失败按用户和来源做有界速率限制，阈值与窗口配置化并接受测试；
- 登录成功必须轮换 Session 标识；Session 有明确 TTL，Cookie 至少启用
  `HttpOnly`、`SameSite=Lax`，生产 HTTPS 启用 `Secure`；
- 登出只接受带 CSRF 的 `POST`；其他方法返回 `405` 且零状态变化；
- 所有基于 Session 的写请求必须校验 CSRF，并采用 Post/Redirect/Get 防止重复提交；
- 所有响应保留已验证的安全 Header，模板默认转义，业务文本不得用未审查的 raw HTML；
- Mobile Review Token 与后台 Session 是两个独立凭据域，不得相互兑换或复用；
- 审计和错误页不得展示 secret、raw token、完整 webhook、买家 PII 或敏感本机路径。

### 6.2 Presenter 权威输入矩阵

Presenter 只把权威事实翻译成简明中文、数据质量和下一步，不重算领域状态或重新解释
业务合同：

| 页面领域 | 唯一权威输入 |
| --- | --- |
| 今日运营 | Automation、Incident、Settlement 的 Query Service / Read Model |
| 平台商品 | ProductObservation 与正式 listing status 投影 |
| 销售分析 | Settlement、OrderObservation、SalesEstimate 的只读查询 |
| 系统健康 | 正式 heartbeat、Queue、Importer、Outbox 健康事实 |

Route 或 Presenter 不得从原始表自行推导 `FINAL`、Incident 严重度、任务可执行性、平台
在线状态或 Worker 健康。缺少正式 Query 时，只允许在既有 Service/Repository 上增加窄的
只读查询，不允许复制一套判断逻辑。

## 7. 不兼容切换策略

当前项目没有正式用户，因此新 Web 不保留：

- 旧导航；
- 旧页面布局和 HTML；
- 旧 query-string 详情方式；
- `/runtime` 等历史开发入口；
- 旧页面之间的兼容跳转；
- 旧 Session；
- 旧浏览器书签；
- 旧 Web 测试中只用于证明 HTML 结构相同的快照。

切换时允许改变内部业务页面 URL，但 `/health` 和 Mobile Review 是外部协议，不属于旧
Web 页面兼容成本：

- `/health` 保持稳定、无认证、无副作用，并继续作为公网部署和通知发送门禁；
- 推荐保留 `/mobile/review/{token}`。如果必须改变路径，必须保持 Token、过期、幂等、
  原子处置和错误语义，并完成显式版本化迁移；
- 先让新 `/health` 通过，再验收新 Mobile Review 路由和 Token 合同，然后切换
  `MOBILE_REVIEW_BASE_URL` 与链接生成器；
- 已发出的旧 Token/Outbox 要么由旧端点服务到结束，要么显式作废、重新签发并补发；
- 确认没有活动通知仍引用旧端点后，才可删除旧应用路由；
- 随机临时公网域名不得用于真实通知。

这是外部协议的受控切换，不要求保留旧导航、页面布局或普通业务路由。

旧 Web 不作为运行时回退。紧急恢复固定使用 Git Tag：

```text
checkpoint/pre-task13-5-7-web-rewrite-20260807
```

新 Web 验收完成后删除旧 Route、Renderer、样式和无价值测试。回退通过 Git/部署恢复，
不通过长期保留两套生产应用。

## 8. 八个一级入口

一级导航保持 Issue #20 的业务定义：

1. **今日运营**
2. **平台商品**
3. **销售分析**
4. **自动化**
5. **待处理**
6. **任务中心**
7. **业务资料**
8. **系统维护**

### 8.1 今日运营

首屏按优先级显示：

1. S4/CRITICAL、S3/HIGH 和即将超时待办；
2. 平台交易日、卖家作业日、当前阶段和 18:00/20:00 倒计时；
3. Automation、Worker、Queue 和通知健康；
4. 最近扫描、数据新鲜度和完整性；
5. 销量、成交金额、事实来源和日结状态；
6. 在线商品、库存变化和最近执行结果。

每个状态必须回答：当前结果、数据截止时间、影响范围、是否完整和下一步。

### 8.2 平台商品

- 当前在线与待上架；
- `ONLINE_PULSE` 和完整双页位置事实的区别；
- 商品映射、价格、库存、观察时间和轨迹；
- 关联 Incident、Review、写锁、任务和执行结果；
- 普通运营视图不展示 hash、phase 或原始 JSON。

### 8.3 销售分析

- 按平台交易日、品种、等级和 SKU 汇总；
- `ORDER_OBSERVED` 与 `SCAN_ESTIMATED` 明确分层；
- 时间桶、高峰份额和 18:00—20:00 早期销售；
- 数据质量、日结状态和 supersedes 版本；
- `OPEN`、可信空页、不完整和不可用不得混为 0。

### 8.4 自动化

- 计划、是否启用、下次运行和最近结果；
- `ONLINE_PULSE`、`FULL_MARKET_SCAN`、订单观察、日结和规则评估；
- 父子 Run、失败原因、数据新鲜度和 Incident；
- 允许受控只读补跑；
- 不提供扫描全部 pending 或绕过授权的普通平台写按钮。

### 8.5 待处理

- Review 与 Incident 分开保存、统一呈现；
- 登录、映射、页面、通知、UNKNOWN/RECONCILE 和低质量数据；
- 显示严重度、影响、首次/最近发生、次数、负责人和建议动作；
- 人工表单使用结构化业务动作，不要求编辑 JSON。

### 8.6 任务中心

- 人工、自动、系统紧急和全部任务；
- 候选、已授权、执行中、结果待确认和历史；
- 任务预览、人工生成和受控补跑；
- 详情按业务摘要、状态时间线、复核、执行结果和高级诊断分层。

### 8.7 业务资料

- 商品资料、库存、平台映射、价格规则和上下架规则；
- 日常表单与高级批量维护分层；
- URL 不携带本地文件路径；
- 不提前扩建生产、包装和冷库 ERP。

### 8.8 系统维护

- 服务、Worker、Queue、通知、数据库、备份和配置健康；
- 执行批次、逐项结果、UNKNOWN/RECONCILE 和高级诊断；
- 测试工具有明确环境和副作用标识；
- 原始合同、ID、hash、phase 和 JSON 按需加载。

## 9. 后端复用矩阵

| Web 能力 | 权威模块 | 处理方式 |
| --- | --- | --- |
| 任务生成与状态 | `RuntimeTaskService`、既有 evaluator | 原样或参数化复用 |
| Review | `ReviewTaskService`、Mobile Review Token | 原样复用 |
| Automation | Automation Repository/Service/Run/Event | 原样复用 |
| 商品观察 | ProductObservation、正式 listing sync Importer | 原样复用 |
| 订单观察 | OrderObservation Importer | 只读复用 |
| 销售日结 | TradeDaySettlement、SalesPlanInput | 只读/受控应用服务复用 |
| Incident | Incident Application Service | 原样复用 |
| 商品与库存录入 | `product_inventory_input.py` | Route 调用既有 validate/apply，再由既有工作簿持久化 |
| 平台映射录入 | `platform_mapping_input.py` | Route 调用既有 validate/apply，再由既有工作簿持久化 |
| 价格规则录入 | `price_rule_input.py` | Route 调用既有 validate/apply，再由既有工作簿持久化 |
| 上下架规则录入 | `listing_rule_input.py` | Route 调用既有 validate/apply，再由既有工作簿持久化 |
| 平台写操作 | v4/v5、Operation/Attempt/Lock、Queue/Importer | 只显示既有事实，不从 Web 发起普通 COMMIT |
| UNKNOWN | 唯一 RECONCILE | 不新增恢复路径 |
| 通知 | Outbox、通知 Worker | 原样复用 |
| 运营显示 | 新 Presenter/ViewModel | 确需新增，仅显示语义 |

不得新增一个包揽所有领域的“统一控制面 Service”。新 Web 通过 Composition Root 组装现有
领域服务，避免把稳定模块重新耦合到一起。

业务资料 Route 不得直接调用通用 `save_table_records`，也不得重新实现 SKU、库存、
`base_cost`、平台映射、价格规则或上下架规则校验。现有服务缺少 Web 参数时只做最小
参数化；工作簿仍是该类主数据的权威持久化边界。

普通 v4/v5 `COMMIT`、发布 pending、改价、上架、下架均不在新 Web 提供按钮或 Route
调用。13.5-6 已有 `SYSTEM_EMERGENCY` 自动链不受影响；Web 只展示 Incident/Review、
接收既有人工处置，不能手工触发或伪造系统紧急来源。

### 9.1 任务来源最小参数化

现有 `task_generation.py` 多个创建分支把来源固定为 `AUTOMATION`，构造入口只接收
`origin_ref_id`。13.5-7D 只在承担相关职责的既有权威服务上补充最小来源参数，不复制
任务生成器，也不新增名为 `TaskApplicationService` 的万能服务：

| 调用来源 | `origin_type` | `origin_ref_id` |
| --- | --- | --- |
| Web 人工生成 | `MANUAL` | `web:<stable-request-or-run-ref>` |
| Automation 日常生成 | `AUTOMATION` | `automation-run:<run_id>` |
| Incident 人工处置 | `MANUAL` | `incident-review:<review_id>` |
| 未来 Agent | 预留 `AGENT`，本任务不落库 | `agent-run:<stable-run-id>` |

`SYSTEM_EMERGENCY` 继续只能由 13.5-6 专用授权服务创建；Web、Automation 普通 Handler、
CLI 和未来 Agent 均不能传入该来源。不得为来源对齐新增表、状态或通用万能 Service。
未来 Agent 的真实平台写任务仍必须先经人工 Review；本来源预留不构成 Agent 实现授权。

## 10. 实施工作包

### 13.5-7A：计划、恢复点与页面合同

- 关闭旧控制面收口 PR；
- 推送当前 `main` 恢复 Tag；
- 冻结八入口、CLI 迁移矩阵、R4 配置、安全属性、Presenter 输入、外部协议、Agent
  Gateway 预留和复杂度预算；
- 不修改生产代码。

### 13.5-7B：新应用骨架

- 建立 `operations_web/`、路由、模板、静态资源和 Composition Root；
- 抽取登录、Session、CSRF 和安全 Header，并逐项证明 6.1 的属性；
- 建立设计 token、导航、错误页和基础 ViewModel；
- 完成源码与 wheel 安装测试。

### 13.5-7C：只读运营页面

- 今日运营、平台商品、销售分析、自动化和系统健康；
- 列表分页、筛选、独立详情和按需高级诊断；
- Presenter 只使用 6.2 的权威 Query/Read Model；
- 先完成只读事实链，不迁移真实平台写按钮。

### 13.5-7D：人工流程与 CLI 残留迁移

- 待处理、任务中心和业务资料；
- 任务预览/生成、Review、Incident 处置和允许的只读补跑；
- 业务资料 Route 复用四个现有输入服务，不直接保存通用表格；
- 任务创建按 9.1 参数化来源，不开放普通平台 COMMIT；
- 在既有 Automation 框架注册两个薄 Handler：复核超时 Handler 只调用
  `ReviewTaskService` 的既有 expire 能力并保留 `emergency_protection` 例外、原子
  task/review/outbox/时间策略；每日自动生成 Handler 只调用既有 task/rule service，
  使用 `AUTOMATION + automation-run:<run_id>`；
- 两个 Handler 复用 Automation Job/Run/Event、租约和错误语义，不新增 Scheduler、
  Task 系统或万能 Service；
- CLI 正式业务入口降级，测试/管理员入口保留。

### 13.5-7E：切换与删除

- 新 Web 接管正式启动入口；
- 按“新 `/health` → 新 Mobile Review/Token 验收 → 切换 Base URL/链接生成器 →
  消化或作废补发旧 Token/Outbox → 删除旧端点”的顺序切换外部协议；
- 删除旧 Web Route、Renderer、样式和只服务旧结构的测试；
- 更新 README、运行手册和项目状态；
- 不保留旧路由兼容层。

### 13.5-7F：运营验收

- 桌面与手机浏览器完整走查；
- 完成日常运营脚本；
- 验证 CLI 不再是日常业务必需入口；
- 输出实施报告、页面地图、操作手册和已知限制。

### 13.5-7 实现 PR 门禁

7B—7F 是工作包，不允许重新合成一个大爆炸 PR。至少拆为以下可独立回滚、顺序评审的
小 PR，后一项只有在前一项通过审查后才能开始：

1. 新应用骨架、安全属性与只读页面；
2. 人工写流程、业务输入复用与 CLI 日常职责迁移；
3. 正式入口切换、外部协议迁移和旧 Web 删除；
4. 桌面/手机/安装包/完整回归及最终运营验收。

## 11. 复杂度预算与非目标

预算：

```text
新增数据库表：0
新增数据库字段：0
新增任务/Review/Incident 状态：0
新增全局锁或租约：0
新增 Queue/Worker/Importer：0
新增平台合同版本：0
新增真实平台动作：0
新增旧 Web 兼容路由：0
```

允许新增：

- 新 Web package、模板、静态资源和 Presenter/ViewModel；
- 为既有 Service 增加 Web 需要的只读查询或参数化入口；
- 删除旧 Web 和已迁移的正式 CLI 业务入口；
- 测试 CLI 的隔离与命名修正。

R4 实现预算还必须在每个实现 PR 中声明：

- 预计生产代码与测试代码影响范围（以模块/文件和测试类别估算，不用硬性 LOC 指标）；
- 新 Web Composition Root 允许建立的跨模块关系，只能是 Route/Presenter → 既有
  Query/Application Service；
- 本 PR 将删除的旧 Route、Renderer、样式、兼容测试和 CLI 正式业务入口；
- 当前部署假设、故障模型、人工外部介入风险、最坏事故和恢复成本；
- 该 PR 的非目标及其与后续小 PR 的边界。

明确不做：

- 重构 v4/v5、Queue、Worker、Importer、写锁或 RECONCILE；
- 重新设计 Automation、日结或 Incident 状态机；
- 全量统一历史 CLI 和验收脚本；
- 前后端分离或引入大型前端框架；
- 第二平台；
- AI 自动定价；
- 生产、包装和冷库 ERP；
- 为尚未投入使用的旧 Web 建立兼容层。

当前 R4 基线假设为单机 Windows、单个受信 Runtime DB、一个正式 Web 进程以及既有
Automation/Queue/Worker/Importer 独立运行。主要故障包括错误数据库绑定、重复表单、
认证/Token 泄漏、通知旧链接、部分切换和外部人工直接操作平台。最坏事故是错误任务被
授权进入既有写链或 Incident 处置丢失；恢复成本包括停用新入口、从固定 Git Tag 恢复、
核对 Runtime Task/Review/Outbox/Operation/Attempt、对不明写结果执行唯一 RECONCILE。
因此普通平台写入口明确不进入 Web，外部人工平台操作继续通过后续只读观察和既有
Incident/RECONCILE 发现，不在本任务重构全部控制面。

任何实现 PR 如果需要改变核心业务合同、增加表/状态/锁/队列或新增真实平台动作，必须
从 Web PR 中拆出并单独评审，不能恢复 PR #29 的全控制面扩张。

## 12. 测试与验收

### 12.1 后端边界

- 所有 Web 写操作调用既有权威 Service；
- Web 不直接写 SQL、拼 Queue JSON 或调用 ShadowBot Adapter；
- Web 不提供普通平台写入口；既有平台写事实只读展示；
- Web 人工、Automation 和 Incident 人工任务分别写入冻结的来源身份；
- 两个薄 Automation Handler 复用既有 Job/Run/Event、租约和领域 Service；
- `UNKNOWN` 只进入唯一 RECONCILE；
- Web 失败不影响 Automation、Queue 和 Worker 正常运行。

### 12.2 CLI 迁移

- 日常任务生成、Review、超时维护和运营查询有正式 Web/Automation 路径；
- CLI 不再是完成任何日常运营步骤的唯一方式；
- 测试、Mock、验收、诊断、备份和恢复 CLI 仍可独立运行；
- 测试 CLI 默认使用临时/测试数据，不污染正式 Runtime DB。

### 12.3 Web 功能

- 八个一级入口均有真实后端数据；
- 3 次点击内可以找到最紧急待办；
- 任务、Review、Incident、Automation Run 和执行批次可以互相追溯；
- 运营主流程不要求输入 JSON、hash、ID 组合键或本地路径；
- 0、未同步、不完整、失败和不适用明确区分；
- 内部枚举有简明中文和下一步。

### 12.4 安全与打包

- 认证前登录 CSRF、速率限制、成功登录 Session 轮换、TTL/Cookie flags 通过；
- 登出仅允许带 CSRF 的 POST，其他方法 `405` 且零状态变化；
- 所有 Session 写请求的 CSRF、PRG 和安全 Header 通过；
- 不展示 secret、raw token、完整 webhook、买家 PII 或敏感本机路径；
- Mobile Review Token 与后台 Session 隔离，模板默认转义；
- request/query/form/session 不能选择 Runtime DB；
- 源码运行和隔离 wheel 安装均可加载模板与静态资源；
- 新 Web 不提供 `SYSTEM_EMERGENCY` 手工旁路。

### 12.5 可用性与性能

- 桌面 1440/1024 和手机 390 像素视口通过；
- 列表默认 20 或 25 条；
- 详情不附带完整列表；
- 原始 JSON、日志和证据按需加载；
- 普通页面不产生整页横向滚动；
- 键盘焦点、表单 label、状态颜色和错误信息可用。

### 12.6 Ready for review

- Web 专项和受影响 Service 测试；
- CLI 迁移与隔离测试；
- 完整 pytest；
- 系统冒烟；
- Linux/Windows CI；
- 源码与 wheel 安装；
- `/health`、Mobile Review、旧 Token/Outbox 消化或作废补发的切换验收；
- Presenter 权威输入、四类业务输入服务复用、任务来源和两个薄 Handler 专项；
- 内置浏览器桌面/手机运营验收；
- 不执行未经单独授权的真实平台写操作。

## 13. 完成定义

13.5-7 完成时必须满足：

1. 新 Web 是唯一人工运营入口；
2. 旧 Web 已删除，不存在双 Web 或兼容路由；
3. 八个一级入口均使用正式经营事实；
4. 日常运营不需要 CLI；
5. 测试、验收、诊断、维护和恢复 CLI 仍然可用；
6. Automation、Queue、Worker、Importer 和平台写链路未被重写；
7. 所有人工写操作调用已有权威 Service；
8. Git Tag 可以恢复到重写前检查点；
9. 文档、通知链接、运行手册和测试已切换到新 Web；
10. `/health` 与 Mobile Review 外部协议已按受控顺序切换，无活动通知引用旧端点；
11. 根级规则、项目总览、文档索引和父 Issue 都冻结 Agent Gateway 唯一通道，且本任务
    没有实现或伪造 `AGENT` 来源，没有批准 proposal 表或 Agent 真实平台自主写权限；
12. 父 Issue 的阶段描述已与本计划同步。
