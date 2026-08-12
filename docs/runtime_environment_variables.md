# 运行态环境变量说明

本文档说明本地运行 Web 后台、Mobile Review、飞书 Webhook 通知时需要配置的环境变量。

真实密钥、Webhook、密码不要写入仓库。推荐把真实配置放在 `scripts/local_env.ps1`，该文件已加入 `.gitignore`。

当前项目状态总览见 [project_current_status.md](project_current_status.md)。

## 1. 推荐文件结构

- `scripts/start_local.ps1`：可提交的启动脚本。
- `scripts/check_runtime_env.py`：可提交的环境检查脚本。
- `scripts/local_env.example.ps1`：可提交的示例配置。
- `scripts/local_env.ps1`：本地真实配置，不提交 git。

首次使用时复制示例文件：

```powershell
Copy-Item scripts/local_env.example.ps1 scripts/local_env.ps1
```

然后编辑 `scripts/local_env.ps1`，替换所有占位值。

加载本机真实配置时使用：

```powershell
. .\scripts\local_env.ps1
```

若 PowerShell 返回 `running scripts is disabled on this system`，可只对当前终端进程临时放开执行策略后再加载，避免修改系统级策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\local_env.ps1
```

## 2. 必填变量

### `REVIEW_TOKEN_SECRET`

用途：生成和校验 mobile review token。

要求：

- 必须配置。
- 建议至少 32 个字符。
- 必须长期稳定；修改后，旧 mobile review 链接会失效。
- 不得提交到 git。

### `RUNTIME_ADMIN_PASSWORD`

用途：Web `/runtime` 后台登录密码。

要求：

- 必须配置。
- 建议使用强密码。
- 不得提交到 git。

## 3. 飞书通知变量

当 `DEFAULT_NOTIFICATION_CHANNEL=feishu` 时，以下变量用于真实发送飞书群通知。

### `DEFAULT_NOTIFICATION_CHANNEL`

可选值：

- `mock`：默认值，只模拟发送，不触达外部渠道。
- `feishu`：使用飞书自定义机器人 Webhook 发送通知。

### `FEISHU_WEBHOOK_URL`

用途：飞书自定义机器人 Webhook 地址。

要求：

- `DEFAULT_NOTIFICATION_CHANNEL=feishu` 时必须配置。
- 不得提交到 git。

### `FEISHU_WEBHOOK_SECRET`

用途：飞书自定义机器人签名密钥。

要求：

- 如果飞书机器人开启了签名校验，则必须配置。
- 如果未开启签名校验，可以留空。
- 不得提交到 git。

### `FEISHU_MESSAGE_TYPE`

用途：控制飞书自定义机器人消息格式。

可选值：

- `post`：默认值，使用飞书富文本消息，字段更清晰，并将手机复核链接显示为“👉 点击处理复核”。
- `text`：保留纯文本消息作为回退，适合排查飞书 post payload 问题。

要求：

- 只能配置为 `post` 或 `text`。
- `post` 发送失败时不会自动重发 `text`；是否回退未来再通过独立配置控制。
- `notification_logs.message` 不会保存完整 mobile review URL，完整链接只进入实际外发 payload。

### `FEISHU_WEBHOOK_TIMEOUT_SECONDS`

用途：飞书 HTTP 请求超时时间。

默认值：`5`

建议保持在 `5` 到 `10` 秒之间。

## 4. Mobile Review 链接变量

### `MOBILE_REVIEW_BASE_URL`

用途：生成飞书消息中的手机复核链接。

示例：

```powershell
$env:MOBILE_REVIEW_BASE_URL = "https://your-fixed-domain.cpolar.cn"
```

要求：

- `DEFAULT_NOTIFICATION_CHANNEL=feishu` 时建议必须配置。
- 地址需要能从手机访问到本地 Web 服务，并使用受运维管理的稳定 HTTPS 域名；随机临时
  cpolar 域名只能用于本地探索，不得进入发给运营人员的真实通知。
- 结尾是否带 `/` 均可，系统会处理。
- 不建议提交真实固定地址到 git。
- 受控真实通知验收前必须从公网地址检查 `/health`，不能用本机回环地址的 200 替代。
- 地址变更或旧 Token 到期后必须作废旧 Review/Token 并重新生成通知；不得修改、复用或
  宣称旧消息中的链接已经恢复。

## 5. Web 后台登录变量

### `RUNTIME_ADMIN_USER`

用途：Web `/runtime` 后台登录账号。

默认值：`admin`

### `RUNTIME_ADMIN_PASSWORD`

用途：Web `/runtime` 后台登录密码。

必须配置，不得提交到 git。

### `DEV_MODE`

用途：是否启用本地开发 fallback。

推荐值：

```powershell
$env:DEV_MODE = "false"
```

只有字符串 `true` 会被识别为开发模式。

### `PRA_ALLOWED_DATA_DIRS`

用途：配置 Web 请求可以访问或写入的本地数据目录。多个目录使用操作系统路径分隔符；Windows 使用分号（`;`），POSIX 使用冒号（`:`）。

要求：

- 必须填写绝对目录；空项、相对目录、不可解析目录会使路径策略失败关闭。
- 服务只在启动时读取并固定 allowlist；修改后必须重启服务，并在任务交接记录中说明变更。
- URL、query、form、JSON body、Cookie 和转发头不能新增或扩大 allowlist。
- 未配置时仅允许应用默认 `data/runtime` 目录；服务端默认路径也必须经过同一策略。需要使用 `data/samples` 时，必须显式把该目录加入 allowlist。
- 显式 `.`、`..`、URL 编码/多层编码后的 traversal 组件均拒绝；`allow_create=false` 的缺失目标返回稳定 `PATH_NOT_FOUND`，不会创建文件。
- 路径会先规范化，再检查盘符、UNC/设备路径、相对路径、符号链接/junction 和最近存在父目录，拒绝逃逸。

示例：

```powershell
$env:PRA_ALLOWED_DATA_DIRS = "D:\PRA_Runtime\data;D:\PRA_Runtime\imports"
```

### `PRA_COOKIE_SECURE`

用途：开发环境本地 HTTP 的 Session Cookie 安全属性显式开关。

- 新运营 Web 要求显式设置，不能依赖默认值。
- development 必须为 `false`，对应本地 HTTP；production 必须为 `true`，对应 HTTPS。
- Session 始终设置 `HttpOnly`、`SameSite=Lax`；不得通过任意请求头或
  `X-Forwarded-Proto` 降级 Cookie。

### `PRA_WEB_PUBLIC_SCHEME`

用途：声明运营人员实际访问新 Web 使用的协议。该值只允许来自启动环境，不能由请求覆盖。

- development 必须显式设为 `http`，并同时设置 `PRA_COOKIE_SECURE=false`；
- production 必须显式设为 `https`，并同时设置 `PRA_COOKIE_SECURE=true`；
- 协议、环境和 Cookie 任一冲突时，新运营 Web 在启动阶段失败并给出中文原因；
- 生产环境的 TLS 终止和反向代理仍属于部署门禁，不能只凭请求转发头宣称已使用 HTTPS。

### 新运营 Web 固定依赖路径

7B Composition Root 在启动时一次性解析以下可选变量；未提供时使用仓库既有默认路径：

- `PRA_RUNTIME_DB`：Runtime DB；
- `PRA_PRODUCTS_WORKBOOK`：商品工作簿；
- `PRA_PRICE_RULES_WORKBOOK`：价格规则工作簿；
- `PRA_LISTING_RULES_WORKBOOK`：上下架规则工作簿；
- `SHADOWBOT_QUEUE_DIR`：Queue 根目录。

这些值不能出现在 query、form、JSON 或 Session 中。修改后必须重启 Web；GET 不会初始化、
迁移或修复 Runtime DB。

### 登录限流变量

后台登录失败按规范化账号标识和 TCP 对端地址进行有界内存限流；不信任 `Forwarded`、`X-Forwarded-For` 或 `X-Real-IP`。可选配置如下，均有代码内上限：

- `RUNTIME_LOGIN_RATE_LIMIT_MAX_ATTEMPTS`：窗口内失败次数，默认 `5`。
- `RUNTIME_LOGIN_RATE_LIMIT_WINDOW_SECONDS`：失败窗口，默认 `300` 秒。
- `RUNTIME_LOGIN_RATE_LIMIT_COOLDOWN_SECONDS`：触发后的冷却时间，默认 `900` 秒。
- `RUNTIME_LOGIN_RATE_LIMIT_MAX_KEYS`：最多保存的账号/来源桶数量，默认 `4096`。

达到阈值返回稳定错误码 `RATE_LIMITED`；成功登录只清理同一账号和同一 TCP 对端的桶。容量达到上限时优先清理已过期且未封禁的桶；如果剩余桶仍受保护，则对新主体失败关闭，不能淘汰活跃封禁桶来绕过限流。

登录页 GET 只渲染页面，不计入失败次数；登录以外的方法返回 `405`。预登录 CSRF token 绑定有界、带锁的 HttpOnly/SameSite 预登录 Cookie，并且一次性消费；跨浏览器、过期、重放和 query token 均拒绝。

高频登录失败、CSRF、路径拒绝、旧路由拒绝和 Mobile Review token 拒绝的审计日志先按事件类型进行进程级窗口限速，再生成聚合摘要；主体哈希不能通过高基数轮换绕过全局上限。内存审计队列和限流桶均有容量上限。

`/tasks?task_tab=automation` 为只读 GET；数据库初始化/迁移不在 GET 请求中执行，缺失数据库不会被请求自动创建。

`/runtime/logout` 只允许 POST 执行注销并要求 Session CSRF；GET、PUT、PATCH、DELETE 均返回 405，不改变 Session、审计或 Cookie。`/business-inputs` 的 GET 只读已有文件，缺失业务工作簿返回空态，不执行 ensure/create；业务映射创建只能通过受保护的 POST。

## 6. 旧版 Web 路由安全开关

旧版 `/`、`/tables`、`/execution`、`/manual-intervention` 路由默认关闭。生产环境未配置 `PRA_PROXY_MODE` 时按 `reverse_proxy` 处理并拒绝访问；cpolar、Nginx 或其他反向代理/公网隧道场景也必须保持 `reverse_proxy`，不能启用旧路由。

只有同时满足以下条件时，旧路由才会进入后台 Session 校验：

- `PRA_ENV` 未配置时按 `production` 处理。
- `PRA_ENABLE_LEGACY_WEB=1`。
- `PRA_LEGACY_ACCESS_MODE=direct_loopback`。
- `PRA_PROXY_MODE=none`，且服务启动时只绑定 `127.0.0.1` 或 `::1`；启动绑定地址由 `serve` 的启动配置记录并作为唯一权威来源。
- 请求级 `PRA_LISTEN_HOST`、`SERVER_ADDR` 等字段不会被用于证明服务仅监听 loopback；服务没有启动绑定上下文时旧路由保持关闭。
- 请求 TCP 对端为 `127.0.0.1` 或 `::1`，并且不存在 `Forwarded`、`X-Forwarded-For`、`X-Real-IP` 转发头。
- 后台运行态 Session 已认证。

转发头不会参与旧路由放行判断；在 `direct_loopback` 模式出现时会记录拓扑异常并拒绝请求。Mobile Review token 路由不套用上述后台 Session。

## 7. 启动方式

配置好 `scripts/local_env.ps1` 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1
```

