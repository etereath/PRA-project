# PRA 目标职责与 13.7 实现交接

角色：Canonical / Accepted Target Architecture and Handoff。承接已合并 PR #45 的原 G2 与增量吸收，IG-01～IG-13 在本文统一维护。此为目标职责，不代表生产已实现。业务语义见[业务合同](../business_contract.md)，代码现状见[实现图](task13_6_current_implementation_map.md)，阶段许可见[状态页](../project_current_status.md)。

## 1. 责任与宿主

| 责任 | 目标 owner/宿主 | 关键边界 |
|---|---|---|
| 决定价格、Exposure、上下架 | 当前 Human / Operations Web | 未来销售 Agent 只替换决策来源；当前不实现自动策略 |
| 有 scope、TTL、完成条件的 Intent | Business Application 的持久逻辑记录 | 先记录新有效决定；不直接操作平台 |
| Runtime Task | 现有 Task/history | 明确动作；不是第三套平台事实 |
| 等待人工授权 | Human + Operations Web workflow | prepare/submit、supersede/cancel/expire；后台不扫描所有 pending 自动执行 |
| 最终确认后的可靠交接 | Execution Authorization + 最小持久 continuation | 可由持久事实决定继续、跟踪已有发布、重新确认或终止 |
| 持久 execution continuation | TaskExecutionCoordinator，托管现有长期 Queue Service | 重新发现、blocker re-evaluation、publish/result/terminal、UNKNOWN/RECONCILE；不新建 daemon |
| UI 副作用、结果和超时 | 既有 v4/v5、Queue、Worker、Importer、Watchdog | 保持写前读取/旧值比较/写后确认和唯一 RECONCILE |
| 定期 Observation/Closing/恢复校准 | Automation Service | READ_ONLY 排程与资源协调；不成为普通销售写 Controller |
| Observation Health | Automation 侧 ObservationHealthService | provider/cadence/fallback/recovery；Incident 严重度不授予平台写权限 |
| Product Master | 13.7-2B 后的 Runtime DB + 版本化管理 Service | cutover 前仍由 `products.xlsx` 负责；不复制实物库存、不隐式回退工作簿 |
| Platform Product Mapping | 13.7-2B 后的 Runtime DB + 版本化管理 Service | 显式 `platform_name/account_id/platform_product_identity/internal_sku`；cutover 前仍由工作簿编译结果负责 |
| Price / Listing Rules | 本阶段继续由各自 workbook authority | 绑定规范化规则集 digest；未来迁库另做独立 cutover，不与 Product/Mapping 机械捆绑 |
| Qualified Observation | 13.7-2C qualification contract；事实 owner 仍是 immutable evidence | 输出 quality/freshness/scope/identity/source refs，不自行决定 blocker |
| 当前 Commitment | 当前事实 selector/projection | provider/evidence/scope/freshness 可追踪；新累计事实替换旧 current，不相加 |
| Supply | 统一供给事实选择责任 | 同日最高阶段覆盖，Carryover 独立；不为三个 stage 建三个子系统 |
| 实物库存 | 现有唯一 DB Inventory authority | Exposure 不写实物；physical/accounting event 契约另行明确 |
| Review/通知 | 既有 Review/Token/Outbox | 按类型区别授权、Closing S2、Observation 与价格保护，复用通道 |
| ShadowBot identity mapping | 既有 Executor/部署配置 | cutover 后由 Runtime mapping generation 生成/同步为 derived locator，部署只补 UI-only 字段；绑定 generation/digest，不是独立 authority 或 fallback |
| blocker enforcement | 既有 Authorization / Coordinator / Observation Health / Queue 宿主 | 使用共同结构化 scope/category；不新建平行 Queue、Review 状态机或 blocker daemon |

逻辑责任不等于必须新建表、类或状态机。先审计 Task/origin/history、v4/v5 batch、operation、execution_attempt、receipt、Automation Event 的承载能力。

### 1.1 Authority cutover 输入

