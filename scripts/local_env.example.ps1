# Copy this file to scripts/local_env.ps1 and replace placeholder values.
# scripts/local_env.ps1 is intentionally ignored by git.

$env:DEFAULT_NOTIFICATION_CHANNEL = "feishu"
$env:MOBILE_REVIEW_BASE_URL = "https://your-fixed-domain.cpolar.cn"
$env:REVIEW_TOKEN_SECRET = "replace-with-a-long-random-secret-at-least-32-chars"

$env:FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/replace-me"
$env:FEISHU_WEBHOOK_SECRET = "replace-with-feishu-signing-secret-if-enabled"
$env:FEISHU_MESSAGE_TYPE = "post"
$env:FEISHU_WEBHOOK_TIMEOUT_SECONDS = "5"

$env:RUNTIME_ADMIN_USER = "admin"
$env:RUNTIME_ADMIN_PASSWORD = "replace-with-a-strong-password"
$env:DEV_MODE = "false"
