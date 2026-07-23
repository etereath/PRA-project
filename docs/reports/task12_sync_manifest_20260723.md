# 任务12同步清单

本文用于整理任务12当前工作区，并约束后续 GitHub 同步范围。本文不改变任务
状态；任务状态仍等待审查方确认。Google Drive 不属于当前交付步骤，只有用户
再次明确授权时才单独执行。

## 1. 同步原则

- GitHub 保存可复现的源码、测试、配置样例、维护文档和经过筛选的任务12证据。
- 当前文档交接以 GitHub PR 为准；Google Drive 目标仅保留为历史参考，不自动同步。
- `D:\PRA_Runtime`、影刀运行队列、原始运行归档、账号凭据和本机环境配置均不得直接同步。
- 原 Google Docs 任务12实施计划作为历史计划保留，不覆盖修改；最终交接材料以新增文档形式接续。
- 任务12状态保持不变，等待审查方验收后再更新。

## 2. GitHub 目标

### 分支与 PR

- 基线：最新 `origin/main`。
- 新分支：`codex/task12-final-handoff`。
- 交付形式：一个汇总任务12当前实现、清理结果、测试和文档的 Draft PR。
- 不继续使用 `codex/task12-preflight-agents`：该分支已合并，且当前本地分支落后远端，不适合作为最终交接分支。

### 纳入范围

- `app/`：任务中心、正式 COMMIT、平台商品身份、全页面状态回传和任务结果导入相关实现。
- `scripts/`：任务12正式入口、映射核对、影刀同步和队列维护脚本。
- `shadowbot/test2/`：当前 Worker、READ_ONLY 和 COMMIT 执行代码；不包含凭据或本机配置。
- `tests/`：任务12合同、正式队列、成功基线、READ_ONLY 基线、库存映射和状态回写测试。
- `docs/`：当前状态、最终交接、开发复盘、证据索引、对接规范、复用清单和归档索引。
- `data/samples/`：无敏感信息且用于测试/演示的工作簿样例。
- `outputs/task12/`：请求、manifest、队列预览和人工 Markdown 报告；不包含控制台传输日志。
- `AGENTS.md`、`.gitignore` 和必要的启动/维护配置。

### 排除范围

- `outputs/019f8400-1a8d-7ea3-a5a1-e3fef7098eb4/`：独立工具工作区、依赖目录和临时产物。
- `outputs/task12/*.stdout.log`、`outputs/task12/*.stderr.log`：本机控制台传输日志。
- `D:\PRA_Runtime\`：运行数据库、队列、phase、result、截图和完整归档。
- `.env`、实际账号、密码、令牌、验证码、个人路径配置及影刀本机配置。
- 其他任务或用户未确认的临时文件。

### 特别说明

`shadowbot/test2/product_identity_mapping.json` 当前仅作为合同测试夹具使用；正式入口的默认 SKU 映射源是 `data/samples/products.xlsx`，即“商品资料与库存录入”。提交前需要在 PR 描述中明确这一点，避免把测试夹具误认为正式映射真值。

最新 `origin/main` 中的 `docs/task12_handoff_and_implementation_plan.md` 属于历史计划。本次整合已将它明确移动到 `docs/archive/shadowbot_pre_task12/`，没有静默删除。

## 3. Google Drive 历史目标

本节只记录此前确认过的位置，不构成当前上传授权。

目标文件夹：`PRA 项目管理`

目标文件夹 ID：`1LvFY2uTye1A0Mp6t_lZEZErjXUe5n3Yp`

历史任务12文档：`PRA 任务12交接与实施计划：单平台多商品串行改价`

历史文档 ID：`1aGkFXnfM5hTYB3ys6b1S0hzA1DgBAPfqsly5HkmuHm0`

建议新增同步以下人工文档：

1. `docs/reports/task12_final_handoff_20260723.md`
2. `docs/reports/task12_development_report_20260723.md`
3. `docs/reports/task12_evidence_index_20260723.md`
4. `docs/project_current_status.md`
5. `docs/task12_reusable_assets.md`
6. `docs/shadowbot_listing_status_integration.md`

原实施计划不直接覆盖。若需要在原文留下接续入口，只追加一段简短说明和上述新增文档链接，不重写原章节。

## 4. 同步前门禁

1. 在最新 `origin/main` 上重放并审查当前任务12改动，避免旧分支基线造成误删或覆盖已合并内容。
2. 检查 Git diff，确认归档移动、旧实现删除和新实现增加均属于任务12当前架构。
3. 对新增和修改文件执行敏感信息扫描；命中环境变量名或测试占位符不等同于泄密，但实际值必须为零。
4. 回读所有中文 Markdown、JSON 和工作簿关键字段，分别确认文件编码、内容和业务证据。
5. 只运行与重放/冲突处理相关的最小验证；纯文档整理不重复执行影刀实机测试。
6. 创建 GitHub Draft PR，并以 PR 作为当前文档交接入口。
7. 只有用户再次明确授权 Google Drive 上传时，才重新核对目标文件夹并执行上传后回读。

## 5. 当前准备状态

- 已隔离独立工具工作区和任务12控制台日志，未删除本地文件。
- 已定位 GitHub 远端、当前分支差异和最终建议分支。
- 已定位 Google Drive 项目文件夹与历史任务12文档。
- 已按 UTF-8/UTF-8-SIG 回读 126 个待同步文本文件，未发现解码失败。
- 已解析 `outputs/task12/` 中 38 个 JSON，未发现结构错误或非空敏感键。
- 已检查 64 个 Markdown 文件中的 148 个本地链接，未发现失效链接。
- 已回读三个样例工作簿的工作表、表头和前两行样例。
- 已确认 `start_web.bat` 为纯 ASCII，并规范为 CRLF 换行。
- 尚未暂存、提交、推送或创建 PR；当前不计划上传 Google Drive。
- 尚未改变任务12状态。
