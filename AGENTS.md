# AGENTS.md — TEMPORARY TASK 13.6 REBASELINE INSTRUCTIONS

> **临时文件 / TEMPORARY**
>
> 本文件仅在 Task 13.6 文档、业务语义与架构重基线期间作为 Codex 的仓库级工作指令。
> 它不是 PRA 的最终项目规则。
>
> Task 13.6 验收阶段必须根据新的 Canonical 文档生成正式 `AGENTS.md`；正式版经审查后替换本文件，本临时版随后归档。
>
> Task 13.5 结束前的原始 `AGENTS.md` 已原样归档到：
> `docs/archive/AGENTS_task13_5_pre_rebaseline_20260905.md`

## 1. 当前任务

当前阶段：**Task 13.6 — PRA 项目文档重整、业务语义重基线与 AI 开发上下文校准**。

父任务：GitHub Issue #41。

Task 13.5 已 `STOPPED / SUPERSEDED`。不得继续 13.5-7G，也不得把 PR #39 的候选设计直接改名为 13.7 后继续施工。

Task 13.7 只有在 Task 13.6 Stage Goal = `PASS` 后才能开始生产功能开发。

## 2. Task 13.6 的目标

本任务不是生产功能开发，而是重建人类与 AI 可以共同使用的项目基线，明确：

- PRA 的真实经营目标与当前阶段目标；
- 业务事实、已采纳设计、当前实现、验证证据和历史候选之间的区别；
- 时间、供给、成交承诺、平台 Exposure、日结、人工决策与执行恢复的业务语义；
- 当前代码真实实现到哪里；
- 哪些旧合同仍有效、哪些已失效、哪些需要项目 owner 决策；
- Task 13.7 的真实实现输入与边界。

历史对话、Issue、计划、报告和旧 `AGENTS.md` 都是审计输入，不拥有永久业务权威。若存在更简单、更可靠、更符合真实业务的方案，可以提出替代方案。

## 3. 来源分类

Task 13.6 处理信息时必须标明其性质：

1. **Business Fact**：项目 owner 对真实经营、平台行为、人工流程、日期和数量口径的直接说明；
2. **Accepted Design**：项目 owner 曾采纳的设计决定，可在 13.6 中重新审查；
3. **Current Implementation Evidence**：指定 Git SHA 的生产代码、正式入口和真实调用链；
4. **Validation Evidence**：测试、CI、真实 READ_ONLY、受控 COMMIT、实机恢复、长期运行记录；
5. **Historical / Candidate Design**：旧 Issue、旧计划、旧 `AGENTS.md`、PR #39 候选方案及未重新确认的 AI 建议。

不得把“设计已采纳”写成“代码已实现”，也不得把“测试通过”写成“真实运营闭环已验收”。

## 4. 当前工作顺序

### 13.6-0 — Rebaseline Bootstrap

- 归档旧 `AGENTS.md`；
- 使用本临时 `AGENTS.md` 切换 Codex 上下文；
- 建立父 Issue、bootstrap 文档、来源分类和禁止事项；
- 不裁决最终业务架构，不写生产代码。

### 13.6-1 — 业务与文档权威重基线

- 盘点历史文档、Issue、PR 与关键实现证据；
- 重建项目定位、业务主链、核心术语、时间和数量口径；
- 建立 Open Decision Register；
- 在业务主链初稿完成后执行一次 Business Decision Closure；
- G1 业务基线通过后才进入 13.6-2。

### 13.6-2 — 系统架构与实现差距重基线

- 分开描述当前实现和目标职责；
- 审核完整责任链：`业务意图 → 持久状态 → 调度/授权 → 执行 → 结果 → 恢复/复核 → 终态`；
- 输出复用、调整、新增、退役矩阵；
- 形成 Task 13.7 输入，不提前实现 13.7。

### 13.6-3 — 入口收口与验收

- 收口 README、docs index、Current Status、Roadmap 等 Canonical 入口；
- 由 Codex 根据新的 Canonical 文档生成**正式 `AGENTS.md` 候选版**；
- 审查候选版是否准确反映新基线且没有重新复制历史冲突；
- 退役本临时 `AGENTS.md`，将其归档；
- 用通过审查的正式版替换根级 `AGENTS.md`；
- 再执行 cold-start AI 理解验收与项目 owner 最终确认；
- 只有 Task 13.6 Stage Goal = `PASS` 才允许 Task 13.7 开工。

