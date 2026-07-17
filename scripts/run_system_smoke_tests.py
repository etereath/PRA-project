from __future__ import annotations

import argparse
import io
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus  # noqa: E402
from app.exceptions import ValidationError  # noqa: E402
from app.models import Task  # noqa: E402
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository  # noqa: E402
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION, REQUIRED_RUNTIME_TABLES  # noqa: E402
from app.services.runtime import (  # noqa: E402
    NotificationLogService,
    ReviewTaskService,
    ReviewTokenService,
    RuntimeTaskService,
)
from app.web import _RUNTIME_SESSIONS, application  # noqa: E402


TEST_DB = ROOT / "data" / "runtime" / "test_runtime_smoke.sqlite3"
TEST_DB_PARENT = TEST_DB.parent.resolve()
REQUIRED_TABLES = set(REQUIRED_RUNTIME_TABLES)
SMOKE_SECRET = "smoke-review-token-secret-32-chars-minimum"
SMOKE_ADMIN_PASSWORD = "smoke-admin-password-only-for-local-smoke"
SMOKE_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/smoke-placeholder"


class SmokeContext:
    def __init__(self) -> None:
        self.repository = SQLiteRuntimeRepository(TEST_DB)
        self.task_service = RuntimeTaskService(self.repository)
        self.review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        self.token_service = ReviewTokenService(self.repository)
        self.task_id = ""
        self.review_task_id = ""
        self.notification_id = ""
        self.token_review_task_id = ""
        self.raw_token = ""
        self.token_id = ""


