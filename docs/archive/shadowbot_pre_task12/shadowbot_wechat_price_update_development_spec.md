# 影刀控制桌面微信小程序商品改价流程开发文档

> 归档说明（2026-07-23）：本文是单商品垂直切片时期的开发规范，保留安全状态、故障分类和早期验收设计供追溯；其中“单商品”“COMMIT 前强制 READ_ONLY/FILL_PREVIEW”“逐项独立投递”等描述已被任务12 v4 多商品合同替代。当前规范以 [任务12最终交接报告](../../reports/task12_final_handoff_20260723.md)、[当前项目状态](../../project_current_status.md) 和 [平台价格快照与 ShadowBot 改价对接说明](../../shadowbot_listing_status_integration.md) 为准。

版本：1.7

适用环境：Windows 11、影刀 RPA、桌面端微信小程序 `WeChatAppEx`

流程建议名称：`微信小程序_商品改价_生产候选版`

## 1. 文档目的

本文用于指导开发、调试和验收影刀流程。流程接收 PRA 下发的单商品改价任务，通过微信小程序 URI 启动目标小程序，确认登录状态，定位并核对商品，修改供货价格，并将结果、证据和错误信息结构化回传 PRA。

首版只支持单次运行处理一个商品。PRA 负责计算并审批最终 `target_price`；`ShadowBotExecutor` 在服务端验证审批和锁定业务操作后，才生成可提交的执行指令。影刀不计算价格、不查询审批记录，也不把某个非空审批字符串视为授权证明。

## 2. 设计原则

1. **元素优先**：优先使用影刀桌面软件元素的标准/深度模式；元素不可用时，才使用 CV、图像或相对坐标兜底。
2. **先判断后操作**：每次页面跳转后验证页面标志，不以固定等待时间代替状态判断。
3. **提交前再校验**：保存前重新核对商品、旧价、PRA 下发的目标价和执行模式。
4. **失败即停止**：商品匹配不唯一、身份不一致、价格异常或页面状态不明时，不执行保存。
5. **全程可审计**：关键节点记录时间、步骤、业务值、截图和影刀运行标识。
6. **幂等与尝试分离**：`operation_id` 标识一次批准的业务变更，`execution_attempt_id` 标识一次影刀运行；一个业务操作可以有多个执行尝试。
7. **副作用状态显式化**：最早潜在副作用动作发出后、结果验证前发生的任何不确定性都进入对账状态，禁止自动重试。

## 3. 系统边界

```text
PRA 任务/人工复核
  -> ShadowBotExecutor（触发、查询、回写）
  -> 影刀流程
  -> 微信 URI / WeChatAppEx
  -> 销售平台小程序
  -> 结构化结果、截图、运行日志
  -> PRA execution_logs
```

PRA 负责生成任务、计算 `target_price`、业务审批、调度和结果归档。`ShadowBotExecutor` 是审批校验和执行授权边界；影刀只负责执行 Executor 生成的不可变 UI 指令，不负责计算价格或证明审批有效。

### 3.1 服务端审批与授权边界

`ShadowBotExecutor` 在调用影刀前必须在 PRA 服务端完成以下检查：

1. 审批记录真实存在且状态为已批准。
2. 审批记录属于当前 `task_id`。
3. 审批批准的 `platform_name`、`platform_sku` 和 `target_price` 与待执行指令完全一致。
4. 审批未过期、未撤销、未被其他执行操作消费。
5. 审批后的关键参数未被修改；比较批准时保存的规范化载荷摘要 `approved_payload_hash`。摘要至少覆盖 `task_id`、`operation_id`、平台、SKU、商品身份、`expected_old_price`、`target_price` 和提交模式。
6. 对 `operation_id` 获取执行锁，并检查业务操作台账中没有 `VERIFIED` 或 `UNKNOWN` 的未处理记录。

只有全部通过，Executor 才能生成 `execution_mode=COMMIT` 的不可变执行指令。审批 ID 可以留在 Executor 审计日志中，但不是影刀侧的安全判断条件，也不要求作为影刀业务参数。

## 4. 输入参数契约

| 参数 | 类型 | 必填 | 示例 | 校验规则 |
| --- | --- | --- | --- | --- |
| `task_id` | string | 是 | `TASK-20260620-001` | 非空，与 PRA 任务一致 |
| `operation_id` | string | 是 | `OP-20260620-001` | 业务幂等键；同一次批准的价格变更保持不变 |
| `execution_attempt_id` | string | 是 | `ATTEMPT-20260620-001-02` | 每次影刀运行唯一，重试必须生成新值 |
| `platform_name` | string | 是 | `蚂蚁花团供应商` | 必须与审批载荷及任务平台一致 |
| `applet_uri` | string | 是 | `weixin://launchapplet/?app_id=...` | 必须以允许的 URI 前缀开头 |
| `platform_sku` | string | 条件必填 | `SKU-001` | 与 `product_keyword` 至少填写一个；优先使用 SKU |
| `product_keyword` | string | 条件必填 | `C级艾莎` | 不允许只用过短或模糊关键词提交 |
| `expected_product_name` | string | 是 | `艾莎` | 编辑页必须一致 |
| `expected_grade` | string | 是 | `C级` | 编辑页必须一致 |
| `expected_spec` | string | 否 | `20枝/扎` | 传入时必须一致 |
| `target_price` | decimal | 条件必填 | `9.00` | `FILL_PREVIEW/COMMIT/RECONCILE` 必填；由 PRA 计算，影刀不得重新计算 |
| `expected_old_price` | decimal | 条件必填 | `8.50` | `FILL_PREVIEW/COMMIT/RECONCILE` 必填；必须与页面旧价一致 |
| `min_price` | decimal | 否 | `0.01` | 目标价不得低于该值 |
| `max_price` | decimal | 否 | `9999.99` | 目标价不得高于该值 |
| `execution_mode` | enum | 是 | `COMMIT` | 新接口必填：`READ_ONLY/FILL_PREVIEW/COMMIT/RECONCILE` |
| `should_submit` | boolean | 否 | `false` | 仅兼容旧调用，内部立即转换为执行模式后不再使用 |
| `instruction_hash` | string | 是 | `sha256:...` | Executor 对不可变执行指令生成的审计摘要 |
| `login_wait_seconds` | integer | 否 | `120` | 建议 30 至 300 秒 |
| `step_timeout_seconds` | integer | 否 | `20` | 建议 5 至 60 秒 |
| `max_retry_count` | integer | 否 | `2` | 建议 0 至 3 次 |
| `evidence_share_dir` | string | 建议 | `\\pra-share\evidence` | PRA 与机器人机都可访问的 Windows UNC 共享目录；配置后截图必须复制并哈希复核 |
| `evidence_storage_uri_prefix` | string | 否 | `\\pra-share\evidence` | PRA 展示或下载证据时使用的 URI 前缀；为空时使用复制后的共享路径 |

参数校验失败时返回 `INPUT_INVALID`，不得启动微信或产生 UI 修改。影刀只检查指令结构是否完整，不能自行证明审批真实有效；审批失败应由 Executor 在调用影刀前返回 `EXECUTION_NOT_AUTHORIZED`。

`execution_mode` 是生产接口的唯一权威模式字段。兼容规则仅用于旧调用：`should_submit=true -> COMMIT`，`should_submit=false -> FILL_PREVIEW`。布尔字段无法表达 `READ_ONLY` 和 `RECONCILE`，不得用于新接入。

当前垂直切片实现是 `蚂蚁花团供应商` 的单平台适配器。代码中可传 `window_title` 只表示窗口获取阶段的标题；元素库 XML 仍固定 `title=蚂蚁花团供应商`、`app=WeChatAppEx`。因此文档中的 `platform_name`、`applet_uri` 和窗口标题不应被理解为已经支持任意平台切换。多平台接入应建立独立元素库或在运行前动态生成完整窗口选择器。

| 模式 | 允许的 UI 行为 | 使用场景 |
| --- | --- | --- |
| `READ_ONLY` | 只导航、搜索、读取和截图；不得聚焦或修改价格输入框 | 日常探测、审批前取数、元素冒烟 |
| `FILL_PREVIEW` | 允许填写并回读；不得点击任何可能确认或持久化价格的按钮 | 调试输入控件，不是审批步骤 |
| `COMMIT` | 服务端审批通过后，执行填写、确认、保存和复核 | 正式改价 |
| `RECONCILE` | 只读查询实际价格；不得填写、确认或保存 | 提交结果未知后的对账 |

正常生产链路应为“PRA 建议 -> PRA 人工审批 -> Executor 验证 -> `COMMIT`”，不要求先执行 `FILL_PREVIEW`，也不因预览结果再创建一次审批。`READ_ONLY` 和 `FILL_PREVIEW` 是诊断或预检模式。

## 5. 输出参数契约

影刀主流程统一返回一个 JSON 对象；无论成功、待确认还是失败，字段结构保持一致。

