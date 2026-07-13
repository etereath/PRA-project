# 影刀控制微信小程序探索状态与后续计划

版本：1.4

日期：2026-06-25

适用对象：影刀 RPA、桌面端微信小程序 `WeChatAppEx`、`蚂蚁花团供应商` 小程序、PRA `ShadowBotExecutor`

关联开发规范：`docs/shadowbot_wechat_price_update_development_spec.md`

## 1. 文档目的

本文记录截至目前对影刀、桌面端微信小程序和 `蚂蚁花团供应商` 商品改价流程的探索结论、当前编程策略、已经验证的垂直切片结果，以及后续进入稳定流程和生产接入前的计划。

本文不是最终生产接口契约。生产接口、安全边界、状态码和证据要求以 `shadowbot_wechat_price_update_development_spec.md` 为准。本文偏向工程现场记录，用于指导后续继续录制元素、完善代码和做真实平台验证。

## 2. 当前工作资产

| 类别 | 名称或路径 | 说明 |
| --- | --- | --- |
| 影刀应用 | `test2` | 当前用于垂直切片验证的新建影刀应用 |
| 主流程代码 | `C:\Users\etere\AppData\Local\ShadowBot\users\940455499808497666\apps\fb717589-c95c-4228-935d-c61d54df494c\xbot_robot\vertical_slice_read_price.py` | 当前主流程，已支持 `READ_ONLY`、`FILL_PREVIEW`、`COMMIT` 和 `RECONCILE`，当前 schema 为 `vertical-slice-1.5` |
| 诊断流程代码 | `C:\Users\etere\AppData\Local\ShadowBot\users\940455499808497666\apps\fb717589-c95c-4228-935d-c61d54df494c\xbot_robot\vertical_slice_read_price(copy)1.py` | 用于输出和分析捕获元素属性 |
| 元素库 | `C:\Users\etere\AppData\Local\ShadowBot\users\940455499808497666\apps\fb717589-c95c-4228-935d-c61d54df494c\xbot_robot\selectorsV2.xml` | 已捕获商品列表元素、价格弹窗元素、取消/确认按钮和输入框 |
| 运行结果目录 | `C:\Users\etere\AppData\Local\ShadowBot\results\vertical_slice` | 每次运行输出结构化 JSON |
| 证据截图目录 | `C:\Users\etere\AppData\Local\ShadowBot\evidence\vertical_slice` | 每次运行输出截图和 SHA-256 |
| 目标窗口 | `蚂蚁花团供应商` | 桌面端微信小程序窗口标题 |
| 目标进程 | `WeChatAppEx` | 微信小程序桌面端运行进程 |

当前垂直切片是单平台适配器。虽然 Python 参数中仍有 `window_title`，但元素库 XML 中的窗口选择器固定为 `title=蚂蚁花团供应商`、`app=WeChatAppEx`，因此该参数只能用于当前适配器的窗口获取阶段，不应描述为可任意切换平台的通用参数。

## 3. 已完成探索结论

### 3.1 微信小程序启动

微信小程序可以通过桌面快捷方式的 URI 方式启动，快捷方式目标类似：

```text
weixin://launchapplet/?app_id=...
```

这说明后续影刀流程可以把小程序启动作为可配置步骤处理。若小程序窗口已经打开，则流程应直接复用现有窗口；若未打开，则由 Executor 或影刀通过 URI 启动，然后等待窗口标题 `蚂蚁花团供应商` 出现。

### 3.2 元素识别方式

目前确认影刀可通过桌面软件元素进行识别和操作，主要依赖 accessibility/控件树属性，而不是单纯依赖图像识别。已观察到的常用属性包括：

