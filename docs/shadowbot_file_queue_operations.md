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
$env:SHADOWBOT_WORKER_POLL_SECONDS = "3"
$env:SHADOWBOT_WORKER_MAX_HOURS = "8"
$env:SHADOWBOT_WORKER_MAX_TASKS = "50"
```

`filedrop` 和 `SHADOWBOT_REQUEST_DIR` 仅作为兼容名称保留。跨机器部署时可将 `SHADOWBOT_QUEUE_DIR` 改为 UNC，不能使用映射盘符。

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

影刀代码同步后必须关闭并重新打开 `test2`。主流程只调用 `module1.py`；影刀自动执行其中的 `main(args)`，该薄入口再委托 `shadowbot_queue_worker.main(args)`。Worker 将复用 `vertical_slice_read_price.main` 的现有元素逻辑。

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
python scripts\sync_shadowbot_test2.py --check
```

实际同步会先备份影刀应用目录中的旧 Python 文件：

```powershell
python scripts\sync_shadowbot_test2.py
```

同步后关闭并重新打开影刀应用，否则影刀可能继续使用内存中的旧代码。
