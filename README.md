# PRA 运行态运营后台

PRA 是面向鲜切花预测性销售与多平台执行任务的运行态运营系统。当前项目仍以 Excel 作为商品、规则、预测等业务输入来源，以 SQLite 作为运行态事实来源，负责生成、追踪、复核和通知运营任务。

当前已完成的主线能力包括：SQLite 运行态任务系统、人工复核闭环、Mobile Review、飞书 Webhook 真实通知、飞书 post 富文本消息、cpolar 外网访问链路，以及 Web 运行态运营后台。

## 当前状态

已完成：

- Excel 业务输入：商品、规则、预测、产能、冷库等输入仍保留为 Excel。
- 商品资料与库存录入：`Business Inputs` 已支持通过运营表单补充公共库存、维护商品基础资料，并保存回 `products.xlsx`。
- 价格规则管理：`Business Inputs` 已支持通过运营表单新增、编辑和查看价格规则，并保存回 `price_rules.xlsx`。
- SQLite 运行态：保存 `tasks`、`review_tasks`、`notification_logs`、`execution_logs`、`task_status_history`、`review_tokens`。
- Web 运营后台：`Dashboard`、`Tasks`、`Reviews`、`Notifications`、`Execution Logs`、`Business Inputs`、`System`。
- 人工复核：Web Session 复核与 Mobile Review token 复核均已跑通。
- 飞书通知：支持真实飞书 Webhook，默认使用 post 富文本消息。
- 系统检查：`/system` 可检查配置、schema、运行态表计数，并可手动发送飞书测试通知。

当前未做：

- 尚未形成真实销售平台的无人值守生产改价闭环；影刀微信小程序已完成真实平台 UI 自动化实验。
- 尚未完成生产级真实 RPA 调度闭环；当前已有 `ShadowBotExecutor` 骨架、文件投递 runner 和结果回灌脚本。
- 不引入 AI Agent 自动决策。
- 不迁移 Excel 主数据。
- 不引入 React/Vue 或前后端分离。
- 不做完整权限系统。

## ShadowBot 凭据与可复部署

ShadowBot 的凭据 provider 位于 `shadowbot/test2/shadowbot_credentials.py`，随仓库提交且只通过 Python 标准库 `ctypes` 按单一 target 调用 Windows Credential Manager 的 `CredReadW`。仓库不保存真实 credential target、账号、密码或 `CredentialBlob`；影刀应用目录中的 `shadowbot_worker_config.json` 必须在部署机本地填写 `login_credential_target`，该路径已由 `.gitignore` 精确保护而示例文件仍可跟踪。生产凭据创建使用 Credential Manager 图形界面，不把密码作为命令行参数传入工具。

部署和验证步骤见 [docs/shadowbot_file_queue_operations.md](docs/shadowbot_file_queue_operations.md)。必须先在影刀中创建或导入 `test2` 应用，再把其真实 `xbot_robot` 目录通过 `--app-dir` 或 `SHADOWBOT_APP_DIR` 显式传入；不再使用开发机默认路径：

```powershell
$env:SHADOWBOT_APP_DIR = "C:\ShadowBot\users\<user>\apps\<app-id>\xbot_robot"
python scripts\sync_shadowbot_test2.py --app-dir $env:SHADOWBOT_APP_DIR --check
python scripts\verify_shadowbot_deployment.py --app-dir $env:SHADOWBOT_APP_DIR
```

provider 在凭据缺失、权限不足、Credential Manager 不可用或记录格式错误时只返回稳定的非敏感错误码，不把 target、账号、密码或 `CredentialBlob` 写入请求、结果、phase、日志、SQLite、截图或证据目录。登录字段继续使用元素原生输入 API，禁止剪贴板输入。

更完整的状态说明见 [docs/project_current_status.md](docs/project_current_status.md)。

## 快速启动

安装项目：

```powershell
pip install -e .
```

核心发行物和 ShadowBot 的独立部署步骤见
[docs/core_wheel_shadowbot_deployment.md](docs/core_wheel_shadowbot_deployment.md)。核心 wheel 只包含 `app*`，不包含 `shadowbot`、`tests`、运行态数据库或部署机配置；安装后可使用 `pra-mvp`（或 `pra`）CLI。

准备本地环境变量：

```powershell
Copy-Item scripts/local_env.example.ps1 scripts/local_env.ps1
notepad scripts/local_env.ps1
```

