# 运行态环境变量说明

本文档说明本地运行 Web 后台、Mobile Review、飞书 Webhook 通知时需要配置的环境变量。

真实密钥、Webhook、密码不要写入仓库。推荐把真实配置放在 `scripts/local_env.ps1`，该文件已加入 `.gitignore`。

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
- 地址需要能从手机访问到本地 Web 服务。
- 结尾是否带 `/` 均可，系统会处理。
- 不建议提交真实固定地址到 git。

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

## 6. 启动方式

配置好 `scripts/local_env.ps1` 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1
```

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
