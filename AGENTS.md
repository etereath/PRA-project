# AGENTS.md — PRA 项目工作指令

PRA 是鲜切花持续观察、人工经营决策、可靠执行与恢复系统。当前 Controller 是 Human / Operations Web；未来 Agent 仍经既有校验、授权、执行与恢复接口接入。

## 入口与任务范围

1. 新会话读取本文件、[当前状态](docs/project_current_status.md)和用户指定的目标文件或 PR / Issue，确认本轮目标与完成证据。
2. 按任务核验 branch / worktree 和相关版本，保留已有改动；同一任务后续只读增量，复用内容未变的规则、源码和有效证据。
3. 当前任务以用户指定对象为准。除非用户明确要求审核，否则开发 Agent 不运行全量项目审核；文档任务检查直接语义、引用与编码，代码整改检查原问题和直接回归，出现硬安全边界或真实依赖时才扩大。

以下材料按实际涉及内容读取，不是固定必读集：

| 涉及内容 | 入口 |
|---|---|
| 产品目标、路线 | [产品概览](docs/project_overview.md) |
| 业务定义、数据权威 | [业务合同](docs/business_contract.md) |
| 既有实现、能力复用 | [实现责任图](docs/rebaseline/task13_6_current_implementation_map.md)及对应 SHA 源码 |
| 跨模块 owner、IG、阶段职责 | [目标职责与缺口](docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md) |
| WHY / WHEN：范围、投入与停止判断 | [开发范围与效率治理](docs/pra_development_scope_and_efficiency_governance.md)，按需读取相关节 |
| HOW：接口适配、测试、CI 定位与交付步骤 | [开发执行参考](docs/development_workflow.md)，按需读取相关节 |
| 其他主题 | [文档索引](docs/index.md)中对应条目 |

`.archive/` 是人工或网页端参考归档，不作为 Codex 工作规范，默认不检索、不读取；仅用户当前明确点名时使用。Reviewer / Review Governance 材料不通过开发入口间接导入。

## 工作原则

- 目标看最新用户裁决与业务定义，实现看指定 SHA 源码，验证看相应范围和环境的证据。历史设计可修订，已确认的历史事实不改写。
- 同时考虑业务可行性、安全正确性和维护成本。边界妨碍目标时，提出 BOUNDARY CONFLICT，说明证据、后果和最小替代方案；不静默扩权或增加旁路。
- 跨组件非终态须明确 owner、下一步、触发、失败、重启与收尾；最终确认后的执行责任不能只留在页面、内存或审计日志。
- 新增表、状态机或服务前说明具体事故与既有能力缺口，优先复用 v4/v5、Queue、Worker、Importer、Watchdog、Task、Review/Outbox 和库存 ledger。
- 修改接口、DTO、状态或持久字段时，先查生产与测试调用处并同步适配。开发期默认定向验证；授权、事务、并发和恢复保留必要真实集成，不因父任务规模大就重做全系统验收。
- 正式提交审核或交付前完成适用完整门禁；已有等效 CI 证据时不重复本地全量。新改动、失败或未解除风险才扩大验证，简述依据。文档修改不自动触发本地主动全量或 CI 配置调整。
- 小步修改、集中交付；读取局部 diff，正常日志只取摘要，失败再查相关细节。每个主题保留一个主要定义处，不为小修新增长报告或反复提交文档 SHA。

## 硬边界

- 保留主体授权、精确范围、有效期、幂等与原子事务。真实写必须经过“写前读取 → 比较预期旧状态 → 执行 → 写后读取”；外部员工修改是正常经营事实，过时 Intent 不默认写回。
- UNKNOWN / NEEDS_RECONCILIATION 不得猜测成功或失败，也不得第二次猜测写；沿既有唯一 RECONCILE 或正式人工证据收口。
- PENDING 未授权 Task 不自动执行；Coordinator 只接管已有持久 execution continuation 的授权对象。
- Observation S4 不继承 Emergency S4 下架权限；新旧业务 authority 不得同时针对同一事实扣减、生成计划或写入权威状态。
- 区分 platform_name、account_id、internal_sku、platform_product_identity；UI、登录、selector 留在 Adapter / Executor / ShadowBot。
- 凭据、完整 Review token URL、本地生产配置与真实 Runtime 数据不得进入 Git、公开日志或公开证据。
- 显式编码：普通文本 UTF-8，CSV/TSV UTF-8-SIG，bat ASCII + CRLF；写前检查占用，含中文文件每批写后回读并验证内容。终端显示不作为文件真值；历史原样 archive 和 hash-bound evidence 不为格式或编码整理而重写。

## 权限、交付与收尾

- 用户有效授权在会话内持续，授权范围内的正常步骤不反复确认。commit / push / PR 不等于 merge、结束 Draft、部署、真实 Runtime 或平台操作授权；不修改无关远端状态，不自行扩大权限。
- 开发 Agent 默认只实施用户授权的开发任务；用户明确要求审核时才切换审核职责。按用户指定时机推送，文档修改本身不等于推送授权，现有 CI Gate 也不能自行绕过。
- 每次 Codex 推送前，必须同步更新 `docs/project_current_status.md` 并纳入待推送提交，纯文档推送也不例外；进度只在该页维护，`docs/index.md` 仅保存索引。推送后同步 PR 正文，具体步骤见开发执行参考。
- 结果只报告当前开发目标、修改、验证和直接限制，不自行宣告 P1 / P2 / Stage Goal 等审核结论。文件、测试、CI、实机及部署证据不互相替代。
- 阶段与历史验收从状态页读取；不重开已完成阶段或套用旧施工限制。cold-start 仅用于明确安排的专项验收，不作为普通开发或文档修改的默认流程。
- 本轮目标与适用验证完成后结束；未涉及的阶段验收、理论问题或无关 CI 失败，不扩张为本轮任务。
