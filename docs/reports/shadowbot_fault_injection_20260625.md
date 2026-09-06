# 影刀微信小程序改价故障注入报告

日期：2026-06-25

对象：影刀应用 `test2`、流程 `vertical_slice_read_price.py`、桌面端微信小程序 `蚂蚁花团供应商`

本报告记录 `COMMIT/RECONCILE` 开发测试版在真实桌面微信小程序上的核心故障注入结果。测试商品为 `C级 艾莎`。测试完成后平台实际价格经只读对账确认为 `7.30`。

## 1. 测试目标

本轮故障注入用于验证以下上线前关键边界：

1. 提交前错误必须安全失败，不进入副作用区。
2. 输入回读不一致必须停止，不得点击确认或保存。
3. 提交点击后、列表复核前发生异常时，必须进入 `NEEDS_RECONCILIATION`，且 `retryable=false`。
4. 结果未知后必须通过 `RECONCILE` 只读流程核对实际价格，不得自动重复提交。
5. 结构化结果必须区分 `status`、`error_code`、`side_effect_state`、`run_success_flag` 和 `business_operation_completed`。

## 2. 本轮发现并修复的问题

| 问题 | 影响 | 修复结果 |
| --- | --- | --- |
| 页面价格已等于 `target_price` 时绕过 `expected_old_price` 校验 | 可能把审批时旧价已变化的任务误判为成功 | 在 `FILL_PREVIEW/COMMIT` 读取列表旧价后、任何成功判定前强制比较 `actual_price == expected_old_price`，不一致返回 `OLD_PRICE_CHANGED` |
| 缺少参数时错误消息乱码 | 影响 PRA 和人工排障 | `_required_text` 缺参消息改为 ASCII：`missing required parameter: <name>` |
| 缺少可控的价格回读不一致注入点 | 难以验证 `TARGET_PRICE_VERIFY_FAILED` 路径 | 新增仅测试请求显式启用的 `fault_injection=PRICE_READBACK_MISMATCH` |
| 提交后结果未知注入点不适配当前页面 | 当前小程序没有稳定暴露独立最终保存按钮；弹窗确认后即可复核 | 新增 `fault_injection=AFTER_SUBMIT_CLICK_UNKNOWN`，在弹窗确认后、列表复核前模拟结果未知 |
| 提交前弹窗阶段失败后未统一清理 | 价格弹窗可能残留，影响下一次运行 | 在 `FILL_TARGET_PRICE/CAPTURE_BEFORE_SUBMIT` 且未进入提交副作用时，失败后尝试点击取消，并记录 `cleanup_action` 或 `cleanup_error` |

## 3. 注入结果汇总

| 编号 | 场景 | attempt id | 结果 | 关键证据 |
| --- | --- | --- | --- | --- |
| FI-01 | 审批旧价变化 | `ATTEMPT-FI-OLD-PRICE-CHANGED-20260625-002` | 通过 | `FAILED / READ_OLD_PRICE / NOT_STARTED / OLD_PRICE_CHANGED / retryable=false / actual_price=7.30` |
| FI-02 | 商品找不到 | `ATTEMPT-FI-PRODUCT-NOT-FOUND-20260625-001` | 通过 | `FAILED / LOCATE_PRODUCT / NOT_STARTED / PRODUCT_NOT_FOUND / retryable=false` |
| FI-03 | 缺少 `target_price` | `ATTEMPT-FI-INPUT-MISSING-TARGET-20260625-002` | 通过 | `FAILED / VALIDATE_INPUT / NOT_STARTED / INPUT_INVALID / missing required parameter: target_price` |
| FI-04 | 价格输入回读不一致 | `ATTEMPT-FI-PRICE-READBACK-MISMATCH-20260625-001` | 通过 | `FAILED / FILL_TARGET_PRICE / NOT_STARTED / TARGET_PRICE_VERIFY_FAILED / cleanup_action=PRICE_DIALOG_CANCELLED` |
| FI-05 | 提交点击后结果未知 | `ATTEMPT-FI-AFTER-SUBMIT-CLICK-UNKNOWN-20260625-002` | 通过 | `NEEDS_RECONCILIATION / CONFIRM_PRICE_DIALOG / UNKNOWN / SUBMIT_RESULT_UNKNOWN / retryable=false` |
| FI-06 | 结果未知后只读对账 | `ATTEMPT-FI-RECONCILE-AFTER-UNKNOWN-20260625-001` | 通过 | `VERIFIED / COMPLETE / VERIFIED / actual_price=7.30` |

## 4. 结果文件

所有结构化结果均位于：

```text
C:\Users\etere\AppData\Local\ShadowBot\results\vertical_slice
```

关键文件：

```text
ATTEMPT-FI-OLD-PRICE-CHANGED-20260625-002.json
ATTEMPT-FI-PRODUCT-NOT-FOUND-20260625-001.json
ATTEMPT-FI-INPUT-MISSING-TARGET-20260625-002.json
ATTEMPT-FI-PRICE-READBACK-MISMATCH-20260625-001.json
ATTEMPT-FI-AFTER-SUBMIT-CLICK-UNKNOWN-20260625-002.json
ATTEMPT-FI-RECONCILE-AFTER-UNKNOWN-20260625-001.json
```

第六条只读对账证据截图已复制到共享目录，并完成哈希复核：

```text
\\LAPTOP-O9O76RQV\pra-evidence\ATTEMPT-FI-RECONCILE-AFTER-UNKNOWN-20260625-001\ATTEMPT-FI-RECONCILE-AFTER-UNKNOWN-20260625-001_reconcile.png
```

SHA-256：

```text
eba7808522a4108b5e9664ef70e1b9918a5b416887e29b4e091e33565cabe2b7
```

## 5. 当前结论

1. `READ_ONLY/FILL_PREVIEW/COMMIT/RECONCILE` 垂直切片已具备真实小程序上的核心安全边界。
2. 提交前失败能保持 `side_effect_state=NOT_STARTED`。
3. 提交点击后失败能进入 `NEEDS_RECONCILIATION`，且不会被标记为可自动重试。
4. `RECONCILE` 能在结果未知后只读核对实际价格，并将 `actual_price == target_price` 归类为 `VERIFIED`。
5. 当前小程序实际副作用边界应按 `INNER_CONFIRM` 管理：弹窗“确认”后已经可能产生可复核的平台结果，不应假设必须存在独立最终保存按钮。
6. 进入生产前仍需补充更大样本的连续运行、登录/网络/元素失效场景，以及 PRA `ShadowBotExecutor` 真实调度闭环。

## 6. 验证限制与 pytest 环境结论

2026-06-25 本轮最后曾尝试运行以下回归测试：

```powershell
python -m pytest tests/test_shadowbot_evidence_share.py tests/test_shadowbot_vertical_slice_reconcile.py
```

当时使用的是 Codex bundled runtime Python：

```text
C:\Users\etere\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

该 Python 环境未安装 pytest，因此报：

```text
No module named pytest
```

2026-06-26 已复查本机系统 Python：

```text
C:\Users\etere\AppData\Local\Programs\Python\Python314\python.exe
```

系统 Python 已安装 `pytest 9.1.1`。使用系统 Python 在非沙箱环境运行同一组测试，结果通过：

```text
12 passed in 0.15s
```

结论：项目 pytest 测试应使用 `python -m pytest` 调用系统 Python。不要使用 Codex bundled runtime Python 运行 pytest，除非先为该 runtime 单独安装 pytest。Codex 沙箱内还可能因临时目录权限导致 `PermissionError: [WinError 5] 拒绝访问`，这属于沙箱临时目录权限问题，不代表 pytest 未安装。
