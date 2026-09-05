# AGENTS.md

## 当前任务

当前项目阶段为 **Task 13.6：PRA 项目文档重整、业务语义重基线与 AI 开发上下文校准**。

父级任务入口：GitHub Issue #41。

Task 13.5 已正式冻结为 `STOPPED / SUPERSEDED`。PR #38 已合并；PR #39 已关闭且未合并。

**不得继续 Task 13.5-7G、不得按 7G 候选切片继续编码，也不得因为历史 Issue、计划、实施报告或旧 AGENTS 内容存在，就把其中的业务假设自动当作当前 Canonical Contract。**

Task 13.7 必须等待 Task 13.6 Stage Goal = `PASS` 后才可开始生产功能开发。

---

## Task 13.6 的工作目标

Task 13.6 不是普通文档整理，也不是生产功能开发。

目标是重新建立一套让项目 owner 与没有参与历史讨论的 AI 都能正确理解的项目基线，至少回答：

1. PRA 当前为什么存在、服务哪条真实经营链；
2. 当前阶段谁负责销售决策；
3. 供给、库存、成交承诺、平台 Exposure、交易日、页面日期和日结分别是什么；
4. 当前代码实际已经实现到哪里；
5. 哪些历史合同仍有效、哪些已经失效、哪些只是历史证据或候选设计；
6. Task 13.7 真正需要实现和验证什么。

Task 13.6 必须把以下内容明确区分：

- 项目 owner 明确提供的业务事实；
- 项目 owner 曾采纳的设计决定；
- 当前指定 Git SHA 的生产代码事实；
- 测试、CI、实机验收和运行记录；
- AI 的建议、推断和历史候选架构。

历史对话、Issue、计划和实施报告都是重要输入，但不拥有永久业务权威。若发现更简单、更可靠、更符合真实业务的方案，可以提出替代方案；不得为了保持历史设计一致而继续错误方向。

---

## 当前工作顺序

### 13.6-0：Rebaseline Bootstrap

当前先完成：

- Codex 仓库上下文从 Task 13.5 切换到 Task 13.6；
- 建立 Task 13.6 当前入口、来源分类、范围、禁止事项和完成条件；
- 保留历史证据，但取消旧 13.5/Issue #20 对 Task 13.6 的自动现役约束；
- 不写生产功能。

### 13.6-1：业务与文档权威重基线

- 盘点历史文档、Issue、PR 和关键实现证据；
- 重建项目定位、核心术语与业务主链；
- 建立 Open Decision Register；
- 在本阶段末尾集中执行 Business Decision Closure，再进入架构重基线。

### 13.6-2：系统架构与实现差距重基线

- 分别描述当前实现与目标职责；
- 审核 `业务意图 → 持久状态 → 调度/授权 → 执行 → 结果 → 恢复/复核 → 终态` 的责任链；
- 输出复用、调整、新增、删除/退役和 Task 13.7 输入；
- 不提前实现 13.7。

### 13.6-3：入口收口与 Cold-start 验收

- 收口 README、AGENTS、docs index、Current Status、Roadmap；
- 由不携带历史聊天的独立 AI 只读 Canonical 文档进行冷启动理解验收；
- 项目 owner 确认核心经营情景与阶段边界；
- 只有 Stage Goal = `PASS` 才允许 Task 13.7 开始。

---

## Task 13.6 当前禁止事项

Task 13.6 期间不得：

- 修改生产业务代码；
- 修改 Runtime Schema；
- 修改真实 Runtime DB、Queue、Worker、运行配置或平台状态；
- 执行真实平台写入；
- 继续 13.5-7G 或把 7G 直接改名为 13.7 继续施工；
- 提前实现 Current Sales Commitment、Sales Control Intent、Dispatch Attempt、TaskExecutionCoordinator、新的 S0–S4 逻辑或 Agent；
- 因为历史代码已经存在，就未经业务裁决新增第二状态机、第二 Summary、第二 Queue、万能 Service 或其他平行控制面；
- 将“设计已采纳”写成“实现已完成”；
- 将“测试通过”写成“真实业务闭环已验收”。

如果在 13.6 审计中发现生产代码缺陷，只记录为实现差距、风险或 Task 13.7 输入；除非项目 owner 单独开启独立修复任务，否则本任务不顺手修改生产代码。

---

## 仍然有效的基础安全边界

虽然历史 13.5 业务合同需要重新审计，但以下已经验证且与本次业务重基线不冲突的基础安全边界继续生效：

1. 真实平台写动作不得由 Web Route、文档脚本、Agent 或临时工具绕过正式应用服务和执行链直接点击平台。
2. `UNKNOWN / NEEDS_RECONCILIATION` 不得通过猜测性重复写入解决；保持唯一、只读的 RECONCILE 原则，直到后续任务明确修改并重新验收。
3. 平台人工直接操作属于正常经营场景。系统不得假设 PRA 永远是平台状态唯一写入者，也不得在无法确认实际状态时盲目覆盖人工变化。
4. 真实写操作继续坚持执行前读取、比较预期旧状态、执行、执行后回读确认的原则。
5. 凭据、密码、Token、Cookie、Webhook secret、完整 Mobile Review token URL 和本地生产配置不得提交到 Git 或输出到日志/文档证据。
6. 平台专属页面、登录、选择器和 UI 操作逻辑继续限制在平台 Adapter / ShadowBot 执行边界；公共业务文档和公共核心不得把蚂蚁花团页面细节当成所有平台的共同业务规则。
7. 当前单机、SQLite、单 Worker 架构不因为“未来多平台”自动升级为分布式消息总线、外部锁服务或多节点高可用；新增复杂度必须由真实需求证明。