class SmokeRunner:
    def __init__(self) -> None:
        self.context = SmokeContext()
        self.results: list[tuple[str, bool, str, str]] = []

    def run(self) -> int:
        checks: list[tuple[str, str, Callable[[], None]]] = [
            ("runtime DB 初始化成功", "SQLiteRuntimeRepository / RuntimeTaskService.init_schema", self.check_init_db),
            ("schema version exact v6", "runtime_schema_migrations", self.check_schema_version),
            ("关键运行态表存在", "SQLite schema", self.check_required_tables),
            ("v6 Outbox/RetryAuthorization 结构完整", "runtime schema health check", self.check_schema_integrity),
            ("创建 runtime task", "RuntimeTaskService.create_tasks", self.check_create_task),
            ("dedupe_key 去重有效", "tasks partial unique index", self.check_task_dedupe),
            ("创建 pending review_task", "ReviewTaskService.create_from_tasks", self.check_create_review_task),
            ("pending review_task 仅写入待发送 Outbox", "OutboxReviewNotificationService", self.check_mock_notification),
            ("notification_logs.message 不泄露 token 或 mobile review URL", "NotificationLogService / ReviewNotificationService", self.check_notification_message_safe),
            ("Web/session approved 复核可推动 source task", "ReviewTaskService / RuntimeTaskService", self.check_web_session_approve),
            ("task_status_history 写入", "RuntimeTaskService.change_status", self.check_status_history),
            ("非 pending review 重复处理失败", "ReviewTaskService.resolve_review_task", self.check_repeat_review_rejected),
            ("review token 创建、校验、使用后失效", "ReviewTokenService", self.check_review_token_lifecycle),
            ("expired token 或重复 token 使用失败", "ReviewTokenService.validate_token", self.check_expired_or_reused_token_invalid),
            ("运行态 Web 页面登录保护不泄露数据", "app.web session guard", self.check_web_login_protection),
            ("/system 不展示敏感信息", "app.web render_system_page", self.check_system_page_safe),
        ]
        for name, module, check in checks:
            self._run_check(name, module, check)
        passed = sum(1 for _, ok, _, _ in self.results if ok)
        failed = len(self.results) - passed
        print("")
        print(f"系统冒烟测试完成：通过 {passed} 项，失败 {failed} 项。")
        return 1 if failed else 0

    def _run_check(self, name: str, module: str, check: Callable[[], None]) -> None:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - smoke script should continue after failures.
            reason = sanitize_text(str(exc) or type(exc).__name__)
            self.results.append((name, False, reason, module))
            print(f"[FAILED] {name}")
            print(f"  原因：{reason}")
            print(f"  建议检查模块：{module}")
        else:
            self.results.append((name, True, "", module))
            print(f"[OK] {name}")

    def check_init_db(self) -> None:
        ensure_clean_test_db()
        self.context.task_service.init_schema()
        if not TEST_DB.exists():
            raise AssertionError("测试运行态数据库未创建")

    def check_schema_version(self) -> None:
        versions = self.context.repository.schema_versions()
        expected = list(range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1))
        if versions != expected:
            raise AssertionError(f"schema version 不满足精确 v{LATEST_RUNTIME_SCHEMA_VERSION} 要求：{versions}")

    def check_required_tables(self) -> None:
        with closing(self.context.repository.connect()) as connection:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        tables = {str(row["name"]) for row in rows}
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise AssertionError(f"缺少关键表：{', '.join(missing)}")

    def check_schema_integrity(self) -> None:
        health = self.context.repository.check_schema_health()
        if not health.ok:
            raise AssertionError(health.summary)

    def check_create_task(self) -> None:
        task = runtime_task("SMOKE-TASK-APPROVE", status=TaskStatus.MANUAL_REVIEW)
        inserted = self.context.task_service.create_tasks([task])
        if inserted != 1:
            raise AssertionError(f"期望写入 1 条任务，实际 {inserted}")
        stored = self.context.task_service.get_task(task.task_id)
        if stored is None:
            raise AssertionError("写入后未能读取任务")
        self.context.task_id = task.task_id

    def check_task_dedupe(self) -> None:
        first = runtime_task("SMOKE-TASK-DEDUPE-1", status=TaskStatus.MANUAL_REVIEW)
        duplicate = runtime_task("SMOKE-TASK-DEDUPE-2", status=TaskStatus.MANUAL_REVIEW)
        duplicate.dedupe_key = first.dedupe_key
        inserted_first = self.context.task_service.create_tasks([first])
        inserted_duplicate = self.context.task_service.create_tasks([duplicate])
        if inserted_first != 1 or inserted_duplicate != 0:
            raise AssertionError(
                f"dedupe 结果异常：first={inserted_first}, duplicate={inserted_duplicate}"
            )

    def check_create_review_task(self) -> None:
        task = self.context.task_service.get_task(self.context.task_id)
        if task is None:
            raise AssertionError("source task 不存在")
        summary = self.context.review_service.create_from_tasks([task])
        if summary.inserted_review_tasks_count != 1:
            raise AssertionError(f"期望创建 1 条复核任务，实际 {summary.inserted_review_tasks_count}")
        if not summary.review_tasks:
            raise AssertionError("复核任务创建摘要为空")
        review = summary.review_tasks[0]
        if review.review_status != ReviewTaskStatus.PENDING:
            raise AssertionError(f"复核任务状态不是 pending：{review.review_status.value}")
        self.context.review_task_id = review.review_task_id

    def check_mock_notification(self) -> None:
        logs = NotificationLogService(self.context.repository).list_logs(
            related_review_task_id=self.context.review_task_id
        )
        if len(logs) != 1:
            raise AssertionError(f"期望 1 条通知记录，实际 {len(logs)}")
        log = logs[0]
        if log.channel != "mock":
            raise AssertionError(f"通知 channel 应为 mock，实际 {log.channel}")
        if log.send_status != "pending":
            raise AssertionError(f"业务创建路径的 mock 通知应保持 pending，实际 {log.send_status}")
        outbox = self.context.repository.get_notification_outbox(log.notification_id)
        if outbox is None or outbox.status != "PENDING":
            raise AssertionError("业务创建路径不得自动执行 FakeSender 或将 Outbox 标记为 SENT")
        self.context.notification_id = log.notification_id

    def check_notification_message_safe(self) -> None:
        logs = NotificationLogService(self.context.repository).list_logs()
        if not logs:
            raise AssertionError("未找到通知记录")
        unsafe = [
            log.notification_id
            for log in logs
            if contains_forbidden_secret_text(log.message) or contains_forbidden_secret_text(log.error_message)
        ]
        if unsafe:
            raise AssertionError(f"通知日志包含敏感链接或 token 痕迹：{', '.join(unsafe)}")

    def check_web_session_approve(self) -> None:
        resolved = self.context.review_service.resolve_review_task(
            review_task_id=self.context.review_task_id,
            status=ReviewTaskStatus.APPROVED,
            actor="admin",
            actor_source="web_session",
            note="smoke approved by web session",
            resolution_payload={"smoke": True},
            source_task_status=TaskStatus.PENDING,
        )
        if resolved.review_status != ReviewTaskStatus.APPROVED:
            raise AssertionError("复核任务未变为 approved")
        task = self.context.task_service.get_task(self.context.task_id)
        if task is None or task.task_status != TaskStatus.PENDING:
            raise AssertionError("源任务未被推动到 pending")

    def check_status_history(self) -> None:
        history = self.context.task_service.list_status_history(self.context.task_id)
        if not history:
            raise AssertionError("未写入 task_status_history")
        if not any(item.to_status == TaskStatus.PENDING for item in history):
            raise AssertionError("状态历史中没有 manual_review -> pending 结果")
        metadata = history[-1].metadata
        if metadata.get("actor_source") != "web_session":
            raise AssertionError("状态历史 metadata 未记录 web_session 来源")

    def check_repeat_review_rejected(self) -> None:
        try:
            self.context.review_service.resolve_review_task(
                review_task_id=self.context.review_task_id,
                status=ReviewTaskStatus.APPROVED,
                actor="admin",
                actor_source="web_session",
                note="repeat should fail",
                source_task_status=TaskStatus.PENDING,
            )
        except ValidationError:
            return
        raise AssertionError("非 pending review 重复处理未被拒绝")

    def check_review_token_lifecycle(self) -> None:
        review = self._create_pending_review("SMOKE-TOKEN-TASK", "smoke token lifecycle")
        token_result = self.context.token_service.create_token(
            review.review_task_id,
            token_subject="smoke_mobile_user",
            created_by="smoke",
        )
        self.context.token_review_task_id = review.review_task_id
        self.context.raw_token = token_result.raw_token
        self.context.token_id = token_result.review_token.token_id
        stored = self.context.repository.get_review_token(token_result.review_token.token_id)
        if stored is None:
            raise AssertionError("token 未入库")
        if token_result.raw_token in stored.token_hash:
            raise AssertionError("token_hash 中疑似保存了明文 raw token")
        validation = self.context.token_service.validate_token(
            review.review_task_id,
            token_result.raw_token,
            ReviewTaskStatus.APPROVED.value,
        )
        if not validation.is_valid:
            raise AssertionError(f"token 校验失败：{validation.failure_reason}")
        self.context.token_service.record_detail_access(token_result.review_token.token_id)
        accessed = self.context.repository.get_review_token(token_result.review_token.token_id)
        if accessed is None or accessed.last_used_at is None or accessed.used_at is not None:
            raise AssertionError("详情访问应只更新 last_used_at，不应写 used_at")
        self.context.token_service.record_resolve_usage(token_result.review_token.token_id)
        used = self.context.repository.get_review_token(token_result.review_token.token_id)
        if used is None or used.used_at is None or used.last_used_at is None:
            raise AssertionError("resolve 使用后未写入 used_at / last_used_at")

    def check_expired_or_reused_token_invalid(self) -> None:
        reused = self.context.token_service.validate_token(
            self.context.token_review_task_id,
            self.context.raw_token,
            ReviewTaskStatus.APPROVED.value,
        )
        if reused.is_valid:
            raise AssertionError("used_at 非空后 token 仍可再次校验通过")
        review = self._create_pending_review("SMOKE-EXPIRED-TOKEN-TASK", "smoke expired token")
        expired = self.context.token_service.create_token(
            review.review_task_id,
            token_subject="smoke_expired_user",
            expires_at=datetime.now() - timedelta(minutes=1),
            created_by="smoke",
        )
        validation = self.context.token_service.validate_token(
            review.review_task_id,
            expired.raw_token,
            ReviewTaskStatus.APPROVED.value,
        )
        if validation.is_valid:
            raise AssertionError("已过期 token 仍可校验通过")

    def check_web_login_protection(self) -> None:
        sensitive_markers = [
            self.context.task_id,
            self.context.review_task_id,
            self.context.notification_id,
        ]
        for path in ["/dashboard", "/tasks", "/reviews", "/notifications", "/system"]:
            status, _, body = call_app(path=path, query=urlencode({"runtime_db": str(TEST_DB)}))
            if not status.startswith("200"):
                raise AssertionError(f"{path} 未登录访问未返回 200：{status}")
            if "token=" in body:
                raise AssertionError(f"{path} 未登录页面泄露 token= 痕迹")
            leaked = [marker for marker in sensitive_markers if marker and marker in body]
            if leaked:
                raise AssertionError(f"{path} 未登录页面泄露运行态数据：{', '.join(leaked)}")
        for path in ["/", "/tables", "/execution", "/manual-intervention"]:
            status, _, body = call_app(path=path)
            if status != "403 Forbidden":
                raise AssertionError(f"{path} 旧路由默认未安全关闭：{status}")
            if "旧版 Web 路由当前已安全关闭" not in body:
                raise AssertionError(f"{path} 旧路由关闭提示缺失")

    def check_system_page_safe(self) -> None:
        _RUNTIME_SESSIONS.clear()
        _, login_headers, login_page = call_app(
            path="/runtime/login",
            query=urlencode({"runtime_db": str(TEST_DB)}),
        )
        login_csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page)
        if login_csrf_match is None:
            raise AssertionError("登录页未提供一次性 CSRF token")
        preauth_cookie = login_headers.get("Set-Cookie", "").split(";", 1)[0]
        login_body = urlencode(
            {
                "runtime_db": str(TEST_DB),
                "username": os.environ["RUNTIME_ADMIN_USER"],
                "password": os.environ["RUNTIME_ADMIN_PASSWORD"],
                "next": "/system",
                "csrf_token": login_csrf_match.group(1),
            }
        )
        status, headers, _ = call_app(
            path="/runtime/login",
            method="POST",
            cookie=preauth_cookie,
            body=login_body,
        )
        if status != "303 See Other":
            raise AssertionError(f"登录失败：{status}")
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = call_app(path="/system", query=urlencode({"runtime_db": str(TEST_DB)}), cookie=cookie)
        if not status.startswith("200"):
            raise AssertionError(f"/system 登录后访问失败：{status}")
        forbidden_values = [
            SMOKE_SECRET,
            SMOKE_ADMIN_PASSWORD,
            os.environ["FEISHU_WEBHOOK_SECRET"],
            SMOKE_WEBHOOK,
            str(TEST_DB),
        ]
        leaked = [value for value in forbidden_values if value and value in body]
        if leaked:
            raise AssertionError("系统检查页展示了敏感值或完整本地路径")
        if contains_forbidden_secret_text(body):
            raise AssertionError("系统检查页包含 token、完整 mobile review URL 或敏感链接痕迹")

    def _create_pending_review(self, task_id: str, result_message: str):
        task = runtime_task(task_id, status=TaskStatus.MANUAL_REVIEW, result_message=result_message)
        inserted = self.context.task_service.create_tasks([task])
        if inserted != 1:
            raise AssertionError(f"无法创建 token 测试源任务：{task_id}")
        summary = self.context.review_service.create_from_tasks([task])
        if summary.inserted_review_tasks_count != 1 or not summary.review_tasks:
            raise AssertionError(f"无法创建 token 测试复核任务：{task_id}")
        return summary.review_tasks[0]