```json
{
  "schema_version": "vertical-slice-1.5",
  "task_id": "TASK-20260620-001",
  "operation_id": "OP-20260620-001",
  "execution_attempt_id": "ATTEMPT-20260620-001-02",
  "shadowbot_run_id": "影刀运行实例 ID",
  "execution_mode": "COMMIT",
  "status": "SUCCESS",
  "run_success_flag": true,
  "business_operation_completed": true,
  "submitted": true,
  "side_effect_state": "VERIFIED",
  "side_effect_boundary": "INNER_CONFIRM",
  "submit_intent_at": "2026-06-20T10:00:14+08:00",
  "submit_clicked_at": "2026-06-20T10:00:15+08:00",
  "current_step": "VERIFY_LIST_PRICE",
  "platform_name": "蚂蚁花团供应商",
  "platform_sku": "SKU-001",
  "product_name": "艾莎",
  "grade": "C级",
  "spec": "20枝/扎",
  "old_price": "8.50",
  "target_price": "9.00",
  "verified_price": "9.00",
  "product_list_refreshes": [
    {
      "stage": "BEFORE_PRICE_READ",
      "refresh_entry": "蚂蚁_首页_商品管理_入口",
      "status": "SUCCESS",
      "matched_row_index": 17,
      "started_at": "2026-06-20T10:00:05+08:00",
      "ended_at": "2026-06-20T10:00:07+08:00"
    }
  ],
  "error_code": "",
  "error_message": "",
  "retryable": false,
  "retry_suggestion": "",
  "evidence_status": "COMPLETE",
  "evidence": [
    {
      "evidence_id": "EVD-001",
      "type": "BEFORE_SUBMIT",
      "local_path": "D:/rpa-evidence/.../02_before_submit.png",
      "storage_uri": "file://pra-share/evidence/.../02_before_submit.png",
      "sha256": "0123456789abcdef...",
      "size_bytes": 245817,
      "captured_at": "2026-06-20T10:00:13+08:00",
      "upload_status": "SUCCESS"
    }
  ],
  "started_at": "2026-06-20T10:00:00+08:00",
  "ended_at": "2026-06-20T10:00:18+08:00"
}
```

`status` 取值：

| 状态 | 含义 | `run_success_flag` | `business_operation_completed` | PRA 建议动作 |
| --- | --- | --- | --- | --- |
| `SUCCESS` | 已保存且列表复核通过 | `true` | `true` | 任务置为成功 |
| `ALREADY_APPLIED` | 页面价格原本已是目标价 | `true` | `true` | 记录幂等成功，不重复提交 |
| `READ_COMPLETED` | 只读取数和证据采集完成 | `true` | `false` | 记录探测结果，不改变业务任务状态 |
| `PREVIEW_COMPLETED` | 填写和回读完成，未执行确认或保存 | `true` | `false` | 记录预检结果，不创建第二次审批 |
| `VERIFIED` | `RECONCILE` 只读对账确认实际价等于目标价 | `true` | `true` | 将原结果未知操作归并为已验证成功 |
| `NOT_APPLIED` | `RECONCILE` 只读对账确认实际价仍为审批旧价 | `true` | `false` | 记录保存未生效；是否重新执行由 Executor 重新决策 |
| `FAILED` | 当前模式未达到预期结果，且不属于独立对账终态 | `false` | `false` | 写错误日志；仅由 Executor 按规则决定是否创建新尝试 |
| `NEEDS_RECONCILIATION` | 保存可能已发生但结果未知 | `null` | `null` | 禁止自动重试，启动只读对账流程 |

`run_success_flag` 只表示本次影刀技术模式是否按预期完成，不代表 PRA 商品改价业务已经完成。PRA 只允许 `status in ["SUCCESS", "ALREADY_APPLIED", "VERIFIED"]` 且 `business_operation_completed=true` 时更新业务任务为完成；其中 `VERIFIED` 只能由 `RECONCILE` 对账流程产生，用于归并此前结果未知的业务操作。状态与布尔字段冲突时返回 `RESULT_CONTRACT_INVALID`，不得猜测。

旧状态 `AWAITING_CONFIRMATION` 只作为历史数据兼容，不再由 1.3 流程产生。

`side_effect_state` 取值：

| 状态 | 含义 |
| --- | --- |
| `NOT_STARTED` | 尚未进入最早潜在副作用区 |
| `SUBMIT_INTENT_RECORDED` | 已持久化提交意图，即将点击内层确认或平台适配器提交动作 |
| `SUBMIT_CLICKED` | 内层确认或平台适配器提交动作已发出，尚未完成列表复核 |
| `VERIFIED` | 已在列表确认目标价 |
| `NOT_APPLIED` | 已在列表明确确认仍为旧价 |
| `UNKNOWN` | 点击可能生效，但无法确认平台最终状态 |

异常分类必须遵守以下矩阵：

`status`、`error_code` 和 `side_effect_state` 是三个独立字段：状态表示流程终态，错误码表示终态原因，副作用状态表示平台写入阶段。任何适配器都不得把错误码写入 `status`，也不得用 `error_code` 代替终态判断。

| 最后持久化副作用状态 | `status` | `error_code` | `retryable` | 后续动作 |
| --- | --- | --- | --- | --- |
| `NOT_STARTED` | `FAILED` | 具体的提交前错误码 | 按错误码 | Executor 可在审批仍有效时创建新尝试 |
| `SUBMIT_INTENT_RECORDED` | `NEEDS_RECONCILIATION` | `SUBMIT_RESULT_UNKNOWN` | `false` | 先执行只读对账 |
| `SUBMIT_CLICKED` | `NEEDS_RECONCILIATION` | `SUBMIT_RESULT_UNKNOWN` | `false` | 先执行只读对账 |
| `VERIFIED` | `SUCCESS` | 空字符串 | `false` | 完成业务操作 |
| `NOT_APPLIED` | `NOT_APPLIED` | `SUBMIT_NOT_APPLIED` | `false` | Executor 重新决策，不自动提交 |
| `UNKNOWN` | `NEEDS_RECONCILIATION` | `SUBMIT_RESULT_UNKNOWN` 或 `POST_SUBMIT_PRICE_MISMATCH` | `false` | 人工或只读对账 |

上表描述改价执行流程自身的异常归类。独立 `RECONCILE` 只读对账流程不点击确认或保存，它可以直接返回 `VERIFIED`、`NOT_APPLIED` 或 `NEEDS_RECONCILIATION`：

| 对账实际价格 | `status` | `error_code` | `side_effect_state` | 含义 |
| --- | --- | --- | --- | --- |
| `actual_price == target_price` | `VERIFIED` | 空字符串 | `VERIFIED` | 目标价已在平台生效 |
| `actual_price == expected_old_price` | `NOT_APPLIED` | `SUBMIT_NOT_APPLIED` | `NOT_APPLIED` | 仍是审批旧价，未观察到保存生效 |
| 其他价格 | `NEEDS_RECONCILIATION` | `POST_SUBMIT_PRICE_MISMATCH` | `UNKNOWN` | 需要人工核对或更高权限对账 |

已确认保存未生效时，结果必须使用不同字段表达状态和原因：

```json
{
  "status": "NOT_APPLIED",
  "error_code": "SUBMIT_NOT_APPLIED",
  "side_effect_state": "NOT_APPLIED",
  "run_success_flag": true,
  "business_operation_completed": false,
  "retryable": false
}
```

保存结果未知时：

```json
{
  "status": "NEEDS_RECONCILIATION",
  "error_code": "SUBMIT_RESULT_UNKNOWN",
  "side_effect_state": "UNKNOWN",
  "run_success_flag": null,
  "business_operation_completed": null,
  "retryable": false
}
```

映射到当前 PRA `ExecutionLog` 时：`executor_name=shadowbot_wechat_price_update`，完整 JSON 写入 `raw_output`，摘要写入 `ai_summary`。现有数据库 `success_flag` 暂时映射 `run_success_flag`，不能用于判断业务任务完成；其余字段分别映射 `error_code`、`error_message`、`start_time` 和 `end_time`。后续数据库迁移应增加独立的 `business_operation_completed` 字段。

## 6. 流程变量

| 变量 | 类型 | 用途 |
| --- | --- | --- |
| `software_window` | 窗口对象 | 当前微信小程序窗口 |
| `step_name` | string | 当前步骤，异常回传时使用 |
| `retry_count` | integer | 当前动作重试次数 |
| `old_price_text` | string | 页面原始价格文本 |
| `old_price` | decimal | 解析后的旧价 |
| `target_price` | decimal | PRA 下发的已审批目标价 |
| `execution_mode` | string | 标准化后的执行模式 |
| `run_success_flag` | boolean/null | 当前影刀技术模式是否按预期完成 |
| `business_operation_completed` | boolean/null | PRA 业务变更是否已完成 |
| `side_effect_state` | string | 当前副作用阶段 |
| `side_effect_boundary` | string | 本版本采用的最早潜在副作用动作 |
| `submit_intent_at` | datetime/null | Executor 确认持久化提交意图的时间 |
| `submit_clicked_at` | datetime/null | 内层确认或平台适配器提交动作发出时间 |
| `readback_price` | decimal | 输入框回读值 |
| `verified_price` | decimal | 列表复核值 |
| `matched_product_count` | integer | 搜索结果精确匹配数量 |
| `evidence` | list | 带哈希和存储状态的证据对象列表 |
| `evidence_status` | string | 证据清单汇总状态 |
| `result` | dictionary | 最终结构化结果 |

`target_price` 是 PRA 下发的已审批价格。影刀仅用十进制定点数解析、格式化和比较金额，不执行加减价或其他定价计算。

## 7. 主流程详细设计

### 7.1 接收并校验 PRA 参数

1. 将 `step_name` 设置为 `VALIDATE_INPUT`。
2. 检查必填字段、字段类型、URI 前缀、任务平台一致性和金额范围。
3. 检查 `operation_id`、`execution_attempt_id` 和 `instruction_hash` 格式完整，但不在影刀侧模拟审批验证。
4. Executor 在服务端检查 `operation_id` 的业务台账；若已经 `VERIFIED`，不应再次调用影刀。
5. 同一 `operation_id` 的技术重试使用新的 `execution_attempt_id`；影刀日志和证据按尝试 ID 隔离。

### 7.2 初始化运行日志

1. 记录 `started_at`、`task_id`、`operation_id`、`execution_attempt_id` 和影刀运行实例 ID。
2. 创建本次证据目录：`evidence/{yyyyMMdd}/{operation_id}/{execution_attempt_id}/`。
3. 日志至少包含：时间、步骤名、动作、动作结果、重试次数、页面关键文本和业务值。
4. 日志不得记录微信凭据、Cookie、Token 或完整个人信息。

