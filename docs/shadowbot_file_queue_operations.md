# ShadowBot 常驻文件队列运行手册

## 1. 运行边界

第一版不调用影刀 OpenAPI。PRA 使用本地文件队列投递已审批指令，操作员每天手工启动一次影刀 `test2` 常驻流程，独立 PRA 队列服务负责结果导入和超时监测。

逐阶段实机验收步骤见 [shadowbot_filequeue_real_machine_acceptance.md](shadowbot_filequeue_real_machine_acceptance.md)。

- Worker 单线程运行，默认最多 8 小时或 50 个任务。
- `ShadowBotResultImporter` 只处理 `results/*.result.json`。
- `ShadowBotQueueWatchdog` 只处理 `heartbeat.json`、`working/*.phase.json`、超时和遗留 working。
- Worker 和 Watchdog 都不创建对账任务；只有 `ShadowBotExecutor.ensure_reconcile_attempt(...)` 可以创建 `RECONCILE`。
- 普通 COMMIT 失败不自动重试；提交结果未知会自动投递唯一的只读 `RECONCILE`。

## 2. 配置

`scripts/local_env.ps1` 使用以下配置：

```powershell
$env:SHADOWBOT_RUNNER_TYPE = "filequeue"
$env:SHADOWBOT_QUEUE_DIR = "D:\PRA_Runtime\shadowbot_queue"
$env:SHADOWBOT_EVIDENCE_DIR = "\\LAPTOP-O9O76RQV\pra-evidence"
$env:SHADOWBOT_APPLET_URI = "weixin://launchapplet/?app_id=<目标小程序AppID>"
$env:SHADOWBOT_WORKER_POLL_SECONDS = "3"
$env:SHADOWBOT_WORKER_MAX_HOURS = "8"
$env:SHADOWBOT_WORKER_MAX_TASKS = "50"
```

`filedrop` 和 `SHADOWBOT_REQUEST_DIR` 仅作为兼容名称保留。跨机器部署时可将 `SHADOWBOT_QUEUE_DIR` 改为 UNC，不能使用映射盘符。

`SHADOWBOT_APPLET_URI` 是蚂蚁花团供应商小程序的已验证微信 URI，不含账号或密码。Worker 先尝试复用已有“蚂蚁花团供应商”窗口；仅在窗口不存在时才启动此 URI，并在最长 20 秒内轮询目标窗口。URI 必须以 `weixin://launchapplet/` 开头；缺失或前缀不符合时，Worker 在提交前返回 `APPLET_URI_MISSING` 或 `APPLET_URI_INVALID`。URI 已启动但窗口仍未出现时返回可重试的 `WINDOW_NOT_AVAILABLE`，启动协议本身失败时返回 `APPLET_URI_OPEN_FAILED`。

无论窗口来自复用还是 URI 启动，流程顺序固定为：准备窗口 -> 检查/恢复登录 -> 强制刷新商品管理列表 -> 读取价格。登录页上不尝试点击“商品管理”；若需要员工模式、账号密码或手机验证码，必须先完成登录恢复后才进入列表刷新。

### 登录凭据与手机验证码

账号密码不属于 PRA 请求参数。Worker 只在本机运行时通过受版本控制的 `shadowbot/test2/shadowbot_credentials.py`，按部署配置的单一 target 从 Windows Credential Manager 读取 Generic Credential；仓库不保存真实 target、账号或密码。`UserName` 保存平台账号，`CredentialBlob` 保存密码。不得把账号或密码写入 `local_env.ps1`、请求、结果、phase、日志、截图、SQLite 或证据目录。

provider 使用 Python 标准库 `ctypes` 调用 `CredReadW`/`CredFree`，不依赖影刀运行时是否安装 pywin32，也不会枚举 Credential Manager。读取失败只返回稳定的非敏感错误码：`CREDENTIAL_TARGET_MISSING`、`CREDENTIAL_MANAGER_UNAVAILABLE`、`CREDENTIAL_NOT_FOUND`、`CREDENTIAL_ACCESS_DENIED`、`CREDENTIAL_FORMAT_INVALID` 或 `CREDENTIAL_READ_FAILED`；错误文本不包含 target、账号、密码或 `CredentialBlob`。

