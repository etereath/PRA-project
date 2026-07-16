# 核心 CI：Windows Core 与最低限度 Linux Core

`.github/workflows/core-ci.yml` 为面向 `main` 的 Pull Request、`main` push 和手工触发提供两个稳定检查：`Windows Core` 与 `Linux Core`。两个 Job 都固定使用 Python 3.11、最小 `contents: read` 权限、并发取消和明确超时，不读取仓库 Secrets，也不依赖真实账号、真实 UI、cpolar 或外部通知渠道。

## Windows Core

Windows 是当前 PRA 的主要运行门禁，执行：

- 完整 `pytest` 套件；
- Credential Manager provider mock 与脱敏失败测试；
- ShadowBot 文件队列、登录、lease、Importer、Watchdog 与恢复回归；
- Windows 路径、防逃逸、中文路径和 UTF-8 编码回归；
- wheel/sdist 构建、严格 allowlist、源码敏感信息扫描和仓库外隔离安装；
- 临时 ShadowBot 宿主 fixture 的同步、`--check`、部署验证、哈希漂移失败、缺宿主文件失败和空目录失败；
- 使用仓库外临时 SQLite 的 core smoke，以及 `app`、`shadowbot`、`scripts` 语法检查。

托管 Runner 不启动真实影刀，不读取部署机 Credential Manager，不连接真实销售平台。真实 UI、人工验证码、cpolar 和真实通知继续属于受控手工验收。

## 最低限度 Linux Core

Linux Job 只证明核心 Python 包没有被 Windows 隐式依赖锁死，执行：

- wheel/sdist 构建、严格制品审计，以及 `app`/`shadowbot` 源码静态敏感信息扫描；
- 仓库外 venv 安装最终 wheel，验证关键 import、`pra-mvp --help`、Runtime Schema `1..5` 初始化和 health；
- `python -m pytest -q tests -k "not shadowbot"`，运行核心、SQLite、Schema v5、Web/安全和平台无关测试；
- 使用仓库外临时 SQLite 的 core smoke；
- `app` 与共享 `scripts` 的语法检查。

Linux Job 不导入或运行 ShadowBot/xbot，不同步或部署 ShadowBot，不访问 Credential Manager，不实现 Linux 凭据后端，也不新增 systemd、Docker、日志轮转、备份恢复或生产安装能力。Linux Core 通过不代表 PRA 已支持 Linux 生产部署。

当前任务 7 分支的选择基线为 `199 passed, 145 deselected, 60 subtests passed`。`145 deselected` 是节点 ID 含 `shadowbot` 的明确排除清单，不是运行后 skip；若后续测试数量变化，应从 Workflow 日志复核新增或减少的节点是否仍符合最低限度 Linux Core 边界。

## 本地复现

Windows 开发机可执行：

```powershell
python -m pip install "setuptools>=68" wheel build pytest "openpyxl>=3.1.5,<4"
python -m pytest -q tests
Remove-Item -LiteralPath build, dist, pra_mvp.egg-info -Recurse -Force -ErrorAction SilentlyContinue
python -m build --no-isolation
python scripts/verify_packaging.py --scan-dir app --scan-dir shadowbot
python scripts/verify_core_wheel_install.py --dist-dir dist
python scripts/verify_windows_core_fixture.py
python scripts/run_system_smoke_tests.py --temporary-db
python -m compileall -q app shadowbot scripts
```

Linux 的正式证据来自 GitHub 托管的 `ubuntu-latest`，本机无需安装 Linux、WSL2 或 Docker。

## 失败语义

以下故障必须返回非零退出码并使对应 Job 失败：

- pytest 或 core smoke 失败；
- wheel/sdist 出现未知成员，或敏感信息扫描命中违规值；
- 隔离 venv 无法导入核心模块、运行 CLI、初始化 Schema v5 或通过 health；
- ShadowBot fixture 缺少 `package.py`/`selectorsV2.xml`，或 `sync --check` 发现哈希漂移；
- CI 命令修改 checkout。

`tests/test_packaging.py` 中以下测试覆盖失败语义：

- `test_strict_allowlist_rejects_extra_wheel_and_sdist_members`：未知制品成员必须失败；
- `test_secret_scan_distinguishes_safe_provider_fields_from_values`：真实凭据样式值必须失败；
- `test_multiple_dist_artifacts_require_explicit_selection`：制品数量不唯一时退出码为 `2`；
- `test_shadowbot_sync_requires_real_host_fixture`：空宿主和缺宿主文件必须失败。

Windows fixture 脚本在 Job 中另外验证真实 CLI 退出码：正常同步/检查/部署为 `0`，哈希漂移、缺宿主文件和空目录均为 `1`。Workflow 不使用 `continue-on-error`，也不忽略命令退出码。

## 分支保护

首次在同一 PR head 上获得双绿后，由项目负责人在 GitHub 的 `main` 分支保护或 Ruleset 中将以下检查设为合并必需：

- `Linux Core`
- `Windows Core`

仅合并 Workflow 文件不会自动启用分支保护；启用后应在任务交接记录中保留文字确认或截图。
