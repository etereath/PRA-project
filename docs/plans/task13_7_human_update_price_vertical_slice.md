# Task 13.7：首条人工 UPDATE_PRICE 纵向切片

角色：Implementation Plan / Codex Handoff；不替代[业务合同](../business_contract.md)与[目标职责](../rebaseline/task13_6_target_responsibility_and_gap_matrix.md)。日期：2026-09-06。负责人已明确“验收通过,准备下一阶段”，13.6状态见[唯一状态页](../project_current_status.md)。本次提交为开发交接，未实现本计划；接下来的代码由 Codex 承接。

开发入口：[可直接交给Codex的Goal](task13_7_first_slice_codex_goal.md)。

## 1. 起点与最小业务结果

13.6已接受语义版本为 `4d51f51edcafc4168149928f6ee64467cd12421a`，PR #46 已合并。本次交接从核验时最新 main `f227cd2517687e4a6dfadea90c2e126a5da69711` 创建 `codex/task13-7-1-human-update-price`。本计划首次生产源码审计绑定 `08041bfe25a7f31f032564a2abca35e5eb5f5330`；当前 main 相对该提交仅有文档净变更，下面的源码引用保留原版本。Codex 承接时仍须核对最新 main、任务 PR Head/正文/评论、AGENTS、相关源码、测试与 CI，以新事实更新实现判断。

推荐首个实现任务标识：**13.7-1 / One-SKU Human UPDATE_PRICE**。它要证明：管理者的一个有限期改价决定，经正式授权后有人持续推进，即使Web退出或合法blocker暂时存在，也能恢复、回读并明确完成/失败/终止；发布未知不能猜测重写。

范围限定为当前已支持平台/账号的一种商品与价格维度。公共身份仍区分platform/account/internal_sku/platform_product_identity，不把蚂蚁UI写进公共核心。最小不是绕过正式Web或把中间组件替换为测试直达函数；仍须沿完整经营旅程。

### 承接方式与角色

- **Codex**：fetch 并跟踪 `origin/codex/task13-7-1-human-update-price`，在此 Draft PR 上完成复用审计、实现、必要迁移/测试和证据；这是从已合并 main 创建的 13.7 分支，不是旧 13.6 分支。先检查本地 branch/worktree 并保留已有改动；远端已有新提交时按最新 Head 接续，不覆盖他人提交。
- **ChatGPT 审核者**：材料交付后回到业务、架构和代码审核；依真实代码和旅程证据出具双结论，首审冻结 blocker，复审限定原问题与直接回归。
- **项目负责人**：确认经营与现场验收，决定合并及真实平台副作用授权；已完成的 13.6 验收不重复请求。

本 PR 初始提交只含文档，Codex 无需等待其先合并才能承接代码。复用选择直接记录在同一 PR 说明或必要的短设计记录，不先另开一轮总纲。较大边界冲突按治理报告，日常实现选择由 Codex 完成。交付时更新同一 PR 的实现范围、最新 Head、验证与未验证项，保持 Draft 直到收到相应明确指令。

## 2. 已读取的生产资产与复用约束

下表证据均绑定main `08041bf`，描述的是现状，不是本计划已实现。