| 属性类别 | 示例 | 当前用途 |
| --- | --- | --- |
| 窗口属性 | `title=蚂蚁花团供应商`、`app=WeChatAppEx` | 锁定目标窗口 |
| 控件角色 | `role=StaticText`、`role=Grouping`、`role=Text`、`role=PushButton` | 区分文本、容器、输入框、按钮 |
| 标签 | `tag=wx-view`、`tag=input`、`tag=wx-button` | 区分微信小程序内部控件 |
| 文本属性 | `acc-name=艾莎`、`acc-name=C级`、`acc-name=￥19.00` | 动态匹配商品、等级和价格 |
| 样式类 | `class=van-checkbox-group`、`contains:class=van-dialog`、`contains:class=van-icon-plus` | 识别列表容器、弹窗和加价图标 |
| 几何信息 | `bounding rectangle` | 用于把同一商品行的名称、等级和价格关联起来 |

当前结论是：元素识别稳定性整体高于图像识别和固定坐标点击，但仍依赖小程序控件树结构、组件库版本、列表布局和窗口缩放。后续生产流程应把元素定位作为主路径，把截图作为审计证据和人工排查材料，不把截图识别作为常规主路径。

### 3.3 商品列表结构

商品管理列表中，同类元素属性具有规律：

1. 商品名通常是 `StaticText`，`role=StaticText`，`index=1`。
2. 商品等级通常是 `StaticText`，`role=StaticText`，`index=0`。
3. 商品行父级 `wx-view` 的 `index` 在列表中大致按 16 递增，例如第一行、第二行、第三行分别落在不同父级 index 上。
4. 价格元素可直接点击打开价格编辑弹窗，不必先进入完整编辑页。
5. 第一个商品的价格元素曾观察到类似 `wx-view[@index="10"]/StaticText[@role=StaticText,@index="0"]`，但后续不再把这个偏移作为硬编码规则。

由于列表可能滚动、商品可能上下架、行内结构可能变化，当前代码不采用固定行号或固定偏移作为主策略，而是先枚举商品名元素，再结合父级 index 和几何位置推断目标行。

### 3.4 价格弹窗结构

目前已捕获以下价格弹窗相关元素：

| 元素名称 | 用途 | 当前状态 |
| --- | --- | --- |
| `价格弹窗_容器` | 判断弹窗是否出现 | 已捕获，可用 |
| `价格弹窗_修改后价格_输入框` | 填写目标价 | 已捕获，可用 |
| `价格弹窗_取消按钮` | 关闭弹窗并避免业务变更 | 已捕获，可用 |
| `价格弹窗_确认按钮` | 内层确认按钮 | 已捕获，但当前代码禁止使用 |
| `价格弹窗_当前商品_值模板` | 验证弹窗商品名 | 已捕获，代码会动态替换 `acc-name` |
| `价格弹窗_当前等级_值模板` | 验证弹窗等级 | 已捕获，代码会动态替换 `acc-name` |
| `价格弹窗_当前价格_值模板` | 验证弹窗旧价 | 已捕获，代码会动态替换 `acc-name` |
| `价格弹窗_加价按钮` | 加价图标 | 已捕获，基于 `contains:class=van-icon-plus` |
| `价格弹窗_减价按钮` | 减价图标 | 已捕获，后续建议复核 class 匹配是否应改为 `van-icon-minus` |

当前安全约束是：`FILL_PREVIEW` 模式只允许打开弹窗、填写输入框、回读输入框、截图、点击取消。不得点击 `价格弹窗_确认按钮`，也不得触发任何平台适配器保存/提交按钮。

### 3.5 输入框回读方式

价格输入框的可访问性属性不直接暴露当前 value。已尝试的读取方式包括：

1. `get_value`
2. `get_text`
3. 常见属性读取
4. 选中输入框后通过剪贴板 `Ctrl+A/C` 回读

最终有效方式是剪贴板回读。当前代码会保存原剪贴板文本，读取完成后尽量恢复，避免污染用户剪贴板。填写目标价时使用影刀元素的 `clipboard_input`。从 `vertical-slice-1.5` 起，复制后不再只读取一次剪贴板，而是在短时间内轮询，降低系统繁忙、远程桌面或微信响应较慢时读到 marker 或旧值的概率。