影刀 `shadowbot_worker_config.json` 必须在人工捕获后配置以下三个元素名称：

```json
{
  "login_auto_enabled": true,
  "login_credential_target": "",
  "login_employee_mode_required": true,
  "login_employee_mode_selector": "登录页_员工按钮",
  "login_account_selector": "登录页_账号输入框",
  "login_password_selector": "登录页_密码输入框",
  "login_submit_selector": "登录页_登录按钮",
  "login_verification_wait_seconds": 600
}
```

将 `login_credential_target` 仅写入部署机未纳入版本控制的 `shadowbot_worker_config.json`，例如使用组织内部约定的 `ShadowBot/<deployment-target>`（该路径已由仓库精确忽略，示例文件仍纳入版本控制；不要把真实 target 回填到仓库、日志或交接记录）。在同一 Windows 用户上下文中创建 Generic Credential，并按以下步骤验证：

1. 先把 `SHADOWBOT_APP_DIR` 设置为影刀已创建的真实 `xbot_robot` 应用目录，并运行 `python scripts\sync_shadowbot_test2.py --app-dir $env:SHADOWBOT_APP_DIR --check`，确认 provider 与 Worker 源文件哈希一致；需要同步时运行同一目录的不带 `--check` 命令。
2. 生产部署使用 Windows Credential Manager 图形界面（运行 `control /name Microsoft.CredentialManager`，选择 Windows 凭据并交互填写 Generic Credential）；禁止把密码作为命令行参数传入凭据创建工具。随后启动 Worker；实际 target、用户名和密码不得写入日志或交接记录。
3. 投递一个脱敏的登录测试请求，确认结果为成功或 `LOGIN_CREDENTIALS_UNAVAILABLE`/`LOGIN_CREDENTIALS_REJECTED` 等稳定登录错误；凭据 provider 失败时，Worker 结果最多保留 allowlist 中的 `provider_error_code`（例如 `CREDENTIAL_NOT_FOUND`），未知异常仍使用安全兜底码。检查请求 JSON、结果、phase、日志和证据目录均不含账号、密码、`CredentialBlob` 或明文 target。
4. 测试结束后在 Credential Manager 图形界面删除受控测试凭据，并删除部署机上的未跟踪 `shadowbot_worker_config.json` 副本（若不再使用）。

蚂蚁花团供应商的员工账号需要先点击“员工”模式按钮，再填写账号密码；该点击仅记录无敏感状态，失败时返回 `LOGIN_AUTOFILL_FAILED`，不会尝试其他登录模式。Worker 每个 attempt 最多提交一次账号密码登录。账号和密码仅使用元素原生输入方法，禁止走剪贴板输入，避免进入 Windows 剪贴板历史。识别到手机验证码后写 `LOGIN_VERIFICATION_REQUIRED` phase；PRA 队列服务创建唯一人工介入 review 和通知。该通知使用专用标题“ShadowBot 登录验证码人工接管”，仅展示平台、执行尝试、截止时间和操作提示，不复用通用业务复核的业务日期、处理对象和原因字段。操作员只在由 Worker 打开或此前已存在的小程序中完成验证码，不向 PRA、影刀请求或飞书回复验证码。首页“商品管理”入口重新出现后，Worker 继续同一 attempt。等待超时返回 `FAILED/LOGIN_VERIFICATION_TIMEOUT/NOT_STARTED`；账号密码错误返回 `LOGIN_CREDENTIALS_REJECTED`，均不自动重试。

默认验证码人工接管窗口为 10 分钟（`600` 秒）。验证码等待到期时，Worker 会额外执行一次无副作用的首页入口检查，避免操作员恰好在最后一轮轮询后完成验证而被误判超时。该检查不延长等待时限，也不重新提交账号密码。

实机验收约定：