def runtime_task(
    task_id: str,
    *,
    status: TaskStatus,
    result_message: str = "smoke manual review required",
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=TaskActionType.MANUAL_REVIEW,
        priority=2,
        task_status=status,
        created_at=datetime(2026, 5, 7, 9, 0),
        trade_date=date(2026, 5, 7),
        scope_type="global",
        scope_key="smoke",
        dedupe_key=f"2026-05-07|global|smoke|manual_review|{task_id}",
        decision_trace={"smoke": True, "task_id": task_id},
        result_message=result_message,
        required_by=datetime.now() + timedelta(hours=4),
    )


def ensure_clean_test_db() -> None:
    resolved = TEST_DB.resolve()
    expected_parent = TEST_DB_PARENT
    if resolved.parent != expected_parent or resolved.name != "test_runtime_smoke.sqlite3":
        raise RuntimeError("测试库路径保护失败，拒绝清理")
    if resolved.exists():
        resolved.unlink()
    resolved.parent.mkdir(parents=True, exist_ok=True)


def call_app(
    *,
    path: str,
    method: str = "GET",
    query: str = "",
    body: str = "",
    cookie: str = "",
) -> tuple[str, dict[str, str], str]:
    captured: dict[str, object] = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    payload = body.encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(payload)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": io.BytesIO(payload),
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    response = application(environ, start_response)
    response_body = b"".join(response).decode("utf-8", errors="replace")
    headers: dict[str, str] = {}
    for name, value in captured.get("headers", []):
        headers.setdefault(name, value)
    return str(captured.get("status", "")), headers, response_body


