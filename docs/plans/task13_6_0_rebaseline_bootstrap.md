# Task 13.6-0：Rebaseline Bootstrap

更新时间：2026-09-06  
状态：Draft / Bootstrap  
父任务：GitHub Issue #41  
基线：`main@18e4049906fd8e58ab2879558db8ab78bd5975d0`

## 1. 任务定位

Task 13.6-0 是 Task 13.6 的启动步骤，不是业务架构最终定稿，也不进行生产功能开发。

它只解决一个问题：

> 在 Task 13.5 已 `STOPPED / SUPERSEDED` 后，为 Codex 建立一个**临时、可退役的 Task 13.6 工作上下文**，同时完整保存旧 `AGENTS.md` 作为历史证据；Task 13.6 最终验收阶段再根据新的 Canonical 文档生成并启用正式 `AGENTS.md`。

因此本阶段不“改造旧 AGENTS 成新 AGENTS”，而采用三段生命周期：

```text
旧 AGENTS.md
→ 原样归档，作为 Task 13.5 历史证据

Task 13.6 临时 AGENTS.md
→ 仅服务 13.6-0/1/2 与 13.6-3 前半段

13.6-3 正式 AGENTS.md
→ 根据新 Canonical 文档生成、审查、替换临时版
→ 在正式版生效后执行 cold-start 最终验收
```

## 2. 已确认的 GitHub 状态

- PR #38 已合并，保留 13.5-7F 的有效外部生命周期与真实 READ_ONLY 修复；
- PR #39 已关闭且未合并；其 7G 计划不构成当前施工授权；
- PR #40 已合并，将 Task 13.5 冻结为 `STOPPED / SUPERSEDED`；
- 当前父任务为 Issue #41；
- Task 13.7 必须等待 Task 13.6 Stage Goal = `PASS`。

## 3. 13.6-0 的输入分类

Task 13.6 后续不得再把所有“旧文档里的 must/冻结/权威”视为同一种事实。

### 3.1 Business Fact

来自项目 owner 对实际经营、平台行为、人工流程、时间和数量口径的直接说明。

### 3.2 Accepted Design

项目 owner 曾明确采纳的设计方案。它是后续架构的重要输入，但允许在 13.6 中发现更简单、更可靠、更符合真实业务的方案后重新提出替代设计。

### 3.3 Current Implementation Evidence

以指定 Git SHA 的生产代码、正式入口和实际调用链为准。不得因为计划或报告写着“完成”就跳过实现核对。

### 3.4 Validation Evidence

包括自动测试、CI、真实 READ_ONLY、受控平台 COMMIT、实机恢复记录和长期运行观察。代码存在、单测通过、CI 通过、真实平台验收和长期生产可用不是同一种证据等级。

### 3.5 Historical / Candidate Design

包括旧 Issue、历史任务计划、旧实施合同、旧 `AGENTS.md`、PR #39 的 7G 候选方案和未被当前 owner 重新确认的 AI 建议。

这些材料需要保留来源和上下文，但不得自动成为 Task 13.6 的现役业务合同。

## 4. AGENTS 生命周期

### 4.1 旧版归档

Task 13.5 结束时 `main@18e4049` 的根级 `AGENTS.md` 必须**原样保存**，不得在归档副本中重写、删减或替换旧规则。

归档路径：

`docs/archive/AGENTS_task13_5_pre_rebaseline_20260905.md`

该文件只用于历史审计和理解 Task 13.5 当时 Codex 实际受到什么指令，不对 Task 13.6 产生现役约束。

### 4.2 Task 13.6 临时版

Task 13.6 期间根级 `AGENTS.md` 是明确标记为 `TEMPORARY TASK 13.6 REBASELINE INSTRUCTIONS` 的临时文件。

它只负责：

- 告诉 Codex 当前任务为 13.6；
- 定义来源分类、阶段顺序和禁止事项；
- 保留必要的真实平台写安全底线；
- 允许 13.6 重新审计旧业务设计；
- 明确自身最终必须退役。

临时版不是新 PRA 长期规范，不得在后续文档中被引用为最终业务权威。

### 4.3 生成正式版

只有在：

1. 13.6-1 业务基线完成并通过 G1；
2. 13.6-2 当前/目标架构与实现差距完成审查；
3. Canonical 文档已经形成稳定候选；

之后，才允许 Codex根据这些新 Canonical 文档生成正式 `AGENTS.md` 候选版。

正式版应精简，只保留后续开发真正需要长期强制执行的项目级规则，不复制完整业务规范、历史报告或阶段计划。

### 4.4 临时版退役与最终验收

13.6-3 验收阶段：

1. 审查正式 `AGENTS.md` 候选版；
2. 将临时 `AGENTS.md` 原样归档；
3. 用通过审查的正式版本替换根级 `AGENTS.md`；
4. 在正式版已经生效的仓库状态下执行 cold-start AI 理解验收；
5. 如果正式版造成关键误解，修订正式版并重新验收，不恢复旧 Task 13.5 `AGENTS.md`。

## 5. 13.6-0 的最小变更

本 bootstrap 只允许：

1. 原样归档旧 `AGENTS.md`；
2. 启用 Task 13.6 临时 `AGENTS.md`；
3. 建立 Task 13.6 父级 Issue；
4. 新增本 bootstrap 文档；
5. 必要时补充纯文档导航；
6. 做纯文档一致性检查。

