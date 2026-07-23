# 核心 wheel 与 ShadowBot 独立部署

本文档定义两套发行物的边界。核心 wheel 用于 PRA 运行态服务；ShadowBot 使用仓库中的独立源码和同步脚本部署到 Windows 影刀应用目录。ShadowBot 不默认进入核心 wheel，也不从核心 wheel 反向加载未安装的仓库源码。

## 1. 发行物边界

核心 wheel 只允许包含：

- `app/` 及其 Python 子包；
- 构建元数据和运行时所需的 Python 依赖。

核心 wheel 明确禁止包含：

- `shadowbot/`、`tests/`、`scripts/`、`docs/`、`data/`；
- `*.sqlite3`、`*.db`、日志、结果或证据目录；
- `shadowbot_worker_config.json`、`scripts/local_env.ps1` 或任何部署机凭据配置。

ShadowBot 独立发行物由以下受版本控制内容组成：

- `shadowbot/test2/` 中的 Worker、凭据 provider 和业务入口；
- `shadowbot_worker_config.example.json`；
- `scripts/sync_shadowbot_test2.py`；
- 本文档和 [ShadowBot 文件队列运行手册](shadowbot_file_queue_operations.md)。

真实凭据只在部署机 Windows Credential Manager 中创建；真实 target、账号、密码和 `CredentialBlob` 不进入仓库、wheel、请求、结果、日志或证据目录。

## 2. 构建与边界审计

在 Windows PowerShell 中：

```powershell
python -m pip install build
Remove-Item -LiteralPath build, dist -Recurse -Force -ErrorAction SilentlyContinue
python -m build --no-isolation
python scripts/verify_packaging.py
```

在 Linux/macOS 中使用等价命令：

```bash
python -m pip install build
rm -rf build dist
python -m build --no-isolation
python scripts/verify_packaging.py
```

`verify_packaging.py` 会检查 sdist/wheel 的 SHA-256、必须存在的核心子包、禁止目录、运行态数据库和构建产物中的敏感标记。构建前清理 `build/` 与 `dist/`，确保连续构建不会夹带上一次运行态文件。

## 3. 核心 wheel 隔离安装

下面的验证在仓库外目录执行，不使用 editable 安装：

```powershell
$wheel = (Get-ChildItem dist\*.whl | Select-Object -First 1).FullName
$verifyRoot = Join-Path $env:TEMP "pra-mvp-wheel-smoke"
$venv = Join-Path $verifyRoot "venv"
Remove-Item -LiteralPath $verifyRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $verifyRoot | Out-Null
python -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --no-cache-dir $wheel

Push-Location $verifyRoot
& "$venv\Scripts\python.exe" -c "import app, app.repositories.sqlite_runtime_repository, app.services.runtime, app.runtime_schema"
& "$venv\Scripts\pra-mvp.exe" --help
& "$venv\Scripts\pra-mvp.exe" init-runtime-db --runtime-db "$verifyRoot\runtime.sqlite3"
& "$venv\Scripts\pra-mvp.exe" health --runtime-db "$verifyRoot\runtime.sqlite3"
Pop-Location
```

预期结果：import、CLI 帮助、数据库初始化和 health 均为退出码 `0`；初始化输出包含从 `1` 连续到 `app.runtime_schema.LATEST_RUNTIME_SCHEMA_VERSION` 的完整版本序列（当前为 `1..12`），health 输出包含 `ok=True`。数据库应位于临时目录，不写入 wheel 或仓库。

如果机器上没有 `pra-mvp.exe`，可使用等价入口：

```powershell
& "$venv\Scripts\python.exe" -m app.cli health --runtime-db "$verifyRoot\runtime.sqlite3"
```

## 4. ShadowBot 干净克隆部署

部署机只需要从 GitHub 获取受版本控制源码；不要把 ShadowBot 源码复制进 Python wheel 的安装目录。`--app-dir` 必须指向影刀已经创建或导入的真实 `xbot_robot` 应用目录，不能指向新建的空目录。该目录至少必须包含影刀宿主生成的 `package.py` 和项目选择器资源 `selectorsV2.xml`；运行时还需要影刀提供的 `xbot` 宿主模块。

```powershell
git clone https://github.com/etereath/PRA-project.git
Set-Location PRA-project
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip

$appDir = $env:SHADOWBOT_APP_DIR
if (-not $appDir) { throw "Set SHADOWBOT_APP_DIR to an existing ShadowBot xbot_robot directory" }
& ".\.venv\Scripts\python.exe" scripts\sync_shadowbot_test2.py --app-dir $appDir
& ".\.venv\Scripts\python.exe" scripts\sync_shadowbot_test2.py --app-dir $appDir --check
& ".\.venv\Scripts\python.exe" scripts\verify_shadowbot_deployment.py --app-dir $appDir
```

第二次 `--check` 必须为每个受同步文件报告 `CURRENT`；配置文件只报告 `EXISTS`。在部署机上将 `shadowbot_worker_config.json` 从 example 复制后填写本机配置，但该文件不得回写仓库：

```powershell
Copy-Item shadowbot\test2\shadowbot_worker_config.example.json $appDir\shadowbot_worker_config.json
notepad $appDir\shadowbot_worker_config.json
```

`login_credential_target` 只填写 Windows Credential Manager 中已创建的 Generic Credential target。密码不得作为命令行参数传递。真实影刀 UI、cpolar、飞书通知和真实账号属于受控手工验收，不是普通干净部署的自动通过条件。

不安装核心 wheel 时，`verify_shadowbot_deployment.py` 只验证 ShadowBot 发行物、宿主文件和 Python 语法，不导入 `app`；实际运行由影刀 `xbot`/`package.py`/选择器资源提供宿主能力。该边界不依赖开发机 `PYTHONPATH`。

构建物、核心源码、ShadowBot 源码、部署目录、结果/证据目录和日志使用同一 secret scan：

```powershell
python scripts\verify_packaging.py `
  --scan-dir app `
  --scan-dir shadowbot `
  --scan-dir $appDir `
  --scan-dir D:\PRA_Runtime\shadowbot_queue\results `
  --scan-dir D:\PRA_Runtime\shadowbot_queue\evidence `
  --scan-dir D:\PRA_Runtime\shadowbot_queue\logs
```

扫描区分 provider 中安全的字段名（例如 `CredentialBlob`）和非空的真实账号、密码、target、token 值；缺失目录、真实值或构建物中未知成员都会返回非零退出码。

## 5. 验收命令

```powershell
python -m pytest -q tests -k "package or packaging or wheel or install"
python -m pytest -q tests/test_shadowbot_credentials.py
python -m pytest -q tests/test_shadowbot_queue.py -k "login or read_only"
python -m pytest -q tests -k "schema or health"
python scripts/run_system_smoke_tests.py
python scripts/sync_shadowbot_test2.py --check --app-dir $env:SHADOWBOT_APP_DIR
python scripts/verify_shadowbot_deployment.py --app-dir $env:SHADOWBOT_APP_DIR
python scripts/verify_packaging.py --scan-dir app --scan-dir shadowbot --scan-dir $env:SHADOWBOT_APP_DIR
```

构建产物、ShadowBot 部署目录和日志完成 secret scan 后，才能把任务 6 标记为完成。任务 7 再将这些命令固化到 GitHub Actions；任务 6 不创建 workflow，也不替代任务 4 的 ShadowBot 状态机与 lease 验收。