该脚本只启动 Web，不再随 Web 启停 Queue Service、Worker 或 Automation。需要独立运行 Queue
Service 时，另开终端执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_local_services.ps1
```

停止或重启 Web 不会结束由该脚本启动的后台服务；后台进程仍按各自生命周期和恢复手册管理。

默认启动地址：

```text
http://127.0.0.1:8765
```

可以指定端口：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1 -Port 8877
```

只检查环境变量：

```powershell
python scripts/check_runtime_env.py
```

## 7.1 影刀 OpenAPI Runner 变量

这些变量用于 `ShadowBotExecutor` 通过影刀开放 API `JOB运行/启动应用` 启动 `test2` 或后续正式影刀应用。真实密钥只允许写入本机 `scripts/local_env.ps1`，不得提交到仓库。

### `SHADOWBOT_RUNNER_TYPE`

可选值：

- `filequeue`：默认生产候选值，将请求和 SHA-256 checksum 原子发布到 `SHADOWBOT_QUEUE_DIR`。
- `filedrop`：`filequeue` 的兼容名称；`SHADOWBOT_REQUEST_DIR` 仅作为旧配置回退。
- `yingdao_openapi`：调用影刀开放 API 启动应用，返回 `yingdao-job:{jobUuid}`。

### `SHADOWBOT_QUEUE_DIR`

