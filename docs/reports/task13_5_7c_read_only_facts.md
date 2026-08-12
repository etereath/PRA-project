# 任务 13.5-7C：四入口只读事实与详情实施报告

- 实施日期：2026-08-12
- Review Profile：R4
- 分支：`codex/task13-5-7c-read-only-facts`
- 基线：`d492dd6`（7B PR #32 合并提交）
- 范围：今日、数据库、业务管理、系统、详情页和 Mobile Review 的只读事实

## 1. 结论

7C 在 7B 的固定 Composition Root、认证、capability、安全 Header 和 GET 零写边界上，
接入既有 Runtime Repository、Automation、Incident、销售日结、订单观察和商品工作簿事实。
页面不再展示静态样板经营值，也没有把旧 `app/web.py` 的 HTML 拼接、请求级数据库选择或
隐式 `init_schema()` 带入新应用。

本阶段完成：

- `/today`：当前 PRA 交易日、OPEN 状态、销售事实状态、销量、成交金额、均价、仅含允许
  销售商品的当前可售库存、品种/等级表、今日待办和自动化时间轴；
- `/database`：业务数据；
- `/database/project`：任务、人工复核、自动化运行、异常、执行记录和通知；
- `/database/sales-analysis`：只展示已经存在的确定性销售事实；
- `/database/dictionary` 与 `/database/quality`：字段口径、质量和新鲜度；
- `/management`：当前任务、人工复核和自动化运行的只读事实；
- `/system`：Runtime DB、工作簿、Queue 和 Worker 的当前组件状态；
- 唯一归属的商品、销售、结算、任务、复核、Automation Run 和执行记录详情；
- Mobile Review 的有效、无效、过期、撤销、错绑和已处理只读状态，以及与正式复核策略
  一致的运营动作名称。

7C 没有业务 POST，没有新表、Schema 迁移、平台动作、Queue 发布、Worker/Importer 调用、
Agent Schema 或第二平台实现。

## 2. 复用矩阵

| 能力 | 分类 | 7C 处理 |
| --- | --- | --- |
| `PlatformTradeDaySummary`、六级质量、唯一日结状态 | 原样复用 | 今日、交易日结算和销售分析 Read Model 只解释既有事实 |
| `OrderSnapshot` 与完整性/尾部/OPEN-CLOSED 事实 | 原样复用 | 销售与订单列表及详情，不重算或伪造订单 |
| Runtime Task、Review、Execution、Notification Outbox | 原样复用 | 项目数据集、待办和详情只读展示 |
| Automation Run、Incident | 原样复用 | 当前时间轴、项目历史和系统影响提示 |
| `ReviewTokenService.validate_token()` | 参数化复用 | Mobile Review 只校验，不消费 Token、不更新 `last_used_at` |
| 商品工作簿读取器 | 参数化复用 | 7D 切换前只读显示当前产品库存资料，并显式标注来源 |
| Repository 查询 | 参数化复用 | 为既有查询增加 `limit/offset` 和窄详情读取，不复制状态机 |
| 7B 认证、权限、模板渲染和安全响应 | 原样复用 | 所有新增路由继续经过同一后端 capability 和安全 Header |
| Read Model、Presenter、运营中文状态 | 确需新增 | Route 不再拼业务 HTML，也不向运营者展示原始错误或内部结果消息 |

## 3. 数据状态和运营口径

页面统一区分：

- 可用；
- 可信零；
- 无记录；
- 不完整；
- 已过期；
- 不可用；
- 读取失败；
- 权限不足。

可信零只在权威事实明确记录为 0 时展示。缺失、不完整和失败不会伪装成 0。当前
交易日始终标为 OPEN，不作为完整闭市事实；日结状态继续使用既有
`PROVISIONAL / OBSERVED / RECONCILED / FINAL` 语义。

Web 不再用自创的固定 30 分钟阈值把经营汇总判为过期。来源节奏和权威新鲜度策略尚未冻结
时，页面保留质量状态并展示最近更新时间；后续只能由正式来源合同增加失效判断。普通结算
目录只查询 `is_current=true`，避免同一交易日销量重复出现；稳定详情仍允许追溯旧版本，但
必须显示“历史版本 · 已被取代”、版本号和当前权威版本关系。

当前交易日只由 Runtime 中唯一有效的版本化运营时间策略得出。读取失败、没有策略或当前
时刻不存在唯一有效策略时，今日页和未显式选择日期的数据集显示失败/不可用，并且不会用
代码默认值查询猜测日期。显式选择的历史交易日、项目运行数据、字段说明和不依赖交易日的
业务数据仍可读取。

