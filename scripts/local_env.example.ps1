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

# ShadowBot local queue. Start test2 manually, then run the PRA queue services process.
$env:SHADOWBOT_RUNNER_TYPE = "filequeue"
$env:SHADOWBOT_QUEUE_DIR = "D:\PRA_Runtime\shadowbot_queue"
$env:SHADOWBOT_EVIDENCE_DIR = "\\LAPTOP-O9O76RQV\pra-evidence"
$env:SHADOWBOT_WORKER_POLL_SECONDS = "3"
$env:SHADOWBOT_WORKER_MAX_HOURS = "8"
$env:SHADOWBOT_WORKER_MAX_TASKS = "50"
$env:SHADOWBOT_RUNNER_COMMAND = ""

$env:YINGDAO_API_BASE_URL = "https://api.yingdao.com"
$env:YINGDAO_ACCESS_KEY_ID = ""
$env:YINGDAO_ACCESS_KEY_SECRET = ""
$env:YINGDAO_ROBOT_UUID = ""
$env:YINGDAO_ACCOUNT_NAME = ""
$env:YINGDAO_ROBOT_CLIENT_GROUP_UUID = ""
$env:YINGDAO_REQUEST_PARAM_NAME = "request_json"
$env:YINGDAO_INCLUDE_FLAT_PARAMS = "1"
$env:YINGDAO_WAIT_TIMEOUT_SECONDS = "600"
$env:YINGDAO_RUN_TIMEOUT_SECONDS = "600"
$env:YINGDAO_PRIORITY = "middle"