### 7.3 通过 URI 启动小程序并获取窗口

1. 将 `step_name` 设置为 `OPEN_APPLET`。
2. 使用影刀的打开 URI/运行程序能力调用 `applet_uri`。
3. 循环获取窗口对象，标题优先精确匹配 `platform_name`，进程名辅助匹配 `WeChatAppEx`。
4. 取得窗口后执行“还原”，再移动到固定位置并调整为录制时的基准尺寸。
5. 若已有目标窗口，复用并激活，不重复打开多个小程序实例。
6. 超时未找到窗口时返回 `WINDOW_NOT_FOUND`。

### 7.4 检查登录状态并处理登录

1. 将 `step_name` 设置为 `CHECK_LOGIN`。
2. 依次检测以下状态，按优先级命中一个分支：
   - 已登录：首页业务元素可见，如“商品管理”或经营主体信息。
   - 登录中：存在加载指示，继续等待，但不得无限等待。
   - 未登录：出现“登录”“微信授权”“手机号登录”或二维码等元素。
   - 异常页：出现“网络错误”“加载失败”“重新加载”等元素。
3. 已登录时记录 `LOGIN_OK` 并继续。
4. 识别到账户密码页时，Worker 先按平台适配配置点击员工账号模式，再通过本机 `CredentialProvider` 自动读取 Windows Credential Manager 的账号密码并填写；凭据不得进入请求、结果、phase、日志、截图、数据库或证据，也不得通过剪贴板输入。
5. 每个 attempt 最多点击一次登录提交。账号密码错误或提交后仍停留在登录页时返回 `LOGIN_CREDENTIALS_REJECTED`，不得自动重复尝试。
6. 若出现手机验证码页，写入 `LOGIN_VERIFICATION_REQUIRED` phase；PRA 队列服务调用 `ShadowBotExecutor` 创建幂等人工介入 review，并用既有 `NotificationSender` 通知操作员。
7. Worker 每 2 秒检查首页“商品管理”入口，操作员仅在小程序内完成验证码；首页出现后继续同一 attempt。等待超过 `login_verification_wait_seconds` 返回 `FAILED/LOGIN_VERIFICATION_TIMEOUT/NOT_STARTED`。
8. 登录等待阶段检测到 `stop.signal` 时返回 `FAILED/WORKER_STOP_REQUESTED/NOT_STARTED`，不触及价格输入、确认或保存动作。
9. 网络错误可点击一次“重新加载”；仍失败返回 `NETWORK_OR_LOAD_ERROR`。

### 7.5 进入商品管理

1. 将 `step_name` 设置为 `OPEN_PRODUCT_MANAGEMENT`。
2. 纯导航时可复用已打开的商品管理页；但任何列表价格读取前不得据此跳过刷新。
3. 价格读取流程必须进入 `REFRESH_PRODUCT_LIST`：等待“商品管理”入口可用并通过元素点击，即使商品列表容器已经存在。
4. 元素定位失败时依次尝试：深度模式元素、CV 文本/图像锚点、基于稳定锚点的相对位置。
5. 不允许使用无页面校验的绝对屏幕坐标直接提交业务动作。

### 7.5.1 强制刷新商品列表

1. `READ_ONLY`、`FILL_PREVIEW`、`COMMIT` 和 `RECONCILE` 在首次读取列表价格前必须点击一次“商品管理”，以重新拉取平台数据。
2. 刷新前必须确认价格弹窗未打开；若存在未清理草稿，返回 `PRODUCT_LIST_REFRESH_FAILED`，不得通过导航丢弃草稿。
3. 点击后等待列表容器连续两次可见、加载态消失，再重新定位商品行；刷新前的 `row_index` 不得复用。
4. 刷新事件写入 `product_list_refreshes`，至少记录阶段、开始/结束时间、刷新入口、结果和刷新后匹配行索引。
5. 提交前刷新失败返回 `FAILED / PRODUCT_LIST_REFRESH_FAILED / NOT_STARTED`；达到 `SUBMIT_INTENT_RECORDED` 后的刷新失败按既有副作用规则进入 `NEEDS_RECONCILIATION / UNKNOWN`。

### 7.6 等待并验证商品管理页面

商品管理页判定至少满足以下两项：

- 页面标题或导航文本包含“商品管理”。
- 可见搜索框或商品列表容器。
- 可见商品状态筛选，如“上架中/已下架”。
- 可见至少一个商品行或明确的“暂无商品”提示。

采用“每 500 毫秒轮询一次，连续两次满足条件才通过”的稳定判定。超时先刷新或返回首页重进一次，仍失败返回 `PRODUCT_PAGE_NOT_REACHED`。

### 7.7 搜索商品编码或名称

1. 将 `step_name` 设置为 `SEARCH_PRODUCT`。
2. 优先输入 `platform_sku`；缺少 SKU 时输入 `product_keyword`。
3. 输入前全选并清空搜索框，输入后回读搜索框确认文本一致。
4. 触发搜索，等待加载完成和结果列表稳定。
5. 对结果执行精确匹配，不将“包含关键词”等同于身份一致。
6. 无精确结果返回 `PRODUCT_NOT_FOUND`；多条精确结果返回 `PRODUCT_MATCH_AMBIGUOUS`。
7. 搜索到已下架商品时，仅允许查看；若任务限定上架商品，返回 `PRODUCT_NOT_ACTIVE`。

### 7.8 核对商品身份

1. 将 `step_name` 设置为 `VERIFY_PRODUCT_IDENTITY`。
2. 从列表读取 SKU、名称、等级、规格和当前状态。
3. 按 `platform_sku -> expected_product_name -> expected_grade -> expected_spec` 顺序核对。
4. 所有已传入字段都必须一致；文本比较前只允许去除首尾空格和统一全半角，不允许模糊修正业务字段。
5. 任一字段不一致时截图并返回 `PRODUCT_IDENTITY_MISMATCH`，不得进入编辑页。

### 7.9 进入编辑页并验证页面

1. 将 `step_name` 设置为 `OPEN_EDIT_PAGE`。
2. 通过当前已核对商品行内的“编辑”元素进入，不点击列表中固定序号。
3. 编辑页判定至少满足：页面标题包含“编辑商品”；商品名称/等级可读；供货价格字段或价格设置入口可见。
4. 再次核对名称、等级和规格，避免跳转后对象变化。
5. 超时返回 `EDIT_PAGE_NOT_REACHED`；二次身份不一致返回 `PRODUCT_CONTEXT_CHANGED`。

### 7.10 读取并解析旧价

1. 将 `step_name` 设置为 `READ_OLD_PRICE`。
2. 读取“供货价格”对应字段，不依赖页面上第一个金额文本。
3. 移除货币符号、千分位和空格后，按十进制金额解析。
4. 价格必须大于 0 且不超过允许上限；解析失败返回 `OLD_PRICE_PARSE_FAILED`。
5. `FILL_PREVIEW/COMMIT/RECONCILE` 模式必须提供 `expected_old_price`，页面值必须与其相等，否则返回 `OLD_PRICE_CHANGED`，提示 PRA 重新审核；`READ_ONLY` 可不提供该字段。
6. 截取包含商品身份和旧价的证据图 `01_old_price.png`。

### 7.11 按执行模式分流

1. `READ_ONLY`：不聚焦价格输入框，返回 `READ_COMPLETED`、页面旧价和只读证据。
2. `RECONCILE`：不聚焦价格输入框，只比较实际价、原旧价和目标价，按第 11.1 节返回对账结果。
3. `FILL_PREVIEW`：进入目标价校验和填写回读，但禁止点击内层确认或任何平台适配器保存/提交按钮。
4. `COMMIT`：进入目标价校验、填写、确认、必要时保存和列表复核。
5. 任何未识别模式返回 `INPUT_INVALID`；不得用默认分支推断为 `COMMIT`。

### 7.12 校验 PRA 下发的目标价

1. 将 `step_name` 设置为 `VALIDATE_TARGET_PRICE`。
2. 解析 PRA 下发的 `target_price`，只做格式规范化，不执行价格计算。
3. 金额必须能精确表示为两位小数。
4. 校验 `min_price <= target_price <= max_price`；这些范围是执行安全护栏，不是定价规则。
5. 目标价等于页面旧价时返回 `ALREADY_APPLIED`，并保留当前页面截图。
6. 目标价格式错误或越过执行护栏时返回 `TARGET_PRICE_INVALID`。

### 7.13 填写并回读目标价

1. 将 `step_name` 设置为 `FILL_TARGET_PRICE`。
2. 点击与“供货价格”锚点关联的输入框，全选、清空并输入格式化金额。
3. 不使用 `+/-` 步进按钮，因为平台步长可能不是 `0.50`。
4. 触发失焦，使前端校验和格式化生效。
5. 重新读取输入框，将结果解析为 `readback_price`。
6. `readback_price` 必须严格等于目标价。当前垂直切片采用零重试策略，首次不一致即返回 `PRICE_READBACK_MISMATCH` 或 `PREVIEW_VALUE_MISMATCH`；若后续运行数据证明需要容错，再增加“一次清空重填”。
7. 若页面出现“价格无效”“超出范围”等提示，返回 `PLATFORM_PRICE_VALIDATION_FAILED`。
8. 截图 `02_before_submit.png`，必须能看到商品身份和目标价。

### 7.14 `FILL_PREVIEW` 分支

