# PRA 当前阶段与验证状态

角色：Current Status；唯一当前阶段状态页。更新于 2026-09-06，工作 PR [#46](https://github.com/etereath/PRA-project/pull/46)。生产实现基线为 `08041bfe25a7f31f032564a2abca35e5eb5f5330`，本 PR 为纯文档。

| 项目 | 状态 | 依据/下一步 |
|---|---|---|
| Task 13.5 | STOPPED / SUPERSEDED | PR #40；不继续 7G，旧 PR #39 未合并 |
| Task 13.6-0 | PASS | PR #42 已合并 |
| Task 13.6-1 / G1 | PASS | PR #43 已合并；OD-01～OD-06 已关闭 |
| Task 13.6-2 / G2 与增量吸收 | PASS | PR #45 已合并；不是待合并任务 |
| Task 13.6-3 | IN PROGRESS / OWNER VALIDATION PENDING | 已吸收三份作答及复核反馈修订文档；新版定向复核及正式接受由负责人主持 |
| Task 13.6 Overall | NOT YET VALIDATED | 最终 AGENTS、独立 cold-start、负责人最终确认全部通过后才能 PASS |
| Task 13.7 Readiness | NOT READY | 不开始生产功能开发 |

## 当前能力与限制

已有正式人工 Web 创建/授权入口和 v4/v5、Queue/Worker/Importer、UNKNOWN/RECONCILE、Review/Outbox、DB 实物库存等资产。新 one-shot Intent、持久执行 owner、Commitment、冻结期销售 Provider、Closing、Supply、Observation Health 及 authority cutover 仍属于实现缺口。完整事实与来源只在[当前实现图](rebaseline/task13_6_current_implementation_map.md)维护。

经营目标由[业务合同](business_contract.md)定义，13.7 的职责/复用/gates 由[目标架构](rebaseline/task13_6_target_responsibility_and_gap_matrix.md)定义；这两份目标文档不证明生产能力已经运行。

## 13.6-3 验收

- Canonical entrypoint convergence：已完成入口收口；本轮局部语义澄清已写入，文档检查与受测结果分开记录。
- 正式 AGENTS：YES，已审查并在本 PR 分支实际生效；临时版原样归档。
- 实施者独立 AI 预检：历史 PASS，五组完整情景，输入提交 `a7bf4aa2919a0462c62d52046e6e3f9c6cde22c5`；原始问答保留，不代替负责人正式验收。
- 负责人提供的外部样本：输入 `0de43bf78f8c61847e6406c3b74dc1fbc7995f32`。DeepSeek 首答+定向复核的内容审查 PASS，D-1 CLOSED；GLM G-1 部分解决，累计范围/唯一映射仍未通过；Luna 首答有计算/状态语义错误，未收到复核。各环境限制与摘录见报告。
- 正式 Cold-start Validation：NOT YET VALIDATED。新版 Exposure 计算、粒度/映射、authority 说明及 AGENTS 验收身份已经改动；旧样本不能直接证明新版通过。由负责人固定包含修订的 SHA，按计划进行有限复核并作正式判定，不要求全部模型 PASS。
- Owner final confirmation：NOT YET CONFIRMED。
- PR CI：旧语义提交 `a7bf4aa` 的 Windows/Linux [Core CI 已通过](https://github.com/etereath/PRA-project/actions/runs/34023342547)，旧补证提交 `0de43bf` 的 [Core CI 也已通过](https://github.com/etereath/PRA-project/actions/runs/34023796458)。本轮包含语义修改；新的 Head、差异与 CI 回读记录在 [PR #46](https://github.com/etereath/PRA-project/pull/46)，不沿用旧结果。

计划：[Task 13.6-3](plans/task13_6_3_canonical_entrypoint_convergence.md)。[本次验收记录与原始问答](reports/task13_6_3_canonical_entrypoint_and_cold_start_20260906.md)。13.6 PASS 只代表认知/文档/交接通过，不代表 13.7 功能完成、现场部署或真实平台写授权。

剩余动作由项目负责人负责：主持修订语义的有限复核，结合独立样本及环境披露确认最终 Canonical 文档、情景结果和 13.7 边界。实施者提供材料与核对结果，不代替正式接受。收到明确确认并核验剩余 Gate 后，再记录 Task 13.6-3 / Overall PASS、13.7 READY。当前授权不包含合并，PR 保持 Draft。

历史证据：[G1](reports/task13_6_1_g1_business_baseline_review_20260906.md)、[G2](reports/task13_6_2_g2_architecture_handoff_review_20260906.md)、[增量 G2](reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)。旧报告中的“next 13.6-2 / merge #45”只代表当时状态。

完整旧进展时间线保存在[收口前状态页](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/docs/project_current_status.md)，不再作为当前施工方向。
