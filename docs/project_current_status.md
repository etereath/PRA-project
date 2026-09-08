# PRA 当前阶段与验证状态

角色：Current Status；项目进度与验证状态的唯一维护页。Codex 每次推送前同步更新，包括纯文档推送；`index.md` 仅负责索引。更新于 2026-09-08。

PR [#47](https://github.com/etereath/PRA-project/pull/47) 与 RM0 交付 PR [#49](https://github.com/etereath/PRA-project/pull/49) 均已合并，当前 main 为 `37523f668cc6abe204b5beaac5e5c012cf18b09f`。Task 13.7-1 Implementation Review = **PASS**，P1-47-01、P2-47-01、P2-47-02 均 **CLOSED**。代码和 RM0 记录已进入 main；RM1-A 真实 READ_ONLY 已通过。RM1-B 的首次授权尝试在平台提交前因 `OLD_PRICE_PARSE_FAILED` 失败，`SIDE EFFECT = NOT_STARTED`；列表筛选根因、`test2` 部署及失败后的 Web 责任状态表达均已完成定向修复，但纵向写业务结果仍待重新验收。

当前下一步仍是 **Controlled Real-Machine Acceptance**。2026-09-08 已在 [RM0 环境准备与 RM1 现场记录](reports/task13_7_1_rm0_controlled_real_machine_preparation_20260907.md)完成现场整改、历史 UNKNOWN 定向核验、RM0.5、RM1-A 和一次 RM1-B 授权尝试：Runtime v18、ShadowBot 源码及 Queue/Worker 环境已对齐；旧 `WEB7E-6646…` 保持历史 UNKNOWN，但运营责任已关闭。负责人选择“蚂蚁花团供应商 / 千芳花卉”的 `AISHA-B-60-Z`、`AISHA-C-55-Z`、`AISHA-D-50-Z`，并确认平台身份采用“商品名＋等级”；对应三条 PRODUCT mapping 已通过受控工作簿流程转为 `VERIFIED`，其他 9 条保持 `DISABLED`。RM1-A 的完整上架中＋待上架快照为 `VERIFIED`。RM1-B 执行前的新鲜 READ-BEFORE 于 16:38:10 确认 B 为上架中、价格 `10.80`；随后对目标价 `10.30` 的唯一授权尝试在 v4 批次预检中把“报名秒杀”误作供货价格，返回 `OLD_PRICE_PARSE_FAILED`。item 为 `NOT_ATTEMPTED`、`submit_attempted=false`、副作用 `NOT_STARTED`，没有平台价格写入，也没有自动重试。持久授权跨 Queue Service 重启后的接管、Importer、Archive 与锁释放均正常。根因是完整扫描最后停在“待上架”，v4 快速路径仅凭结构化商品行便复用当前页；现已强制 v4 预检重新进入商品管理并显式选择“上架中”，定向测试 `111 passed`，`test2` 部署校验通过。失败后的 Web 投影也已按 Task、batch、item 与副作用状态显示“待重新处理 / 管理员”，不再把生命周期 `COMPLETE` 表达为业务已收口；相邻 Web 与 Human price journey 合计 `89 passed`，真实 Runtime 只读回读一致。真实写仍待新授权复验。Stage Goal 保持 **NOT YET VALIDATED**。

| 项目 | 状态 | 依据/下一步 |
|---|---|---|
| Task 13.5 | STOPPED / SUPERSEDED | PR #40；不继续 7G，旧 PR #39 未合并 |
| Task 13.6-0 | PASS | PR #42 已合并 |
| Task 13.6-1 / G1 | PASS | PR #43 已合并；OD-01～OD-06 已关闭 |
| Task 13.6-2 / G2 与增量吸收 | PASS | PR #45 已合并；不是待合并任务 |
| Task 13.6-3 | PASS | 负责人接受语义快照 `4d51f51`；PR #46 已合并，正式 AGENTS 与 Canonical 已进入 main |
| Task 13.6 Overall | PASS | G1/G2、入口/正式AGENTS及负责人最终验收已收口；验收记录见阶段报告追加节 |
| Task 13.7 Readiness | READY | 业务与文档交接条件通过；首条纵切计划/Goal已准备 |
| Task 13.7-1 开工交接 | COMPLETE | Codex 已在原分支/PR 承接，没有另建计划 PR |
| Task 13.7-1 代码实现 | 已合并 / 复核 PASS | #47 已进入 main `08f4e70`；正式人工收口及催办、目标已满足时确认结束决定、回执刷新持久状态；Schema 目标仍为 v18 |
| Task 13.7-1 隔离旅程 | P1/P2 定向回归 PASS | P1 人工收口与后续执行、P2 零写结束及实时回执已有隔离证据；测试时序修复后的 4 个失败用例定向通过，完整门禁见下节 |
| Windows CI 时序修复 | 已修复并验证 | `scan()` 保持整秒精度，必要时等待到旧执行停止后的下一整秒；`eb6e24b` 的 Windows / Linux Core CI 完整通过 |
| 开发文档治理 | 职责调整完成 | AGENTS 已精简，网页审核材料已原样归档；治理文档只定义 WHY / WHEN，执行参考承接 HOW，索引移除进度；每次推送前同步本页 |
| Task 13.7-1 Implementation Review | PASS | 2026-09-07 用户最终裁决；冻结问题 P1-47-01、P2-47-01、P2-47-02 全部 CLOSED，无遗留冻结问题 |
| #47 合并与最终检查 | MERGED / SUCCESS | merge commit `08f4e70`；最终 Head `1011091` 的 Windows / Linux Core 均 SUCCESS |
| Task 13.7-1 RM0 / RM0.5 | PASS WITH HISTORICAL DEBT / PREFLIGHT READY | 旧 `WEB7E-6646…` 保留审计；B/C/D 三个艾莎 SKU 已由负责人确认并完成受控 VERIFIED mapping，当前无活动执行 blocker |
| Task 13.7-1 RM1-A | READ-BEFORE VERIFIED | `ATTEMPT-95a494700a2d4178`；B/C/D 当前价 `10.80 / 6.80 / 6.20`，均为 `online_only`；Importer ACK WRITTEN，活动 Queue=0，Worker STOPPED；未执行平台写 |
| Task 13.7-1 RM1-B | REPAIR VERIFIED / REAL REVALIDATION WAIT | 首次授权尝试 `FAILED / NOT_STARTED`；v4 列表筛选和失败后 Web 责任表达均已修复，`test2` 部署一致；尚未再次执行真实写 |
| Task 13.7-1 Stage Goal | NOT YET VALIDATED | durable continuation 跨 Queue Service 重启交接已验证，但真实写未完成；还需修复 Web 失败状态语义，并在负责人重新授权后完成纵向写旅程 |

## 13.7-1 实现与当前 RM0 验证依据

- 当前 RM0 绑定 main `08f4e70`，核验 #47 已合并且最终 Windows / Linux Core SUCCESS。2026-09-08 已使用正式 migration 入口补齐 `execution_continuations`，schema v18、SQLite integrity 和 FK health 均通过；ShadowBot 受控源码全部 CURRENT，Queue Service 与 Worker heartbeat 新鲜且 lifecycle 一致，Queue Service heartbeat 含 Coordinator。迁移后完整账本重查发现旧 `WEB7E-6646…` set_online batch-level UNKNOWN；定向核验进一步确认 AISHA-C 的旧 `MANUAL_HANDLED` 已关闭当时恢复责任，历史副作用仍为 INDETERMINATE，且无 active attempt、lock、Review 或 continuation。RM0 因此为 PASS WITH HISTORICAL DEBT；完整范围见 [RM0 记录](reports/task13_7_1_rm0_controlled_real_machine_preparation_20260907.md)。
- 合并前按 2026-09-07 用户最终裁决同步的历史结论为：**Implementation Review = PASS**，**P1-47-01、P2-47-01、P2-47-02 全部 CLOSED**，Core CI #188 SUCCESS。该运行绑定当时 Head `366a757`；#47 最终 Head 检查已另行核验，不混用版本。
- `scan()` 原夹具的微秒观察时间与后续整秒回读不一致；`35d08ad` 单独截断微秒后，[CI](https://github.com/etereath/PRA-project/actions/runs/34109479644) 仍有 4 个用例因扫描未严格晚于旧执行停止时刻而失败。`eb6e24b` 补齐时序，四个失败用例定向 **4 passed**，其 [Core CI](https://github.com/etereath/PRA-project/actions/runs/34110475983) 的 Windows / Linux 均 SUCCESS。失败记录保留，未通过重跑碰绿收口。
- 治理文档已增加归档读取授权、CI 排查范围和有证据的同版本重跑规则，并在 `366a757` 完成 WHY / WHEN 与 HOW 拆分、索引去进度及推送前状态同步要求；对应 CI #188 已通过。
- [P2 修复与证据](reports/task13_7_1_p2_authorization_receipts_20260907.md)、[P1 修复与证据](reports/task13_7_1_p1_human_resolution_20260907.md)、[首版实现及 v18 迁移](reports/task13_7_1_human_update_price_20260907.md)保留各自绑定版本。实现审核已按用户裁决通过；受控实机证据仍未验证，绿色 CI 不替代现场验收。

## 当前能力与限制

已有正式人工 Web 创建/授权入口和 v4/v5、Queue/Worker/Importer、UNKNOWN/RECONCILE、Review/Outbox、DB 实物库存等资产。PR #47 已为一次人工改价补齐 Task 决定记录、授权后持久交接与 Queue Service owner 并合入 main；2026-09-08 的 RM0 整改已把 Runtime、Queue Service 和 ShadowBot Worker 对齐到当前 main。旧 `WEB7E-6646…` batch 作为历史审计债务保留，不再全局阻止 RM1；新任务必须按当前 SKU 的 active lock、open Review、新鲜平台事实、mapping、Task 和新授权单独判断。三个选定 SKU 的 mapping、负责人测试对象确认和真实平台 READ-BEFORE 已完成；B 的目标价已决定为 `10.30` 并执行过一次授权尝试。该尝试证明 durable continuation 可跨 Queue Service 重启交接到 Worker、Importer 和 Archive，但旧 v4 商品列表复用在提交前读错页面。现已强制 v4 预检显式选择“上架中”并完成定向测试及 `test2` 部署；Web 仍把失败后的生命周期收口错误表达为“人工 / 已收口”，且真实写尚未重新验收，因此纵向写旅程仍未证明可用。Commitment、冻结期销售 Provider、Closing、Supply、Observation Health 及 authority cutover 仍是后续缺口。[原版本实现图](rebaseline/task13_6_current_implementation_map.md)保持其指定 SHA 身份，本次增量按[首版实现](reports/task13_7_1_human_update_price_20260907.md)、[P1 修复](reports/task13_7_1_p1_human_resolution_20260907.md)和[P2 修复](reports/task13_7_1_p2_authorization_receipts_20260907.md)各自绑定版本读取。

RM0.5 已完成三个测试 SKU 的负责人裁决、mapping 和活动责任预检，RM1-A 又完成了真实平台 READ-BEFORE；这两步都不授权 RM1-B 平台写。商品/平台映射以及价格/上下架规则从 Excel 迁移到 SQLite 的范围、旧未合并 Runtime 主数据分支可复用内容与迁移顺序，登记为本任务收口后的独立讨论项；不得在 RM1-B 中顺带扩大施工。

经营目标由[业务合同](business_contract.md)定义，13.7 的职责/复用/gates 由[目标架构](rebaseline/task13_6_target_responsibility_and_gap_matrix.md)定义；这两份目标文档不证明生产能力已经运行。

## 13.6-3 验收

- Canonical entrypoint convergence：PASS；负责人已接受测试反馈后的最终交付。
- 正式 AGENTS：YES，已审查、在验收版本实际生效并随 PR #46 合入 main；临时版原样归档。
- 实施者独立 AI 预检：历史 PASS，五组完整情景，输入提交 `a7bf4aa2919a0462c62d52046e6e3f9c6cde22c5`；原始问答保留，不代替负责人正式验收。
- 负责人提供的外部样本：输入 `0de43bf78f8c61847e6406c3b74dc1fbc7995f32`。DeepSeek 首答+定向复核的内容审查 PASS，D-1 CLOSED；GLM G-1 部分解决，累计范围/唯一映射仍未通过；Luna 首答有计算/状态语义错误，未收到复核。各环境限制与摘录见报告。
- 正式 Cold-start Validation：PASS（负责人最终验收裁决）。接受版本 `4d51f51edcafc4168149928f6ee64467cd12421a`，正式AGENTS blob `2e580b9c9169717743f265a2e20085039c38ef46`；本记录不虚构新模型运行或改写旧样本结果，证据边界见报告。
- Owner final confirmation：CONFIRMED，2026-09-06，原话“验收通过,准备下一阶段”。
- CI：已接受语义版本 `4d51f51` 的 [Core CI](https://github.com/etereath/PRA-project/actions/runs/34041256409)、PR #46 最终交接 Head `c585b0b` 的 [Windows/Linux CI](https://github.com/etereath/PRA-project/actions/runs/34044060100)，以及合并 main `f227cd2` 的 [Core CI](https://github.com/etereath/PRA-project/actions/runs/34044462783) 均已通过。13.7 分支新增提交的 CI 在对应 PR 按 Head 单独核验，不能引用这些历史结果代替。

计划：[Task 13.6-3](plans/task13_6_3_canonical_entrypoint_convergence.md)。[本次验收记录与原始问答](reports/task13_6_3_canonical_entrypoint_and_cold_start_20260906.md)。13.6 PASS 只代表认知/文档/交接通过，不代表 13.7 功能完成、现场部署或真实平台写授权。

## 当前交接：13.7-1 第一条纵向切片

先完成1 SKU、1次人工UPDATE_PRICE，从决定、Runtime Task、正式授权、持久交接，经既有v4/Queue/Worker/Importer到终态与平台回读，并验证重启或阻塞解除后的责任连续。详见[首切片计划](plans/task13_7_human_update_price_vertical_slice.md)与[开发Goal](plans/task13_7_first_slice_codex_goal.md)。

ChatGPT 对 `ceb88a0` 的[首审 FAIL 与冻结问题](https://github.com/etereath/PRA-project/pull/47#pullrequestreview-5126336815)保留历史身份。P1、两个 P2 及测试时序修复交付后，用户于 2026-09-07 明确裁决 **Implementation Review = PASS、冻结问题全部 CLOSED**；#47 随后合入 main `08f4e70`。Controlled Real-Machine Acceptance 的 RM0 技术准备现为 PASS WITH HISTORICAL DEBT，RM1-A READ-BEFORE 已通过；RM1-B 已对 B `10.80 → 10.30` 执行一次授权尝试，但以 `FAILED / NOT_STARTED` 结束，没有平台写入。v4 列表筛选、失败后 Web 责任状态、定向回归和 `test2` 部署均已完成；下一步由负责人基于新鲜旧价格重新授权一次真实写。Stage Goal 仍未验证，必要配置和证据边界见实现、修复与 [RM0 / RM1 现场记录](reports/task13_7_1_rm0_controlled_real_machine_preparation_20260907.md)。

PR #44 已关闭且未合并，仍作历史平行分析；有效增量已随 #45 吸收，未采纳设计不成为施工合同。Issue #41 与旧报告的阶段叙述按其历史范围读取，不重新打开已完成的 13.6。

历史证据：[G1](reports/task13_6_1_g1_business_baseline_review_20260906.md)、[G2](reports/task13_6_2_g2_architecture_handoff_review_20260906.md)、[增量 G2](reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)。旧报告中的“next 13.6-2 / merge #45”只代表当时状态。

完整旧进展时间线保存在[收口前状态页](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/project_current_status.md)，不再作为当前施工方向。