1. 将 `step_name` 设置为 `PREVIEW_COMPLETE`。
2. 不点击价格弹窗确认、页面保存、提交或确定等会产生平台状态变化的按钮。
3. 返回 `PREVIEW_COMPLETED`、`submitted=false`、旧价、目标价和证据清单。
4. 预览不是审批节点，不创建新的复核任务；后续正式执行必须由 Executor 重新验证 PRA 审批，并以新的 `execution_attempt_id` 启动完整 `COMMIT`。
5. 当前 UI 草稿不得作为后续提交依据；`COMMIT` 必须重新读取旧价并完成全部校验。

### 7.15 `COMMIT` 分支

执行前再次检查以下条件。此处是 UI 一致性检查，不是审批真实性检查：

- 当前指令仍为 `execution_mode=COMMIT`，且 `instruction_hash` 与本次日志中的初始值一致。
- Executor 已为当前 `operation_id` 获取执行锁，且未向影刀发送取消信号。
- 当前页面商品身份仍与输入一致。
- `old_price` 与提交前基准一致。
- 输入框回读值等于目标价。

条件全部通过后：

1. 当前适配器设置 `side_effect_boundary=INNER_CONFIRM`。故障注入已确认，价格弹窗“确认”后平台可能已经产生可复核结果，独立最终保存按钮不得作为必经步骤假设。
2. 点击内层确认前，将 `side_effect_state=SUBMIT_INTENT_RECORDED` 和 `submit_intent_at` 发送到 Executor 的持久化运行检查点，并等待成功确认；写入失败、超时或未确认时禁止继续。
3. 点击内层“确认”后，立即设置 `side_effect_state=SUBMIT_CLICKED` 和 `submit_clicked_at`，并写入运行检查点。
4. 检测页面状态：若已返回列表或列表价格可复核，直接进入列表复核；若仍停留在编辑页，则等待价格显示稳定并再次读取。
5. 若平台适配配置明确存在后续保存步骤，且已通过该平台真实验证，则点击配置的可选保存/提交按钮，并记录 `final_save_clicked_at`、`final_save_label` 和 `final_save_node`；没有明确配置时不得用通用按钮枚举强行寻找保存按钮。
6. 不把任何按钮“点击成功”视为业务成功，必须继续列表复核。
7. 从提交意图写入开始到列表复核完成之间，任何超时、异常、进程退出或通信中断都返回或由 Executor 推导为 `NEEDS_RECONCILIATION`、`side_effect_state=UNKNOWN`、`retryable=false`。
8. `final_save_button` 是平台适配器的可选能力，不是通用流程的固定步骤；只有后续平台验证证明真实副作用边界在该按钮后，才允许把对应平台的 `side_effect_boundary` 调整为 `FINAL_SAVE`。

### 7.16 返回列表并复核实际价格

1. 将 `step_name` 设置为 `VERIFY_LIST_PRICE`。
2. 点击一次“商品管理”执行 `REFRESH_PRODUCT_LIST`，重新定位目标商品并等待列表稳定；同一复核阶段的后续轮询不得重复导航。
3. 再次核对商品身份，并读取刷新后商品行的实际供货价格。
4. `verified_price == target_price` 时设置 `side_effect_state=VERIFIED`，截图 `03_after_submit.png`，返回 `SUCCESS`。
5. 若仍为旧价，刷新一次后复核；仍未更新则设置 `side_effect_state=NOT_APPLIED`，返回 `status=NOT_APPLIED`、`error_code=SUBMIT_NOT_APPLIED`。
6. 若显示其他价格，设置 `side_effect_state=UNKNOWN`，返回 `POST_SUBMIT_PRICE_MISMATCH`，标记为高优先级人工检查，不自动重试提交。
7. 无法进入列表或无法可靠读取价格时，返回 `NEEDS_RECONCILIATION` 和 `SUBMIT_RESULT_UNKNOWN`，禁止自动重试。

### 7.17 统一结束和回传

1. 设置 `ended_at`，补齐 `result` 全部字段。
2. 将业务结果 JSON 输出给影刀调用方或应用运行结果。
3. PRA 轮询/回调适配器解析结果并写入 `execution_logs`。
4. 失败时同时写入 `error_code`、简明错误信息、当前步骤、证据对象和重试建议。
5. 影刀自身异常捕获不到业务结果时，Executor 必须读取最后一个持久化检查点：最后状态早于 `SUBMIT_INTENT_RECORDED` 才可使用 `SHADOWBOT_RUNTIME_FAILED`；达到或晚于该检查点则使用 `SUBMIT_RESULT_UNKNOWN` 并进入对账。

## 8. 影刀子流程拆分

建议不要把全部动作堆在主流程中，按以下结构实现：

| 子流程 | 输入 | 输出 | 职责 |
| --- | --- | --- | --- |
| `SF_ValidateInput` | Executor 指令 | 标准化参数 | 类型、任务一致性和指令完整性检查，不验证审批 |
| `SF_OpenApplet` | URI、平台名 | `software_window` | URI 启动、窗口获取、还原和定尺 |
| `SF_EnsureLogin` | 窗口、超时 | 登录状态 | 登录判断、人工等待、网络恢复 |
| `SF_GotoProductPage` | 窗口 | 页面状态 | 进入并验证商品管理页 |
| `SF_FindProduct` | SKU/关键词 | 商品行、匹配数 | 搜索、精确匹配、状态识别 |
| `SF_VerifyIdentity` | 商品数据、预期值 | 核对结果 | 名称、等级、规格、SKU 核对 |
| `SF_ReadPrice` | 页面、价格锚点 | 十进制价格 | 读取、清洗、解析和范围检查 |
| `SF_SetPrice` | 目标价 | 回读价格 | 填写、失焦、平台校验、回读 |
| `SF_SubmitAndVerify` | 授权后的执行指令、目标价 | 实际价格 | 持久化副作用检查点、保存、列表复核 |
| `SF_ReconcilePrice` | 商品身份、目标价、旧价 | 实际价格 | 只读查询实际状态，绝不点击保存 |
| `SF_CaptureEvidence` | 步骤、窗口 | 证据对象 | 截图、哈希、传输、目标校验和归档 |
| `SF_BuildResult` | 上下文、异常 | JSON | 统一输出结构 |

每个子流程应只有一个明确返回点，业务错误通过标准错误对象返回，由主流程统一决定是否重试和结束。

## 9. 元素库设计

元素命名建议采用 `应用_页面_语义_类型`：

```text
WX_首页_商品管理_入口
WX_登录页_登录_按钮
WX_商品管理_搜索_输入框
WX_商品管理_商品行_容器
WX_商品管理_SKU_文本
WX_商品管理_编辑_按钮
WX_编辑商品_商品名称_文本
WX_编辑商品_供货价格_输入框
WX_价格弹窗_确认_按钮
WX_编辑商品_最终保存_按钮（平台适配器可选）
WX_通用_加载中_标志
WX_通用_成功提示_文本
WX_通用_错误提示_文本
```

元素选择器优先使用稳定属性和父子关系，避免依赖动态序号、易变化的坐标或完整窗口层级。商品行内元素必须以已匹配商品行作为父级范围，避免点击其他商品的编辑按钮。

兜底顺序：标准元素 -> 深度模式元素 -> CV 文本/图像 -> 稳定锚点相对坐标。每次使用兜底方式都写入日志，便于统计元素失效率。

## 10. 等待、重试和恢复策略

| 场景 | 最大重试 | 恢复动作 | 失败后是否可自动重跑 |
| --- | ---: | --- | --- |
| URI 启动后窗口未出现 | 2 | 再次激活已有窗口或重开 URI | 是 |
| 页面加载超时 | 1 | 刷新或返回首页重进 | 是 |
| 元素暂时不可见 | 2 | 滚动到可见区域、重新捕获页面状态 | 是 |
| 网络加载失败 | 1 | 点击重新加载 | 是 |
| 登录失效 | 0 | 等待人工登录 | 人工处理后可重跑 |
| 商品无结果/多结果 | 0 | 无 | 否，需修正参数 |
| 商品身份不一致 | 0 | 无 | 否，需人工核对 |
| 旧价变化 | 0 | 无 | 否，需重新审批 |
| 输入回读不一致 | 0 | 当前垂直切片直接安全失败；后续可按数据增加一次重填 | 否 |
| 已记录提交意图后结果不明 | 0 | 进入独立只读对账流程 | 否，禁止自动重跑改价流程 |

重试只允许针对无副作用动作。每次技术重试沿用 `operation_id` 并创建新的 `execution_attempt_id`。达到 `SUBMIT_INTENT_RECORDED` 后，后续只能查询平台实际状态，不得直接重复点击保存。

## 11. 错误码

