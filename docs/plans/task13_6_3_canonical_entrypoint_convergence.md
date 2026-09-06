# Task 13.6-3：Canonical 入口收口与正式 AGENTS 冷启动验收计划

日期：2026-09-06  
仓库：`etereath/PRA-project`  
核验基线：`main@08041bfe25a7f31f032564a2abca35e5eb5f5330`  
父任务：[Issue #41](https://github.com/etereath/PRA-project/issues/41)  
本文状态：IN PROGRESS；用户已授权创建 PR 并正式开始 13.6-3。总体验收与 13.7 readiness 以 [当前状态](../project_current_status.md) 为准。

策略修订：2026-09-06 阅读用户补充的历史总纲后，修正审查分级与冷启动验收组织方式。只吸收开发方法，现行业务语义与阶段进度继续采用本计划核验的 G1/G2 基线。

本次从 GitHub 读取最新 main、PR #43/#45 正文与讨论、两 PR changed files、#45 提交列表、Issue #41 及评论、根级 AGENTS、G1/G2 主文档和增量吸收材料，并抽查相关生产代码、测试源码和 CI。上述为规划阶段的读取记录；未重新开展 13.6-2 全量审查。当前进入纯文档实施，不操作生产环境。

## 1. 结论与任务目标

当前可以进入 **Task 13.6-3 的规划与纯文档收口**。PR #45 已合并；其 G2 与增量吸收结论作为已完成工作承接。Task 13.6 Overall 仍为 `NOT YET VALIDATED`，Task 13.7 仍为 `NOT READY`。

13.6-3 的交付能力是：一个没有参与历史聊天的 AI，从正式 AGENTS 和精简的 Canonical 阅读集出发，能准确解释 PRA 的真实业务、当前实现、目标职责、已知缺口及下一阶段边界，并能为结论指出来源。

这一阶段要消除相互竞争的“项目真相”。不要求实现 Intent、Coordinator、Commitment、Closing、Supply 或 Observation Health，也不以这些功能尚未实现判定 13.6-3 失败；必须把这些缺口准确交给 13.7。

## 2. 已核验的 GitHub 事实

| 证据 | 本次核验结果 | 对 13.6-3 的影响 |
|---|---|---|
| [最新 main 提交](https://github.com/etereath/PRA-project/commit/08041bfe25a7f31f032564a2abca35e5eb5f5330) | PR #45 的合并提交，合并时间 2026-09-06 07:41:21 UTC | 后续施工重新解析最新 main，从该主线承接 |
| [PR #43](https://github.com/etereath/PRA-project/pull/43) | 已合并；head `dde33afa`；6 个 Markdown 文件 | G1 业务基线及已关闭 OD-01～OD-06 不重复裁决 |
| [PR #45](https://github.com/etereath/PRA-project/pull/45) | 已合并；head `61ad48d0`；6 个 Markdown 文件 | 承接原 G2 与增量吸收，包含 IG-01～IG-11 |
| [PR #45 head CI](https://github.com/etereath/PRA-project/actions/runs/33995743603) | Windows Core、Linux Core 均 success | 是该 head 的既有自动验证证据 |
| [main CI](https://github.com/etereath/PRA-project/actions/runs/34019822157) | Windows Core、Linux Core 均 success | 不等于 13.6 cold-start 或 Stage Goal 已通过 |
| 根级 `AGENTS.md` | 仍为 TEMPORARY TASK 13.6 REBASELINE INSTRUCTIONS | 正式版审查、临时版原样归档、根文件替换、冷启动的顺序必须保持 |

本轮未在本地执行 pytest、未重新运行 CI、未连接真实 Runtime DB 或平台。以下代码发现是读取 main 的结果，不是本轮运行验证。

### 2.1 入口中的具体混淆

- `README.md` 仍描述旧 Dashboard/Business Inputs 等页面，以及将公共库存保存回 `products.xlsx` 的旧路径；它不适合作为当前产品和能力入口。
- `docs/index.md` 将旧计划、当前实现说明和历史验证报告混排，缺少清楚的权威层次。
- `docs/project_current_status.md` 顶部正确写了 13.5 STOPPED，但仍停留于 13.6 即将开始的叙述，正文大量保留旧双日界及历史进展。
- G1 业务基线文件仍名为 `task13_6_business_baseline_draft.md`，并保留候选与旧 Gate 状态。正式 G1 报告已经明确覆盖这些状态；新读者不应继续靠阅读多份报告自行拼出当前结论。
- G2 实现图、目标矩阵仍带 G2 INPUT/DRAFT 元数据，IG-08～IG-11 又在增量材料中。正式阅读集需要把已采纳结论放在对应现役正文。
- `docs/project_overview.md` 和 `doc/project_overview.md` 同时存在，均包含“当前不对接具体平台”等早期描述；后者不能成为另一套当前总览。
- `docs/ai_agent_integration_spec.md` 仍写实际 Agent 接入不属于 Task 14，与 G1/G2 的 14-A/14-B 分工不一致。
- 仓库 `docs/pra_review_risk_and_complexity_governance.md` 仍是 v0.1 评审草案。本轮用户提供的《PRA 项目审核治理规范 v2.0》与当前明确指令，应被正式入口吸收；不能假称 v2.0 已在 main 生效。

### 2.2 抽查的生产代码与测试

| 路径/对象 | 读取结果 | 文档应怎样表达 |
|---|---|---|
| `app/services/manual_task_orchestration.py` | 创建 Runtime Task；存在开放 Task blocker；465 行附近仍有 Exposure 对 DB current_qty 的硬上限 | 当前事实与目标 supersession/Exposure 语义分列 |
| `app/services/execution_authorization.py` | 已有 prepare/submit；preparation 和幂等映射在内存；303 行附近先记录授权审计，再发布 v4/v5；499 行附近仍有库存硬上限 | 复用正式人工授权，明确 IG-08 durable handoff 缺口 |
| `scripts/run_shadowbot_queue_services.py` | 已组合 Importer、Watchdog、登录监视、Review reminder、Outbox；多个组件有异常隔离；未装配目标 Coordinator | 写为现有宿主及待增加职责，不宣称完整隔离/执行闭环已验证 |
| `app/operations_web/queries.py` / `today()` | 今日销售指标仍由当前 PLATFORM Summary 计算 | 13.7 authority cutover 同时切换 Web 当前销售读模型 |
| `tests/test_manual_task_orchestration.py` | 明确测试创建不写 Queue、超 DB 库存上架被拒绝 | 测试证明现有合同，不能反向证明新业务模型正确 |
| `tests/test_execution_authorization.py` | 覆盖主体、精确任务集合、授权、v4/v5 调用；发布函数使用测试替身 | 不能用这些孤立测试冒充重启后的完整经营旅程 |

以上缺口与 G2 的主要结论一致。本阶段只更新描述和后续责任，不顺手修生产代码或测试。

## 3. 审核配置与复杂度预算

Task Type：**Architecture / Documentation / Governance**。

Review Profile 按变更影响判断，不能仅由文件格式或是否有运行时副作用决定：公共业务合同、跨模块责任和正式 AGENTS 的规范性/授权边界按 **R4 设计审查**；已批准内容的机械整理、链接维护、历史标注可按 **R1**。R4 的审查范围限于本次变更可能造成的业务误述、责任遗漏或错误授权，不重新开展已通过 G1/G2 的全量审查，也不自动要求文档修改重跑实机或本地全量业务测试。

本轮聚焦五个问题：

1. 常用入口是否给出唯一、一致的当前方向？
2. G1 语义和 G2 全部采纳结论是否完整进入现役正文？
3. 当前实现、目标能力、历史证据是否分开？
4. 授权、持续执行、恢复和终态责任是否能被冷启动读者准确复述？
5. 最终生效的 AGENTS 与 Canonical 阅读集是否通过独立理解验收？

复杂度预算：新增生产表、字段、状态机、Service、锁、daemon、恢复链均为 0；不修改运行配置、生产代码、Schema、真实数据或平台。不新建文档生成框架或专用永久测试系统。尽量一个文档 PR 顺序完成收口与验收。

### BOUNDARY CONFLICT：旧治理材料的合同权威

旧 v0.1 文档强调“不修改已经冻结的任务合同”；如果正式 AGENTS 继续将这句话解释为历史施工边界高于业务目标，会与用户现行治理要求冲突。

推荐处理：在现役治理正文中明确“真实业务目标 → 必要安全/数据硬不变量 → 架构原则 → 当前施工边界 → 当前实现方式”，保留复杂度预算、风险分级和复审收敛规则。旧版本保留历史身份。用户已明确本轮治理优先级，无需重新询问是否允许纠正这处入口冲突。

发现新的实质业务矛盾时，单独报告证据和修改方案；不得借文档重写静默改变已确认语义。

## 4. 推荐文档结构：按责任收口，避免整套复制

优先升级既有正文和入口。以下是职责分配；文件改名应先检查入链和证据绑定，不把目录美化作为目标。

| 文档/入口 | 收口后的职责 | 处理方式 |
|---|---|---|
| `README.md` | 一页项目入口：产品定位、当前阶段、短阅读顺序、当前启动/运维文档入口 | 重写陈旧能力说明；规则细节只链接 |
| `docs/index.md` | 文档身份和导航 | 分为 Canonical、Current Implementation & Operations、Historical/Draft；正文保留一个权威角色表 |
| `docs/project_current_status.md` | 唯一当前阶段状态页 | 列 G1/G2 已通过、13.6-3 当前状态、13.7 readiness、证据 SHA 和链接；缩短历史时间线 |
| `docs/project_overview.md` | 简短产品导读与 Roadmap | 用现有文件承载 13.6→13.7→14-A/14-B，无需额外建立重复 Roadmap |
| G1 business baseline 正文 | 唯一业务语义 | 升级为已采纳基线；建议去除文件名中的 `_draft`，同步入链；历史 Closure/G1 报告保留 |
| G2 current implementation map | 指定 SHA 的当前实现说明 | 保留代码路径和事实限制；从候选状态升级，不能把目标写成现状 |
| G2 target responsibility/gap matrix | 唯一目标职责、实现差距及 13.7 handoff | 合入 IG-01～IG-11 与增量采纳结论；把未采纳设计明确排除；不要求读者再跨报告拼装约束 |
| `docs/pra_review_risk_and_complexity_governance.md` | 唯一现役审核治理 | 吸收用户 v2.0 和当前指令，保留仍适用的风险/复杂度条款，清除冲突 |
| `AGENTS.md` | 精简长期工作指令与必读路径 | 由上述 Canonical 生成，不复制整份业务规范 |
| `doc/project_overview.md`、旧 business/Agent spec 等 | 历史或特定实现参考 | 顶层导航明确降级；非绑定文件可加简短指向；历史证据内容不重写 |

正文数量不是验收指标。关键是同一规则只有一个现役维护位置。Current Status 指向完整实现图，不复制实现细节；Roadmap 指向 handoff，不再维护第二套 gap 矩阵。

源于新治理附件的内容需标注为本轮用户输入，源于 G1/G2 的内容绑定合并证据。对历史报告、原样归档文件、hash-bound evidence，只改上层分类/链接，不为了消除搜索结果中的旧词而改写证据。

## 5. 施工顺序与交付物

### A. 固定施工基线与阅读范围

实际施工开始时重新核验最新 main SHA、工作区状态和适用 AGENTS；不从旧 PR 分支继续。如果 main 已前进，核对相对本计划基线的相关变化。

读取已合并 G1、原 G2、增量 G2 及吸收补充；PR #44 只作为 donor，未采纳表集合/Dispatch 状态机等不得进入 Canonical。建立简短的“原位置→现役位置→历史位置”映射，放进本阶段报告即可。

### B. 收口 Canonical 正文和入口

升级业务基线、当前实现图、目标矩阵；把 IG-08～IG-11 合入对应现役章节，并保存采纳来源。更新 README/index/status/overview 与治理。

明确两个时间层次：当前蚂蚁花团为 18:00 唯一销售日界、19:00 Closing、20:00 planning；未来平台通过 capability 给出 cutoff/rollover/cadence，不将 18:00 硬编码为所有平台共用规则。

明确两种“尚未完成”：13.6-3 缺的是入口及冷启动验证；13.7 缺的是生产能力。当前 13.7 的最小物理实现选择仍保留余地。

### C. 生成并审查正式 AGENTS

正式 AGENTS 应包括：

- PRA 定位、当前人工 Controller、Canonicals 阅读顺序；
- 事实来源与证据等级，当前实现必须读取指定 SHA 的代码；
- 三项同级审核目标、BOUNDARY CONFLICT、必要性与复审停止规则；
- 授权、写前/写后读取、UNKNOWN/唯一 RECONCILE、正常外部人工修改等硬边界；
- 平台隔离与复杂度约束；
- 当前任务从 status/roadmap 获取，未通过 13.6 Overall Gate 不开始 13.7；
- 仓库变更、UTF-8、历史证据保护和未经用户明确要求不得 merge/结束 Draft/修改分支等工作规则。

AGENTS 不写成完整业务词典，不重复固定全部字段、Schema、类名、IG 条目正文，也不重新恢复旧 13.5 must 条款。

### D. 正式切换

候选版通过文档审查后：

1. 将原临时 AGENTS 原样归档，例如 `docs/archive/AGENTS_task13_6_temporary.md`；核对字节一致。
2. 正式版替换根级 `AGENTS.md`。
3. 检查没有意外的嵌套 AGENTS 覆盖现役规则。
4. 固定用于验收的提交 SHA 和输入文件列表。

这里的“生效”指验收工作树中，根级正式 AGENTS 已实际替换并会被新 AI 加载；不要求为做验收先合并 PR。未来合并仍需用户明确指令。

### E. 独立 cold-start 验收与阶段收口

正式 cold-start 由项目负责人主持与验收；实施者预检单独记录。使用新的 AI 会话/独立上下文，只提供仓库定位、已生效的正式 AGENTS、Canonical 阅读集和中性情景问题。不传历史聊天摘要、当前交接长提示或预先写好的答案。

固定阅读集为：`AGENTS.md`、`docs/project_current_status.md`、`docs/project_overview.md`、`docs/business_contract.md`、`docs/rebaseline/task13_6_current_implementation_map.md`、`docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`、`docs/pra_review_risk_and_complexity_governance.md`、`docs/index.md`。本计划及历史报告/答卷/评分表不属于受测阅读集，不沿其链接读取答案。

启动新会话前，把受测 SHA 的正式 AGENTS 与阅读文件实际放入隔离工作树，确认启动时会读取的是这一版；记录是否注入旧 AGENTS、项目记忆或历史答案。在已注入旧上下文的会话内再用 git show 读取正确文件，只解决文件版本，不消除上下文影响。无需为此建设审计系统；如实保留主持人的运行记录和受测者披露即可。

当前参与整理的 AI 可以准备问题与评分依据，但其自答不能作为独立验收证据。受测 AI 不读取评审方答案或历史 Review 报告中的结论；可以从 Canonical 的证据链接识别来源，但本轮理解任务不依赖重读历史材料才能成立。

保留：输入提交 SHA、AGENTS 内容版本、阅读文件列表、受测提示、原始回答、逐项判断、误解修复及复测结果。用一份 cold-start/阶段报告承载，不建立额外系统。

全部文档与 cold-start 通过后，再向负责人展示最终阅读入口、核心情景结果和仍未实现的能力，完成针对最终交付物的确认。这是根级临时 AGENTS 和 Issue #41 的 Overall Gate，不重新索要已关闭 OD-01～OD-06 的批准。

## 6. Cold-start：完整情景理解与有限覆盖

验收评价业务推理、证据边界和责任连续性，不要求背出类名、逐字复述规范或完成固定数量的问答。使用一个品种等级、两个相邻交易日及同一批供给/成交/操作事实贯穿情景；只有为检验必要边界时才加入明确的分支条件，避免每章分别采用互不兼容的理想案例。

推荐组织为五组完整情景：

| 情景组 | 主要检验内容 |
|---|---|
| 一轮跨日经营 | 供给覆盖、Carryover 与 Commitment、18:00/19:00/20:00 及订单页显示日；数值口径前后一致 |
| 观察到经营事实 | Exposure 调整证据、confidence/granularity、直接 Provider 接管、无法证明的数量如何表达 |
| 一次决定走到终态 | 新旧 Intent、授权前后、发布边界、重启、外部人工修改、UNKNOWN、组件故障隔离及下一步 owner |
| 故障、恢复与 authority 切换 | Observation 与 Closing 各自失败路径、S4 权限、shadow/cutover、Web 切读与 no-double-count |
| 陌生开发者接手 | 当前实现与目标、已验证与未验证、13.7 首条纵切、复用选择、14-A/14-B 及禁止擅自执行的动作 |

以下 14 条保留为评审方的参考覆盖点，不是每轮必须逐条提问的固定考试，也不新增 14 个独立阶段门禁。受测提示只提供情景事实和问题，去掉答案列；允许受测者指出材料不足，不强迫猜测不存在的实现或平台事实。

| 情景/问题 | 必须答对的要点 |
|---|---|
| 现在谁决定销售动作？下一任务是什么？ | 人工 Operations Web；13.6-3 收口；13.7 等 Overall PASS；没有自动销售 Agent 不构成当前缺陷 |
| 18:30 当前交易日 D+1，订单页仍显示 D；20:00 又发生什么？ | 冻结期实时销售 Provider 服务 D+1；19:00 Closing 服务 D；20:00 只是当前日策略修订 |
| Carryover=40，Forecast=120，Harvest=115，Packaged=113，Commitment=20 | Supply 逐步覆盖；最终经营量 133；旧承诺不重复扣；Packaged 不自动复制为下日 Carryover |
| Exposure A+B 大于 Supply 是否直接超卖？ | 不能仅据此判断；成交 Commitment 才能按合适口径汇总；Exposure 与实物分离 |
| 一个品种等级实时累计值对应多个 SKU；订单 Provider 随后接管 | confidence 与 granularity 分离；不任意拆 SKU；新累计事实替换旧 current，不相加 |
| PRA 调高 Exposure 后扫描数量下降 | 先核对 adjustment evidence 和 observation 资格；UNKNOWN/未证明人工修改不能伪造已知 adjustment |
| Closing 已成功，后来有普通补跑；或连续失败两次 | 成功后自动链不再重扫；管理员维护留原因；失败只自动重试一次，再 Closing S2+人工；实时健康独立评级 |
| Closing 需要哪些研究字段？ | 保留 qty/amount/order_created_at；purchase_sequence 是待实现缺口，不能用 occurrence_no 代替；页面单价从金额/数量派生 |
| 新人工价格决定到来，旧任务 PENDING、QUEUED 或 UNKNOWN | 先记录有效新 Intent；按副作用边界 supersede 或等待旧执行收口；回读后必要时 correction，仍走正常授权 |
| Web 在 prepare 后重启；或最终确认记审计后、发布前崩溃 | 前者短时确认可以失效；后者须由持久事实确定恢复/重确认路径；不能扫描所有 pending 自动执行，也不能盲目重发 |
| Queue Service 一个 continuation 出错 | 单对象/Coordinator 故障隔离；不拖垮 Importer、Watchdog、Review、Outbox；已有持久执行责任继续有人承担 |
| S3 恢复排队、合法 UI 占用，或平台级恢复真的失败 | 排队保持 S3/RECOVERING；真正失败才 S4；不是固定超时升级；Observation S4 不取得 Emergency 下架权限 |
| 新事实仅 shadow、旧链独占经营写入且 Web 读旧链；与双写/新 authority 生效但 Web 未切分别有何区别？ | 首者是合法过渡；后两者分别违反 IG-04/IG-09，按目标矩阵 §5 切换与回滚，不双计或删除历史事实 |
| 13.7 第一条切片及 14-B 责任 | 1 SKU 人工 UPDATE_PRICE 经授权、既有 v4/Queue/Worker/Importer 到 terminal/readback，并覆盖恢复；不先建大批表；14-B 诊断/受控风险中性工具，不填补 deterministic owner 缺口 |

推荐通过规则：五组情景体现正确业务推理，结论能指向 Canonical 位置，并明确区分已知、推断和待验证。不得遗漏真实影响授权、双计、日期、Provider、S4 权限或目标/现状区分的关键边界；表达差异、未使用预期术语、无关样式问题不构成失败。

默认一次完整验收，再对失败项及直接影响定向复核。先判明失败来自文档缺陷、情景问题歧义还是受测者推理错误，再修正相应部分；不因单个模型误答就自动新增项目规则或复杂结构。若关键误解仍未解决，继续保留阻塞；停止规则不用于强行宣告通过。

上述阻塞是相应样本/问题的判断，不要求每个参与模型都 PASS。可由负责人采用合格的独立首答与必要的定向复核完成验收；原会话纠错保留为复核记录，已透露答案的复核注明受指导，不重命名为全新独立首答。内容正确性、环境独立性、正式接受分别报告。

若独立 AI 或负责人最终确认尚未完成，明确保留 `NOT YET VALIDATED`。不得把当前 AI 的解释、一次 grep 或绿色 CI 写成 cold-start PASS。

## 7. 验证投入与停止规则

本阶段只要求与文档风险匹配的验证：

- UTF-8 严格回读、中文段落抽查、现役相对链接/锚点及文件路径检查。
- 通过 diff 确认没有生产代码、Schema、tests、运行配置或真实数据变更；归档与 evidence 不被重写。
- 在现役正文内检查旧 20:00 换日、13.5-7G 当前任务、Exposure 库存硬上限、Task14 禁止 Agent、已实现 Coordinator 等误导语义；历史引用不要求删除。
- 核对 G1、G2、增量吸收及 IG-01～IG-11 均已覆盖；不把未采纳设计纳入永久规则。
- 正式 AGENTS 生效后独立 cold-start；随后负责人对最终产物确认。
- 后续 PR 使用既有 Core CI。当前 workflow 对 main push/PR 已配置 Windows 和 Linux 验证；不为本次文档任务额外要求本地全量回归或实机 COMMIT，也不擅自削弱 CI。

初审尽量一次列出完整 blocker。复审验证冻结问题和直接回归；除非用户要求完整重审，或出现实质高风险新证据，不无限扩大范围。若最终 AGENTS/Canonical 的语义又改动，针对改动造成的理解风险重新验证；若只追加证据链接/状态记录，记录差异，不无意义地循环重跑全部验收。

## 8. 最终报告格式与阶段门槛

13.6-3 实施交付后，分别报告：

```text
Implementation Review:
P1 = 实际数量
P2 = 实际数量
Merge Gate = 实际数量及未完成项目

Canonical Entrypoint Convergence: PASS / FAIL / NOT YET VALIDATED
Final AGENTS Effective: YES / NO
Cold-start Validation: PASS / FAIL / NOT YET VALIDATED
Owner Final Confirmation: CONFIRMED / NOT YET CONFIRMED

Task 13.6-3 Stage Goal: PASS / FAIL / NOT YET VALIDATED
Task 13.6 Overall: PASS / FAIL / NOT YET VALIDATED
Task 13.7 Readiness: READY / NOT READY

我没有执行合并。
```

只有现役入口一致、正式 AGENTS 实际生效、独立 cold-start 通过、负责人确认且无夹带生产开发，才能将 13.6 Overall 标为 PASS。13.7 Readiness=READY 也不自动等于已经实施 13.7 或已获得任何真实平台写授权。

计划启动状态：**13.6-3 IN PROGRESS；13.6 Overall NOT YET VALIDATED；13.7 NOT READY。** 后续实施和验收进展统一记录在当前状态页及本阶段报告，不反复改写本计划的历史证据。

### 测试反馈后的定向复核（2026-09-06）

三份外部作答与后续澄清绑定旧输入 `0de43bf78f8c61847e6406c3b74dc1fbc7995f32`，结果见本阶段报告的“测试反馈修订”附录。该 SHA 不再代表修订后的语义。负责人开始复核时固定含本修订的 PR Head，记录 SHA 和正式 AGENTS blob；不直接复用旧 PASS 作为新版验收。

本轮只修订原五组中的数量/粒度、authority 和验收身份说明，复核 B、D 及直接受影响的 A 算术即可；不重开 G1/G2，不要求其他情景全部重答。以下为可转发的中性问题，评分依据留给主持人：

> 仅依据固定新 SHA 的八份文档，回答并引用章节；不读本计划、报告或评分答案，不修改仓库或平台。说明这次是独立首答还是原会话定向复核，以及已收到哪些额外上下文。对下列明确假设计算和判断；缺少证据时说明缺什么，不补造事实或生产功能。
>
> A. Carryover=36、Forecast=130，当前成交累计22；同生产日随后Harvest=124、Packaged=118，期间累计仍22；之后累计31。逐步给经营参考数，不假定题目未提供的页面 rollover 或新观察。
>
> B. 同平台/商品/单位/区间的Exposure从90变127，PRA期间目标增加45。分别考虑实际+45已证明且其他非销售变化已排除、调整UNKNOWN、存在无法解释的人工修改；给净变化与候选销量并说明条件。另有同品种等级累计30对应A/B，管理员决定分为18/12：各数能证明什么？随后B下架，页面仍按原交易日起点累计，稍后显示32，能否归给A？若再取得同范围完整订单证据A14/B16（对应累计30的截至时点），如何使用和保留各来源？
>
> D. 分别判断旧链唯一写入/新链shadow/Web读旧链、新旧都写、新链唯一写入/Web当前销售仍读旧Summary三种情况。说明谁负责、下一步及触发、切换与回滚边界。

评分不要求逐字复述：A 数值为144/138/132/123；B 要分清负Exposure残差与正候选消耗、未知调整不能代入、聚合可信不等于SKU可分配、管理员决定及商品下架均不证明累计归属、后来的订单事实不重复相加；D 按目标矩阵 §5 区分合法shadow、双authority与读模型未切换。具体推导只在业务合同/目标矩阵维护，不再生成另一份业务规则。

允许这些数量作为直接影响的有限复核，不要求所有模型追加一轮。文档修订通过不自动关闭原模型尚未答对的问题；负责人可选择足够可信的样本结束阶段验收。本轮不由实施者运行或宣告新的正式 cold-start PASS。

## 9. 主要来源

以下 GitHub 文件链接均固定于本次 main SHA，避免后续分支变化使本计划的事实依据漂移。

- [根级临时 AGENTS](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/AGENTS.md)
- [G1 业务基线](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/rebaseline/task13_6_business_baseline_draft.md)
- [G1 正式报告](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/reports/task13_6_1_g1_business_baseline_review_20260906.md)
- [当前实现责任图](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/rebaseline/task13_6_current_implementation_map.md)
- [目标职责及 gap 矩阵](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md)
- [原 G2 Gate](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/reports/task13_6_2_g2_architecture_handoff_review_20260906.md)
- [平行分析吸收补充](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/rebaseline/task13_6_parallel_analysis_absorption_addendum.md)
- [增量 G2 Gate](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)
- [文档权威盘点](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/rebaseline/task13_6_document_authority_inventory.md)
- [人工任务生产代码](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/manual_task_orchestration.py)
- [执行授权生产代码](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/execution_authorization.py)
- [Queue Service](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/scripts/run_shadowbot_queue_services.py)
- [Web 当前读模型](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/operations_web/queries.py)
- [人工任务测试](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/tests/test_manual_task_orchestration.py)
- [执行授权测试](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/tests/test_execution_authorization.py)
- [Core CI 配置](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/.github/workflows/core-ci.yml)
- 治理输入：本轮用户明确指令，以及附件《PRA 项目审核治理规范 v2.0》（2026-09-01）；附件不是 GitHub 已合并事实。