## 4. 已验证的垂直切片

### 4.1 READ_ONLY

`READ_ONLY` 已验证可完成：

1. 获取并激活 `蚂蚁花团供应商` 窗口。
2. 进入商品管理。
3. 枚举商品列表。
4. 根据商品名和等级定位 `C级 艾莎`。
5. 读取供货价格。
6. 截图并输出结构化 JSON。

代表性结果：

| 字段 | 值 |
| --- | --- |
| `schema_version` | `vertical-slice-1.3` |
| `execution_mode` | `READ_ONLY` |
| `status` | `READ_COMPLETED` |
| `product_name` | `艾莎` |
| `grade` | `C级` |
| `old_price` | `7.00` |
| `business_operation_completed` | `false` |

说明：后续商品价格已经变化，`7.00` 只是当时运行时的页面值，不代表当前平台价格。

### 4.2 FILL_PREVIEW

`FILL_PREVIEW` 已验证可完成：

1. 定位 `C级 艾莎`。
2. 读取列表供货价格。
3. 点击价格元素打开弹窗。
4. 验证弹窗商品名、等级和当前价格。
5. 填写 PRA 模拟下发的目标价。
6. 通过剪贴板回读输入框。
7. 截图并计算 SHA-256。
8. 点击取消关闭弹窗。
9. 返回列表后价格保持不变，未产生业务变更。

最新成功运行结果：

| 字段 | 值 |
| --- | --- |
| 结果文件 | `C:\Users\etere\AppData\Local\ShadowBot\results\vertical_slice\VS-95adfaac7d5041d68bb0021cce3110ff.json` |
| `schema_version` | `vertical-slice-1.4` |
| `execution_mode` | `FILL_PREVIEW` |
| `status` | `PREVIEW_COMPLETED` |
| `current_step` | `COMPLETE` |
| `product_name` | `艾莎` |
| `grade` | `C级` |
| `old_price` | `19.00` |
| `target_price` | `19.50` |
| `preview_initial_value` | `19.00` |
| `preview_value` | `19.50` |
| `run_success_flag` | `true` |
| `business_operation_completed` | `false` |
| 截图文件 | `C:\Users\etere\AppData\Local\ShadowBot\evidence\vertical_slice\VS-95adfaac7d5041d68bb0021cce3110ff_fill_preview.png` |
| 截图 SHA-256 | `b5e586c16bd9866d9497364860d916273171f459b1a27c5328fddb2027e625d2` |

该结果证明当前代码可以完成“定位目标商品、读取旧价、预填目标价、回读校验、保留证据、取消退出”的最小闭环。

### 4.3 vertical-slice-1.5 安全修正与 RECONCILE

当前主流程已完成以下安全修正：

1. 默认请求不再携带 `product_keyword`、`expected_product_name`、`expected_grade`、`target_price`，也不再默认进入 `FILL_PREVIEW`。外部调用漏传商品或目标价时会在参数校验阶段失败。
2. `task_id` 和 `execution_attempt_id` 改为必填；若结果目录中已存在相同 `execution_attempt_id` 的 JSON 文件，则返回 `DUPLICATE_EXECUTION_ATTEMPT_ID`，拒绝覆盖旧结果。
3. 新增 `expected_old_price`。`FILL_PREVIEW/COMMIT/RECONCILE` 必须传入该字段，影刀在打开价格弹窗前比较页面旧价和审批旧价，不一致返回 `OLD_PRICE_CHANGED`。
4. 失败时尽量自动保存错误现场截图，截图失败不会覆盖原始错误，而是写入 `error_evidence_message`。
5. `FILL_PREVIEW` 中若业务步骤先失败，随后取消弹窗也失败，结果会保留原始错误码，并把取消失败追加到错误信息中。
6. 输入回读不一致当前保持零重试，直接安全失败。该策略与当前实现一致；是否增加一次重填，等待更多运行数据后再决定。
7. 新增独立 `RECONCILE` 模式。该模式复用商品定位和列表价格读取能力，只读核对 `actual_price`、`expected_old_price`、`target_price`，不打开价格弹窗，不点击内层确认，不点击任何平台适配器保存/提交按钮。
8. 新增共享证据发布机制。`READ_ONLY`、`FILL_PREVIEW`、`RECONCILE` 截图后都会尝试复制到 `evidence_share_dir`，并对共享文件重新计算 SHA-256，写入 `storage_uri`、`storage_path`、`storage_sha256`、`hash_verified` 和 `upload_status`。