| 对象 | 13.7-2A 冻结输入 | cutover owner、触发与完成证据 |
|---|---|---|
| Product Master | 新 SKU 只建立版本化商品身份；缺少库存事实显示 `NOT_INITIALIZED`，不得自动建立 0 balance | 2B 实施者；additive schema、受控 import、shadow compare 后，全部正式 Runtime consumers 同 gate 读 DB snapshot/version |
| Platform Mapping | configured target `account_id` 由负责人/部署配置显式提供；active session binding 由 Adapter/Executor/登录链另行证明；唯一性为同一 platform/account/product identity 与有效期内最多一个 VERIFIED SKU | 2B 实施者；切换证据包括 direct-reader inventory、旧新解析逐项对比、identity/mapping generation、derived locator digest、target/session binding contract 与 rollback 演练 |
| Price / Listing Rules | 继续 workbook authority，Task/评估绑定规范化规则集 digest；本批不迁 DB | 现有 Operations Web / Automation owner；未来迁库另立任务，不因 2B 合入而切源 |
| Physical Inventory | 既有 DB ledger 唯一 authority；Product Master 不写 balance | Inventory Application；初始化/入库分别保留 actor/source/idempotency，缺失不是 0 |
| Observation | immutable evidence 是历史事实，qualified projection 是可失效的当前事实 | 2C 实施者；Operating fact qualification 覆盖 scope/identity/completeness/time/source integrity，Evidence delivery/archive health 单列；ACK/archive 局部失败不机械否定可验证事实 |
| Human Decision / execution | 决定、授权、continuation、执行事实分层 | 2D 实施者复用 #47～#50 链；新决定先记录，已跨副作用边界者按最小范围收口，不恢复 create 后自动 submit |

2B 开工先枚举 Product/Mapping 的全部生产 direct readers，并把 Web/Manual Task/Authorization/Automation/Observation/Order mapping/Task generation/Queue/Executor/Importer/Emergency/Workflow/business-rule evaluation 等实际路径逐项标为 `CUTOVER` 或明确的 `OFFLINE/IMPORT/EXPORT/DIAGNOSTIC EXCEPTION`。切换后正式路径不得依赖旧 workbook 环境变量；例外不得进入正常运行 authority 或成为 fallback。

Product/Mapping 回滚只允许把所有正式 consumers 原子恢复到一个已验证 authority。旧工作簿可作显式 import/export、离线诊断、history/rollback 工具；ShadowBot locator 是绑定 Runtime authority generation/digest 的派生执行 artifact。cutover 后若已有新的 authoritative master-data mutation 或依赖新 mapping 的平台副作用，则禁止静默回到旧 authority，改用 forward correction 或负责人显式维护/re-cutover。

### 1.2 Blocker owner 与作用域

Authorization 对新写、Coordinator 对 continuation/UNKNOWN、Observation Health 对 provider/account health 分别消费同一结构化 qualification/blocker context；运行期记录必须含 category、evidence refs、精确 scope、blocked/allowed operations、owner、自动释放条件和人工恢复路径。Review 使用已有 `blocked_actions`，不得再以“同 SKU+platform 任意 pending Review”建立平行 blanket gate。

真实写前必须把 configured target account 与 active session binding evidence 区分并核对，连同 internal SKU、platform product identity、authority/mapping generation 和 locator artifact digest 进入 authorization/execution identity evidence。无法证明或 mismatch 时按 GQB-4 停止受影响账号全部新写，保留安全的 identity-calibration READ_ONLY/diagnostics，且不记录凭据或冻结无关账号。

作用域只能按 `Task → SKU+Action → SKU All Writes → Platform/Account Write Queue → Cross-platform System` 逐级证明。platform/account 全队列仅允许 GQB-1 Critical Business Risk、GQB-2 Observation Blindness、GQB-3 Critical Control Plane Failure、GQB-4 Side-effect/Identity Integrity Failure；仍应保留安全的 READ_ONLY、Recovery Calibration、唯一 RECONCILE、Health/diagnostics 与人工恢复。新增第五类或阻断无共享风险对象前必须先提交并获得接受的 Global Queue Blocker Proposal。

## 2. 三条主要旅程与恢复

### 人工决定与执行

Human decision → 持久 one-shot Intent → Task → 人工 prepare/submit → durable handoff → Queue Service continuation owner → 既有 Worker/Importer/Watchdog → terminal + platform readback → 对比 latest valid Intent。

未跨副作用边界的旧动作尽量原子 supersede/cancel/replace。已 QUEUED/RUNNING/UNKNOWN 的旧动作保留历史，先完成/回读/对账；随后必要时生成 correction Task，仍需正常授权。外部员工修改平台属于正常经营；旧 Intent 默认失效或重新确认，不无限写回旧目标。