本步骤不生成正式长期 `AGENTS.md`，也不裁决最终业务 Schema、状态机或系统模块边界。

## 6. 明确禁止事项

13.6-0 不得：

- 修改 `app/`、`shadowbot/`、生产 `scripts/` 或 Runtime Schema；
- 修改真实 Runtime DB、Queue、Worker、Automation 配置或平台状态；
- 执行真实平台 READ_ONLY 或 COMMIT 验收；
- 新增 Current Sales Commitment、Intent、Dispatch Attempt、Coordinator、ObservationHealthService 或 Agent 生产实现；
- 将 PR #39 的 7G 计划整体 cherry-pick / merge 回 `main`；
- 修改旧 `AGENTS.md` 的归档副本；
- 把临时 `AGENTS.md` 当成 13.6 最终产物；
- 在没有实现证据时把目标状态写成“当前已完成”。

## 7. 仍然有效的基础安全约束

Task 13.6 重新审计业务语义，不代表以下已验证安全原则失效：

- 真实平台写不得绕过正式执行链直接调用平台 UI；
- UNKNOWN / NEEDS_RECONCILIATION 不得通过猜测性重复写解决；
- 人工直接在平台/App 操作属于正常运营场景；系统不得假设自己是平台唯一写入者；
- 写前读取、旧状态比较、写后回读继续作为真实写安全基础；
- secrets、token、凭据和本地生产配置不得进入 Git 或日志；
- 平台页面与选择器逻辑必须留在平台 Adapter / ShadowBot 边界；
- 当前单机/SQLite/单 Worker 不自动升级为分布式架构。

这些规则是安全基础，不代表旧 13.5 的库存、日结、Automation、S0-S4 或 Agent 业务设计全部继续有效。

## 8. 历史材料处理规则

### 8.1 Issue #20

降级为 Task 13.5 的历史宏观计划和重要审计输入。真实平台时间、历史验收事实和仍被当前 owner 确认的规则可以继续引用；旧阶段划分、旧交易窗口、旧 S0-S4 细节、Agent 边界等必须在 13.6 中重新判断现行性。

### 8.2 PR #39

保持关闭且未合并。

- `task13_5_7f_automation_queue_failure_analysis_20260831.md`：重要历史故障证据，需保留其工作树和版本边界；
- 7G Coordinator 计划：候选设计，只作为 13.6-2 的输入之一；
- 其对 `AGENTS.md`、Current Status 和“下一步继续 7G”的改动不得恢复为现役合同。

### 8.3 Task 12 / Task 13 已验证执行资产

这些资产不因为 13.5 停止而自动失效。13.6-2 需要按实际代码和证据重新判断哪些能力继续原样复用，例如 v4/v5、operation/attempt、write lock、UNKNOWN→RECONCILE、ShadowBot Queue、Worker/Importer/Watchdog、Review 和 Notification Outbox。

是否继续复用必须基于当前调用链和新业务合同，而不是因为历史文档写过“禁止重写”。

## 9. 下一步：13.6-1

13.6-0 合并后进入 13.6-1：

1. 完整文档与重要 Issue/PR 盘点；
2. 项目定位、业务主链、核心术语和时间/数量语义初稿；
3. 将冲突项登记到 Open Decision Register；
4. 不在发现一个问题时立即局部拍板；
5. 在业务主链初稿完成后，一次性执行 Business Decision Closure；
6. G1 业务基线通过后才进入 13.6-2 系统架构重基线。

Task 13.6 临时 `AGENTS.md` 在 13.6-1/2 期间继续生效，直到 13.6-3 正式退役。

## 10. 13.6-0 完成条件

以下全部满足，13.6-0 才算完成：

- [ ] Issue #41 已成为当前 Task 13.6 父级入口；
- [ ] Task 13.5 结束时的原始 `AGENTS.md` 已原样归档；
- [ ] 根级 `AGENTS.md` 已明确标记为 Task 13.6 临时指令；
- [ ] 临时 `AGENTS.md` 不再阻止 Task 13.6 重新审计 Issue #20 和旧 13.5 业务合同；
- [ ] 临时 `AGENTS.md` 明确其 13.6-3 退役条件；
- [ ] 必要安全底线继续保留；
- [ ] 旧 13.5/PR #39 被标记为 Historical / Audit Input，而非当前施工授权；
- [ ] Task 13.7 仍明确等待 Task 13.6 Stage Goal = `PASS`；
- [ ] 本分支没有生产代码、Schema、真实数据或运行配置修改；
- [ ] 本分支没有真实平台副作用；
- [ ] 下一步明确进入 13.6-1，而不是继续 7G。

## 11. Review 输出

13.6-0 Review 只需要回答：

- 旧 `AGENTS.md` 是否完整原样保留；
- 当前根级 `AGENTS.md` 是否明确只是 13.6 临时工作指令；
- 临时指令是否保留必要安全边界而不把旧业务设计重新冻结；
- 是否明确了 13.6-3 生成正式版、退役临时版、再 cold-start 验收的生命周期；
- 是否夹带生产功能修改。

如果以上通过：

```text
Implementation Review: PASS
13.6-0 Stage Goal: PASS
Next: Task 13.6-1
```

Task 13.6 总体 Stage Goal 仍保持 `NOT YET VALIDATED`，直到正式 `AGENTS.md` 已启用并通过 cold-start 验收。
