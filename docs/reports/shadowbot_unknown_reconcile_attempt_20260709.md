# COMMIT 后 UNKNOWN→RECONCILE 实机尝试记录（2026-07-09）

对象：影刀 `test2`、微信小程序 `蚂蚁花团供应商`、PRA 文件队列。

## 目标

验证真实商品 `COMMIT` 点击内层确认后、列表复核前出现结果未知时，系统进入 `NEEDS_RECONCILIATION`，并由 PRA/Executor 自动创建唯一 `RECONCILE`。

## 最终结果

首轮因旧价变化安全中止；第二轮已完成真实 `UNKNOWN -> 自动 RECONCILE -> VERIFIED`。未发生自动重复 `COMMIT`。

## 时间线

1. `ATTEMPT-UNKNOWN-PRECHECK-20260709-001` 完成 `READ_ONLY`，读到 `C级艾莎` 价格 `12.50`，证据与 hash 校验通过。
2. 用户确认文本：`COMMIT C级艾莎 12.50 -> 13.00`。
3. `ATTEMPT-UNKNOWN-COMMIT-20260709-001` 投递后追加测试专用 `fault_injection=AFTER_SUBMIT_CLICK_UNKNOWN`。由于小程序已退到登录态或页面状态不完整，流程在 `OPEN_PRICE_DIALOG` 阶段失败：
   - `status=FAILED`
   - `error_code=ELEMENT_NOT_FOUND`
   - `side_effect_state=NOT_STARTED`
   - `actual_price=12.50`
   - 未产生平台副作用。
4. 该失败结果已导入归档。过程中发现测试补丁脚本修改请求后未同步数据库中的 `request_file_sha256`，导致首次导入被 `RESULT_CONTRACT_INVALID` 拒绝；随后修正测试脚本，使后续 patch 请求时同步更新 DB hash。
5. 用户重新登录后，重新执行 `ATTEMPT-UNKNOWN-PRECHECK-20260709-002`，正式 `READ_ONLY` 读到当前价格已变为 `8.00`：
   - `status=READ_COMPLETED`
   - `side_effect_state=NOT_STARTED`
   - `actual_price=8.00`
   - 共享证据 hash 校验通过。

## 第一轮结论

原确认文本绑定的是 `12.50 -> 13.00`，但最新平台旧价为 `8.00`，因此不得继续执行该 COMMIT。系统在真实运行前通过重新 `READ_ONLY` 安全门发现旧价变化，正确中止了后续提交。

## 第二轮完成样本

用户基于最新旧价重新确认：

```text
COMMIT C级艾莎 8.00 -> 13.00
```

随后执行受控 COMMIT，并在请求投递后通过测试专用入口追加 `fault_injection=AFTER_SUBMIT_CLICK_UNKNOWN`。本次测试入口同步更新了数据库中的 `request_file_sha256`，避免请求被 patch 后与数据库记录不一致。

COMMIT attempt：

- `execution_attempt_id=ATTEMPT-UNKNOWN-COMMIT-20260709-002`
- `execution_mode=COMMIT`
- `status=NEEDS_RECONCILIATION`
- `error_code=SUBMIT_RESULT_UNKNOWN`
- `side_effect_state=UNKNOWN`
- `retryable=false`
- `expected_old_price=8.00`
- `target_price=13.00`
- `input_price_readback=13.00`
- `submit_intent_at=2026-07-09T17:16:59+08:00`
- `submit_clicked_at=2026-07-09T17:17:02+08:00`

Importer 导入后，`ShadowBotExecutor` 自动创建唯一对账 attempt：

- `execution_attempt_id=RECONCILE-57cc1892fb8d24961556`
- `execution_mode=RECONCILE`
- `status=VERIFIED`
- `side_effect_state=VERIFIED`
- `actual_price=13.00`
- `business_operation_completed=true`

Operation `OP-UNKNOWN-COMMIT-20260709-002` 最终归并为 `VERIFIED`。

## 配置修复与回归

本轮最初暴露两个运行配置问题：单次 Importer 未显式绑定队列根目录，且自动 RECONCILE 未继承共享证据目录。首个真实样本已人工迁移请求并完成账本验证；随后完成以下代码收尾：

1. `run_shadowbot_queue_services.py --queue-dir` 现在同时强制设置 `SHADOWBOT_QUEUE_DIR` 和兼容别名 `SHADOWBOT_REQUEST_DIR`，防止自动 RECONCILE 落到默认队列。
2. Result Importer 从已校验的源请求提取 `evidence_share_dir`、`applet_uri` 与 `window_title`，并作为受限运行上下文传给 Executor 创建的 RECONCILE；Worker、Importer 和 Watchdog 仍不自行创建对账任务。
3. 手工回归已验证显式 `--queue-dir` 会覆盖冲突的旧环境变量，且自动 RECONCILE 请求会继承 `\\TEST-HOST\pra-evidence`。生产运行仍应从 `local_env.ps1` 加载真实 `SHADOWBOT_EVIDENCE_DIR`。

后续已修复 `.pytest_cache` 和 `%TEMP%\pytest-of-etere` 的 ACL，并在普通 Windows 用户环境完成 4 项定向回归，结果为 `4 passed`。下一次非副作用 `READ_ONLY` 或独立对账演练仍应再确认自动 RECONCILE 在真实共享目录上的证据上传。

## 最终收尾状态

- COMMIT UNKNOWN attempt 已归档。
- 自动 RECONCILE attempt 已归档。
- Operation 已归并 `VERIFIED`。
- 队列 `inbox/working/results` 无活动文件。
- Worker 已自然 `STOPPED`。
- `control/stop.signal` 已删除。
- 测试专用 `allow_fault_injection` 已恢复为 `false`。