成交金额只表示平台页面展示的成交金额，不表示卖家实收、扣佣收入、退款净额或财务到账。
任务、Automation 和执行失败页面不展示原始异常类名、路径、内部 ID、Hash 或底层错误正文；
技术原因留给后续系统高级诊断。

## 4. 分页、筛选和详情归属

数据库列表默认每页 25 条。任务、复核、Automation Run、Incident、执行记录、通知、订单
观察和日结均由 Repository 使用 `LIMIT/OFFSET` 返回有界页面；浏览器不再接收全表后自行
分页。商品当前仍由 7D 前的工作簿读取器在服务端读取，但响应同样限制为 25 条；7D 切换为
DB 唯一库存权威后再使用数据库窄查询。

详情所有权固定为：

- 数据库：商品、销售观察、结算、Automation Run、执行记录；
- 业务管理：任务、人工复核。

交叉所有权路由返回 404，不为同一事实维护两套详情页。

## 5. 当前明确不展示或不实现

- “第 N 次购买”/复购：真实页面可见但当前 Worker、Importer 和 v14 未持久化；
- 买家客户端实时价格：只保留未来合同，不显示假窗口；
- Agent 预测、建议或外部市场指数：没有 Agent Query Adapter 实现前不展示；
- 每日人工花材质量“好/中/差”：没有录入和持久化合同前不展示；
- 完整度百分比：没有权威定义，不以装饰性百分比替代质量状态；
- 数据库真实库存账本、自动销售差额扣减和库存预警：属于 7D；
- 创建任务、执行授权和 Automation 配置写入：属于后续 7D/7E；
- 系统恢复、备份/回读和账号权限管理：属于 7F。

库存调整流水和商品映射在权威查询尚未接入时显示“暂不可用”，不会用样板记录填充。

Mobile Review 只读 GET 状态固定为：有效链接 200；已有正式结果的链接 200 并显示“已经
处理”；过期或撤销 410；未知 Token、复核不存在或 Token/复核错绑统一 404，且不泄露内部
标识。动作名称复用正式复核策略：紧急保护显示“改价到/立即下架/我来处理”，执行失败显示
“重试任务/取消任务”，普通复核使用“通过/拒绝/调整/取消”。7C 不实现处置 POST；7E 将
继续复用既有单事务写入口及其写入状态码，而不是从 GET 反推一套新协议。

## 6. GET 零写和真实 Runtime DB 验收

合成测试把 `SQLiteRuntimeRepository.init_schema()` 替换为立即失败，并覆盖健康检查、四个
一级入口、数据库四个二级入口、详情、Mobile Review 和静态资源；所有 GET 仍完成且快照
不变。

2026-08-12 对固定真实 Runtime DB 执行 READ_ONLY 验收：

- 已知 1 条外键违规只使 `/health` 返回 `503 Service Unavailable`；本任务未推断来源、
  未迁移、未修复真实数据；
- `/today`、五个数据库入口、`/management` 和 `/system` 均返回 200；
- 无效详情和无效 Mobile Review 返回 404；
- `init_schema()` 被替换为立即失败后，上述 GET 仍完成；
- Runtime 主库/WAL/SHM、三份真实业务工作簿、Worker 心跳与生命周期文件共 8 个文件，
  前后大小、主文件 mtime 和 SHA-256 均未变化；
- `inbox / working / results` 均为空，未创建 `stop.signal`；
- 未启动、停止或投递 Worker，平台写副作用为 0。

生命周期记录仍为历史 `RUNNING`，而事实心跳为 2026-08-03 的 `STOPPED`；7C 只报告这一
既有状态不一致，没有写回生命周期文件。

## 7. 测试与视觉验收

- 7C Read Model 专项：`27 passed`；
- 本轮受影响集成：`188 passed, 36 subtests passed`；
- 完整 pytest：`1179 passed, 3 skipped, 97 subtests passed`；
- 隔离系统冒烟：`16 passed, 0 failed`；
- wheel/sdist 构建、严格包边界和 secret scan：通过；
- 内置浏览器桌面与 390×844 手机视口：今日、数据库和横向表格通过；
- 浏览器控制台错误/警告：0。

CI 的 Windows/Linux 结论以 Draft PR 检查为准，本报告不把本地 Windows 结果写成远端 CI
已经通过。

## 8. 回滚和下一步

回滚只需撤销本 PR；7B 应用骨架、旧 Web 检查点、Runtime Schema、真实数据、Queue 和平台
均未被 7C 改写。下一阶段按施工计划进入 7D，在独立 R4 评审后建设数据库真实库存权威、
不可变流水、自动销售差额扣减和库存预警；不得在 7D 之外顺手实现 7E 的执行授权控制面。