`RECONCILE` 三态输出规则：

| 条件 | `status` | `side_effect_state` | 说明 |
| --- | --- | --- | --- |
| `actual_price == target_price` | `VERIFIED` | `VERIFIED` | 目标价已在平台生效，原结果未知操作可归并为已验证成功 |
| `actual_price == expected_old_price` | `NOT_APPLIED` | `NOT_APPLIED` | 仍是审批旧价，未观察到保存生效 |
| 其他价格 | `NEEDS_RECONCILIATION` | `UNKNOWN` | 价格既不是旧价也不是目标价，需要人工核对 |

结构化结果样例已写入：

1. `docs/examples/shadowbot_reconcile_verified.json`
2. `docs/examples/shadowbot_reconcile_not_applied.json`
3. `docs/examples/shadowbot_reconcile_needs_reconciliation.json`
4. `docs/examples/shadowbot_read_only_shared_evidence.json`
5. `docs/examples/shadowbot_fill_preview_shared_evidence.json`

回归测试已写入 `tests/test_shadowbot_vertical_slice_reconcile.py`，覆盖三态分类和“不引用确认按钮选择器”的源码约束。

共享证据测试已写入 `tests/test_shadowbot_evidence_share.py`，使用临时目录模拟 UNC 共享目录，验证文件复制、共享路径、共享哈希和跳过状态。

UNC 共享目录创建脚本已写入 `scripts/setup_shadowbot_evidence_share.ps1`。该脚本创建本地证据目录和 SMB 共享，输出可填入 `evidence_share_dir` 的 UNC 路径。

### 4.4 COMMIT 与故障注入验证

2026-06-24 至 2026-06-25 已在真实桌面微信小程序上完成 `COMMIT` 开发测试版和核心故障注入验证。

真实 `COMMIT` 成功样例：

| 字段 | 值 |
| --- | --- |
| 结果文件 | `C:\Users\etere\AppData\Local\ShadowBot\results\vertical_slice\ATTEMPT-COMMIT-C-AISHA-20260624-005.json` |
| `execution_mode` | `COMMIT` |
| `status` | `SUCCESS` |
| `side_effect_state` | `VERIFIED` |
| `old_price` | `6.80` |
| `target_price` | `7.30` |
| `actual_price` | `7.30` |
| `business_operation_completed` | `true` |

2026-06-25 故障注入结果：

| 场景 | 结果 |
| --- | --- |
| 审批旧价变化 | `FAILED / READ_OLD_PRICE / NOT_STARTED / OLD_PRICE_CHANGED` |
| 商品找不到 | `FAILED / LOCATE_PRODUCT / NOT_STARTED / PRODUCT_NOT_FOUND` |
| 缺少 `target_price` | `FAILED / VALIDATE_INPUT / NOT_STARTED / INPUT_INVALID` |
| 价格输入回读不一致 | `FAILED / FILL_TARGET_PRICE / NOT_STARTED / TARGET_PRICE_VERIFY_FAILED`，并自动取消弹窗 |
| 提交点击后结果未知 | `NEEDS_RECONCILIATION / UNKNOWN / SUBMIT_RESULT_UNKNOWN / retryable=false` |
| 结果未知后只读对账 | `VERIFIED / COMPLETE / VERIFIED / actual_price=7.30` |