| 停留位置 | owner 与触发 | 失败/重启/收尾 |
|---|---|---|
| PENDING、尚未最终授权 | Human/Web；显式 prepare/submit | 内存 preparation 可过期；用户可重新确认、取消、替代，业务有效期可终止 |
| 最终确认后 | 持久交接事实与 Coordinator | 审计与发布之间崩溃也能判断下一步；查 batch/operation/attempt/Queue/receipt，不能盲重发 |
| blocker、result pending | Coordinator 协同既有组件；周期重评估/导入 | 重启从持久 continuation 恢复；明确继续、重新确认、人工处置或终止 |
| UNKNOWN | 既有唯一 RECONCILE，Coordinator 跟踪 | 不做第二次猜测写；若仍不能判定历史副作用，允许 stopped boundary 后的 qualified READ_ONLY 结束旧 one-shot 当前责任但保留 historical UNKNOWN；否则交人工 fallback |
| terminal | Importer/既有账本投影，Coordinator 完成业务衔接 | 保存结果与回读；不因旧目标未持续维持而自动重开 one-shot Intent |

### Observation 与 Health

Automation 根据 versioned capability 和当前页面模式选 Provider → READ_ONLY Adapter → immutable evidence → current selector/Web → Health。Light Scan 持续观察 price/exposure/status；冻结期用 CurrentTradeDaySalesObservation，订单页 rollover 后用 current-order Provider。证据可信度、粒度和 freshness 分开。

Qualification 同时输出 Operating fact trust 与 Evidence delivery/archive health，但两者不得互相替代。历史失败、superseded、retry/recovery batch 可以共存；selector 按正式 run/attempt、scope、完成状态、时间和 identity 选择唯一 current qualified candidate。合法 retry 成功可接管 current；多份候选同时合格且冲突才 fail closed，不拼接或静默合并。

S1 首次超 cadence；S2 主直接校准缺失但有可信 fallback；S3 无足够可信校准立即请求 Recovery Calibration。排队/合法 UI 资源等待保持 S3/RECOVERING；真正平台级恢复失败才 S4。单 SKU 故障不直接升平台 S4。风险动作规则见业务合同；Observation S4 没有 Emergency S4 自动下架权限。

### 历史 Closing

Automation 在当前蚂蚁约 19:00 先检查同平台/交易日成功记录，再读冻结的上一日订单。日期、scope、tail/可信空页完整后绑定 immutable batch 并锁定自动重扫。首次失败报告+一次 retry，第二次失败 Closing S2+Review，停止自动重试。管理员显式维护保留 actor/reason/provenance；不复用旧多版本 Settlement 生命周期作为正常 Closing。

Closing 需要 quantity、amount、order_created_at、purchase_sequence 及品种等级等事实；不另存页面单价。具体持久结构、管理员维护形状尚未固定。

## 3. 原 G2 与增量吸收的实现门槛

| ID | 必须成立的责任结果 |
|---|---|
| IG-01 | PENDING 授权阶段有 Human/Web owner 和 submit/supersede/cancel/expire 出口；可失效 preparation 不需要恢复成自动执行 |
| IG-02 | 先记录有效新 Intent；开放 Task 仅是调度条件；按副作用边界替代或等待收口，correction 正常授权 |
| IG-03 | 单 continuation/attempt 与 Coordinator 可恢复异常各自隔离，不拖垮 Importer/Watchdog/Review/Outbox；DB/schema/process 根本故障可使宿主明确 FAILED |
| IG-04 | 新事实在切换前 shadow/read-only；禁止旧 Summary 与新 Commitment 同时作为扣减/计划 authority |
| IG-05 | 保留实物库存 ledger；切换前明确唯一 physical/accounting event，跨日数值证明 Supply/Carryover/Commitment/calibration/rollback 不重复扣减 |
| IG-06 | 先审计既有 Task/history/batch/operation/attempt/receipt/Automation Event；必要时才新增最小持久结构，不机械建四套状态机 |
| IG-07 | 新 CurrentTradeDaySalesObservation、purchase_sequence 先完成真实 READ_ONLY 定位、类型/空值/粒度、证据/重放/回归，再进入正式 authority selector |
| IG-08 | 最终确认后任何 Web 崩溃点均可从持久事实判断下一步；已有 ledger 只跟踪；未建立则安全恢复或明确重确认；禁止 audit-only 永久悬空及发布不确定时盲重发 |
| IG-09 | Current Operating State/Commitment authority 生效时，Today/Quality/相关 Web 当前销售读模型同 gate 切换；旧 Summary 仅历史/legacy 展示 |
| IG-10 | Platform capability 的 timezone、cutoff、订单 rollover、历史读取、实时 Provider、cadence、Closing offset、effective range/version/source 可追踪；复用 TimePolicy，不因此建配置中心或 selector DSL |
| IG-11 | 14-B facade 只读/受控调用已存在的确定性接口，不成为 13.7 observation/execution/recovery owner 或前置依赖 |
| IG-12 | Product/Mapping 从工作簿迁 Runtime DB 采用 additive schema、account-aware identity、shadow compare 和 explicit cutover；全部正式 Runtime consumers 同 gate 切换并逐项登记离线例外，无隐式 fallback；Product 新增不自动制造 0 库存事实 |
| IG-13 | blocker 采用可证明的最小作用域；platform/account 全队列只允许 GQB-1～4，并保留安全恢复/READ_ONLY；第五类必须先经 Global Queue Blocker Proposal 接受 |