| 真实入口/资产 | 已确认行为 | 对首切片的意义 |
|---|---|---|
| [ManualTaskApplicationService.create](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/manual_task_orchestration.py) | 基于主体、幂等键、preview digest，在事务中写Task；已有开放Task会成为创建blocker，不发Queue | 复用范围/身份/幂等与审计；价格新决定必须先记录，开放执行只能挡调度，不能继续拒绝决定 |
| [ExecutionAuthorizationApplicationService](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/execution_authorization.py) | prepare绑定主体、精确Task集合、payload digest、expires_at；确认缓存与幂等索引在内存；submit重验后先写授权history，再调v4_publish | 内存prepare失效可重确认；最终授权到可靠交接的空隙必须补齐，不能把history当作完整可恢复授权包 |
| [v4 prepare/publish](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/shadowbot_commit_pipeline.py) | Web的_prepare_v4可在最终确认前持久化PREPARED manifest；publish进入PUBLISHING并建立operation/attempt/write lock，再runner.start；成功后转QUEUED，发布边界不确定有UNKNOWN处理 | **PREPARED不等于已最终授权**。现有批次可复用，但不能靠扫描所有PREPARED或pending自动执行；PUBLISHING异常须查账本/Queue证据，不能重新调用publisher生成另一attempt |
| [Task/history与执行账本](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/repositories/sqlite_runtime_repository.py)、[Schema contract](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/runtime_schema.py) | Task已有origin、scope、expiry、target、decision_trace/history；已有commit batch/items、operation/attempt、write lock、receipt及schema gates | 优先承载决定与执行关联，逐项检查字段语义和查询能力，不机械建Intent/Task/Attempt三张表，也不把任意JSON无校验当成完整合同 |
| [Queue Service.run_cycle](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/scripts/run_shadowbot_queue_services.py) | 长期宿主已有Importer、Watchdog、登录监视、Review提醒、Outbox；部分组件隔离，Importer直接调用 | Coordinator托管此宿主，只新增所需业务衔接；自身/单continuation业务异常不得阻止其他组件循环，不宣称现有所有异常已隔离 |
| [Importer/Watchdog](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/shadowbot_queue.py)、[恢复服务](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/shadowbot_recovery.py) | v4结果导入、receipt、实际价格/page snapshot、超时与既有RECONCILE能力已存在 | Coordinator发现和跟踪结果，复用结果导入与唯一对账，不复制第二套Worker、重试器或执行状态机 |
| [授权测试](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/tests/test_execution_authorization.py)、[v4编排测试](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/tests/test_shadowbot_commit_v4_orchestration.py)、[Queue故障注入gate](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/tests/test_shadowbot_queue_fault_injection_gate.py) | 授权测试使用fake_v4_publish/fake_v5_publish；其他测试覆盖各自协议/编排/故障注入边界 | 继续保留，另用正式应用组合和同一Runtime证明跨组件旅程；孤立测试不能替代首切片验收 |

## 3. 先回答复用问题，再写最小结构

在实现PR的说明或短设计记录中，一次回答下面三项；这是首切片内的代码审计，不另拆长期架构前置阶段。

1. **决定记录**：现有Task/origin/history能否在旧执行仍开放时保存最新有效价格决定，并区分它与旧执行目标？能否表达scope、TTL、完成/替代/过期与来源，且不修改旧immutable manifest？若现有开放Task索引阻止保存，说明最小调整或独立记录的必要性。
2. **授权后持久交接**：在哪里原子绑定主体/capability语义、精确目标与Task集合、有效期、确认内容摘要、幂等身份及对应执行账本？现有AUTH history缺少的恢复信息是什么？不把PREPARED、TaskStatus.PENDING或一条日志升级成授权。
3. **继续执行与跟踪**：Coordinator如何只找到持久授权交接，区分尚未发布、合法等待、已发布与发布未知，并从operation/attempt/receipt恢复？需要何种最小防重入措施，既有事务/唯一约束/锁为何不足或足够？

建议先评估扩展现有账本的方案；若不能正确承载职责，再选择最小新增持久结构。新增每个表、字段、状态或锁，都须说明当前首切片会发生的具体事故及复用不足。逻辑职责与物理表数量不画等号；运行约束（如现有单Queue Service实例）可明确复用，不提前建设多实例调度平台。

## 4. 第一条旅程的责任连续性

| 位置 | owner、触发与出口 |
|---|---|
| 人工提交价格决定 | Web/Application持久记录scope、目标、TTL和来源；合法新决定即使遇到旧开放执行也能保存。无效输入仍正常拒绝，保存决定不等于授权执行 |
| 等待prepare/confirm | Human/Web；显式授权、取消、替代、过期。Web重启可使未提交准备失效，后台不得自动补授权 |
| 最终确认提交 | Authorization与持久交接在明确事务边界成立；API只在可靠接受后报告已接受。崩溃后可继续/跟踪或明确重确认/终止，不留audit-only永久悬空 |
| 已授权但暂时阻塞 | Queue Service内Coordinator周期重评估既有blocker；解除后在授权仍有效且事实未漂移时推进。失效或事实改变则持久记录重新确认/终止，并通过既有Web/Review能力交回人，不能只是不可见日志 |
| PUBLISHING / QUEUED / RUNNING | 既有v4、Queue、Worker、Importer/Watchdog拥有副作用与结果；Coordinator跟踪已存在执行。以持久身份/receipt/Queue证据消除重复，未知不得重发猜测写 |
| UNKNOWN / RECONCILE | 既有唯一RECONCILE与人工结论负责收口，Coordinator跟踪；保留锁/证据，禁止第二次不安全写。不能因最新Intent到来删除未知旧执行 |
| 终态与回读 | Importer结果及合格平台回读进入Web；目标已由人工完成应形成有依据的完成/无需写结果。没有合格回读不能只以请求已发或Worker返回成功宣称业务完成 |