def contains_forbidden_secret_text(text: str) -> bool:
    lowered = text.lower()
    if "token=" in lowered:
        return True
    if re.search(r"/mobile/review/[^\s\"'<>]+", text):
        return True
    if re.search(r"https?://[^\s\"'<>]+/mobile/review/[^\s\"'<>]+", text, flags=re.IGNORECASE):
        return True
    if "open.feishu.cn/open-apis/bot" in lowered:
        return True
    return False


def sanitize_text(text: str) -> str:
    sanitized = text
    sanitized = sanitized.replace(str(TEST_DB), "<test_runtime_db>")
    sanitized = sanitized.replace(SMOKE_SECRET, "<review_token_secret>")
    sanitized = sanitized.replace(SMOKE_ADMIN_PASSWORD, "<runtime_admin_password>")
    sanitized = sanitized.replace(SMOKE_WEBHOOK, "<webhook_redacted>")
    sanitized = re.sub(r"token=[^\s&\"'<>]+", "token=***", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"/mobile/review/[^\s\"'<>]+", "[mobile_review_url_redacted]", sanitized)
    sanitized = re.sub(r"https?://[^\s\"'<>]*open\.feishu\.cn[^\s\"'<>]*", "[webhook_redacted]", sanitized)
    return sanitized


@contextmanager
def smoke_environment():
    keys = [
        "DEFAULT_NOTIFICATION_CHANNEL",
        "DEFAULT_NOTIFICATION_RECIPIENT_TYPE",
        "DEFAULT_NOTIFICATION_RECIPIENT",
        "ENABLE_MOBILE_REVIEW_URL_IN_NOTIFICATION",
        "REVIEW_TOKEN_SECRET",
        "MOBILE_REVIEW_BASE_URL",
        "RUNTIME_ADMIN_USER",
        "RUNTIME_ADMIN_PASSWORD",
        "FEISHU_WEBHOOK_URL",
        "FEISHU_WEBHOOK_SECRET",
        "FEISHU_MESSAGE_TYPE",
        "DEV_MODE",
        "PRA_ENV",
        "PRA_ENABLE_LEGACY_WEB",
        "PRA_LEGACY_ACCESS_MODE",
        "PRA_PROXY_MODE",
        "PRA_ALLOWED_DATA_DIRS",
    ]
    original = {key: os.environ.get(key) for key in keys}
    os.environ.update(
        {
            "DEFAULT_NOTIFICATION_CHANNEL": "mock",
            "DEFAULT_NOTIFICATION_RECIPIENT_TYPE": "role",
            "DEFAULT_NOTIFICATION_RECIPIENT": "operations",
            "ENABLE_MOBILE_REVIEW_URL_IN_NOTIFICATION": "false",
            "REVIEW_TOKEN_SECRET": SMOKE_SECRET,
            "MOBILE_REVIEW_BASE_URL": "https://smoke.example.invalid",
            "RUNTIME_ADMIN_USER": "admin",
            "RUNTIME_ADMIN_PASSWORD": SMOKE_ADMIN_PASSWORD,
            "FEISHU_WEBHOOK_URL": SMOKE_WEBHOOK,
            "FEISHU_WEBHOOK_SECRET": "smoke-feishu-signature-secret",
            "FEISHU_MESSAGE_TYPE": "post",
            "DEV_MODE": "false",
            "PRA_ENV": os.environ.get("PRA_ENV") or "production",
            "PRA_ENABLE_LEGACY_WEB": "0",
            "PRA_LEGACY_ACCESS_MODE": "direct_loopback",
            "PRA_PROXY_MODE": "reverse_proxy",
            "PRA_ALLOWED_DATA_DIRS": str(TEST_DB.parent),
        }
    )
    _RUNTIME_SESSIONS.clear()
    try:
        yield
    finally:
        _RUNTIME_SESSIONS.clear()
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the PRA core system smoke checks")
    parser.add_argument(
        "--temporary-db",
        action="store_true",
        help="Create the smoke SQLite database under the operating system temporary directory",
    )
    return parser


def _run_smoke(database_label: str) -> int:
    print("PRA 系统冒烟测试")
    print(f"测试库：{database_label}")
    print("通知模式：mock，不发送真实飞书，不访问 cpolar。")
    print("")
    with smoke_environment():
        return SmokeRunner().run()


def main() -> int:
    args = _build_parser().parse_args()
    if not args.temporary_db:
        return _run_smoke("data/runtime/test_runtime_smoke.sqlite3")

    global TEST_DB, TEST_DB_PARENT
    with tempfile.TemporaryDirectory(prefix="pra-core-smoke-") as temp_dir:
        TEST_DB = Path(temp_dir) / "test_runtime_smoke.sqlite3"
        TEST_DB_PARENT = TEST_DB.parent.resolve()
        return _run_smoke("操作系统临时目录（运行结束自动清理）")


if __name__ == "__main__":
    raise SystemExit(main())