- 投递实机请求前必须加载 `scripts\local_env.ps1`；否则请求会进入项目内开发队列 `data\runtime\shadowbot_queue`，不会被 `D:\PRA_Runtime\shadowbot_queue` 的影刀 Worker 领取。
- 当前在售测试商品为“艾莎 B级”。请求中的 `expected_grade` 必须使用 `B级`；传入 `C级` 得到 `PRODUCT_NOT_FOUND` 是业务条件不匹配，不代表登录、刷新或元素定位失败。
- 验证码测试需要留出足够时间让小程序跳转回首页；验收通过的标志是同一 `execution_attempt_id` 从 `LOGIN_VERIFICATION_REQUIRED` 继续到 `READ_COMPLETED`，而不是另起 attempt 的后续读取成功。
- 验收结束后，必须让 Result Importer 导入结果并确认 `working/`、`results/` 没有该 attempt 的活动文件，随后再停止 Worker 并关闭影刀残留运行窗口。
- 商品管理页的 WebView 页面实例 ID（如 `page-103`）会在登录后变化。适配器会优先使用人工捕获的列表容器选择器；未命中时自动移除 `page-*` 临时 ID 后再次查找，稳定的结构属性仍保留。不要把这种容器未命中直接归类为小程序白屏。
- 2026-07-12 实机验证：`ATTEMPT-DYNAMIC-CONTAINER-20260712-114222` 在同一 attempt 内完成员工模式、账号密码单次提交、验证码人工接管、回到首页、商品管理刷新及“艾莎 B级”价格读取；结果为 `READ_COMPLETED`、`old_price=11.80`、`side_effect_state=NOT_STARTED`。
- 2026-07-12 实机停止验证：`ATTEMPT-LOGIN-STOP-20260712-115208` 在 `LOGIN_VERIFICATION_REQUIRED` 阶段收到 `stop.signal` 后写出 `FAILED/WORKER_STOP_REQUESTED/NOT_STARTED`，Result Importer 归档完成后 Worker 心跳变为 `STOPPED`。预提交停止验收的请求绑定、checksum、执行日志、无副作用与队列清空检查均通过。
- 2026-07-13 URI 冷启动验收：`ATTEMPT-URI-FINAL-READ-20260713-154700` 在目标窗口关闭时返回 `applet_launch.source=URI_LAUNCHED`，约 1 秒后获取窗口；随后先完成登录检查，再刷新商品管理列表并读取第 `38` 行“艾莎 B级”价格 `10.00`。最终 `READ_COMPLETED/NOT_STARTED`，证据已复制到共享目录且 SHA-256 一致，完整文件队列验收通过。

## 3. 目录与文件

```text
shadowbot_queue/
├── inbox/
├── working/
├── results/
├── archive/
├── quarantine/
├── evidence/
├── control/
└── heartbeat.json
```

请求以 `<attempt>.ready.json` 和 `.sha256` 发布。Worker 原子领取后改为 `<attempt>.request.json`，并持续写 `<attempt>.phase.json`。结果使用 `<attempt>.result.json` 和 `.sha256`。

`approved_payload_hash` 保护审批业务载荷，`instruction_hash` 保护本次执行指令，`request_file_sha256` 保护最终请求文件字节。`request_file_sha256` 存放在独立 checksum 文件和结果中，不写入被计算 hash 的请求 JSON。

## 4. 启动与停止

加载环境并启动 PRA 队列服务：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\local_env.ps1
python scripts\run_shadowbot_queue_services.py
```

`--queue-dir` 是队列服务的显式运行边界。服务会同步覆盖 `SHADOWBOT_QUEUE_DIR` 与兼容别名 `SHADOWBOT_REQUEST_DIR`，因此 Executor 自动创建的 `RECONCILE` 会回到同一队列。自动对账从已校验的源请求继承 `evidence_share_dir`、`applet_uri` 和 `window_title`；仍需先加载 `SHADOWBOT_EVIDENCE_DIR`，以便共享证据可用。

主流程只调用 `module1.py`；影刀自动执行其中的 `main(args)`，该薄入口再委托 `shadowbot_queue_worker.main(args)`。Worker 将复用 `vertical_slice_read_price.main` 的现有元素逻辑。

外部同步 Python 后，首选在影刀“应用”主页面选中 `test2`，点击其行内圆形“运行应用”图标直接启动主流程。不要为了运行而进入编辑页面：已打开的设计器可能保留旧代码缓存，甚至把旧内容写回磁盘。只有人工录制或改元素时才进入编辑器；外部同步前必须先退出编辑器，随后恢复使用应用列表直接运行。

请求安全停止：

```powershell
New-Item -ItemType File -Force D:\PRA_Runtime\shadowbot_queue\control\stop.signal
```

空闲时立即退出；提交前仅在安全检查点清理 UI 后退出；达到 `SUBMIT_INTENT_RECORDED` 后必须完成验证或写出 `NEEDS_RECONCILIATION`。

若存在已经写出 result 的 `working`，应先让 Result Importer 完成归档，再等待停止；Worker 不会丢弃未归档 working。

确认 `heartbeat.json` 的 `status` 已变为 `STOPPED` 后，删除停止信号，避免下次启动立即退出：

```powershell
Remove-Item D:\PRA_Runtime\shadowbot_queue\control\stop.signal
```

运行期间不要只用肉眼比较 heartbeat 时间。推荐使用严格健康检查：

```powershell
python scripts\check_shadowbot_worker_health.py `
  --queue-dir D:\PRA_Runtime\shadowbot_queue `
  --expected-status RUNNING `
  --max-age-seconds 15 `
  --strict