详见 [reports/shadowbot_fault_injection_20260625.md](reports/shadowbot_fault_injection_20260625.md)。

本轮修正包括：

1. `FILL_PREVIEW/COMMIT` 在读取列表旧价后、任何成功判定前强制校验 `expected_old_price`。
2. 缺参错误消息改为可读 ASCII，避免影刀日志链路出现乱码。
3. 新增仅测试请求启用的 `fault_injection` 钩子，用于验证价格回读不一致和提交后结果未知。
4. 提交前弹窗阶段失败后会尝试取消弹窗，并记录清理结果。
5. 当前小程序实际副作用边界按 `INNER_CONFIRM` 管理：弹窗确认后已经可能产生可复核的平台结果。

## 5. 当前编程策略

### 5.1 影刀职责边界

影刀只作为 UI 执行器，不负责业务计算和审批判断：

1. 不接收 `price_delta`。
2. 不计算目标价。
3. 只接收 PRA 或 `ShadowBotExecutor` 已计算好的 `target_price`。
4. 不根据 `approval_id` 是否非空判断授权是否有效。
5. 不把 `READ_COMPLETED` 或 `PREVIEW_COMPLETED` 当成业务改价完成。
6. 不使用代码内默认商品或默认目标价代替 PRA 指令；缺少关键参数时必须失败退出。

审批真实性、任务归属、SKU、目标价格、审批有效期、审批后参数是否被篡改，都应由 PRA 服务端的 `ShadowBotExecutor` 在调用影刀前完成。

### 5.2 执行模式

生产接口应使用显式 `execution_mode`，而不是单一 `should_submit`：

| 模式 | 允许动作 | 业务副作用 |
| --- | --- | --- |
| `READ_ONLY` | 只读商品和价格、截图 | 无 |
| `FILL_PREVIEW` | 填入目标价、回读、截图、取消 | 理论上无，仍需持续验证 |
| `COMMIT` | 已授权后执行真实保存，并回到列表复核 | 有 |
| `RECONCILE` | 只读核对实际价格 | 无 |

当前已实现 `READ_ONLY`、`FILL_PREVIEW`、`COMMIT` 和 `RECONCILE`。其中 `RECONCILE` 是只读模式，只复用定位和读价，不进入价格弹窗。`COMMIT` 已在测试商品上完成开发测试版真实验证；进入生产前仍需由 PRA `ShadowBotExecutor` 完成审批、幂等、调度和对账闭环。

### 5.3 元素定位策略

当前主路径不依赖视觉点击，而是使用元素属性加动态推断：

1. 使用影刀捕获的元素作为模板。
2. 对模板中的 `acc-name`、父级 `wx-view` index 等属性做动态克隆。
3. 使用 `find_all()` 枚举候选商品名。
4. 通过商品名元素的父级属性推断商品行。
5. 用同一行的等级元素校验商品身份。
6. 用几何纵向区间关联价格文本，避免固定 `row_index + 9` 这种脆弱偏移。
7. 对弹窗内商品名、等级、当前价格使用动态 `acc-name` 校验。
8. 截图只作为证据和异常排查，不作为当前常规定位主路径。

### 5.4 价格读取和写入策略

当前价格读取分两层：

1. 列表价格：通过候选 decimal 文本和商品行几何位置关联。
2. 弹窗当前价格：通过动态 `acc-name=￥{old_price}` 验证。

`RECONCILE` 只使用列表价格读取能力，并将读到的价格同时写入 `old_price` 和 `actual_price`。对账模式不会调用价格弹窗、输入框或取消/确认按钮。

当前目标价写入分三步：

1. 打开价格弹窗。
2. 使用 `clipboard_input` 写入 `target_price`。
3. 使用剪贴板回读输入框，严格比较是否等于 `target_price`。

如果回读失败或回读值不一致，流程应失败退出并点击取消，不允许继续提交。

`FILL_PREVIEW/COMMIT/RECONCILE` 必须下发 `expected_old_price`，旧价校验发生在打开弹窗之前：