| 错误码 | 阶段 | 可重试 | 含义/建议 |
| --- | --- | --- | --- |
| `INPUT_INVALID` | 参数 | 否 | 修正 PRA 参数 |
| `DUPLICATE_EXECUTION_ATTEMPT_ID` | 参数 | 否 | `execution_attempt_id` 已存在，拒绝覆盖旧运行结果 |
| `RESULT_CONTRACT_INVALID` | Executor | 否 | 状态与结果字段矛盾，禁止据此更新业务任务 |
| `EXECUTION_NOT_AUTHORIZED` | Executor | 否 | 审批不存在、不匹配、已过期、载荷变化或操作锁失败；不得调用影刀 |
| `DUPLICATE_OPERATION_BLOCKED` | Executor | 否 | 业务操作已完成或正在对账 |
| `APPLET_URI_OPEN_FAILED` | 启动 | 是 | 检查 URI、微信和协议关联 |
| `WINDOW_NOT_FOUND / WINDOW_NOT_AVAILABLE` | 启动 | 是 | 检查 `WeChatAppEx` 进程和窗口标题；瞬时无效句柄先重试三次 |
| `LOGIN_REQUIRED` | 登录 | 否 | 人工恢复登录后由 PRA 创建新 attempt |
| `NETWORK_OR_LOAD_ERROR` | 登录/页面 | 是 | 检查网络后重跑 |
| `PRODUCT_PAGE_NOT_REACHED` | 导航 | 是 | 更新页面元素或检查权限 |
| `PRODUCT_NOT_FOUND` | 搜索 | 否 | 检查 SKU、商品状态或是否下架 |
| `PRODUCT_MATCH_AMBIGUOUS` | 搜索 | 否 | 补充 SKU/规格，禁止选第一条 |
| `PRODUCT_NOT_ACTIVE` | 搜索 | 否 | 商品不在允许状态 |
| `PRODUCT_IDENTITY_MISMATCH` | 核对 | 否 | 参数与列表商品不一致 |
| `EDIT_PAGE_NOT_REACHED` | 编辑 | 是 | 检查编辑元素和页面加载 |
| `PRODUCT_CONTEXT_CHANGED` | 编辑 | 否 | 跳转后商品身份变化 |
| `OLD_PRICE_PARSE_FAILED` | 价格 | 否 | 更新价格元素或解析规则 |
| `OLD_PRICE_CHANGED` | 价格 | 否 | 平台价已变化，重新审核任务 |
| `TARGET_PRICE_INVALID` | 价格 | 否 | 修正目标价或业务范围 |
| `PRICE_READBACK_MISMATCH` | 填写 | 否 | 输入控件未接受目标值 |
| `PLATFORM_PRICE_VALIDATION_FAILED` | 填写 | 否 | 平台规则拒绝该价格 |
| `SUBMIT_NOT_APPLIED` | 复核 | 否 | 保存后价格未更新，人工核查 |
| `POST_SUBMIT_PRICE_MISMATCH` | 复核 | 否 | 实际价异常，立即人工检查 |
| `SUBMIT_RESULT_UNKNOWN` | 提交后 | 否 | 保存可能已生效；进入只读对账，禁止自动重试 |
| `SCREENSHOT_FAILED` | 证据 | 视阶段 | 副作用边界前失败则阻断；边界后保留业务结果并告警 |
| `EVIDENCE_UPLOAD_FAILED` | 证据 | 视阶段 | 提交边界前失败则阻断；提交后不得重做业务操作 |
| `EVIDENCE_HASH_MISMATCH` | 证据 | 否 | 源文件和存储文件不一致，证据不可采信 |
| `SHADOWBOT_RUNTIME_FAILED` | 运行时 | 视检查点 | 仅在最后检查点早于提交意图时可重试；否则转换为 `SUBMIT_RESULT_UNKNOWN` |
| `UNKNOWN_ERROR` | 未分类 | 否 | 保留当前步骤、堆栈和截图后人工分析 |

不同错误应通过 `error_code`、`side_effect_state`、`retryable` 和 `retry_suggestion` 向 PRA 发送不同信息；PRA 不应只依赖自由文本判断后续动作。`side_effect_state` 的约束优先级高于错误码默认重试属性。

### 11.1 结果未知的只读对账流程

`NEEDS_RECONCILIATION` 必须启动独立的 `SF_ReconcilePrice`，该流程不包含价格填写和保存元素：

1. 使用相同 `operation_id` 创建新的对账尝试 ID，但不创建改价执行尝试。
2. 打开小程序并按 SKU 精确定位商品。
3. 只读获取平台实际价格并截图。
4. 实际价等于目标价：将原业务操作标记为 `VERIFIED`，`business_operation_completed=true`，允许 PRA 将业务任务完成；是否在展示层归并为成功摘要，不改变结构化状态。
5. 实际价等于旧价：标记 `NOT_APPLIED`；是否再次执行必须由 Executor 重新检查审批有效性并创建新的 `execution_attempt_id`，不能由对账流程自动触发。
6. 实际价为其他值或无法读取：保持 `NEEDS_RECONCILIATION`，交由人工处理。

## 12. 证据和日志要求

必需证据：

1. `00_login_or_home.png`：确认已登录及目标平台。
2. `01_old_price.png`：商品身份和旧价。
3. `02_before_submit.png`：商品身份和已填写目标价。
4. `03_after_submit.png`：提交后的列表商品和实际价格，仅提交分支需要。
5. `99_error_{error_code}.png`：异常现场。

按模式要求：`READ_ONLY` 至少归档登录/首页和旧价证据；`FILL_PREVIEW` 另需填写回读证据；`COMMIT` 需要旧价、提交前和提交后证据；`RECONCILE` 需要对账时实际价格证据。

异常分支应尽量自动补充 `ERROR` 证据截图，但错误截图是辅助证据，不得覆盖原始业务或技术错误。若错误截图本身失败，应保留原始 `error_code` 和 `error_message`，并把截图失败原因写入独立字段，例如 `error_evidence_message`。

每项证据使用结构化对象，不得将机器人本地路径作为 PRA 的最终证据引用：

```json
{
  "evidence_id": "EVD-001",
  "type": "BEFORE_SUBMIT",
  "local_path": "D:/rpa-evidence/.../02_before_submit.png",
  "storage_uri": "file://pra-share/evidence/.../02_before_submit.png",
  "storage_path": "\\\\pra-share\\evidence\\...\\02_before_submit.png",
  "sha256": "0123456789abcdef...",
  "storage_sha256": "0123456789abcdef...",
  "hash_verified": true,
  "size_bytes": 245817,
  "captured_at": "2026-06-20T10:00:13+08:00",
  "upload_status": "SUCCESS",
  "upload_error": ""
}
```

字段规则：

- `local_path` 仅用于机器人机排错，可以为空，不保证 PRA 可访问。
- `storage_uri` 必须指向 PRA 可访问的共享目录或统一对象存储；上传失败时为空。
- `storage_path` 是机器人机复制后的共享目录路径。初版可直接是 Windows UNC 路径，例如 `\\pra-share\evidence\ATTEMPT-001\01_old_price.png`。
- `sha256` 对截图原始字节计算，复制或上传后必须对目标文件重新计算并一致。
- `storage_sha256` 对共享目录中的目标文件重新计算。
- `hash_verified=true` 表示 `sha256 == storage_sha256`，PRA 可用该字段快速判断证据是否可校验。
- `upload_status` 取 `SKIPPED/SUCCESS/FAILED`；只有目标对象存在且哈希复核通过才是 `SUCCESS`。`SKIPPED` 只允许本地调试使用，表示未配置共享目录。
- `evidence_status` 由清单汇总，取 `COMPLETE/LOCAL_ONLY/PARTIAL/FAILED`。

业务 `status` 与 `evidence_status` 相互独立。平台价格已复核为目标价时，业务状态保持 `SUCCESS`；提交后证据补传失败只降低 `evidence_status` 并触发证据告警，不能生成新的改价重试。

初版可将证据复制到 PRA 与影刀都能访问的 UNC 共享目录，并将其规范化为 `file://主机/共享名/...` 或直接使用 UNC 路径；正式阶段改为对象存储或证据服务 URI。数据库保存 `storage_uri`、`storage_sha256`、哈希校验结果、文件大小和采集时间，不只保存本地路径。

执行顺序：截图 -> 计算源文件 SHA-256 -> 复制/上传到 `evidence_share_dir` -> 计算共享目标文件 SHA-256 -> 比较 -> 生成证据对象。源文件后续发生变化不影响已归档对象；PRA 展示或下载证据时可再次验算哈希。

建议目录结构：

```text
\\pra-share\evidence\
  ATTEMPT-20260623-001\
    ATTEMPT-20260623-001_supply_price.png
    ATTEMPT-20260623-001_fill_preview.png
    ATTEMPT-20260623-001_reconcile.png
    ATTEMPT-20260623-001_error.png
```

机器人机需要对该目录有写权限，PRA 服务需要有读权限。`READ_ONLY`、`FILL_PREVIEW` 和 `RECONCILE` 必须先使用同一套共享证据机制，不能等到 `COMMIT` 才首次验证。未配置 `evidence_share_dir` 时，非提交模式可以输出 `upload_status=SKIPPED` 和 `evidence_status=LOCAL_ONLY` 作为本地调试结果；进入真实提交前，Executor 必须要求 `upload_status=SUCCESS`、`hash_verified=true`。

