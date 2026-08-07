# 任务 13.5-7：运营 Web 替代重写与 CLI 残留业务迁移计划

- 决策日期：2026-08-07
- 当前状态：编码前计划评审
- Review Profile：R3
- 真实平台写操作：否；本计划只规定现有正式服务的调用边界
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

Issue #20 当前仍把 13.5-7、13.5-8、13.5-9 分成入口对齐、架构拆分和 UI 重写。该阶段文字
需要在本计划合并前同步更新；双时间轴、八个一级入口、扫描父子关系、S0–S4、唯一
RECONCILE、唯一 `FINAL` 和任务 14 边界继续保持不变。

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

### 3.4 系统维护和开发控制面：CLI

CLI 只承担：

- 开发测试和合成 fixture；
- Mock 平台；
- 系统冒烟、集成和实机验收；
- 初始化、健康检查、部署、备份和诊断；
- 明确授权的紧急恢复；
- 启动 Web、Automation、Queue 等常驻进程。

CLI 不再作为日常任务生成、复核处理、运营查询或计划维护的唯一入口。

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

- `composition_root.py` 绑定 Runtime Repository、配置和权威 Service；
- Route 只解析 HTTP 输入、授权、调用 Service/Presenter 并返回响应；
- Presenter/ViewModel 只转换运营显示语义，不修改状态；
- Template 默认转义，不包含业务状态转换；
- Static 资源必须随 wheel 打包并通过隔离安装验证；
- Web 请求线程不得运行 ShadowBot、长期脚本或轮询 Worker；
- Web 不直接拼 Queue JSON，不调用平台 Adapter，不直接写业务 SQL。

现有认证、Session、CSRF 和安全 Header 可以抽取并复用已经验证的实现，但复用目的是保留
安全属性，不构成旧 Web 兼容承诺。

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

切换时允许改变所有业务页面 URL。飞书和 Mobile Review 使用的链接由新 Web 重新生成，
并在同一个切换 PR 中更新通知链接测试。因为尚未正式投入使用，不维护旧链接跳转。

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
| 平台写操作 | v4/v5、Operation/Attempt/Lock、Queue/Importer | 只显示或调用既有正式入口 |
| UNKNOWN | 唯一 RECONCILE | 不新增恢复路径 |
| 通知 | Outbox、通知 Worker | 原样复用 |
| 运营显示 | 新 Presenter/ViewModel | 确需新增，仅显示语义 |

不得新增一个包揽所有领域的“统一控制面 Service”。新 Web 通过 Composition Root 组装现有
领域服务，避免把稳定模块重新耦合到一起。

## 10. 实施工作包

### 13.5-7A：计划、恢复点与页面合同

- 关闭旧控制面收口 PR；
- 推送当前 `main` 恢复 Tag；
- 冻结八入口、CLI 迁移矩阵、无兼容切换和复杂度预算；
- 不修改生产代码。

### 13.5-7B：新应用骨架

- 建立 `operations_web/`、路由、模板、静态资源和 Composition Root；
- 抽取登录、Session、CSRF 和安全 Header；
- 建立设计 token、导航、错误页和基础 ViewModel；
- 完成源码与 wheel 安装测试。

### 13.5-7C：只读运营页面

- 今日运营、平台商品、销售分析、自动化和系统健康；
- 列表分页、筛选、独立详情和按需高级诊断；
- 先完成只读事实链，不迁移真实平台写按钮。

### 13.5-7D：人工流程与 CLI 残留迁移

- 待处理、任务中心和业务资料；
- 任务预览/生成、Review、Incident 处置和允许的只读补跑；
- Automation 接管超时维护和日常自动生成；
- CLI 正式业务入口降级，测试/管理员入口保留。

### 13.5-7E：切换与删除

- 新 Web 接管正式启动入口；
- 更新飞书/Mobile Review 链接；
- 删除旧 Web Route、Renderer、样式和只服务旧结构的测试；
- 更新 README、运行手册和项目状态；
- 不保留旧路由兼容层。

### 13.5-7F：运营验收

- 桌面与手机浏览器完整走查；
- 完成日常运营脚本；
- 验证 CLI 不再是日常业务必需入口；
- 输出实施报告、页面地图、操作手册和已知限制。

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

明确不做：

- 重构 v4/v5、Queue、Worker、Importer、写锁或 RECONCILE；
- 重新设计 Automation、日结或 Incident 状态机；
- 全量统一历史 CLI 和验收脚本；
- 前后端分离或引入大型前端框架；
- 第二平台；
- AI 自动定价；
- 生产、包装和冷库 ERP；
- 为尚未投入使用的旧 Web 建立兼容层。

任何实现 PR 如果需要改变核心业务合同、增加表/状态/锁/队列或新增真实平台动作，必须
从 Web PR 中拆出并单独评审，不能恢复 PR #29 的全控制面扩张。

## 12. 测试与验收

### 12.1 后端边界

- 所有 Web 写操作调用既有权威 Service；
- Web 不直接写 SQL、拼 Queue JSON 或调用 ShadowBot Adapter；
- 普通平台写继续经过任务、授权、Operation/Attempt、写锁和 Importer；
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

- 登录、Session、CSRF、PRG 和安全 Header 通过；
- 不展示 secret、raw token、完整 webhook、买家 PII 或敏感本机路径；
- 模板默认转义；
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
10. 父 Issue 的阶段描述已与本计划同步。