原始依据：[G2](../reports/task13_6_2_g2_architecture_handoff_review_20260906.md)、[增量 G2](../reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)。原平行分析补充是采纳历史，不是另一个现役合同。

## 4. 复用与后续责任

| 能力 | 判定 | 后续任务/方向 |
|---|---|---|
| 认证/capability、Review/Token/Outbox、Task/history | REUSE / 局部 ADAPT | 13.7 复用正式入口，补 Intent/continuation 衔接 |
| Manual preview/create、Execution Authorization | ADAPT | 13.7 第一纵切/Exposure；改 supersession 与两处旧库存上限，保留其他校验 |
| v4/v5、operation/attempt、write lock、Queue/Worker/Importer/Watchdog、UNKNOWN/RECONCILE | REUSE | 13.7 跟踪/投影既有结果，不复制执行状态机 |
| one-shot Intent、统一持久 execution owner | MISSING logical responsibility | 13.7 第一纵切；先复用账本再决定 Schema |
| Product/Order READ_ONLY、Mapping、hash/scope/tail | REUSE | 13.7 Commitment/Closing 的证据基础 |
| CurrentTradeDaySalesObservation、purchase_sequence | MISSING | 13.7 平台 READ_ONLY gate；purchase_sequence 按新合同重新引入，不恢复 retired provisional 设计 |
| Commitment selector/current state | MISSING / ADAPT | 13.7；可持久 projection 或由 immutable evidence 重建；来源与 restart correctness 必须可追踪 |
| Daily Closing、成功锁定、retry/S2/admin maintenance | MISSING / ADAPT | 13.7；复用读取/Job/Event/Review，不包装旧 Settlement 生命周期 |
| Daily Supply / Carryover | MISSING / ADAPT | 13.7；复用 HarvestForecast/录入资产，最高 stage 选择 |
| DB physical inventory ledger | REUSE | 13.7 保留唯一实物 authority；不得被 Exposure 替代 |
| `a3485af` Runtime Master Data repository/service/version/idempotency | ADAPT | 13.7-2B；重做 migration、account identity 与 Product/Inventory 分离，禁止整体 cherry-pick和空库 replacement |
| `a3485af` listing qualification / READ_ONLY 接线 | ADAPT | 13.7-2C；拆分 Operating fact trust 与 ACK/archive health，按唯一 current candidate 选择合法 retry；不自行升级 global blocker；接 2B mapping snapshot |
| `a3485af` Queue Read Model / structured blocker presentation | ADAPT | 13.7-2D；Current Queue 与 History 分离，重写 blanket blocker/cancel policy |
| Settlement-driven sales baseline | ADAPT / CUTOVER RISK | 13.7 IG-05；并未 blanket retire 所有可能的 sales-driven accounting |
| 20:00 seller day、旧 Settlement→Plan→DailyTask/普通订单导入强耦合 | RETIRE / 局部 ADAPT | 13.7 authority cutover 后再考虑物理删字段；保留历史证据 |
| Incident infrastructure | REUSE | 13.7 Health/Closing 新类别；现有价格 Emergency 权限继续独立 |
| provider-centric Observation Health | MISSING | 13.7 Automation 侧；不新 daemon |
| Agent Intervention | DEFER / interface | Task14-B 诊断与风险中性工具；不是自动销售策略 |
| 多平台组合分配 | DEFER / gate | 第二平台接入前设计；当前不实现复杂 Allocator |

## 5. Authority cutover 与回滚

