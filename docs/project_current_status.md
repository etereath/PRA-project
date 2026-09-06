# PRA 当前阶段与验证状态

角色：Current Status；唯一当前阶段状态页。更新于 2026-09-07。PR [#46](https://github.com/etereath/PRA-project/pull/46) 已合并，本次交接起点为 main `f227cd2517687e4a6dfadea90c2e126a5da69711`。当前在 Draft PR [#47](https://github.com/etereath/PRA-project/pull/47)、分支 `codex/task13-7-1-human-update-price` 交付首切片实现；源码版本 `8a1040da308caf6f2ccc60fda7f18b9b2237be24`。未合并、未部署；[实现与证据](reports/task13_7_1_human_update_price_20260907.md)，[开发 Goal](plans/task13_7_first_slice_codex_goal.md)。

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
| Task 13.7-1 代码实现 | COMPLETE / 待审核 | 持久授权、Queue Service owner、价格决定替代/等待、恢复与 Web 回读；Schema v18 |
| Task 13.7-1 隔离旅程 | PASS | 正式 Web/Application、v4/file Queue/Worker/Importer，同一临时 Runtime；仅替换外部 UI |
| Task 13.7-1 Implementation Review | 待 ChatGPT 首审 | 开发自检与固定 SHA 测试见实现报告；最终 Head CI 见 PR，Merge Gate 未放行 |
| Task 13.7-1 Stage Goal | NOT YET VALIDATED | 尚无包含重启或 blocker 恢复的受控实机证据，需负责人主持 |

## 当前能力与限制

已有正式人工 Web 创建/授权入口和 v4/v5、Queue/Worker/Importer、UNKNOWN/RECONCILE、Review/Outbox、DB 实物库存等资产。PR #47 为一次人工改价补齐 Task 决定记录、授权后持久交接与 Queue Service owner，尚未合入 main 或证明现场可用。Commitment、冻结期销售 Provider、Closing、Supply、Observation Health 及 authority cutover 仍是后续缺口。[原版本实现图](rebaseline/task13_6_current_implementation_map.md)保持其指定 SHA 身份，本次增量事实见[13.7-1 实现报告](reports/task13_7_1_human_update_price_20260907.md)。

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

Codex 已完成原 Draft PR 的实现及隔离验证交付。下一步由 ChatGPT 按指定 SHA 进行业务/架构/代码首审、冻结 blocker；负责人安排受控现场验收并决定合并。必要迁移和配置说明见实现报告。合并、结束 Draft、部署和真实平台写入仍按各自授权处理；本次均未执行。

PR #44 已关闭且未合并，仍作历史平行分析；有效增量已随 #45 吸收，未采纳设计不成为施工合同。Issue #41 与旧报告的阶段叙述按其历史范围读取，不重新打开已完成的 13.6。

历史证据：[G1](reports/task13_6_1_g1_business_baseline_review_20260906.md)、[G2](reports/task13_6_2_g2_architecture_handoff_review_20260906.md)、[增量 G2](reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)。旧报告中的“next 13.6-2 / merge #45”只代表当时状态。

完整旧进展时间线保存在[收口前状态页](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/project_current_status.md)，不再作为当前施工方向。
