# 影刀小程序 UI 状态故障注入报告（2026-07-04）

对象：影刀 `test2`、桌面微信小程序 `蚂蚁花团供应商`、PRA 文件队列。

## 当前进度

| 场景 | 状态 | 实机结论 |
| --- | --- | --- |
| 登录状态失效 | 已完成 | `FAILED / LOGIN_REQUIRED / NOT_STARTED`，禁止自动重试 |
| 小程序白屏 | 暂不做实机注入 | 当前平台不存在稳定、可重复且不改变系统安全配置的白屏触发机制；分类逻辑仅通过单元测试 |
| 网络或加载异常 | 已完成 | `FAILED / NETWORK_OR_LOAD_ERROR / NOT_STARTED`；恢复后 `READ_COMPLETED` |

## 登录状态失效

人工将小程序停留在供应商登录页，页面可见“欢迎使用蚂蚁花团供应商端”、账号输入框、密码输入框和“登录”按钮。测试未填写账号、密码，也未触发登录操作。

首次真实 `READ_ONLY`：

- attempt：`ATTEMPT-FI-LOGIN-EXPIRED-20260704-002`
- 结果：`FAILED / ELEMENT_NOT_FOUND / NOT_STARTED`
- 当前步骤：`OPEN_PRODUCT_MANAGEMENT`
- 结论：副作用边界正确，但错误分类过于笼统。

修复后真实 `READ_ONLY`：

- attempt：`ATTEMPT-FI-LOGIN-EXPIRED-20260704-003`
- 结果：`FAILED / LOGIN_REQUIRED / NOT_STARTED`
- `run_success_flag=false`
- `business_operation_completed=false`
- `retryable=false`
- 当前步骤：`OPEN_PRODUCT_MANAGEMENT`
- result 已由 `ShadowBotResultImporter` 导入并归档。

`retryable=false` 表示 PRA 不得在登录状态未恢复时自动重复投递；人工重新登录后可创建新的 execution attempt。

## 实现补充

`vertical_slice_read_price.py` 在找不到商品管理入口时读取可访问 UI 文本，并按顺序识别：

1. 登录页标记，返回 `LOGIN_REQUIRED`。
2. 网络或加载错误标记，返回 `NETWORK_OR_LOAD_ERROR`。
3. 仅剩微信外壳文本、没有业务内容，返回 `MINI_PROGRAM_BLANK_SCREEN`。
4. 页面仍有其他业务文本时保留原始 `ELEMENT_NOT_FOUND`，避免把选择器失效误报成白屏。

本地回归：登录、网络、白屏、窗口重试、证据和对账相关测试共 `28 passed`。

## 待完成

白屏场景当前不伪造实机通过。后续只有在平台出现可重复的自然白屏，或建立不依赖生产 UI 的专用测试页面后，才补做实机验收。

## 网络或加载异常

第一次真实断网：

- attempt：`ATTEMPT-FI-NETWORK-20260704-001`
- 在 Worker 断点前断开网络，继续运行后等待，再恢复网络。
- 小程序进入商品管理页，但商品列表长期停留在“加载中...”。
- 结果：`FAILED / ELEMENT_NOT_FOUND / NOT_STARTED`，`retryable=true`。
- 结论：副作用边界正确，但原分类只覆盖“商品管理入口不存在”，未覆盖“入口可点击、业务容器加载失败”。

修复内容：

- 将“加载中”纳入 `NETWORK_OR_LOAD_ERROR` 标记。
- 商品管理入口查找失败和目标业务容器加载失败均调用统一 UI 状态分类。
- 页面仍有正常业务文本时不误报白屏。
- 登录、网络、白屏、窗口重试、证据和对账相关回归共 `28 passed`。

网络恢复后只读验证：

- attempt：`ATTEMPT-FI-NETWORK-20260704-002`
- 结果：`READ_COMPLETED / NOT_STARTED`。
- 实际读取 `C级艾莎` 价格 `20.00`，共享证据上传和 hash 校验成功。
- 该结果证明恢复路径正常，但执行时页面已经恢复，不能证明修复后的网络错误码。

中间协调样本：

- `ATTEMPT-FI-NETWORK-20260704-003` 和 `004` 因人工执行间隔超过有效期，均安全返回 `REQUEST_EXPIRED / NOT_STARTED`。
- `ATTEMPT-FI-NETWORK-20260706-005` 在点击“继续”后又被人工强制停止，窗口准备阶段返回无效窗口句柄；实现已增加三次窗口获取/准备重试，最终失败改为明确的 `WINDOW_NOT_AVAILABLE`。
- `ATTEMPT-FI-NETWORK-20260706-006` 从已加载的商品列表断网，仍可读取缓存 DOM 并返回 `READ_COMPLETED`，证明该注入前置条件无效。

最终有效样本：

- 前置状态：已登录并停在首页，断网后才点击影刀“继续”。
- attempt：`ATTEMPT-FI-NETWORK-20260706-007`。
- 页面状态：进入商品管理后持续显示“加载中...”。
- 结果：`FAILED / NETWORK_OR_LOAD_ERROR / NOT_STARTED`。
- `run_success_flag=false`、`business_operation_completed=false`、`retryable=true`。
- phase：`RESULT_WRITTEN`，result 已导入归档。

恢复验证：

- 恢复网络并返回首页后投递 `ATTEMPT-FI-NETWORK-RECOVERY-20260706-008`。
- 结果：`READ_COMPLETED / NOT_STARTED`。
- 读取 `C级艾莎` 实际价格 `12.00`，共享证据上传成功且本地/共享 SHA-256 一致。
- result 已导入归档，队列未自动重试写操作。

## 结论

登录失效和网络加载异常均已通过真实小程序验证，并保持 `side_effect_state=NOT_STARTED`。白屏没有稳定、可重复的真实触发机制，因此只保留分类逻辑和单元测试，不宣称实机通过。