## 5. Task 13.6 禁止事项

Task 13.6 期间不得：

- 修改生产业务代码、Runtime Schema、真实 Runtime DB、Queue、Worker 或生产运行配置；
- 执行真实平台写入；
- 继续 13.5-7G；
- 提前实现 Current Sales Commitment、Sales Control Intent、Dispatch Attempt、TaskExecutionCoordinator、Observation Health 或 Agent；
- 因为旧代码或旧计划存在，就未经业务裁决新增第二状态机、第二 Summary、第二 Queue 或万能 Service；
- 为了文档整齐而修改被测试、哈希或验收绑定的历史证据内容。

发现生产代码问题时，登记为实现差距、风险或 Task 13.7 输入；除非项目 owner 另开独立修复任务，否则不要顺手改代码。

## 6. 仍然有效的基础安全边界

在新业务合同完成前，以下安全底线继续保持：

- 真实平台写不得绕过正式应用服务与执行链直接点击平台；
- `UNKNOWN / NEEDS_RECONCILIATION` 不得通过猜测性重复写解决；
- 人工直接在平台/App 修改属于正常经营场景，系统不得假设自己是唯一写入者；
- 真实写继续遵循写前读取、旧状态比较、写后回读；
- secrets、密码、Token、Cookie、Webhook secret、完整 Mobile Review token URL 和本地生产配置不得进入 Git、日志或公开证据；
- 平台专属页面、登录、选择器与 UI 操作留在平台 Adapter / ShadowBot 边界；
- 当前单机、SQLite、单 Worker 架构不因“未来多平台”自动升级为分布式系统；新增复杂度必须由真实需求证明。

这些底线不意味着旧 13.5 的库存、日结、Automation、S0–S4、Agent 或 Web 业务语义自动继续有效。

## 7. 历史材料

以下内容默认属于 Historical / Audit Input，需保留版本和上下文，但不得自动限制 Task 13.6：

- GitHub Issue #20；
- `docs/plans/task13_5_*`；
- `docs/reports/task13_5_*`；
- `docs/archive/AGENTS_task13_5_pre_rebaseline_20260905.md`；
- PR #39 的 7G 候选计划；
- 旧 `business_decision_spec.md` 中尚未重新确认的业务参数。

Task 12/13 的 v4/v5、operation/attempt、write lock、UNKNOWN→RECONCILE、Queue、Worker、Importer、Watchdog、Review、Outbox 等资产仍可能继续复用，但必须在 13.6-2 依据当前代码和新业务合同重新确认，不因历史“禁止重写”条款自动获得永久豁免。

## 8. 临时 AGENTS 生命周期

本文件必须有明确退役条件，不能演变为新的永久历史包袱。

### 启用

Task 13.6-0 合并后，本文件作为根级 `AGENTS.md` 对 Codex 生效。

### 生成正式版

13.6-3 中，只有在业务基线和系统架构基线已经通过相应评审后，才根据新的 Canonical 文档生成正式 `AGENTS.md` 候选版。正式版应保持精简，只包含后续开发真正需要长期强制遵守的项目规则，不复制整套业务规范。

### 退役

正式候选版通过审查后：

1. 将本临时文件原样归档，例如 `docs/archive/AGENTS_task13_6_temporary.md`；
2. 用正式版替换根级 `AGENTS.md`；
3. cold-start AI 必须在正式版已生效的状态下完成最终理解验收；
4. 若验收发现正式版本身造成误解，应修订正式版并重新验收，而不是恢复旧 13.5 `AGENTS.md`。

## 9. Stage Goal

Task 13.6 总体 Stage Goal 在 13.6-0/1/2 期间保持 `NOT YET VALIDATED`。

只有 Canonical 文档、正式 `AGENTS.md`、冷启动理解验收和项目 owner 最终确认全部通过后，才可标记：

```text
Task 13.6 Stage Goal: PASS
Task 13.7 Readiness: READY
```
