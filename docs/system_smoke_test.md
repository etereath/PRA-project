# 系统冒烟测试说明

本文档说明 `scripts/run_system_smoke_tests.py` 的用途、运行方式和排查方法。该脚本用于建立当前主控流程的测试基线，防止后续 Code Review、真实 RPA、平台适配器或更多 Web 改造破坏已经跑通的运行态闭环。

## 什么时候运行

建议在以下场景运行：

- 每次进入 Code Review 前。
- 每次完成 Code Review 风险修复后。
- 下一轮功能开发、发布或回归排查前。
- 修改 `RuntimeTaskService`、`ReviewTaskService`、`ReviewTokenService`、`NotificationSender`、Web 运行态页面或 SQLite repository 后。
- 准备接入真实 RPA、真实平台适配器或真实通知渠道前。
- 手机端复核、飞书通知、Web 复核流程出现异常时，用于确认主控闭环是否仍然健康。

## 如何运行

在项目根目录执行：

```powershell
python scripts/run_system_smoke_tests.py
```

输出为中文，每一项会显示 `OK` 或 `FAILED`。如果存在失败项，脚本会显示失败原因、建议检查模块，并以非 0 退出码结束。

示例：

```text
[OK] runtime DB 初始化成功
[OK] schema version exact v5
[OK] v5 RetryAuthorization 结构完整
[FAILED] review token 创建、校验、使用后失效
  原因：REVIEW_TOKEN_SECRET is required
  建议检查模块：ReviewTokenService
```

## 测试库隔离

脚本固定使用独立测试库：

```text
data/runtime/test_runtime_smoke.sqlite3
```

运行前会清理并重建该测试库，不会写入或污染真实运行库：

```text
data/runtime/pra_runtime.sqlite3
```

脚本内部会临时设置 smoke 专用环境变量，并在结束后恢复当前进程环境。它不会读取或打印你的真实 secret、飞书 webhook、raw token 或完整 Mobile Review 链接。

## 不会做的事

冒烟测试保持安全边界：

- 不真实发送飞书通知，默认使用 `mock` notification sender。
- 不访问 cpolar 或任何外网入口。
- 不接真实平台。
- 不接真实 RPA。
- 不引入 AI Agent 自动决策。
- 只初始化隔离的 smoke SQLite schema，不修改真实运行库。
- 不修改业务规则。
- 不打印 secret、raw token、完整 webhook、完整 `mobile_review_url` 或 `token=`。

## 当前覆盖范围

脚本至少覆盖以下主控流程：

- runtime DB 初始化成功。
- schema version 必须精确为 v5，迁移记录连续包含 `1..5`。
- 关键表存在：`tasks`、`review_tasks`、`notification_logs`、`execution_logs`、`task_status_history`、`review_tokens`、`script_runs`、`script_run_items`、`shadowbot_operations`、`shadowbot_execution_attempts`、`shadowbot_side_effect_checkpoints`、`retry_authorizations`。
- v5 RetryAuthorization 表的列、外键、`max_uses=1`、状态约束、两个唯一约束和 operation/status/expires_at 索引完整。
- 创建 runtime task。
- `dedupe_key` 去重有效。
- 创建 `pending review_task`。
- `pending review_task` 自动触发 mock `notification_log`。
- `notification_logs.message` 不包含 `token=` 或完整 Mobile Review URL。
- Web Session 来源的 `approved` 复核可以推动 source task。
- `task_status_history` 正常写入。
- 非 `pending review_task` 重复处理会失败。
- review token 可以创建、校验，使用后失效。
- 过期 token 或重复使用 token 会失败。
- `/dashboard`、`/tasks`、`/reviews`、`/notifications`、`/system` 未登录时不暴露运行态数据。
- `/system` 不展示 secret 明文、完整 webhook、完整 runtime DB 路径或 `token=`。

## 失败后如何排查

按脚本输出的“建议检查模块”优先定位：

- `SQLiteRuntimeRepository / RuntimeTaskService.init_schema`：检查运行态 schema 初始化、测试 DB 路径和表结构。
- `runtime_schema_migrations`：检查 schema version 是否精确为 `5` 且迁移记录连续。
- `runtime schema health check`：检查 v5 必需表、列、约束和索引，避免“伪 v5”数据库通过检查。
- `RuntimeTaskService.create_tasks`：检查 task 模型、状态枚举、`dedupe_key` 和 partial unique index。
- `ReviewTaskService.create_from_tasks`：检查人工复核任务生成、`MANUAL_INTERVENTION_ACTIONS` 和复核 dedupe。
- `ReviewNotificationService / MockNotificationSender`：检查 `DEFAULT_NOTIFICATION_CHANNEL=mock` 和通知日志写入。
- `ReviewTaskService / RuntimeTaskService`：检查复核状态流转、source task 推动和历史记录。
- `ReviewTokenService`：检查 `REVIEW_TOKEN_SECRET`、token hash、过期、撤销和 `used_at` 逻辑。
- `app.web session guard`：检查 Web Session 登录保护，避免公网未登录暴露运行态数据。
- `app.web render_system_page`：检查 `/system` 的脱敏展示，避免泄露 secret、webhook、raw token 或本地完整路径。

如果冒烟测试失败，建议先修复并重新运行脚本，再进入更深入的 Code Review、发布验收或下一轮功能开发。

## 与 Code Review 的关系

推荐作为 Code Review 前后共同使用的基线：

1. Code Review 前先运行 `python scripts/run_system_smoke_tests.py`，确认主控流程基线。
2. 再运行完整单元测试：

```powershell
python -m unittest discover -s tests
```

3. Code Review 修复后再次运行冒烟测试和完整单元测试。
4. 两者都通过后，再进入下一轮功能开发或发布验收。

冒烟测试不是完整测试套件的替代品。它的作用是快速确认“Excel 输入之外的运行态主控闭环”没有被破坏，完整业务规则和边界仍需要依赖单元测试、人工验收和后续真实链路测试。