```json
{
  "expected_old_price": "19.00"
}
```

页面旧价与该值不一致时，返回：

```json
{
  "status": "FAILED",
  "error_code": "OLD_PRICE_CHANGED",
  "retryable": false
}
```

### 5.5 证据和结果策略

当前每次运行输出结构化 JSON，关键字段包括：

1. `schema_version`
2. `task_id`
3. `execution_attempt_id`
4. `execution_mode`
5. `status`
6. `current_step`
7. `product_name`
8. `grade`
9. `old_price`
10. `target_price`
11. `actual_price`
12. `side_effect_state`
13. `preview_initial_value`
14. `preview_value`
15. `run_success_flag`
16. `business_operation_completed`
17. `error_code`
18. `error_message`
19. `evidence`

证据当前先落本地目录，并记录 SHA-256。正式接入时应复制到 PRA 与机器人机共享目录，或上传到统一存储，然后在输出中提供 `storage_uri`。

当前实现已经支持共享目录发布：

| 参数 | 说明 |
| --- | --- |
| `evidence_share_dir` | PRA 与机器人机都可访问的共享目录，建议使用 Windows UNC，例如 `\\pra-share\evidence` |
| `evidence_storage_uri_prefix` | 可选 URI 前缀；为空时 `storage_uri` 使用复制后的共享路径 |

证据对象新增字段：

| 字段 | 说明 |
| --- | --- |
| `storage_path` | 复制到共享目录后的实际路径 |
| `storage_sha256` | 共享文件重新计算出的 SHA-256 |
| `hash_verified` | 本地截图哈希与共享文件哈希是否一致 |
| `upload_status` | `SUCCESS` 表示共享证据可访问且哈希一致；`SKIPPED` 表示未配置共享目录；`FAILED` 表示复制或复核失败 |
| `upload_error` | 共享证据发布失败或跳过原因 |

## 6. 当前主要风险

| 风险 | 说明 | 当前处理 |
| --- | --- | --- |
| 小程序结构变化 | 微信、小程序或 Vant 组件升级后，控件树和 class 可能变化 | 需要只读冒烟测试和元素版本维护 |
| 窗口标题参数误用 | Python 层可传 `window_title`，但 XML 选择器固定为当前平台窗口 | 当前按单平台适配器管理，不作为通用多平台参数 |
| 商品列表动态变化 | 商品上下架、排序变化会导致固定行号失效 | 已改为枚举商品名和几何关联 |
| 价格输入框 value 不暴露 | 无法直接通过属性读取输入值 | 已用剪贴板回读兜底 |
| 内层确认是当前小程序副作用边界 | 故障注入确认，弹窗内确认后平台已经可能产生可复核结果，不能假设还必须存在独立最终保存按钮 | 当前适配器按 `INNER_CONFIRM` 管理副作用边界；点击前记录 `SUBMIT_INTENT_RECORDED`，点击后记录 `SUBMIT_CLICKED`，随后只通过列表价格复核判断结果 |
| 保存结果未知 | 提交后若进程异常，不能简单重试 | 生产设计要求 `NEEDS_RECONCILIATION` 和只读对账 |
| 本地截图路径不可长期依赖 | PRA 和机器人机分离时路径不可访问 | 已支持 `evidence_share_dir` 共享目录复制和哈希复核；真实提交前必须配置 UNC |
| 剪贴板副作用 | 回读输入框需要临时占用剪贴板 | 已尝试保存和恢复原文本，并增加短轮询；生产中需监控失败情况 |
| 多商品同名 | 只按商品名匹配可能误选 | 必须同时校验等级，后续优先使用 SKU |
| 影刀代码缓存写回 | 已打开的影刀设计器不会即时同步外部对 `.py` 文件的修改，继续在编辑器中测试可能执行旧代码，甚至把旧缓存写回磁盘 | 外部同步前关闭编辑器；随后留在影刀“应用”主页面，从 `test2` 行内“运行应用”图标直接启动，不为测试重新打开编辑器 |