用途：常驻 Worker、Result Importer 和 Queue Watchdog 共用的队列根目录。同机部署建议使用本地 NTFS 绝对路径，跨机器部署使用 UNC。

### `SHADOWBOT_EVIDENCE_DIR`

用途：PRA 与机器人均可访问的证据目录。当前环境使用 `\\LAPTOP-O9O76RQV\pra-evidence`。

### `SHADOWBOT_WORKER_POLL_SECONDS` / `SHADOWBOT_WORKER_MAX_HOURS` / `SHADOWBOT_WORKER_MAX_TASKS`

用途：控制影刀 Worker 轮询间隔和有界常驻生命周期；默认分别为 3 秒、8 小时和 50 个任务。

### `YINGDAO_API_BASE_URL`

用途：影刀开放 API 根地址。

默认值：`https://api.yingdao.com`

专有云企业应改为对应专有云地址。

### `YINGDAO_ACCESS_KEY_ID` / `YINGDAO_ACCESS_KEY_SECRET`

用途：调用 `/oapi/token/v2/token/create` 获取 accessToken。

要求：

- `SHADOWBOT_RUNNER_TYPE=yingdao_openapi` 时必填。
- 必须由具备调度权限的影刀企业管理员在控制台创建。
- 不得提交到 git。

### `YINGDAO_ROBOT_UUID`

