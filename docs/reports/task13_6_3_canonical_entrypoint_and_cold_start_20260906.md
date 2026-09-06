# Task 13.6-3 入口收口与冷启动验收

日期：2026-09-06。PR：[#46](https://github.com/etereath/PRA-project/pull/46)。父任务：[#41](https://github.com/etereath/PRA-project/issues/41)。

## 范围与证据边界

从已合并 PR #45 的 main `08041bfe25a7f31f032564a2abca35e5eb5f5330` 新建分支 `codex/task13-6-3-canonical-entrypoints`，计划提交 `6c287368de7973935415d6a795cc1c8ca6e9d28d`。没有沿用 PR #44 donor 分支，也没有重新执行 G1/G2 Gate。

Task Type：Architecture / Documentation / Governance。Review Profile：主文档权威及跨模块交接 R4；机械链接、历史标注 R1。本 PR 的 PASS 只适用于文档理解与交接，不能证明 13.7 功能、实机或部署。

## 文档与正式 AGENTS 候选审查

实施者于正式替换前审查候选。此项是作者的静态审查，不冒称独立冷启动。

本轮聚焦五项：

1. G1 业务口径是否完整迁入 Canonical，日期、Supply/Commitment/Exposure/Closing 是否分清。
2. G2 原结论和 IG-01～IG-11 是否统一；目标职责是否被误写为生产能力。
3. 正式 AGENTS 是否精简指向主文档，并保留授权、UNKNOWN、实物唯一 authority、组件故障隔离与阶段边界。
4. 常用入口是否一致，历史材料是否退出施工权威且原始证据不被改写。
5. 是否可以由独立新上下文以完整情景检验，而不通过术语背诵或额外持久结构制造假完成。

核对 G1/G2 主文档与报告，重新读取当前正式 Web 路由/composition，并沿 Manual Task、Execution Authorization、Queue Service、Web query 和相关测试确认关键 gap 的证据边界。发现草稿将 Quality 写为 `/quality` 主路由；源码实际为 `/database/quality` 子路由，已修正 README 与实现图。治理输出模板把 Task Type 与 R1～R4 分列。未发现需要重开 G1/G2 的业务冲突。

候选静态审查：PASS；原临时 AGENTS 原样归档、正式根文件实际替换之后才允许执行下一节。归档直接复用原 Git blob `3042a7555ee0ece0e65d09ef5290de44e870b632`，不添加文字。

变更集中为：短 README/index/status、产品与路线、G1 业务合同、G2 实现图/目标职责、同路径治理 v2.0、历史转向和来源映射、正式 AGENTS 与临时归档。没有生产代码、Schema、运行配置、历史 evidence 或旧 Gate 报告修改。迁移详见[来源表](../rebaseline/task13_6_document_authority_inventory.md)。

## 独立 cold-start

PASS。正式 AGENTS 已实际进入 PR 文件树之后，由没有本次历史上下文的独立 AI，仅阅读固定 Canonical 集合，回答五个贯穿情景。以下保存输入版本、完整提问、原始回答与评价。负责人对最终交付的确认另行记录，不由本结果代替。

### 输入版本与独立性

执行者：独立子代理 `/root/cold_start_13_6_3`，`fork_turns=none`；不传历史聊天或答案列。正式版已提交到 PR head `a7bf4aa2919a0462c62d52046e6e3f9c6cde22c5` 后才启动；全部 8 个本地输入文件已逐字节计算 Git blob 并与远端树比对一致。

| 输入文件 | Git blob SHA | 字节数 |
|---|---|---:|
| `AGENTS.md` | `326d929cc7b4d9033bc178d9d22c0a511c93b834` | 4265 |
| `docs/business_contract.md` | `02d131118cc8f2c6a66b7c5835ad8d662d317ab4` | 23512 |
| `docs/index.md` | `ee24de29db1fd3ba8a5bab745d6c0be0bcc1cd8a` | 4207 |
| `docs/pra_review_risk_and_complexity_governance.md` | `aeddb24e9c054030b9a079dc1ab7149766e15c66` | 19991 |
| `docs/project_current_status.md` | `c12813175c905e2e33023b366423aac8d254e586` | 2718 |
| `docs/project_overview.md` | `4e3e076b94526c0d16a1b99b3f53bab1ccc1db77` | 2520 |
| `docs/rebaseline/task13_6_current_implementation_map.md` | `c94a9510f0d23a75f0da30eccd684e449825e85a` | 7716 |
| `docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md` | `15e908d3bafb77d8c1187e24d8c6b070899a966c` | 12908 |

### 完整受测提示

```text
你承担 PRA Task 13.6-3 的独立 cold-start 理解验收。你没有历史聊天；不检索其他会话，不读历史/计划/验收报告，不向其他代理询问答案，不修改文件或 GitHub。此次是固定文档理解验证，不是生产实现审查；只读下列输入是刻意限定的验收边界。

输入快照：etereath/PRA-project PR #46 commit a7bf4aa2919a0462c62d52046e6e3f9c6cde22c5，正式根级 AGENTS 已在该提交实际生效。本地目录 /workspace/scratch/bdb2acc8fc4b/PRA-project-task13.6-3 中以下文件与该提交对应。允许读取且必须完整读：AGENTS.md、docs/project_current_status.md、docs/project_overview.md、docs/business_contract.md、docs/rebaseline/task13_6_current_implementation_map.md、docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md、docs/pra_review_risk_and_complexity_governance.md、docs/index.md。不要沿链接读取其他文件，也不读目录外资料。

请先概括当前产品、当前实现与阶段状态，再回答以下五个贯穿情景。逐题给文档路径与章节依据；区分已知、目标要求和缺少的实现/平台证据，不要求复述特定类名。没有足够证据时明确说明，不自行补合同。

A. 某品种等级在当前蚂蚁平台 18:30 时订单页仍显示 D，而实时销售页面显示新周期累计8；19:00 进行历史采集；20:00确认结余40、目标生产日预测120，此时当前周期累计成交20。随后同生产日采摘估计115、包装实数113，累计成交后来达到35。解释各日期与经营参考数怎样变化、平台额度高于供给意味着什么、这些数是否已经决定实物库存该怎样记账。若未来另一平台的交易截止时刻不同，应如何处理？

B. 同范围平台可购数量先100后142，期间有PRA调整+50的执行证据。分别讨论证据充分、写结果UNKNOWN及存在无法解释员工修改时的结论。实时页面只有品种+等级+累计量且对应多个SKU；随后订单页rollover，同范围累计来源由20变成23。解释应展示什么、可以推断什么、还缺什么证据。

C. 旧人工价格目标9.5，新决定10.5；旧动作分别为尚未发布、已入队、UNKNOWN。另考虑Web在prepare之后重启、最终确认审计后但执行发布不确定时退出、员工在App改价，以及Queue Service内某一次执行业务异常。说明各情况下谁负责、触发下一步的条件、恢复与终态；区分现在已实现与目标缺口。

D. 当前观察中断，恢复扫描分别处于排队等UI资源、真正平台级失败；同一天历史Closing第一次失败、第二次失败或已成功后19:10又有普通观察。说明后续owner、风险行为、是否自动下架或重扫。假设新经营事实已能在shadow中计算，旧Settlement还在扣库存并生成计划、Web也还读旧Summary，如何决定何时切换以及回滚边界？

E. 新开发者建议同时新建Intent/Task/Attempt三张表，先完成所有Supply/Commitment/Closing模块最后集成Web，并让Task14-B Agent接管堵住的执行和销售纠正。根据文档评估建议，给出13.7第一条有证据意义的交付与14-A/14-B职责，说明今天是否能开始13.7及还需谁确认什么。

最后列出你发现的材料矛盾、无法判断处或关键理解风险；没有则写无。直接在最终回答返回完整原始回答，不替实施者宣告Overall PASS。
```

### 原始回答与评价

````text
本回答仅依据指定提交 `a7bf4aa2919a0462c62d52046e6e3f9c6cde22c5` 对应的八份输入文档；已完整阅读，未沿链接读取其他材料，未检查源码、Runtime 或平台，也未修改文件。文档对现有生产代码的描述绑定基线 `08041bfe25a7f31f032564a2abca35e5eb5f5330`，不能据此证明现场已部署或完整业务旅程已通过。

**当前产品、实现与阶段**

PRA 服务鲜切花预测性销售：在生产数量尚未完全确定时持续观察经营事实，由人类通过 Operations Web 决定价格、Exposure 和上下架，再经过确定性校验、授权、执行、回读及恢复。当前销售 Controller 是人类；未来 Agent 接入不能绕过这些基础设施。

现有资产包括正式人工 Task 创建与执行授权入口、v4/v5、Queue、Worker、Importer、Watchdog、UNKNOWN→唯一 RECONCILE、Review/Outbox，以及唯一 DB 实物库存账本。仍缺新的 one-shot Intent 与替代语义、最终确认后的可靠持久交接、统一持续执行 owner、Commitment/provider、Supply/Carryover、独立 Closing、Observation Health，以及旧 authority 和 Web 读模型切换。旧 20:00 seller day、Settlement/Summary 接线仍在代码中；这是已登记的实现偏差。

状态页记载：13.5 STOPPED/SUPERSEDED，不继续 7G；13.6-0、G1、G2及增量吸收已 PASS；13.6-3 IN PROGRESS；13.6 Overall NOT YET VALIDATED；13.7 NOT READY。

依据：`docs/project_overview.md`“产品目标”“路线与出口”；`docs/project_current_status.md`状态表及“当前能力与限制”“13.6-3 验收”；`docs/rebaseline/task13_6_current_implementation_map.md`§1—5；`AGENTS.md`“阶段与权限”；`docs/index.md`“Canonical 与当前实现”。

**A．跨日、供给收敛与经营参考数**

设 D 是刚结束的交易日，目标生产日记为 P。当前蚂蚁平台以 Asia/Shanghai 的18:00为唯一销售换日时刻。

| 情景时点 | 日期及事实解释 | 经营参考 |
|---|---|---|
| 18:30 | `platform_trade_date=D+1`；订单页可见日期仍为 D；实时累计8属于 D+1 | 当前同范围 Commitment 可由合格实时直接来源校准为8。此时尚无题设确认的 Carryover、Supply，不能补算完整参考数 |
| 19:00 | 独立 Closing 采集冻结的 D 日历史，需验证日期、范围、尾部或可信空页 | 不计入 D+1 当前 Commitment |
| 20:00 | 仍为 D+1；确认 Carryover=40，P日 Forecast=120，当前 Commitment=20 | `40+120−20=140` |
| P日 Harvest=115 | 115覆盖同生产日的120 | 若 Commitment仍为20，参考数为135 |
| P日 Packaged=113 | 113覆盖115，当前有效 Daily Supply为113 | 若 Commitment仍为20，参考数为133 |
| 当前累计后来达到35 | 使用当前累计35替换此前20 | `40+113−35=118` |

135和133以成交仍为20为条件；若成交与供给变化交错，应使用各时点实际有效事实，不能把这两个中间值当成已经观察到的结果。

120、115、113是同一生产日供给的逐步收敛，不相加。40是已经排除 D 日旧承诺占用的可继续销售剩余，不能再扣一次旧承诺；113也不会自动变成下一周期 Carryover。

平台可购额度是 Exposure。它高于供给，甚至多平台 Exposure 之和高于供给，都不能单独证明已经超卖。风险还需结合实际 Commitment、成交速度、剩余时间、安全缓冲和人工判断。

这些参考数尚未决定实物库存何时入账、扣减或恢复。Exposure变化不创造供给，Closing成功也不是扣实物理由。实物账本继续保持唯一权威；具体 physical/accounting event 契约与跨日不重复扣减证明留给13.7的IG-05。这里也不能反向推导出“全面禁止销售驱动记账”。

未来平台须使用自身可追踪的 capability/profile，定义时区、cutoff、订单页 rollover、实时来源、cadence、Closing offset及有效版本，不能照搬18:00，也不能重新引入统一20:00销售换日。跨平台 Commitment 汇总须先明确日期、范围、单位和去重条件；第二平台接入前还有独立架构 gate。

当前实现尚保留旧日界、Settlement和库存接线，不能声称上述目标链已运行。

依据：`docs/business_contract.md`§3—10、§17、§19、§21 Scenario A/B、§22；`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`§3 IG-05/IG-10、§4—5、§7；`docs/rebaseline/task13_6_current_implementation_map.md`§3。

**B．Exposure差值、UNKNOWN、聚合粒度与来源接管**

平台可购量100→142，观测净变化为+42。若同范围、同单位、同观察区间内，PRA的+50已被证明成功，其他非销售变化也已充分解释，且符合有效 observation contract，则扣除+50后的净消耗为8，可以贡献 QUICK-derived 销量估计。它仍是辅助估计，不因算式成立就变成直接订单事实。

三种证据情况应分别处理：

- **证据充分：**展示可购量观察、已确认+50调整及其关联证据；满足上述条件后，展示辅助估计8及来源、时间和质量。
- **写结果为UNKNOWN：**+50不能当成已确认成功，也不能当成失败。保留UNKNOWN，沿唯一 RECONCILE 收口；仅凭最终142不能证明写入成功，更不能盲目再写一次或确定销量为8。
- **存在无法解释的员工修改：**可购量变化不能完全归因于销售。保留未知或待校准状态；只有已证明的非销售变化才能作为扣除项，不能为连续曲线猜销量。

实时页面只证明“品种＋等级＋当前交易日累计量”，且对应多个 SKU 时：

- 可以展示该聚合范围的累计量、来源、交易日、观察时间、freshness及质量。
- 不能按平均、比例或任意规则拆成每个 SKU 的直接销量；应保留 SKU 未分配或缺失状态。
- 不能虚构订单ID、金额、下单时间。来源很可信，也不代表其粒度足够细。
- 只有能证明该品种等级唯一映射一个内部SKU，才可安全投影；否则还需要更细的直接事实或足够的映射证据。

订单页 rollover 后，若新的当前订单来源在同一交易日、同范围、兼容粒度下合格，累计23接管此前20，当前 Commitment是23，不能相加为43。旧20及QUICK估计保留为证据。20与23的差异也不能仅凭来源切换就虚构出三笔或三单位新增订单；要解释增量仍需可比较的时间和来源事实。粒度不兼容时应保留缺失或不可分配状态。

实现与平台证据边界：现有Product/Order观察是复用资产，但冻结期实时 Provider 尚无正式实现。IG-07要求真实READ_ONLY定位、字段类型/空值/粒度验证、证据重放和回归后，才进入正式authority selector。Closing的`purchase_sequence`也尚未正式采集持久化；已有`occurrence_no`不能替代复购序号。

依据：`docs/business_contract.md`§6—8、§11.3、§14、§16、§21 Scenario E、§22；`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`§2、§3 IG-07、§4；`docs/rebaseline/task13_6_current_implementation_map.md`§2—3。

**C．新旧价格意图、重启、外部改价与单次执行异常**

首先应持久记录新的有效决定10.5；旧开放Task只影响调度，不能阻止记录新决定。它只替代明确涉及的价格维度，不顺带改写其他维度。产生纠正Task也不等于获得执行授权。

| 情况 | 目标责任、触发和收尾 |
|---|---|
| 旧9.5动作尚未发布，且能证明未跨副作用边界 | Business Application按明确范围尽量原子地supersede/cancel/replace；新动作进入正常人工授权。仅“看不到Queue文件”不足以证明从未发布 |
| 旧动作已入队 | 保留旧动作及执行历史，由持续执行owner协同既有执行链跟踪完成、回读或对账；收口后对比最新有效10.5决定，必要时生成需正常授权的纠正Task |
| 旧动作UNKNOWN | 沿既有唯一RECONCILE，通过只读对账和必要人工结论收口；不能删除、假装取消或再猜测写。之后再处理最新有效决定 |
| Web在prepare后、最终确认前重启 | preparation可失效。owner仍是Human/Web；用户可以重新prepare/确认、替代、取消，或由有效期终止。后台不能因此自动执行PENDING |
| 最终确认审计完成，但发布不确定时退出 | Execution Authorization必须通过最小持久交接与持续执行owner承担恢复；查batch/operation/attempt/Queue/receipt。已有执行账本则跟踪；尚未建立则依据持久证据安全恢复或明确重确认，不能只凭audit判断成功/失败，也不能盲重发 |
| 员工在App改价 | 重新观察的平台事实为当前事实；保留历史Intent与执行审计。过时one-shot Intent默认失效或重新确认，不能自动反复写回旧目标 |
| Queue Service中一次执行发生业务异常 | 该continuation/attempt保留明确状态、原因及下一步，局部隔离并由持续owner按条件重评估、恢复、交人工或终止；不能拖垮无关Importer、Watchdog、Review、Outbox。DB/schema/process根本故障可使宿主明确FAILED |

持续执行owner只接手已经形成持久execution continuation的对象，不能扫描所有PENDING自动执行。blocker解除、周期重评估、结果导入或对账结论是相应下一步的触发；重启从持久事实重新发现工作。既有Importer/账本负责结果与Task投影，持续owner完成业务衔接；终态保留结果和回读，不因旧目标未被永久维持而自动重开one-shot Intent。

每次真实写仍要经过写前读取、预期旧状态比较、执行、写后回读。外部改价不是绕过这些步骤的理由。

**当前已实现**的是人工create、prepare/submit、授权审计、v4/v5发布及既有执行/结果/UNKNOWN链。**当前缺口**包括：

- 开放Task仍可能阻断同SKU＋平台的新建，尚未满足“先记录新有效Intent”。
- preparation和幂等记录在Web内存。
- 最终确认audit之后、可靠执行账本/Queue交接之前存在IG-08窗口。
- 长期Queue Service尚未装配目标业务持续owner。
- 宿主已有部分局部异常处理，不能据此证明所有异常及新增持续owner都已隔离。

因此，上表是已采纳目标责任，不能据输入文档声称这些恢复路径今天都可完成。

依据：`docs/business_contract.md`§14—16、§21 Scenario F；`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`§1—2、§3 IG-01/02/03/08、§6；`docs/rebaseline/task13_6_current_implementation_map.md`§1—2；`AGENTS.md`“硬边界”；`docs/pra_review_risk_and_complexity_governance.md`§2.2、§12—13。

**D．实时观察故障、Closing失败及authority切换**

当前观察中断，应根据该Provider的expected cadence、capability和可信fallback评级，不能统一按固定分钟数判断：

- 首次超cadence为S1；主校准连续缺失但仍有可信fallback为S2。
- 没有足够可信实时校准时进入S3，由Automation侧健康责任请求Recovery Calibration。
- 恢复扫描已排队或合法等待UI资源时，保持`S3/RECOVERING`；排队、人工暂停Automation或合法lease占用不是平台失败。
- 主动恢复实际确认平台级/链路级失败后立即S4，无需再等待时间升级。单SKU故障不自动变成平台S4。
- 恢复成功后依据新证据恢复或重新评级。

风险行为分别是：S2增加风险的动作需额外人工确认；S3普通增加风险动作默认阻止，管理员可经正式授权显式覆盖；S4增加风险动作阻止。降低风险的人工入口仍可保留，但必须满足既有写前核对和写后确认。Observation S4不继承价格保护Emergency S4的自动下架权限。

Closing由Automation独立负责：

| 情况 | 后续行为 |
|---|---|
| 第一次Closing失败 | 故障报告＋一次自动重试 |
| 第二次失败 | Closing S2＋人工Review，停止自动重试；单纯Closing故障不升S3/S4 |
| 已成功且完整性验证通过，19:10又有普通观察 | 成功记录锁定该平台/交易日自动Closing采集；普通观察不得重扫或持续改写历史Closing |
| 成功后发现历史问题 | 管理员显式维护，记录操作者、原因和来源；具体维护入口由13.7最小设计，未规定只能重新扫描generation |

若同一故障也影响实时Provider，实时Health独立评级，不能被Closing最高S2压低。输入材料没有承诺S4之后无限自动重试的次数或间隔，不能自行添加。

关于shadow与旧链：新事实能够计算只是切换前置条件。旧Settlement仍扣库存、生成计划时，新事实只能保持shadow/read-only。切换顺序应为：

1. 比较新旧证据和输出，通过相关能力验收。
2. 明确IG-05唯一physical/accounting事件契约，以跨日、校准和回滚数值证明不重复扣减。
3. 停止旧Settlement业务authority及相关Job写路径，完成baseline/migration对齐。
4. 启用新authority，并在同一gate切换Today、Quality及相关Web当前销售读模型；旧Summary仅用于历史/legacy展示。
5. 验证跨日、重启、blocker、UNKNOWN和外部人工漂移，再考虑物理删除旧结构。

不能让新Commitment与旧Summary同时成为扣减或计划authority，也不能后台已切换、Web仍把旧Summary显示成当前经营事实。IG-05未裁决及证明前，不得切换相关实物扣减接线。

回滚可调整read path、Job或selector，但不能删除观察、Closing、Intent/Task/execution、旧Summary或库存transaction证据，也不能重新同时启用两套authority。

这些Health、Closing和cutover是13.7目标缺口；已有Incident、Job/Event、Review、读扫描及库存ledger是复用基础。

依据：`docs/business_contract.md`§11—13、§19、§21 Scenario C/D/G；`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`§1—3 IG-04/05/09、§4—5；`docs/rebaseline/task13_6_current_implementation_map.md`§1、§3—4。

**E．新表、横向实施顺序与Task14职责**

该建议的三部分都不符合当前交接：

- **同时新建Intent/Task/Attempt三张表：**逻辑责任不等于物理表。先审计现有Task/origin/history、batch、operation、attempt、receipt及Automation Event能否承担责任；只有证明具体事故与复用不足，才增加最小持久结构。这也不等于绝对禁止新增表。
- **先完成全部Supply/Commitment/Closing再集成Web：**推迟了最关键的跨组件验证。文档要求最薄纵切和Web随切片接入，不能把横向模块完成当成业务箭头成立。
- **让14-B接管堵住的执行和销售纠正：**会把13.7确定性责任转交未来Agent，违反IG-11。恢复必须不依赖Agent在线；销售纠正仍由既有业务/执行链处理并正常授权。

13.7第一条有证据意义的交付是：**一个SKU、一次人工UPDATE_PRICE，从持久Intent/Task，经Human Authorization和可靠持久交接，通过既有v4、Queue、Worker、Importer，到终态和平台回读；至少覆盖最终确认崩溃，或重启/阻塞恢复路径。**

证据必须表明谁接手、何时继续、失败如何恢复或终止，而不只是新增表和几个Service单测。实际集成以同一Runtime、正式Service的跨组件旅程为依据；既有Watchdog/Importer已完整处理的部分应复用，不复制状态机。后续按依赖扩展Exposure、rollover后订单Provider、冻结期Provider、Closing、Supply和Health，有authority重叠则先shadow。

14-A负责多品种多动作、正式授权、阻塞和重启恢复、UNKNOWN/RECONCILE、Provider rollover、集成验收、发布与版本冻结。

14-B首版负责诊断、状态解释、Incident/Health辅助及受控风险中性工具，可经确定性接口请求READ_ONLY恢复校准、解释blocker、附加诊断或查询receipt。它不能创建或批准销售Intent、提交或调整销售Task、代替最终人工确认、执行Closing管理维护、直接写DB/Queue/v4/v5或修改权限与emergency标记。两条线可在接口稳定后并行，真实接入仍需共同integration gate与相应授权。

**按输入快照，今天不能开始13.7生产功能开发。**状态页仍为13.7 NOT READY、13.6 Overall NOT YET VALIDATED。还需完成并验证Canonical收口与正式AGENTS交付、记录独立cold-start结果，以及由负责人最终确认13.6交付；PR #46自身最新head的CI须以实际检查为准，不能借用main或#45的CI。当前回答只是独立理解材料，不替实施者或负责人宣告Overall PASS。

13.6通过也不等于13.7功能已完成，更不自动授予合并、部署或真实平台写权限。13.7中的常规物理设计应由实施者按复用证据判断；影响业务口径、近期范围或授权的缺项应集中裁决，明显架构边界冲突提交负责人决定，不能把所有实现选择重新变成审批。

依据：`docs/business_contract.md`§2、§15、§18、§20；`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`§1、§3 IG-06/08/11、§6—7；`docs/project_overview.md`“路线与出口”“开发策略”；`docs/project_current_status.md`状态表及“13.6-3 验收”；`AGENTS.md`“阶段与权限”；`docs/pra_review_risk_and_complexity_governance.md`§5、§6.3、§13、§19—20。

**材料矛盾、无法判断处与关键理解风险**

未发现八份材料之间必须互斥的现役业务合同矛盾。目标规则与旧实现不一致已被明确标为gap，不能把它们误判为两个同时有效的业务合同。

仍无法仅凭输入判断：

- PR #46该head实际CI、现场部署、真实平台运行及完整旅程验收结果。
- 冻结期Provider的实际字段类型、空值、映射和完整采集证据；`purchase_sequence`正式实现也明确缺失。
- IG-05最终采用哪个实物记账事件，以及相关迁移、校准和回滚如何证明不重复扣减。
- IG-08及持续执行owner最终采用何种最小持久结构，是否已通过跨崩溃恢复验证。
- 未来平台的具体capability值，以及S4后进一步恢复安排；文档没有给出可直接套用的统一参数。

关键理解风险是把经营参考余额当成实物库存、把Exposure差值当成直接销量、把高可信聚合量拆成SKU事实、把有审计当成已可靠发布、把Observation S4当成自动下架授权，以及把“架构已采纳/CI通过”当成“实现、部署和阶段验收均完成”。这些边界在材料中已有明确区分。
````

### 逐情景评价

由实施者对上述独立原始回答按计划的有限覆盖标准评价，不以模型自行声称理解作为通过依据。

| 情景 | 实际回答与关键判断 | 结果 |
|---|---|---|
| A 跨日经营 | D/D+1/P 区分；20:00 不换日；140→135→133→118 说明中间成交条件；Exposure 不等于实物；IG-05 与平台 capability 未冒称已完成 | PASS |
| B 观察到事实 | 有条件 QUICK=8；UNKNOWN/员工未知改动不伪造 adjustment；聚合高可信不拆 SKU；20→23 替换；指出 READ_ONLY 与 purchase_sequence gap | PASS |
| C 决定到终态 | 先记新 Intent；未发布与 queued/UNKNOWN 分开；prepare 与 final-confirmation crash 区分；持久 owner、唯一 RECONCILE、外部漂移与局部故障隔离清楚 | PASS |
| D 故障及切换 | UI 排队与平台失败分开；S2/S3/S4 风险权限正确；Closing 一次 retry 后人工、成功锁定；old OFF→baseline→new ON/Web 同 gate；回滚不双计、不删除历史 | PASS |
| E 陌生开发者接手 | 不机械建三张表、不推迟 Web 纵切；1 SKU UPDATE_PRICE+恢复；14-A/14-B 分开；不让 Agent 填补 deterministic owner；拒绝越过 Overall Gate | PASS |

Cold-start Validation：PASS（5 组完整情景）。未发现关键误解；不追加第二轮背诵考试，不新增业务规则。参考覆盖点作为静态复核辅助，不把“14 条”变成 14 个新增 Gate。

受测者指出的未知均有既定责任：真实平台 Provider/字段证据由 IG-07 接手；实物记账与 no-double-count 由 IG-05；durable handoff 物理选择和恢复由 IG-06/08；未来 capability 由 IG-10。S4 后不得据材料杜撰无限自动重试参数。CI/部署不在冷启动输入证明范围，其中本 PR CI 由下述真实检查补证，现场部署不作结论。这些不是新 blocker，也不表示恢复可以无人负责。

在冷启动之后，仅向状态页/索引追加本次证据和待负责人确认状态，没有修改受测 AGENTS、业务合同、产品路线、实现图、目标职责或治理语义。因此不再重跑全部冷启动。

## 验证与结论

文档静态检查：19 个变更文件均严格 UTF-8 回读，无替换字符；相对链接/路径 88 项存在性检查通过，抽查中文标题和关键段落。远端 Git tree diff 无生产代码、Schema、tests、运行配置、历史 Gate/evidence 内容改动或文件删除。临时 AGENTS 的 8,579 字节与原 Git blob 完全一致；原 13.5 AGENTS 归档也未改变。

语义提交 `a7bf4aa2919a0462c62d52046e6e3f9c6cde22c5` 的 [Core CI](https://github.com/etereath/PRA-project/actions/runs/34023342547) 已完成：[Windows Core](https://github.com/etereath/PRA-project/actions/runs/34023342547/job/101459846367) SUCCESS、[Linux Core](https://github.com/etereath/PRA-project/actions/runs/34023342547/job/101459846255) SUCCESS，包含仓库既有 tests、evidence binding、build/package 与 smoke gates。没有为文档额外重复本地全量业务测试，也没有执行实机。

本报告、状态与索引属于后续补证提交；该提交的 CI 必须独立读取 [PR #46 Checks](https://github.com/etereath/PRA-project/pull/46/checks)，不把上述语义提交 CI 冒充后续 Head 结果。最终回读结果记录在 PR 正文，避免为了把报告写入自身提交 SHA 而无限生成补证提交。

- Documentation Implementation：PASS。
- Architecture Review：PASS（G1/G2 收口及独立理解，非 13.7 实现验收）。
- Implementation Review：P1=0 / P2=0；已验语义提交 Merge Gate=0，最终补证 Head CI 以 PR 检查为准。
- Task 13.6-3 / Overall Stage Goal：NOT YET VALIDATED。
- Owner final confirmation：NOT YET CONFIRMED。
- Task 13.7 Readiness：NOT READY。

PR 保持 Draft。没有修改生产文件、真实 Runtime 或平台，也没有执行合并。依据正式 AGENTS 的阶段规则，负责人对最终交付确认前不得把 Overall 写为 PASS。