| 场景 | 判定与当前责任 |
|---|---|
| 旧链独占扣减/计划 authority；新链仅 shadow/read-only；Web 当前页面读旧链 | 合法的切换前状态，符合 IG-04；现有旧链保持经营责任，新链由 13.7 实施者比对和验收。不能把“新事实已能算”当成已启用新 authority |
| 新旧链同时扣减或生成正式计划 | 违反 IG-04，必须停止 authority 重叠；选择保留哪条链须依据实际切换状态与证据，不能用切 Web 读源掩盖双写 |
| 新链已是唯一经营 authority，但 Web 当前销售仍用旧 Summary | 违反 IG-09，属于切换不完整；旧 Summary 可作历史展示，不能继续充当当前销售事实 |

顺序：新事实 shadow/read-only → 比对证据/输出并通过相关能力验收 → 停止旧 Settlement 业务 authority 与相关 Job 写路径 → baseline/migration 对齐 → 启用新 authority 并同 gate 切换 Web 当前读模型 → 验证跨日、重启、blocker、UNKNOWN、外部人工漂移 → 再考虑物理清理。

相关切换由 13.7 实施者负责，触发条件是实现、相关能力验收与 IG-05 的无重复扣减证明成立；13.6 文档通过本身不触发运行切换。切换后当前经营事实与 Web 读模型的责任一起交接，不能留成“后台以后更新、页面继续旧数”的非终态。

IG-05 的库存记账事件选择是明确保留给 13.7 的实现契约；未裁决和证明 no-double-count 前不得切换相关实物扣减接线。Closing 成功本身不是扣实物理由。回滚可切 read path/Job/selector，但不删除 observation、Closing、Intent/Task/execution、旧 Summary/transaction 证据，也不重新同时启用两套 authority。

## 6. 13.7 推荐推进方式

第一条纵切：1 SKU + 一次人工 UPDATE_PRICE，从 Intent/Task、Human Authorization、durable handoff，经既有 v4/Queue/Worker/Importer 到 terminal 与平台回读；至少覆盖 final-confirmation crash 或 restart/blocked recovery。若既有 Watchdog/Importer 已完整处理某段，Coordinator 只持久发现和投影，不复制其逻辑。

随后先以 13.7-2A 冻结 authority/continuity 合同；2B Runtime Product/Mapping 与 2C Qualified Observation 可并行实现但均受 2A 接受门禁约束，2D Queue Continuity 以 2A 为准并消费 2B identity 与 2C qualification。再按依赖扩展 Exposure、rollover 后订单 Provider 的 Commitment、冻结期 Provider、Closing、Supply、Observation Health。存在 authority 重叠的路径先 shadow，验收后显式 cutover；不能因“旧链最后退役”让两套 authority 同时经营。Web 随切片接入，不最后才第一次联调。

两处 Exposure/实物上限校验在相关 Exposure 行为受本切片影响时调整；不因它们是已知 gap，就要求纯 UPDATE_PRICE 首条纵切无条件同时完成 Exposure 改造。也不为所有 13.7 开发前置要求完成后续 Provider 或库存 cutover 的全部门槛。

G2 未批准固定新增表集合、独立 Dispatch 状态机、Commitment 必须 snapshot 表、Closing 维护只能 generation 重扫、全面废除 sales-driven accounting，或按横向 Package A→G 逐个写完才集成。

## 7. Agent 与多平台接口

13.7 只保留 `AgentInterventionHook`：发布 trigger_type、incident_id、platform、risk_level、最新 scan refs 和 diagnostic context refs 等结构化事实，消费者可为任意 Agent 或无人消费。确定性恢复不依赖消费者在线。

Task14-B 的 OpsQueryFacade 返回 scope/granularity、authority role、时间、quality/health 和 source refs；Controlled Tool Facade 首版可请求 READ_ONLY Recovery Calibration、解释 blocker、附加结构化诊断、查询 receipt。仍绑定主体/capability、幂等键、参数 allowlist 和审计。

14-B 首版不得创建/批准 Sales Control Intent、提交/调整销售 Task、代替 Human final confirmation、执行 Closing 管理维护、直接写 DB/Queue/v4/v5、修改权限/capability/emergency flag 或伪造 SYSTEM_EMERGENCY。这不限制未来另行设计的销售 Controller 接入，但不属于当前交付。

公共核心区分 platform_name、account_id、internal_sku、platform_product_identity；平台 UI/登录/selector 留在 Adapter/Executor/ShadowBot。Supply 全局共享，Commitment 按平台观测并在日期/单位/scope/去重条件成立时汇总，Exposure 平台独立。第二平台前完成组合层设计，不提前建设跨平台原子事务。