```

报告会校验心跳年龄、连续写失败、线程重启和遗留临时文件。Worker 会将 heartbeat 写错误追加到 `control/heartbeat_errors.jsonl`；Watchdog 会对 stale 的 `RUNNING` heartbeat 输出一次 `WORKER_HEARTBEAT_STALE`。

Result Importer 遇到 Windows 瞬时文件 I/O 错误时返回 `RETRY_PENDING/RESULT_IO_RETRY_PENDING`，保留 `results` 中的原文件并在下一轮重试，不得立即隔离。只有确定的契约、JSON 或 checksum 错误进入 `quarantine`；同名 `.error.json` 记录隔离原因。修改 Importer 代码后必须重启 PRA 队列服务进程才能生效。

Watchdog 读取 `heartbeat.json`、phase 或 request 时同样可能遇到 Windows 瞬时共享冲突。读取层会自动重试；重试后仍失败时，常驻服务输出 `RETRY_PENDING/WATCHDOG_INSPECTION_FAILED` 并继续下一轮。该事件不得终止 Result Importer 进程，也不得直接把 working attempt 判为失败。

## 5. 恢复规则

| 遗留 phase | 恢复行为 |
| --- | --- |
| `CLAIMED/UI_STARTED/PRICE_VERIFIED` | 写 `WORKER_INTERRUPTED`，不自动重试 COMMIT |
| `TARGET_FILLED` | 仅在确认弹窗已清理时标记可重试，否则要求人工检查 |
| `SUBMIT_INTENT_RECORDED/SUBMIT_CLICKED` | 写 `NEEDS_RECONCILIATION`，由 Executor 自动创建只读对账 |
| `VERIFIED` | 有完整结果快照时补写结果，否则保守进入对账 |
| `RESULT_WRITTEN` | 等待 Result Importer 导入，不重新执行 |

结果或 checksum 不一致时进入 `quarantine` 并返回 `RESULT_CONTRACT_INVALID`。生产 Executor 和 Queue Runner 均拒绝非空 `fault_injection`。

请求若在 PRA 发布后、Worker 领取前过期，且原文件 checksum 可验证，Worker 会写出 `FAILED/REQUEST_EXPIRED/NOT_STARTED` 可导入结果并等待归档；不能可信绑定到数据库 attempt 的损坏请求仍进入 `quarantine`，不得伪造结果。

## 6. 代码同步

只读比较：

```powershell
$env:SHADOWBOT_APP_DIR = "C:\ShadowBot\users\<user>\apps\<app-id>\xbot_robot"
python scripts\sync_shadowbot_test2.py --app-dir $env:SHADOWBOT_APP_DIR --check
python scripts\verify_shadowbot_deployment.py --app-dir $env:SHADOWBOT_APP_DIR
```

实际同步会先备份影刀应用目录中的旧 Python 文件：

```powershell
python scripts\sync_shadowbot_test2.py --app-dir $env:SHADOWBOT_APP_DIR
```

同步后确认编辑器处于关闭状态，并从影刀“应用”主页面的 `test2` 行内“运行应用”图标直接启动。该路径是外部 Python 同步后的默认测试方式；运行完成后仍需关闭影刀残留运行窗口。