这些安全规则不代表旧 13.5 的库存、日结、S0–S4、Agent、Automation 或 Web 业务语义自动继续有效。

---

## 历史 Task 13.5 材料的身份

以下材料仍然需要阅读，但默认身份改为 **Historical / Audit Input**，而不是 Task 13.6 的不可修改业务合同：

- GitHub Issue #20；
- `docs/plans/task13_5_*`；
- `docs/reports/task13_5_*`；
- Task 13.5 的旧 Current Status 段落；
- PR #39 中的 7G 候选计划；
- 旧 `business_decision_spec.md` 中与当前业务冲突的阶段性参数和方案。

读取这些材料时必须保留其时间、Git SHA、工作树、验收范围和上下文。历史文档中的“已冻结”“必须”“权威”等词，只对其当时任务有效，不能自动阻止 Task 13.6 对业务语义重新裁决。

PR #39 中的 `task13_5_7f_automation_queue_failure_analysis_20260831.md` 是重要历史故障证据，但报告针对特定工作树和审查快照；不得把其中行号或候选修复直接当作当前 `main` 的实现事实。

---

## 当前已确认的重基线输入

以下内容是 Task 13.6 的重点审计输入。它们仍需在 13.6-1 中写入新的 Canonical 业务合同，并与当前代码实现状态分开：

- 当前销售 Controller 是人类管理者，通过 Operations Web 作出经营判断；当前阶段不要求自动销售 Agent。
- 同一生产日 `PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL` 表示同一当日供给逐步收敛，三者是覆盖关系，不得相加。
- `CARRYOVER_CONFIRMED` 表示独立确认的上一周期剩余事实，不能自动由上一日全部 `PACKAGED_ACTUAL` 复制得到。
- 平台目标库存属于 sales exposure，不是实物 reservation。单个平台或多个平台 exposure 超过当前供给，不单凭这一点判定已经超卖。
- 盘中需要独立的 Current Sales Commitment 概念；其来源可能包括订单事实、平台提供的“品种 + 等级 + 累计成交数量”聚合窗口、以及 QUICK-derived estimate。
- 聚合窗口只有品种、等级、数量时，不得伪造订单号、订单行、金额、买家、支付状态或其他不存在的订单级事实。
- PRA 自己修改平台 Exposure 后形成的数量变化必须有可审计 evidence；销量估算不得把该调整误认成成交。
- 平台业务交易日与订单页面当前展示交易日是不同概念。页面展示日必须以实际观察为准，不能只按墙上时钟推定。
- 新业务决定需要能够安全替代旧决定；已经跨越 Queue / 平台副作用边界的旧动作不得通过删除记录假装没有发生。
- 未来执行协调器应优先评估复用现有长期 Queue Service 作为宿主，而不是默认新增独立 daemon；是否新增持久结构由 13.7 的字段级复用审计决定。
- Observation Health 的 S3 应主动触发适合当前平台模式的 recovery calibration；主动恢复确认平台级失败后直接进入 S4，不再仅靠继续等待时间升级。具体阈值和业务限制在 13.6 Business Decision Closure 中集中裁决。
- Agent 自动诊断/介入实际实现后置到 Task 14 的并行工作线；确定性恢复和人工处置不能依赖 Agent 可用。

---

## 当前待集中裁决的主题

不要在 13.6-0 或后续编码中零散拍板。先进入 Open Decision Register，在 13.6-1 主业务链初稿完成后集中收口：

- carryover、三阶段供给与成交承诺如何避免重复扣减；
- 聚合成交观察、订单观察与 QUICK 推导之间的接管和 reconciliation；
- 每日 19:00 指定日结与现有 Settlement/Summary 设施的最终关系；
- S0–S3 的 freshness/capability 阈值，以及排队等待、人工停用、单 SKU 异常、平台级链路失败的区别；
- Intent supersession 的作用范围、有效期和外部人工修改后的行为；
- Task 14 原综合验收职责与 Agent Intervention / Ops Agent 并行工作线的最终边界。

若 13.6-2 发现必须改变已经通过 G1 的核心业务定义，应明确标记 `BUSINESS BASELINE REOPENED` 并返回业务基线评审，不得在架构文档中静默改写。

---

## 文档与证据原则

- 业务目标不能由旧代码反向决定；当前代码事实也不能被目标文档伪装成已实现。
- 代码存在、自动测试通过、CI 通过、真实 READ_ONLY、真实平台 COMMIT、长期运行通过是不同等级的证据，必须分别描述。
- 历史证据应尽量保留原文件和原 SHA，不为了“文档更整齐”修改被测试或哈希绑定的证据内容。
- README、AGENTS、`docs/index.md`、`docs/project_current_status.md` 应最终成为短入口，而不是复制完整业务合同。
- 13.6 优先减少重复和冲突的文档，不以增加更多文档数量作为成功标准。

---

## Task 13.6 Stage Goal

只有全部满足以下条件才允许 Stage Goal = `PASS`：

1. 常用项目入口不再把 13.5/7G 描述为当前开发方向；
2. 核心业务语义、时间轴、数量口径和数据来源已经收敛；
3. 当前实现与目标能力分开表达，关键缺口有明确后续 owner；
4. 历史文档有清晰的 Canonical / Current Implementation / Historical / Draft 身份；
5. 不携带历史聊天的独立 AI 仅阅读指定 Canonical 文档后，可以正确回答核心经营情景；
6. 项目 owner 确认新基线准确反映真实经营；
7. 本任务没有夹带生产功能开发或真实平台副作用。

Task 13.6 完成后，Task 13.7 才负责重新实现和验证人工销售控制闭环；Task 14 承接综合验收与 Agent Intervention / Ops Agent 并行工作线。
