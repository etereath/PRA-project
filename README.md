# PRA 运行态运营后台

PRA 是面向鲜切花预测性销售与多平台执行任务的运行态运营系统。Runtime Schema v18 以 SQLite 保存商品目录、平台映射、真实库存和运行态事实；Excel 目前只保留为价格/上下架规则等尚未数据库化的输入，以及受控切换的一次性导入来源。

当前已完成的主线能力包括：SQLite 运行态任务系统、人工复核闭环、Mobile Review、飞书 Webhook 真实通知、飞书 post 富文本消息、cpolar 外网访问链路，以及 Web 运行态运营后台。

## 当前状态

已完成：

- Runtime 主数据：商品目录和平台映射由 v18 数据库统一提供；`products.xlsx` 与 `platform_mappings.xlsx` 只在受控干净重建时一次性导入。
- 真实库存：数据库余额与不可变流水是唯一权威；人工调整、销售扣减和取消恢复均走同一库存服务。
- 规则输入：价格规则和上下架规则暂时仍由受控工作簿提供，后续单独数据库化。
- SQLite 运行态：保存任务、复核、通知、执行、Automation、观察、日结、Incident、商品映射与库存事实。
- Web 运营后台：固定为“今日、数据库、业务管理、系统”四个一级入口。
- 人工复核：Web Session 复核与 Mobile Review token 复核均已跑通。
- 飞书通知：支持真实飞书 Webhook，默认使用 post 富文本消息。
- 系统检查：`/system` 可检查配置、schema、运行态表计数，并可手动发送飞书测试通知。

当前未做：

- 普通真实平台任务仍保持“创建任务”和“执行授权”两个阶段；不得扫描全部待处理任务后自动写平台。
- canonical 真实 Runtime DB 尚未在受控维护窗口完成 v18 重建与激活。
- 不引入 AI Agent 自动决策。
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

运营 Web 的商品、平台映射和真实库存均从同一个 Runtime DB 读取。Schema v18 启用后，
启动或创建任务不再需要 `PRA_PRODUCTS_WORKBOOK` / `PRA_PLATFORM_MAPPINGS_WORKBOOK`；两份
工作簿只用于受控干净重建的准备阶段一次性导入。真实库尚未完成 v18 切换时，Web 应保持
不可用并先执行维护窗口流程，不能回退到 XLSX 继续运营。

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