可使用 `scripts/setup_shadowbot_evidence_share.ps1` 在机器人机上创建首版 SMB 共享。示例：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_shadowbot_evidence_share.ps1 -ShareName pra-evidence -SharePath D:\PRA_Evidence
```

脚本需要在具备创建 SMB 共享权限的 PowerShell 中运行。执行后输出的 `UNC` 值填入影刀参数 `evidence_share_dir`；若 PRA 展示层希望使用不同 URI 前缀，则同步填写 `evidence_storage_uri_prefix`。

截图失败发生在副作用边界前时，流程必须阻断并返回 `SCREENSHOT_FAILED`。`COMMIT` 的 `BEFORE_SUBMIT` 证据必须在提交意图检查点之前完成上传和哈希复核，否则不得点击内层确认或任何平台适配器保存/提交按钮。

提交边界之后证据上传失败时，不得将业务结果改写为普通可重试失败，也不得重新执行改价。应保留平台验证结果，设置 `evidence_status=PARTIAL/FAILED`、返回证据告警并安排补传；若连平台结果也未知，则仍按 `NEEDS_RECONCILIATION` 处理。

## 13. PRA 接入建议

1. 新增独立 `ShadowBotExecutor`，不要修改 Mock 执行器的语义。
2. 执行器必须验证审批实体、任务、SKU、目标价格、有效期和批准载荷摘要，再生成不可变执行指令。
3. 执行器为业务变更分配稳定的 `operation_id`，每次调用影刀分配新的 `execution_attempt_id`。
4. 调用影刀后保存影刀任务/运行 ID，并通过回调或轮询取得终态结果和副作用检查点。
5. 结果写入现有 `execution_logs`；完整 JSON 放入 `raw_output`。
6. `READ_COMPLETED` 和 `PREVIEW_COMPLETED` 只表示对应技术模式执行成功，不得改变 PRA 审批状态，也不得把业务任务标记为完成。
7. 只有 `status in [SUCCESS, ALREADY_APPLIED, VERIFIED]` 且 `business_operation_completed=true` 时，才可把 PRA 业务任务更新为完成；`ExecutionLog.success_flag` 不得参与该判断。
8. `NEEDS_RECONCILIATION` 和 `POST_SUBMIT_PRICE_MISMATCH` 应产生高优先级人工告警并冻结该 `operation_id` 的后续改价尝试。
9. 业务操作台账对 `operation_id` 建立唯一约束；执行日志对 `execution_attempt_id` 建立唯一约束。
10. Executor 收到影刀超时/失联时，必须依据最后持久化检查点分类，不得统一映射为可重试运行时错误。
11. 建议新增独立证据表或证据服务，以 `evidence_id` 关联 `task_id`、`operation_id` 和 `execution_attempt_id`；不要依赖 `execution_logs.raw_output` 中的本地路径长期保存证据。

### 13.1 ShadowBotExecutor 最小骨架现状

当前服务端已先行建立 ShadowBot 执行边界，代码位置：

- `app/services/shadowbot_executor.py`
- `app/repositories/sqlite_runtime_repository.py`
- `app/models.py`
- `tests/test_shadowbot_executor.py`

已新增三类运行态对象和 SQLite 表：

| 对象 | 表 | 关键约束 |
| --- | --- | --- |
| 业务操作台账 | `shadowbot_operations` | `operation_id` 主键唯一 |
| 执行尝试 | `shadowbot_execution_attempts` | `execution_attempt_id` 主键唯一 |
| 副作用检查点 | `shadowbot_side_effect_checkpoints` | `(operation_id, version)` 主键，按业务操作递增 |

已实现的服务端能力：

1. `ShadowBotExecutor.start_execution(...)` 在启动影刀前用 `approval_id` 回查 `review_tasks`，验证审批记录真实存在、状态为 `APPROVED`，并校验有效期、`operation_id` 归属和批准载荷 hash。
2. 批准载荷 hash 使用规范化 JSON 和 SHA-256，覆盖 `operation_id/task_id/platform/product_identity/expected_old_price/target_price`；该 hash 必须同时匹配请求载荷和复核记录中的 `approved_payload_hash`。
3. `operation_id` 写入业务操作台账并受唯一约束保护；同一 `operation_id` 只能复用完全相同的批准载荷。
4. `execution_attempt_id` 写入执行尝试表并受唯一约束保护；同一尝试 ID 不能重复启动。
5. 执行锁写入 `shadowbot_operations.lock_owner`；其他执行器持锁时拒绝启动。
6. 影刀启动通过 `ShadowBotTaskRunner` 接口抽象；当前已提供 fake runner 测试替身、`FileDropShadowBotTaskRunner` 文件投递实现，以及 `YingdaoOpenApiJobRunner` 影刀开放 API `JOB运行/启动应用` 实现。
7. `record_result(...)` 校验影刀结果契约，拒绝 `READ_COMPLETED/PREVIEW_COMPLETED` 被错误解释为业务完成，也拒绝 `FAILED` 缺少 `error_code`、`NEEDS_RECONCILIATION` 可重试等矛盾组合。
8. `record_side_effect_checkpoint(...)` 写入副作用检查点并同步更新执行尝试的 `side_effect_state`。
9. `classify_timeout(...)` 只接受具有唯一活动 COMMIT attempt 且 lease 已实际过期的 operation，并委托 lease/attempt-aware 原子终结路径：attempt 进入 `START_UNKNOWN` 或 `SIDE_EFFECT_UNKNOWN`，operation 同步进入 `NEEDS_RECONCILIATION`，`retryable=false`，下一步只能 `RECONCILE`。lease 尚有效、缺少 lease 或活动 attempt 数量异常时拒绝分类；不得产生“operation=FAILED 但 attempt 仍活动”的状态旁路。
10. 若已有业务操作达到 `SUBMIT_INTENT_RECORDED` 后再次收到 `COMMIT` 启动请求，Executor 不会创建新的改价执行尝试，也不会调用影刀 runner，只返回 `NEEDS_RECONCILIATION` 和 `next_execution_mode=RECONCILE`。
11. `start_execution(...)` 成功启动后会把源 task 推进到 `running`。
12. `record_result(...)` 会写入 `execution_logs.raw_output`，保留 `operation_id`、`execution_attempt_id`、`shadowbot_run_id`、`execution_mode`、状态、价格和证据字段。
13. 成功结果会把 operation 归并为 `VERIFIED`，并把源 task 更新为 `success`；提交前 `FAILED + NOT_STARTED` 会写日志并把 task 更新为 `failed`；提交后 `NEEDS_RECONCILIATION + UNKNOWN` 会冻结 operation，禁止再次 `COMMIT`。
14. Web 执行日志已提供最小 ShadowBot 查看入口，展示 operation、attempt、run、模式、状态、副作用状态、旧价、目标价、实际价、证据状态和共享截图，并对结果未知、提交后价格不一致、旧价变化、证据上传失败显示告警。
15. Web 执行日志已展示队列 heartbeat、working phase、Worker、三类 hash、隔离数量和自动对账 attempt；页面不提供强制重新提交按钮。
16. `ShadowBotFileQueueRunner` 会将规范请求和 SHA-256 checksum 原子发布到 `SHADOWBOT_QUEUE_DIR/inbox`；`filedrop` 与 `SHADOWBOT_REQUEST_DIR` 仅作为兼容名称保留。
17. `YingdaoOpenApiJobRunner` 会按影刀开放 API 文档先调用 `/oapi/token/v2/token/create` 获取 accessToken，再调用 `/oapi/dispatch/v2/job/start` 启动指定应用 `robotUuid`，返回 `shadowbot_run_id=yingdao-job:{jobUuid}`。请求参数默认同时传入完整 `request_json` 和扁平字段，便于影刀主流程按实际已配置的字符串参数读取。
18. `scripts/prepare_shadowbot_e2e_chain.py` 是首条链路准备入口：创建 `update_price` task、写入已批准 review、生成 `approved_payload_hash`，并在显式传入 `--start` 时调用 `ShadowBotExecutor` 启动 `COMMIT`。
19. `scripts/run_shadowbot_executor.py` 提供通用桥接入口：`start` 从已批准 review 启动执行尝试，可通过 `--runner-type filequeue|filedrop|yingdao_openapi` 选择 runner；`filedrop` 是兼容别名。
20. `ShadowBotResultImporter` 仅处理 `results/*.result.json`；`ShadowBotQueueWatchdog` 仅处理 heartbeat、phase、超时和遗留 working。两者可运行在同一进程，但代码职责分离。
21. 自动 `RECONCILE` 只能由 `ShadowBotExecutor.ensure_reconcile_attempt(...)` 创建，并使用来源 attempt 派生的确定性 ID 保证幂等；任何 COMMIT 失败都不自动重试。
22. `check-yingdao-app-params` 可调用影刀 `/oapi/robot/v2/queryRobotParam` 做只读预检，确认主流程存在 `request_json` 入参和 `shadowbot_result_json` 出参；该命令不会启动影刀应用。
23. `poll-yingdao-result` 可调用影刀 `/oapi/dispatch/v2/job/query` 查询 `jobUuid`，从输出参数 `shadowbot_result_json` 提取结果并回写 PRA；若导入 COMMIT 结果为 `NEEDS_RECONCILIATION`，由 Executor 自动创建唯一的只读对账尝试。
24. `scripts/check_shadowbot_readiness.py` 提供真实启动前的离线就绪检查，只报告 runner 必需环境变量和 runtime DB 状态，不启动影刀、不访问影刀 OpenAPI，也不会输出密钥明文。
25. `RetryPolicyService` 的总重试窗口从 operation 创建时间与最早 COMMIT attempt 时间二者较早者起算，并受原审批到期时间进一步收紧。签发事务把 `retry_window_deadline` 和窗口秒数写入 source attempt 审计数据，`RetryAuthorization.expires_at` 不得晚于该截止时间；消费事务会从数据库权威时间重新计算并比对截止时间，超窗时授权保持未消费、不创建 attempt、不改变 operation。

首条链路准备示例：

```powershell
python scripts/prepare_shadowbot_e2e_chain.py `
  --platform "蚂蚁花团供应商" `
  --sku "SKU-AISHA-C" `
  --product-name "艾莎" `
  --grade "C级" `
  --expected-old-price "19.00" `
  --target-price "19.50"
```

确认 `scripts/local_env.ps1` 已配置 runner，并核对任务、审批载荷、旧价和目标价后，再显式增加 `--start`。默认不启动影刀，避免准备数据时误触发真实平台。

若使用 `yingdao_openapi` runner，真实启动前先执行：

```powershell
python scripts/check_shadowbot_readiness.py
python scripts/run_shadowbot_executor.py check-yingdao-app-params
```

返回 `ok=true` 后再启动真实 job。

实机前可先运行本地三分支演练，不触发真实影刀：

```powershell
python scripts/run_shadowbot_e2e_local_demo.py `
  --runtime-db data/runtime/shadowbot_e2e_demo.sqlite3 `
  --request-dir data/runtime/shadowbot_demo_requests
```

该脚本会在同一个 runtime DB 内演练：

- 成功分支：`COMMIT -> VERIFIED`，task 更新为成功，operation 标记 `VERIFIED`。
- 提交前失败：`FAILED + NOT_STARTED`，写 execution log，task 更新为失败。
- 提交后未知：`NEEDS_RECONCILIATION + UNKNOWN`，冻结 operation，再由 Executor 创建 `RECONCILE` attempt 并归并为 `NOT_APPLIED`。

演练结果可通过 Web `/execution-logs` 查看字段、告警、共享证据链接和人工操作入口。该脚本仍使用 `ShadowBotExecutor` 与 `FileDropShadowBotTaskRunner`，不是绕过生产边界的独立模拟器。

影刀 OpenAPI 可选路径仍待完成，但不再是当前文件队列方案的上线前置条件：

1. 在真实环境配置影刀 OpenAPI 密钥、应用 `robotUuid` 和机器人账号或机器人分组，并确认机器人处于调度模式。
2. 若未来启用 OpenAPI，首条联调仍只执行单个已审批测试商品，并在启动前人工核对审批载荷、旧价和目标价。
3. 若未来启用 OpenAPI，单独完成其 job 启动、终态查询和结果导入验收；当前文件队列实机闭环已经完成。
4. 影刀主流程需要新增字符串出参 `shadowbot_result_json`，内容为规范中的 ShadowBot 结果 JSON；若实际参数名不同，调用 `poll-yingdao-result --result-param-name ...` 时必须显式传入。
5. 如采用影刀回调或轮询，应在 `jobUuid` 终态后只负责导入影刀输出 JSON，不得由影刀自动发起 `RECONCILE`。

验证命令：

```powershell
python -m unittest tests.test_shadowbot_executor
python -m pytest tests/test_runtime_persistence.py
python -m unittest tests.test_web.WebTests.test_execution_logs_page_displays_shadowbot_summary_evidence_and_warnings
python -m unittest tests.test_web.WebTests.test_execution_logs_post_starts_shadowbot_reconcile_attempt tests.test_web.WebTests.test_execution_logs_post_confirms_shadowbot_manual_handled_without_resubmit
```

注意：当前 Codex bundled Python 未安装 `pytest`，本机 Python 已安装 `pytest`。本轮通过工作区临时目录运行并验证 `tests.test_shadowbot_executor` 共 25 个测试，以及 Web ShadowBot 执行日志展示、启动只读对账、确认人工处理完成 3 个关键测试。

桥接脚本联调示例：

```powershell
python scripts\run_shadowbot_executor.py start `
  --runtime-db data\runtime\pra_runtime.sqlite3 `
  --approval-id APPROVAL_ID `
  --operation-id OP-C-AISHA-001 `
  --execution-attempt-id ATTEMPT-C-AISHA-001 `
  --execution-mode COMMIT `
  --platform 蚂蚁花团供应商 `
  --sku SKU-AISHA-C `
  --product-name 艾莎 `
  --grade C级 `
  --expected-old-price 19.00 `
  --target-price 19.50 `
  --request-dir data\runtime\shadowbot_requests

python scripts\run_shadowbot_executor.py import-result `
  --runtime-db data\runtime\pra_runtime.sqlite3 `
  --result-json C:\Users\etere\AppData\Local\ShadowBot\results\vertical_slice\ATTEMPT-C-AISHA-001.json
```

`start` 命令不会伪造审批：它会用传入参数重新计算批准载荷 hash，并要求该 hash 与 `review_tasks.review_payload/resolution_payload.approved_payload_hash` 完全一致。hash 不一致时不会启动 runner。

### 13.2 ShadowBot COMMIT 开发测试版现状

当前影刀本地流程 `vertical_slice_read_price.py` 已恢复四种模式：

- `READ_ONLY`：只读列表价格并归档证据。
- `FILL_PREVIEW`：打开价格弹窗，填写 `target_price`，截图后取消，不提交；填写时先尝试元素原生输入方法，失败后使用影刀元素 `clipboard_input` 兜底。
- `COMMIT`：打开价格弹窗，校验商品、等级和 `expected_old_price`，填写 `target_price`，截图，记录 `SUBMIT_INTENT_RECORDED`，点击弹窗确认并立即记录 `SUBMIT_CLICKED`；随后按平台适配配置决定是否存在后续保存按钮，最终短轮询等待列表价格刷新并截图。填写目标价同样必须保留 `clipboard_input` 兜底。
- `RECONCILE`：只读列表价格，与 `expected_old_price/target_price` 对账，不触碰输入框或确认按钮。

`COMMIT` 开发测试版的结果分类：

| 保存后列表价格 | 返回 |
| --- | --- |
| 等于 `target_price` | `status=SUCCESS`，`side_effect_state=VERIFIED` |
| 等于 `expected_old_price` | `status=NOT_APPLIED`，`error_code=SUBMIT_NOT_APPLIED`，`retryable=false` |
| 无法读取列表价格 | `status=NEEDS_RECONCILIATION`，`error_code=SUBMIT_RESULT_UNKNOWN`，`retryable=false` |
| 其他价格 | `status=NEEDS_RECONCILIATION`，`error_code=POST_SUBMIT_PRICE_MISMATCH`，`retryable=false` |

注意：当前真实验证表明，本小程序的副作用边界应按 `INNER_CONFIRM` 管理；页面底部或后续“确定/保存/提交”不再写成通用必经步骤。`final_save_button` 仅作为平台适配器可选能力：只有某个平台已明确捕获并验证该按钮是后续必要保存步骤时，才允许配置并点击。命中后结果 JSON 应记录 `final_save_label`、`final_save_node` 和 `final_save_clicked_at`，便于复核实际命中的按钮来源。

达到 `SUBMIT_INTENT_RECORDED` 或 `SUBMIT_CLICKED` 后，任何未完成列表复核的异常都不能返回普通 `FAILED`。开发测试版会统一返回 `status=NEEDS_RECONCILIATION`、`error_code=SUBMIT_RESULT_UNKNOWN`、`retryable=false`，并额外保留 `original_error_code/original_error_message`，供 PRA 和人工排查原始技术失败原因。

开发测试参数样例见 `docs/examples/shadowbot_commit_request_test.json`。运行前必须把 `execution_attempt_id` 替换为本次唯一值，并按测试商品的当前真实状态替换 `product_keyword`、`expected_product_name`、`expected_grade`、`expected_old_price` 和 `target_price`；不得直接使用样例价格提交真实商品。

### 13.3 真实 COMMIT 与故障注入验证现状

2026-06-24 已在测试商品 `C级 艾莎` 上完成一次真实 `COMMIT`，结果为 `SUCCESS`、`side_effect_state=VERIFIED`，列表复核价格为 `7.30`。

2026-06-25 完成六条核心故障注入，详见 [shadowbot_fault_injection_20260625.md](../../reports/shadowbot_fault_injection_20260625.md)。结论如下：

| 场景 | 预期边界 | 已验证结果 |
| --- | --- | --- |
| 旧价已变化 | 提交前失败，不打开或不继续提交 | `OLD_PRICE_CHANGED`，`side_effect_state=NOT_STARTED` |
| 商品不存在 | 定位阶段失败 | `PRODUCT_NOT_FOUND`，`side_effect_state=NOT_STARTED` |
| 缺少目标价 | 参数校验阶段失败 | `INPUT_INVALID`，`current_step=VALIDATE_INPUT` |
| 输入回读不一致 | 填写后失败，不确认提交 | `TARGET_PRICE_VERIFY_FAILED`，并取消弹窗 |
| 提交点击后异常 | 结果未知，不可自动重试 | `NEEDS_RECONCILIATION`，`SUBMIT_RESULT_UNKNOWN`，`retryable=false` |
| 结果未知后对账 | 只读核对实际价 | `RECONCILE` 返回 `VERIFIED`，`actual_price=7.30` |

本轮验证后，当前适配器应把副作用边界按 `INNER_CONFIRM` 管理。目标小程序在价格弹窗“确认”后已经可能产生可复核的平台结果；独立最终保存按钮不能作为唯一副作用边界假设。

为支持故障注入，开发测试版加入了仅测试请求显式启用的 `fault_injection` 参数。生产 Executor 不应向影刀下发该参数；如果收到非空 `fault_injection`，生产环境应拒绝执行。

## 14. 开发顺序

1. 建立输入/输出参数和统一异常处理。
2. 完成 URI 启动、窗口获取、还原、定尺和登录状态判断。
3. 捕获并验证商品管理、搜索和商品行元素。
4. 完成 `READ_ONLY`：搜索、身份核对、读取旧价和证据归档。
5. 完成 `FILL_PREVIEW`：校验 PRA 目标价、填写、回读，且不点击确认按钮。
6. 完成证据共享目录/统一存储、SHA-256 计算和目标端复核。
7. 已在测试商品上验证 `COMMIT` 开发测试版和 `RECONCILE` 只读对账。
8. 已完成核心故障注入：提交前失败、输入回读失败、提交后结果未知和只读对账。
9. 已完成 PRA 文件队列触发、Result Importer、证据清单和 `execution_logs` 回写；OpenAPI 终态查询/回调保留为可选路径。
10. 已完成登录、网络和证据上传失败实机注入；白屏保留分类单元测试，元素版本漂移仍需专用可重复夹具。
11. 已完成冒烟测试、heartbeat/phase 监控和 8 小时观察；长期告警、磁盘清理、证据保留和元素版本维护仍需运营样本。

## 15. 测试用例

### 15.1 内层确认副作用实验

在允许变更的测试商品上，至少重复 5 次以下实验，并保存每一步证据和平台实际价格：

1. 读取列表基准价格并记录证据。
2. 进入编辑页，填写一个可识别的测试目标价。
3. 点击价格弹窗内层“确认”，不点击页面底部最终确定。
4. 关闭或取消编辑页，重新打开商品管理并读取实际价格。
5. 退出并重新进入小程序，再次读取实际价格。
6. 若任一次实际价格发生变化，内层确认即被认定为有平台副作用，副作用边界永久保持 `INNER_CONFIRM`。
7. 只有所有实验均证明实际价格未变化，且产品/平台版本未变化时，才能在版本化配置中将边界设为 `FINAL_SAVE`。
8. 微信、小程序或页面版本升级后，原实验结论失效，恢复 `INNER_CONFIRM` 并重新验证。

### 15.2 功能和故障用例

| 编号 | 场景 | 预期结果 |
| --- | --- | --- |
| T01 | URI 启动且已登录 | 进入首页并识别商品管理入口 |
| T02 | URI 启动但未登录 | 等待人工登录，超时返回 `LOGIN_REQUIRED` |
| T03 | 网络加载失败 | 重载一次，失败返回 `NETWORK_OR_LOAD_ERROR` |
| T04 | SKU 精确匹配一条 | 进入正确商品编辑页 |
| T05 | 名称匹配多条 | 返回 `PRODUCT_MATCH_AMBIGUOUS`，不进入编辑 |
| T06 | 商品被下架 | 返回 `PRODUCT_NOT_ACTIVE` 或按配置只读 |
| T07 | 等级/规格不一致 | 返回 `PRODUCT_IDENTITY_MISMATCH` |
| T08 | 预期旧价与页面不同 | 返回 `OLD_PRICE_CHANGED`，不填价 |
| T09 | PRA 下发目标价 9.00，页面旧价 8.50 | 影刀不做加价计算，填写并回读 9.00 |
| T10 | `FILL_PREVIEW` | 返回 `PREVIEW_COMPLETED`，不点内层确认，平台价格不变 |
| T11 | 审批不存在、SKU/价格不匹配或已过期 | Executor 返回 `EXECUTION_NOT_AUTHORIZED`，不调用影刀 |
| T12 | 保存成功 | 列表复核目标价并返回 `SUCCESS` |
| T13 | 保存后仍是旧价 | 刷新一次后返回 `SUBMIT_NOT_APPLIED` |
| T14 | 同一业务操作首次运行在提交前失败 | 沿用 `operation_id`，使用新的 `execution_attempt_id` 重试 |
| T15 | 输入框自动改写金额 | 当前垂直切片首次回读不一致即安全失败；若后续启用重填，则重填一次后仍不一致才失败 |
| T16 | 提交前截图失败 | 返回 `SCREENSHOT_FAILED`，不得保存 |
| T17 | 提交点击发出后影刀进程退出 | 返回/推导 `NEEDS_RECONCILIATION`，禁止自动重试 |
| T18 | 影刀在打开商品页时退出 | `side_effect_state=NOT_STARTED`，可由 Executor 创建新尝试 |
| T19 | 对账读取到目标价 | 原 `operation_id` 归并为 `SUCCESS/VERIFIED` |
| T20 | 对账读取到旧价 | 标记 `NOT_APPLIED`，由 Executor 重新检查审批后决策 |
| T21 | 对账读取到其他价格 | 保持 `NEEDS_RECONCILIATION` 并人工告警 |
| T22 | `READ_ONLY` | 不聚焦输入框，返回 `READ_COMPLETED` 和只读证据 |
| T23 | `RECONCILE` | 全程不出现填写、确认或保存动作 |
| T24 | 证据复制成功且哈希一致 | 返回可由 PRA 访问的 `storage_uri` 和 `upload_status=SUCCESS` |
| T25 | 证据目标哈希不一致 | 返回 `EVIDENCE_HASH_MISMATCH`，提交边界前禁止继续 |
| T26 | 提交前证据上传失败 | 返回 `EVIDENCE_UPLOAD_FAILED`，不点击任何潜在副作用按钮 |
| T27 | 提交成功后证据补传失败 | 保留业务结果，标记证据不完整，不得重做改价 |
| T28 | `READ_COMPLETED` 技术运行成功 | `run_success_flag=true`、`business_operation_completed=false`，业务任务不完成 |
| T29 | `SUCCESS` 与 `business_operation_completed=false` 冲突 | 返回 `RESULT_CONTRACT_INVALID`，不更新业务任务 |
| T30 | `NEEDS_RECONCILIATION` 结果 | 两个完成标志均为 `null`，禁止自动重试和任务完成 |
| T31 | `NOT_APPLIED` 结果 | `status=NOT_APPLIED`、`error_code=SUBMIT_NOT_APPLIED`，字段不混写 |

## 16. 上线验收标准

- 连续 30 次只读运行成功率不低于 98%，且无误选商品。
- 连续 20 次 `FILL_PREVIEW` 全部正确回读目标价，且未点击内层确认、平台数据无变化。
- 连续 20 次 `READ_ONLY/RECONCILE` 均未聚焦或修改价格输入框。
- 测试商品至少 10 次真实提交全部完成列表复核，无重复提交。
- 所有失败和结果未知用例均输出标准错误码、当前步骤、`side_effect_state`、证据对象和重试建议。
- Executor 能证明审批与任务、SKU、目标价和批准载荷摘要一致；任一不一致均不会启动影刀。
- PRA 不会因 `READ_COMPLETED/PREVIEW_COMPLETED` 的技术成功标志而完成商品改价任务。
- 状态、错误码和两个完成标志的所有矛盾组合均被契约校验拒绝。
- 提交意图检查点之后的运行失联均不会进入自动重试队列。
- 登录过期、网络中断、商品下架、旧价变化、多结果和元素失效均能安全停止。
- PRA 可按 `task_id` 查看影刀运行 ID、旧价、目标价、实际价和可访问的证据 URI，并能复核 SHA-256。
- `COMMIT` 的提交前证据在进入副作用边界前全部完成归档和哈希校验。
- 关闭或遮挡非目标窗口不会导致商品身份核对被绕过。

## 17. 生产运行约束

- 首版只允许已审批任务、单商品串行执行，并要求旧价校验和提交后对账。
- 运行期间固定 Windows 缩放比例、微信窗口尺寸和显示器布局。
- 微信、目标小程序或影刀升级后，先执行只读冒烟测试再恢复提交。
- 定期检查元素定位成功率；CV/坐标兜底比例升高时应重新录制元素。
- 不在无人值守流程中保存登录密码；验证码和扫码登录由人工完成。
- 内层确认和任何平台适配器保存/提交按钮均不得配置自动重试；未明确验证的最终保存按钮不得进入自动流程。

### 17.1 影刀代码缓存与测试前置要求

影刀设计器不会即时同步外部来源对 `.py` 代码流文件的修改。若使用 Codex、IDE、脚本或其他外部工具修改了影刀应用目录中的 `.py` 文件，不应为了运行测试重新进入编辑器；已打开的设计器可能继续使用旧缓存，也可能在保存或运行时把旧缓存内容写回磁盘，覆盖外部工具刚写入的新版本。

外部修改 `.py` 后的标准测试步骤为：

1. 保存外部代码文件并完成 `sync_shadowbot_test2.py` 同步。
2. 确认 `test2` 编辑器未打开；若已打开，先完整退出编辑器，不在其中保存或运行。
3. 回到影刀“应用”主页面，确认目标行名为 `test2`。
4. 点击该行内圆形“运行应用”图标，直接启动主流程；不以顶部“运行”或编辑器运行作为默认入口。
5. 流程结束后检查并关闭影刀残留运行窗口，再读取队列结果。

只有人工捕获元素、修改 `.flow` 或录制时才允许进入编辑器。此类人工修改完成后保存并退出编辑器；下次外部 Python 同步后仍回到应用列表直接运行。若发现磁盘文件被旧内容覆盖，应停止继续运行，从版本记录或备份恢复最新代码，并在编辑器关闭状态下重新同步。

## 18. 20 步主流程摘要

1. 接收并严格校验 PRA 参数。
2. 初始化运行日志、业务操作 ID、执行尝试 ID 和证据目录。
3. 通过 URI 启动小程序，获取、还原并规范化微信窗口。
4. 判断登录/加载/异常状态，必要时等待人工完成登录。
5. 进入商品管理；纯导航可复用当前页。
6. 在每个列表价格读取阶段点击“商品管理”强制刷新并验证稳定页面。
7. 刷新后重新定位商品；优先按 SKU 搜索，按名称搜索时要求结果唯一。
8. 在刷新后的列表中核对名称、等级、规格、SKU 和商品状态。
9. 从已核对商品行进入编辑页。
10. 验证编辑页并再次核对商品上下文。
11. 读取供货价格，解析并检查预期旧价。
12. 按 `execution_mode` 分流；`READ_ONLY/RECONCILE` 保持只读，其他模式校验 PRA 目标价。
13. 仅 `FILL_PREVIEW/COMMIT` 清空价格输入框并填写目标价。
14. 仅填写模式失焦并回读输入框，严格比较目标价。
15. 将提交前截图归档为带存储 URI 和 SHA-256 的证据对象。
16. `READ_ONLY/FILL_PREVIEW` 返回技术结果，不创建第二次审批；`RECONCILE` 只读核对实际价格。
17. `COMMIT` 复查不可变指令和 UI 业务值，在最早潜在副作用动作前持久化提交意图，再执行内层确认，并按平台适配配置执行后续保存（如存在）；审批真实性已由 Executor 验证。
18. 回到列表，点击“商品管理”刷新一次并重新定位同一 SKU，再轮询复核平台实际价格。
19. 输出统一 JSON；数据库 `success_flag` 仅映射技术运行结果，业务任务只按明确允许完成的状态集合和 `business_operation_completed` 更新。
20. 异常按副作用阶段分类；保存结果未知时进入 `NEEDS_RECONCILIATION`，返回错误码、证据清单和只读对账建议。
