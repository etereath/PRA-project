# Task 13.6-0：Rebaseline Bootstrap

更新时间：2026-09-06  
状态：Draft / Bootstrap  
父任务：GitHub Issue #41  
基线：`main@18e4049906fd8e58ab2879558db8ab78bd5975d0`

## 1. 任务定位

Task 13.6-0 是 Task 13.6 的启动步骤，不是业务架构最终定稿，也不进行生产功能开发。

它只解决一个问题：

> 在 Task 13.5 已 `STOPPED / SUPERSEDED` 后，把 Codex 和仓库入口从旧 13.5/Issue #20 的现役施工合同切换到 Task 13.6 的文档与业务重基线工作，避免后续 AI 一边被要求重新审计旧合同，一边又被旧 `AGENTS.md` 要求不得修改旧合同。

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

它用于回答：

> 真实业务到底是怎样运行的？

### 3.2 Accepted Design

项目 owner 曾明确采纳的设计方案。

它是后续架构的重要输入，但仍允许在 13.6 中发现更简单、更可靠、更符合真实业务的方案后重新提出替代设计。

### 3.3 Current Implementation Evidence

以指定 Git SHA 的生产代码、正式入口和实际调用链为准。

它用于回答：

> 当前代码实际上已经做了什么？

不得因为计划或报告写着“完成”就跳过实现核对。

### 3.4 Validation Evidence

包括自动测试、CI、真实 READ_ONLY、受控平台 COMMIT、实机恢复记录和长期运行观察。

它用于回答：

> 某项实现被验证到了什么程度？

代码存在、单测通过、CI 通过、真实平台验收和长期生产可用不是同一种证据等级。

### 3.5 Historical / Candidate Design

包括旧 Issue、历史任务计划、旧实施合同、PR #39 的 7G 候选方案、旧 AGENTS 条款和未被当前 owner 重新确认的 AI 建议。

这些材料需要保留来源和上下文，但不得自动成为 Task 13.6 的现役业务合同。

## 4. 13.6-0 的最小变更

本 bootstrap 只允许：

1. 更新 `AGENTS.md` 当前阶段说明；
2. 建立 Task 13.6 父级 Issue；
3. 新增本 bootstrap 文档；
4. 必要时补充文档导航或状态入口；
5. 做纯文档一致性检查。

本步骤不裁决最终业务 Schema、状态机或系统模块边界。

## 5. 明确禁止事项

13.6-0 不得：

- 修改 `app/`、`shadowbot/`、生产 `scripts/` 或 Runtime Schema；
- 修改真实 Runtime DB、Queue、Worker、Automation 配置或平台状态；
- 执行真实平台 READ_ONLY 或 COMMIT 验收；
- 新增 Current Sales Commitment、Intent、Dispatch Attempt、Coordinator、ObservationHealthService 或 Agent 生产实现；
- 将 PR #39 的 7G 计划整体 cherry-pick / merge 回 `main`；
- 为了让文档看起来一致而重写历史证据内容；
- 在没有实现证据时把目标状态写成“当前已完成”。

## 6. 仍然有效的基础安全约束

Task 13.6 重新审计业务语义，不代表以下已验证安全原则失效：

- 真实平台写不得绕过正式执行链直接调用平台 UI；
- UNKNOWN / NEEDS_RECONCILIATION 不得通过猜测性重复写解决；
- 人工直接在平台/App 操作属于正常运营场景；系统不得假设自己是平台唯一写入者；
- 写前读取、旧状态比较、写后回读继续作为真实写安全基础；
- secrets、token、凭据和本地生产配置不得进入 Git 或日志；
- 平台页面与选择器逻辑必须留在平台 Adapter / ShadowBot 边界；
- 当前单机/SQLite/单 Worker 不自动升级为分布式架构。

这些规则是安全基础，不代表旧 13.5 的库存、日结、Automation、S0-S4 或 Agent 业务设计全部继续有效。

## 7. 历史材料处理规则

### 7.1 Issue #20

降级为 Task 13.5 的历史宏观计划和重要审计输入。

其中真实平台时间、历史验收事实和仍被当前 owner 确认的规则可以继续引用；旧阶段划分、旧交易窗口、旧 S0-S4 细节、Agent 边界等必须在 13.6 中重新判断现行性。

### 7.2 PR #39

保持关闭且未合并。

其中：

- `task13_5_7f_automation_queue_failure_analysis_20260831.md`：重要历史故障证据，需保留其工作树和版本边界；
- 7G Coordinator 计划：候选设计，只作为 13.6-2 的输入之一；
- 对 `AGENTS.md`、Current Status 和“下一步继续 7G”的改动：不得恢复为现役合同。

### 7.3 Task 12 / Task 13 已验证执行资产

这些资产不因为 13.5 停止而自动失效。

后续 13.6-2 需要按实际代码和证据重新判断哪些能力继续原样复用，例如：

- v4/v5 平台执行；
- operation / attempt；
- write lock；
- UNKNOWN → 唯一 RECONCILE；
- ShadowBot 文件 Queue；
- Worker / Importer / Watchdog；
- Review / Notification Outbox。

是否“继续原样复用”必须基于当前调用链和新业务合同，而不是因为历史文档写过“禁止重写”。

## 8. 下一步：13.6-1

13.6-0 合并后进入 13.6-1。

13.6-1 需要完成：

1. 完整文档与重要 Issue/PR 盘点；
2. 项目定位、业务主链、核心术语和时间/数量语义初稿；
3. 将冲突项登记到 Open Decision Register；
4. 不在发现一个问题时立即局部拍板；
5. 在业务主链初稿完成后，一次性执行 Business Decision Closure；
6. G1 业务基线通过后才进入 13.6-2 系统架构重基线。

## 9. 13.6-0 完成条件

以下全部满足，13.6-0 才算完成：

- [ ] Issue #41 已成为当前 Task 13.6 父级入口；
- [ ] `AGENTS.md` 已明确 Task 13.5 `STOPPED / SUPERSEDED`；
- [ ] `AGENTS.md` 不再阻止 Task 13.6 重新审计 Issue #20 和旧 13.5 业务合同；
- [ ] 必要安全底线继续保留；
- [ ] 旧 13.5/PR #39 被标记为 Historical / Audit Input，而非当前施工授权；
- [ ] Task 13.7 仍明确等待 Task 13.6 Stage Goal = `PASS`；
- [ ] 本分支没有生产代码、Schema、真实数据或运行配置修改；
- [ ] 本分支没有真实平台副作用；
- [ ] 下一步明确进入 13.6-1，而不是继续 7G。

## 10. Review 输出

13.6-0 Review 只需要回答：

- Codex 当前上下文是否已经成功从 13.5 切换到 13.6；
- 是否错误丢失了仍然必要的安全边界；
- 是否还有入口会把 7G 描述成当前下一步；
- 是否夹带了生产功能修改。

如果以上通过：

```text
Implementation Review: PASS
13.6-0 Stage Goal: PASS
Next: Task 13.6-1
```

Task 13.6 总体 Stage Goal 仍保持 `NOT YET VALIDATED`，直到 13.6-1/2/3 全部完成并通过 cold-start 验收。