## 7. 后续计划

### 7.1 近期计划

1. 继续复核 `价格弹窗_减价按钮` 选择器，将 class 匹配统一为更清晰的 `van-icon-minus`。
2. 将成功的 `FILL_PREVIEW` 截图和 JSON 结构沉淀为回归样例。
3. 增加运行前清理：如果价格弹窗已打开，优先点击取消，确保从干净状态开始。
4. 加强异常输出：每个失败分支都带 `current_step`、`retryable`、`side_effect_state` 和可读错误信息。
5. 观察输入回读失败率；若运行数据证明有必要，再增加“一次清空重填”策略。
6. 在真实小程序窗口上运行 `READ_ONLY`、`FILL_PREVIEW`、`RECONCILE` 样例参数，并配置 `evidence_share_dir`，保存一份实际运行 JSON 和共享截图作为现场回归基线。
7. 在 Windows 上创建正式 UNC 共享目录，确认机器人用户具备写权限、PRA 服务用户具备读权限。

### 7.2 中期计划

1. 将已验证的 `INNER_CONFIRM` 副作用边界固化到平台适配配置，`final_save_button` 只作为可选能力保留。
2. 补齐 `ShadowBotExecutor` 到影刀运行器的调度、结果回写、证据归档和告警闭环。
3. 若点击确认后流程异常或价格为第三值，按 `NEEDS_RECONCILIATION` 进入只读对账，不自动重试。
4. 复核 `PRICE_INPUT_FAILED` 的真实发生率；当前已加入 `clipboard_input` 写入兜底，若仍不稳定，再增加清空、重填和剪贴板回读组合策略。
5. 建立元素回归测试：同一窗口尺寸和缩放下，批量验证商品列表、价格弹窗、输入框、取消按钮和确认按钮是否仍可定位。

### 7.3 生产接入计划

1. PRA `ShadowBotExecutor` 完成审批真实性校验，不把 `approval_id` 字符串交给影刀自行判断。
2. 拆分业务幂等键和影刀尝试 ID：`operation_id` 表示一次批准的业务变更，`execution_attempt_id` 表示一次影刀运行。
3. 输出字段拆分技术成功和业务完成：`run_success_flag` 与 `business_operation_completed` 分开处理。
4. 证据从本地路径升级为证据对象，至少包含 `local_path`、`storage_uri`、`sha256`、`captured_at`、`upload_status`。
5. 首批生产只允许已审批任务、单商品串行执行，并要求旧价校验和提交后对账。
6. 建立人工复核入口：`NEEDS_RECONCILIATION`、`POST_SUBMIT_PRICE_MISMATCH`、`OLD_PRICE_CHANGED` 等状态进入人工处理队列。
7. 每次微信、小程序或影刀升级后，先跑 `READ_ONLY` 和 `FILL_PREVIEW` 冒烟测试，再恢复 `COMMIT`。

## 8. 当前阶段结论

截至目前，可以认为“通过影刀元素属性控制桌面端微信小程序进行只读查询和安全预填”已经具备可行性。最有价值的验证是：

1. 不依赖人工逐行捕获商品，也能通过商品名枚举、父级 index 和几何位置推断定位目标商品。
2. 不依赖图像识别，也能打开价格弹窗并校验弹窗上下文。
3. 价格输入框虽然不直接暴露 value，但可以通过剪贴板方式完成写入和回读校验。
4. `FILL_PREVIEW` 已完成一次闭环验证，输入 `19.50` 后成功回读，并通过取消退出，列表价格仍为 `19.00`。

当前可以认为 `COMMIT` 开发测试版已具备真实小程序上的技术可行性和核心故障保护，但仍不能直接进入无人值守生产。进入生产前必须完成 PRA `ShadowBotExecutor` 真实调度闭环、更多连续样本、登录/网络/元素失效注入和人工对账入口。