用途：影刀应用 UUID，即开放 API `job/start` 的 `robotUuid`。

要求：

- `SHADOWBOT_RUNNER_TYPE=yingdao_openapi` 时必填。
- 在影刀控制台应用详情中复制。

### `YINGDAO_ACCOUNT_NAME` / `YINGDAO_ROBOT_CLIENT_GROUP_UUID`

用途：指定执行机器人账号或机器人分组。

要求：

- 二者至少填写一个。
- 二者同时填写时，代码优先使用 `YINGDAO_ROBOT_CLIENT_GROUP_UUID`。
- 对应机器人需要处于可调度状态。

### `YINGDAO_REQUEST_PARAM_NAME`

用途：传递完整 PRA 请求 JSON 的影刀主流程字符串参数名。

默认值：`request_json`

影刀应用中应创建同名字符串入参；如果主流程暂时读取扁平字段，也可保留默认值并使用下面的扁平字段开关。

影刀应用运行完成后，建议输出字符串参数 `shadowbot_result_json`，内容为规范化 ShadowBot 结果 JSON。PRA 可通过：

```powershell
python scripts/run_shadowbot_executor.py poll-yingdao-result --job-uuid <jobUuid>
```

查询影刀 `job/query` 并导入该出参。若实际出参名称不同，使用 `--result-param-name` 指定。