同SKU新价格决定是这条价格旅程的正常变化：旧动作未跨副作用边界时，尽量原子替代/取消；已跨边界先保留最新决定并等旧执行收口，再依据新鲜平台事实生成必要correction，correction仍需正常授权。旧one-shot Intent完成/过期/被替代后不持续写回；外部员工修改是正常经营行为。

## 5. 建议实施顺序与有限范围

在一个首切片PR或紧密衔接的少量PR内持续联调，不按所有后端模块先完成再接Web的顺序。

1. 以真实正式入口建立一个可运行的1 SKU改价旅程骨架，同时完成上述复用选择并补持久决定/授权交接。
2. 把Coordinator接入现有Queue Service，贯通v4发布、Importer结果、Web终态/回读；同一迭代覆盖未授权不执行、阻塞解除及重启。
3. 在同一价格路径补齐新决定替代、外部漂移、发布未知/唯一RECONCILE及组件隔离的直接回归，形成可复核证据。

首切片不实现Exposure写入、Current Commitment/实时冻结期Provider、Daily Closing、Supply、Observation Health或旧Settlement authority切换。它们按目标矩阵在后续切片开展。两处Exposure对实物上限只在相关行为受本切片影响时处理，不强制捆绑纯改价；但不能为了缩小首切片而保留价格新决定被旧Task拒绝、或授权后无人接手的问题。

不新增daemon、第二Queue、自动销售策略、复杂跨平台分配或由14-B代管恢复。既有v5/Emergency/READ_ONLY仍受原授权与身份边界保护；若共用入口受改动影响，做对应回归。真实写继续写前读取、比较旧状态、执行、写后确认。

## 6. 验收证据：同一业务旅程，不只检查组件

| 验收组 | 必须证明的结果 |
|---|---|
| 正常闭环 | 通过正式Web/Application提交与授权；同一Runtime、正式publisher/file Queue/Worker/Importer到明确终态，Web显示有来源的回读价格；Task、批次、attempt、结果引用可连起来 |
| 授权与重启 | 只有Task或PREPARED批次时不会自动执行；prepare后重启需重新确认。最终确认的持久接受边界前后注入退出，重建服务后能确定继续、跟踪、重确认或终止；不丢请求、不盲重发 |
| blocker与隔离 | 选择一种真实可解除blocker；持久交接后阻塞，再释放并重启宿主，仍由正式owner推进。单对象/Coordinator可恢复业务异常不阻断Importer、Watchdog、Review、Outbox；根本DB/schema故障可明确使宿主FAILED |
| 决定变化与未知 | 同SKU旧未发布/已发布/UNKNOWN时均可记录新有效决定；按边界替代或等待、回读再纠正。测试外部人工漂移、已人工完成与TTL失效；UNKNOWN只产生既有唯一对账，不发生第二次猜测写 |

实施验证先用隔离Runtime和临时Queue，通过正式生产Service/Repository/序列化/Importer与可控平台边界重放，注入服务重建或进程退出。允许替换真实UI/外部平台边界，但不得fake掉Authorization→publisher、Coordinator→Queue或result→Importer这些待证明的箭头；测试报告明确替身边界，不能把该验证称为实机成功。

既有Core CI照常运行。至少一次包含重启或blocker恢复的真实平台旅程，使用受控SKU、经批准的目标价格与正式人工授权，绑定运行版本和脱敏证据，才可将首切片的真实闭环Stage Goal判为PASS。若尚无现场授权/环境，完成可审查实现和隔离验证，将实机Stage Goal保持NOT YET VALIDATED，不无限追加Mock测试替代；本计划本身不授权平台写入。

大任务报告分别给Implementation Review（P1/P2/Merge Gate）和Stage Goal Validation（PASS/FAIL/NOT YET VALIDATED），并列明代码、CI、隔离旅程、实机/部署各自版本和范围。首轮冻结blocker，复审只验原问题与直接回归；达到本切片证据要求即停止扩题。

## 7. 完成之后的推进方向

首切片通过后，按业务依赖扩展Exposure、复用rollover后订单Provider的Commitment、冻结期Provider、Closing、Supply、Health；涉及库存/计划authority的部分先shadow，并按IG-04/05/09显式切换，不双计。第二平台前完成组合层设计。14-A负责集成验收与冻结，14-B负责诊断/受控风险中性工具，不成为本切片的owner或前置依赖。

当前交付的是计划与开工Goal，不是新功能完成声明。下一位开发者无需重新取得已给出的13.6验收许可；仍需遵守明确的分支、实现任务及真实副作用授权范围。