编辑 `scripts/local_env.ps1` 后启动 Web：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1
```

该脚本只启动 Web。Queue Service 需要独立终端和独立生命周期：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_local_services.ps1
```

默认地址：

```text
http://127.0.0.1:8765
```

也可以使用：

```bat
start_web.bat
```

## 环境变量

核心必填项：

- `RUNTIME_ADMIN_USER`：Web 后台账号，默认 `admin`。
- `RUNTIME_ADMIN_PASSWORD`：Web 后台密码，必须本地配置。
- `PRA_ENV`：新运营 Web 必须显式为 `development` 或 `production`。
- `PRA_WEB_PUBLIC_SCHEME`：development 使用 `http`，production 使用 `https`。
- `PRA_COOKIE_SECURE`：development 使用 `false`，production 使用 `true`；冲突时启动失败。
- `REVIEW_TOKEN_SECRET`：Mobile Review token HMAC 密钥，必须本地配置。

飞书与手机端复核：

- `DEFAULT_NOTIFICATION_CHANNEL=feishu`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_WEBHOOK_SECRET`：如果飞书机器人未开启签名，可留空。
- `FEISHU_MESSAGE_TYPE=post`
- `MOBILE_REVIEW_BASE_URL=https://你的固定公网地址`

详细说明见 [docs/runtime_environment_variables.md](docs/runtime_environment_variables.md)。

## cpolar / Mobile Review

Mobile Review 需要手机能访问本地 Web 服务。当前已验证的方式是使用 cpolar 将本地 `127.0.0.1:8765` 暴露为公网地址，然后把公网地址写入：

```powershell
$env:MOBILE_REVIEW_BASE_URL = "https://你的固定地址.cpolar.cn"
```

飞书通知中会携带 Mobile Review 链接，用户在手机打开后可处理对应 `review_task`。系统不会在 `notification_logs.message` 中保存完整 `token=` 链接。

## 飞书测试通知

登录 Web 后台后进入：

```text
/system
```

点击“发送飞书测试通知”即可验证：

- `FeishuWebhookNotificationSender`
- 飞书 Webhook URL
- 飞书签名配置
- 当前网络连通性

该测试不会创建业务 `review_task`，不会创建 `review_token`，不会生成 `mobile_review_url`，也不会改变任何任务或复核状态。测试结果会以 `recipient_type=system`、`recipient=system_test` 写入 `notification_logs`，便于后续在通知中心排障。

## 常用命令

初始化运行态数据库：

```powershell
python -m app.cli init-runtime-db
```

检查最新 Runtime Schema 与 SQLite 健康状态：

```powershell
pra-mvp health --runtime-db data/runtime/pra_runtime.sqlite3
```

按持久化渠道执行一次通知 Outbox Worker（同时运行 Watchdog）：

```powershell
python -m app.cli notification-worker --runtime-db data/runtime/pra_runtime.sqlite3 --channel feishu
```

管理员恢复或隔离验收时生成运行态任务（日常任务由 Web/Automation 创建）：

```powershell
python -m app.cli generate-runtime-tasks --admin-recovery
```

查看运行态任务：

```powershell
python -m app.cli list-tasks
```

管理员恢复时过期超时复核任务（日常由 Automation 处理）：

```powershell
python -m app.cli expire-review-tasks --apply --admin-recovery
```

## 运行测试

```powershell
python scripts/run_system_smoke_tests.py
python -m unittest discover -s tests
```

当前建议在 Code Review 前先运行系统冒烟测试脚本和完整单元测试，确认主控流程基线稳定。

## 安全边界

不得提交到 git：

- `.env.local`
- `.env`
- `.env.*`
- `scripts/local_env.ps1`
- `REVIEW_TOKEN_SECRET`
- `RUNTIME_ADMIN_PASSWORD`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_WEBHOOK_SECRET`
- 带 `token=` 的 Mobile Review URL
- `data/runtime/`
- `*.sqlite3`
- `*.db`

Web 页面与日志要求：

- 不展示 secret。
- 不展示 raw token。
- 不展示完整 webhook。
- 不展示完整 `mobile_review_url`。
- `notification_logs.message` 不应保存 `token=`。
- Web 后台运行态页面必须登录后访问。

## 文档入口

文档索引见 [docs/index.md](docs/index.md)。
