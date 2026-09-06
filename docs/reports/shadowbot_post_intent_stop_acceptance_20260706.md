# 提交意图后 stop.signal 实机验收报告（2026-07-06）

对象：影刀 `test2`、微信小程序 `蚂蚁花团供应商`、PRA 文件队列。

## 验收目标

验证 `COMMIT` 达到 `SUBMIT_INTENT_RECORDED` 后收到 `stop.signal` 时：

1. 不在副作用区静默退出。
2. 不返回普通可重试失败。
3. 继续完成当前 attempt，至少写出可导入结果。
4. 若平台结果可复核，返回 `SUCCESS/VERIFIED`；结果归档后 Worker 再安全停止。

## 前置安全门

- 来源 READ_ONLY：`ATTEMPT-STOP-PRECHECK-20260706-002`。
- 来源结果：`READ_COMPLETED / NOT_STARTED`。
- 商品：`C级艾莎`。
- 实际旧价：`12.00`。
- 目标价：`12.50`。
- 人工确认文本：`COMMIT C级艾莎 12.00 -> 12.50`。
- 来源共享证据存在且 SHA-256 校验通过。

## 首次尝试与修复

首次 attempt `ATTEMPT-STOP-POSTINTENT-20260706-001` 在 `VALIDATE_INPUT` 阶段安全失败：高频 phase 监视器读取文件时，与影刀 `os.replace` 发生 Windows 文件共享冲突。

结果为 `FAILED / UNKNOWN_ERROR / NOT_STARTED`，商品未修改。随后完成：

- phase 原子替换遇到 `PermissionError`、WinError 5/32/33 时指数退避重试，最多 8 次。
- 无论成功或失败均清理 `.phase.json.tmp_*`。
- 监视器默认轮询间隔由 20 ms 调整为 50 ms。
- phase 重试、停止注入器、队列和状态机回归共 `34 passed`。

首次失败遗留的临时文件已移动到对应 attempt 的 archive，没有直接删除审计现场。

## 最终有效样本

attempt：`ATTEMPT-STOP-POSTINTENT-20260706-002`。

停止注入记录：

- `observed_phase=SUBMIT_INTENT_RECORDED`
- `observed_side_effect_state=SUBMIT_INTENT_RECORDED`
- phase 时间：`2026-07-06T16:29:13+08:00`
- stop.signal 写入时间：`2026-07-06T08:29:13.846085+00:00`

影刀执行时间线：

- `submit_intent_at=2026-07-06T16:29:13+08:00`
- `submit_clicked_at=2026-07-06T16:29:16+08:00`
- `ended_at=2026-07-06T16:29:35+08:00`

最终结果：

- `status=SUCCESS`
- `side_effect_state=VERIFIED`
- `run_success_flag=true`
- `business_operation_completed=true`
- `old_price=12.00`
- `target_price=12.50`
- `actual_price=12.50`
- `retryable=false`
- `queue_phase=RESULT_WRITTEN`
- `final_save_clicked=false`，符合当前平台由内层确认产生副作用的实测边界。

证据：

- `BEFORE_SUBMIT` 已复制到共享目录并校验 SHA-256。
- `AFTER_SUBMIT` 已复制到共享目录并校验 SHA-256。

## 当前收尾状态

核心 stop.signal 行为已经实机通过。后续检查确认 Result Importer 已导入并归档 attempt `ATTEMPT-STOP-POSTINTENT-20260706-002`：请求、phase、result 和 checksum 均已移动到 archive，`inbox/working/results` 已无该 attempt 残留，SQLite 账本记录为 `SUCCESS/VERIFIED`。

仍需运维收尾：

1. 本轮 PowerShell 窗口已被人工关闭，`heartbeat.json` 停留在旧的 `RUNNING` 状态，未留下正常 `STOPPED` 心跳样本。
2. `control/stop.signal` 仍存在，下一次启动 Worker 前必须先清除，避免 Worker 启动后立即退出。
3. `control/*.lock` 文件可能为历史遗留锁；重启前应结合进程状态判断是否清理。

因此本项验收结论为：核心安全行为、结果导入和归档均通过；仅缺少“由 Worker 自然识别 stop.signal 后写出 STOPPED”的收尾心跳样本。该缺口不影响已验证的副作用区保护结论，但会影响下一阶段启动前的运行态清洁度。

## 后续同步（2026-07-11）

- 历史 `control/stop.signal` 已清除，后续队列运行已形成 `STOPPED` 心跳，活动队列无残留。
- 本报告中“下一次启动前必须清除”是当时现场状态，不再是当前待办。
- 提交意图后不静默退出、不中断副作用区且最终写出可导入结果的实机验收已完成；不再把“补充提交意图后停止”列为未来任务。
- 同一次 Worker 自然消费该停止信号并写出 STOPPED 的时序样本没有单独留存，但不影响已经验证的安全状态机结论。
