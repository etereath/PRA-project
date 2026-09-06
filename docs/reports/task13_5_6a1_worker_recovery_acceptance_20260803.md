# 任务 13.5-6A-1 Worker 恢复实机验收（2026-08-03）

## 1. 结论

本轮在真实 Windows 影刀 6.2.23 和真实长期队列目录上完成了受控 R4 验收。已通过：

- 队列为空且影刀宿主缺失时，由唯一 Coordinator 请求重启已核实安装路径的宿主；
- 影刀登录完成后，唯一选择 `test2`，再调用同一 `myAppsView` 中唯一、具名且支持
  `InvokePattern` 的“运行”按钮；
- Worker 启动请求本身不算成功，必须等到新的 `RUNNING` heartbeat；
- heartbeat 成功后解决原 Incident，写稳定恢复事件和待发送 Outbox，不发送真实飞书；
- 生命周期记录更新为新 Worker 的启动时间、初始处理数 0 和 `RUNNING_VERIFIED`；
- 空队列下通过 `stop.signal` 正常停止，确认 `STOPPED` 后删除信号，再由同一入口恢复。

未执行：`Ctrl+Alt+Q` 故障注入。该动作只允许在活动 working 已由 Watchdog 保留证据、
正常停止仍无效时使用。本轮队列始终为空，不能为了验收伪造真实活动请求或制造平台执行
风险；该分支继续由合成测试覆盖，不能标记为实机通过。

## 2. 隔离和安全边界

- 一次性 Runtime Schema v15 数据库：
  `D:\PRA_Runtime\acceptance\task13_5_6a1_worker_recovery_20260803\runtime_v15.sqlite3`；
- 真实队列：`D:\PRA_Runtime\shadowbot_queue`；
- Incident、恢复 Event 和通知 Outbox 只写一次性数据库；
- 未投递 READ_ONLY 或 COMMIT 请求，未读取或修改平台商品、订单和价格；
- 未发送飞书消息；
- `automatic_emergency_offline=false` 保持不变；
- 验收结束时 `inbox/working/results` 均为空，`stop.signal` 不存在，Worker 保持长期
  `RUNNING`。

## 3. 初始状态与失败关闭证据

初始生命周期仍记录历史 `RUNNING`，但真实 heartbeat 为历史 `STOPPED`，且没有运行中的
`ShadowBot.Shell.exe`。队列三目录为空，`stop.signal` 不存在，因此符合“状态记录滞后、
宿主缺失、允许恢复”的分支。

首次恢复成功请求启动
`C:\Program Files (x86)\ShadowBot\shadowbot-6.2.23\ShadowBot.Shell.exe`。helper 只允许该
批准安装根目录内的进程。登录等待后，旧定位器没有安全识别运行按钮，Coordinator 在截止
时间后只记录一次失败，没有重复重启。

实机诊断发现两个实现问题：

1. 影刀先通过唯一 `test2` `ListItem` 的 `SelectionItemPattern` 选择应用，然后才在列表上方
   工具栏暴露唯一“运行” Button；该按钮不属于订单行，也不能用行内坐标猜测。
2. Windows PowerShell 5.1 会把无 BOM UTF-8 `.ps1` 中的中文控制常量按 ANSI 解释，导致
   “运行”和“保存”匹配失效。正式 helper 已改为纯 ASCII 源码，通过 Unicode 码点构造
   UIA 标签，不依赖当前代码页或 BOM。

前 3 次 Incident 出现分别保存了失败事实；每次出现只领取一轮宿主动作。修复后使用新的
出现次数重试，没有覆盖或改写历史失败事件。

## 4. 成功链证据

第四次出现于 `2026-08-02T18:14:57.419884+00:00` 返回
`WORKER_START_REQUESTED`，helper 明确返回 `The test2 run control was invoked.`。随后
heartbeat 于 `2026-08-02T18:15:11.160665+00:00` 变为 `RUNNING`，下一 Coordinator 周期
返回 `WORKER_RECOVERED` 并将 Incident 转为 `RESOLVED`。

为验证停止和再次恢复，空队列下创建 `stop.signal`，Worker 于
`2026-08-02T18:22:13.266614+00:00` 写出 `STOPPED / processed=0`。确认停止后立即删除
信号，队列仍为空。

第五次出现于 `2026-08-02T18:23:26.115495+00:00` 再次返回
`WORKER_START_REQUESTED`；新 heartbeat 于 `2026-08-02T18:23:39.686353+00:00` 变为
`RUNNING`。最终生命周期为：

- `recorded_state=RUNNING`；
- `shadowbot_window_state=RUNNING_VERIFIED`；
- `worker_started_at=2026-08-02T18:23:26.115495+00:00`；
- `worker_processed_count=0`；
- `reason=WORKER_RECOVERY_SUCCEEDED`。

最终证据文件：

`D:\PRA_Runtime\acceptance\task13_5_6a1_worker_recovery_20260803\step8_final_worker_recovered.json`

SHA-256：`955f1f622f1dd537850aad7b85926153b7a53f2fcff6c8f7b2d22f8dc3130965`。

## 5. 验收裁决

| 分支 | 裁决 |
| --- | --- |
| 缺失宿主与进程路径核实 | 通过 |
| 至少 20 秒登录等待 | 通过 |
| 唯一 `test2` 与语义运行按钮 | 通过 |
| 新鲜 `RUNNING` heartbeat 成功门禁 | 通过 |
| Incident/Event/Outbox/lifecycle | 通过 |
| 空队列正常停止并删除信号 | 通过 |
| 停止后再次恢复长期 Worker | 通过 |
| `Ctrl+Alt+Q` 卡死活动请求故障注入 | 未执行；不得写为通过 |
| 平台写副作用 | 0 |

