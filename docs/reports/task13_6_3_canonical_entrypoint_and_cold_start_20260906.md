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

NOT YET VALIDATED。必须在正式 AGENTS 已实际进入 PR 文件树之后，由没有本次历史上下文的独立 AI，仅阅读固定 Canonical 集合，回答五个贯穿情景。输入版本、完整提问、原始回答、评价和误解处理将在实际执行后记录。

## 验证与结论

文档静态检查与本 PR Head CI：执行后补充结果；未完成时不计为通过。

- Documentation Implementation：IN PROGRESS。
- Architecture Review：静态收口 PASS；独立理解待验证。
- Implementation Review：尚待 cold-start 与文档/CI 验证后冻结最终结论。
- Task 13.6-3 / Overall Stage Goal：NOT YET VALIDATED。
- Owner final confirmation：NOT YET CONFIRMED。
- Task 13.7 Readiness：NOT READY。

PR 保持 Draft。没有修改生产文件、真实 Runtime 或平台，也没有执行合并。依据正式 AGENTS 的阶段规则，负责人对最终交付确认前不得把 Overall 写为 PASS。