配置真实影刀 OpenAPI 变量后，启动真实 job 前先做只读参数预检：

```powershell
python scripts/run_shadowbot_executor.py check-yingdao-app-params
```

该命令会查询影刀 `queryRobotParam`，确认应用主流程存在入参 `request_json` 和出参 `shadowbot_result_json`；不会启动影刀应用。

首条链路建议先准备但不启动：

```powershell
python scripts/prepare_shadowbot_e2e_chain.py --platform "蚂蚁花团供应商" --sku "SKU-AISHA-C" --product-name "艾莎" --grade "C级" --expected-old-price "19.00" --target-price "19.50"
```

确认 Web 执行日志和准备数据无误后，再加 `--start` 触发配置的 runner。

实机前可用本地三分支演练确认 Web 展示和审计链路：

```powershell
python scripts/run_shadowbot_e2e_local_demo.py --runtime-db data/runtime/shadowbot_e2e_demo.sqlite3 --request-dir data/runtime/shadowbot_demo_requests
```

真实启动前可先做离线就绪检查；该命令只报告 runner 必需环境变量和 runtime DB 状态，不启动影刀、不访问影刀 OpenAPI，也不会输出密钥明文：

```powershell
python scripts/check_shadowbot_readiness.py
```

### `YINGDAO_INCLUDE_FLAT_PARAMS`

用途：是否同时传入 `operation_id`、`execution_attempt_id`、`execution_mode`、`target_price`、`product_sku` 等扁平字符串参数。

默认值：`1`

设置为 `0`、`false` 或 `no` 时只传完整 `request_json`。

### `YINGDAO_WAIT_TIMEOUT_SECONDS` / `YINGDAO_RUN_TIMEOUT_SECONDS` / `YINGDAO_PRIORITY`

用途：映射到影刀 `job/start` 的排队等待时间、应用运行超时和排队优先级。

默认值：

- `YINGDAO_WAIT_TIMEOUT_SECONDS=600`
- `YINGDAO_RUN_TIMEOUT_SECONDS=600`
- `YINGDAO_PRIORITY=middle`

## 6.2 手动测试飞书通知

启动 Web 后台并登录后，进入：

```text
/system
```

点击“发送飞书测试通知”可手动验证：

- `FeishuWebhookNotificationSender`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_WEBHOOK_SECRET` 签名配置
- 当前网络是否能访问飞书 Webhook

该测试具有以下边界：

- 不创建业务 `review_task`。
- 不创建 `review_token`。
- 不生成 `mobile_review_url`。
- 不改变 `tasks / review_tasks` 状态。
- 不写 `task_status_history`。
- 会写入一条 `notification_logs` 系统测试记录：
  - `recipient_type = system`
  - `recipient = system_test`
  - `related_task_id = null`
  - `related_review_task_id = null`
  - `message = PRA system test notification`

页面和日志仍不得展示完整 webhook、secret、token、mobile review URL 或 runtime DB 完整路径。

## 7. 不得提交到 git 的内容

以下内容都属于敏感或运行态数据，不得提交：

- `scripts/local_env.ps1`
- `.env`
- `.env.*`
- `REVIEW_TOKEN_SECRET`
- `RUNTIME_ADMIN_PASSWORD`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_WEBHOOK_SECRET`
- 带 `token=` 的 mobile review URL
- `data/runtime/`
- `*.sqlite3`
- `*.db`

## 8. 当前阶段边界

- 飞书只负责通知，不负责审批。
- 审批仍通过 mobile review 页面完成。
- 当前飞书消息默认使用 `post` 富文本消息，并可通过 `FEISHU_MESSAGE_TYPE=text` 回退到纯文本消息。
- `post` 消息中只展示简短摘要：复核类型、交易日、对象、截止时间、原因和“👉 点击处理复核”链接；完整上下文仍以 mobile review 页面为准。
- 当前不实现飞书交互卡片、按钮审批、回调接口、OAuth 或复杂重试队列。
