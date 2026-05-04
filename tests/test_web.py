from __future__ import annotations

import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from unittest.mock import patch

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import ReviewTaskService, ReviewTokenService, RuntimeTaskService
from app.web import (
    TABLE_OPTIONS,
    _RUNTIME_SESSIONS,
    _resolve_table_path,
    application,
    default_dashboard_state,
    default_execution_state,
    default_manual_state,
    default_runtime_state,
    default_table_editor_state,
    render_dashboard_page,
    render_execution_page,
    render_manual_intervention_page,
    render_runtime_page,
    render_table_editor_page,
)


def _runtime_task(
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.PENDING,
    trade_date: date = date(2026, 5, 4),
    action_type: TaskActionType = TaskActionType.MANUAL_PRICE_REVIEW,
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=action_type,
        priority=2,
        task_status=status,
        created_at=datetime(2026, 5, 4, 9, 0),
        trade_date=trade_date,
        scope_type="global",
        scope_key=trade_date.isoformat(),
        dedupe_key=f"{trade_date.isoformat()}|global|{trade_date.isoformat()}|{action_type.value}|{task_id}",
        decision_trace={"reason": "manual review"},
        result_message="需要人工复核",
    )


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        _RUNTIME_SESSIONS.clear()

    def _call_app(
        self,
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
        response_body = b"".join(response).decode("utf-8")
        headers = {name: value for name, value in captured["headers"]}
        return str(captured["status"]), headers, response_body

    def test_render_dashboard_page_contains_console_title(self) -> None:
        html = render_dashboard_page(
            params=default_dashboard_state(),
            message="ok",
            message_level="success",
            validation_summary=None,
            generation_summary=None,
            preview_ready=False,
        )
        self.assertIn("管理台", html)
        self.assertIn("任务生成", html)
        self.assertIn("库存策略", html)

    def test_render_table_editor_contains_management_ui(self) -> None:
        html = render_table_editor_page(
            params=default_table_editor_state(),
            headers=["internal_sku", "product_name"],
            records=[{"internal_sku": "SKU-001", "product_name": "rose"}],
            message="ok",
            message_level="success",
            table_issues=[],
        )
        self.assertIn("Excel", html)
        self.assertIn("保存当前修改", html)
        self.assertIn("内部 SKU", html)
        self.assertIn("商品名称", html)
        self.assertIn("SKU-001", html)

    def test_new_predictive_tables_have_chinese_labels(self) -> None:
        self.assertIn("harvest_forecasts", TABLE_OPTIONS)
        html = render_table_editor_page(
            params={"table_name": "harvest_forecasts", "table_path": "data/samples/harvest_forecasts.xlsx"},
            headers=["forecast_id", "target_trade_date", "predicted_harvest_qty"],
            records=[],
            message="ok",
            message_level="success",
            table_issues=[],
        )
        self.assertIn("产量预测表", html)
        self.assertIn("目标交易日", html)
        self.assertIn("预测采收量", html)

    def test_switching_table_uses_new_default_path_when_old_default_was_posted(self) -> None:
        resolved = _resolve_table_path(
            table_name="listing_rules",
            previous_table_name="products",
            posted_path="D:/PRA project/data/samples/products.xlsx",
        )
        self.assertTrue(resolved.endswith("data\\samples\\listing_rules.xlsx"))

    def test_render_execution_page_contains_form(self) -> None:
        html = render_execution_page(
            params=default_execution_state(),
            message="ok",
            message_level="success",
            execution_summary=None,
        )
        self.assertIn("模拟执行", html)
        self.assertIn("mock_executor", html)

    def test_manual_intervention_page_is_read_only_compatible(self) -> None:
        html = render_manual_intervention_page(
            params=default_manual_state(),
            message="ok",
            message_level="success",
            tasks=[],
        )
        self.assertIn("人工介入", html)
        self.assertIn("只读兼容状态", html)
        self.assertNotIn("name='decision'", html)
        self.assertNotIn("value='resolve'", html)

    def test_render_runtime_page_requires_login_when_session_missing(self) -> None:
        html = render_runtime_page(
            params=default_runtime_state(),
            message="ok",
            message_level="success",
            tasks=[],
            reviews=[],
            notifications=[],
            session_user=None,
            selected_review=None,
            selected_task=None,
            selected_notification=None,
            task_history=[],
        )
        self.assertIn("SQLite", html)
        self.assertIn("运行态登录", html)
        self.assertIn("后台密码", html)

    def test_runtime_review_flow_requires_login_and_records_session_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
            task_service.create_tasks([task])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task])
            review = review_service.list_review_tasks()[0]

            with patch.dict(
                "os.environ",
                {"RUNTIME_ADMIN_USER": "admin", "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                status, _, body = self._call_app(
                    path="/runtime",
                    method="GET",
                    query=urlencode({"runtime_db": str(db_path)}),
                )
                self.assertEqual(status, "200 OK")
                self.assertIn("需要先登录", body)

                login_status, login_headers, _ = self._call_app(
                    path="/runtime/login",
                    method="POST",
                    body=urlencode({"runtime_db": str(db_path), "username": "admin", "password": "secret"}),
                )
                self.assertEqual(login_status, "303 See Other")
                cookie = login_headers["Set-Cookie"].split(";", 1)[0]

                resolve_status, resolve_headers, _ = self._call_app(
                    path="/runtime",
                    method="POST",
                    cookie=cookie,
                    body=urlencode(
                        {
                            "action": "resolve_review",
                            "runtime_db": str(db_path),
                            "review_task_id": review.review_task_id,
                            "task_id": task.task_id,
                            "review_status": "approved",
                            "reviewer_code": "R-001",
                            "resolution_note": "web approved",
                            "resolution_payload_json": '{"adjustment":{"target_price":"8.8"}}',
                        }
                    ),
                )
                self.assertEqual(resolve_status, "303 See Other")
                location = resolve_headers["Location"]
                self.assertIn("review_task_id", location)

                detail = urlparse(location)
                detail_status, _, detail_body = self._call_app(
                    path=detail.path,
                    method="GET",
                    query=detail.query,
                    cookie=cookie,
                )
                self.assertEqual(detail_status, "200 OK")
                self.assertIn("admin", detail_body)
                self.assertIn("任务状态历史", detail_body)
                self.assertIn("notification_id", detail_body)
                self.assertIn("operations", detail_body)

                resolved_review = review_service.get_review_task(review.review_task_id)
                resolved_task = task_service.get_task(task.task_id)
                self.assertEqual(resolved_review.resolved_by, "admin")
                self.assertEqual(resolved_task.task_status, TaskStatus.PENDING)

    def test_runtime_review_repeat_submit_fails_after_first_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
            task_service.create_tasks([task])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task])
            review = review_service.list_review_tasks()[0]

            with patch.dict(
                "os.environ",
                {"RUNTIME_ADMIN_USER": "admin", "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                _, login_headers, _ = self._call_app(
                    path="/runtime/login",
                    method="POST",
                    body=urlencode({"runtime_db": str(db_path), "username": "admin", "password": "secret"}),
                )
                cookie = login_headers["Set-Cookie"].split(";", 1)[0]
                resolve_body = urlencode(
                    {
                        "action": "resolve_review",
                        "runtime_db": str(db_path),
                        "review_task_id": review.review_task_id,
                        "task_id": task.task_id,
                        "review_status": "cancelled",
                        "resolution_note": "stop it",
                        "resolution_payload_json": "{}",
                    }
                )
                first_status, _, _ = self._call_app(
                    path="/runtime",
                    method="POST",
                    cookie=cookie,
                    body=resolve_body,
                )
                self.assertEqual(first_status, "303 See Other")

                second_status, _, second_body = self._call_app(
                    path="/runtime",
                    method="POST",
                    cookie=cookie,
                    body=resolve_body,
                )
                self.assertEqual(second_status, "200 OK")
                self.assertIn("已处理", second_body)

    def test_runtime_page_supports_minimal_filters_and_notification_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task_a = _runtime_task("TASK-A", status=TaskStatus.MANUAL_REVIEW, trade_date=date(2026, 5, 4))
            task_b = _runtime_task("TASK-B", status=TaskStatus.PENDING, trade_date=date(2026, 5, 5))
            task_service.create_tasks([task_a, task_b])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task_a, task_b])
            review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
            notification = repository.list_notification_logs(related_review_task_id=review.review_task_id)[0]

            with patch.dict(
                "os.environ",
                {"RUNTIME_ADMIN_USER": "admin", "RUNTIME_ADMIN_PASSWORD": "secret"},
                clear=False,
            ):
                _, login_headers, _ = self._call_app(
                    path="/runtime/login",
                    method="POST",
                    body=urlencode({"runtime_db": str(db_path), "username": "admin", "password": "secret"}),
                )
                cookie = login_headers["Set-Cookie"].split(";", 1)[0]
                status, _, body = self._call_app(
                    path="/runtime",
                    method="GET",
                    query=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "task_trade_date": "2026-05-04",
                            "task_status": "manual_review",
                            "review_trade_date": "2026-05-04",
                            "review_status": "pending",
                            "notification_related_review_task_id": review.review_task_id,
                            "notification_send_status": "success",
                            "notification_id": notification.notification_id,
                        }
                    ),
                    cookie=cookie,
                )
                self.assertEqual(status, "200 OK")
                self.assertIn("任务筛选", body)
                self.assertIn("复核筛选", body)
                self.assertIn("通知筛选", body)
                self.assertIn("通知详情", body)
                self.assertIn(notification.notification_id, body)
                self.assertIn("send_status", body)

    def test_mobile_review_get_records_detail_access_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task = _runtime_task("TASK-M1", status=TaskStatus.MANUAL_REVIEW)
            task_service.create_tasks([task])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task])
            review = review_service.list_review_tasks()[0]

            with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "unit-test-secret"}, clear=False):
                token_result = ReviewTokenService(repository).create_token(
                    review.review_task_id,
                    token_subject="mobile_reviewer",
                )
                status, _, body = self._call_app(
                    path=f"/mobile/review/{review.review_task_id}",
                    method="GET",
                    query=urlencode({"runtime_db": str(db_path), "token": token_result.raw_token}),
                )

            self.assertEqual(status, "200 OK")
            self.assertIn("manual_price_review", body)
            self.assertIn("TASK-M1", body)
            self.assertIn("approved", body)
            stored_token = repository.get_review_token(token_result.review_token.token_id)
            self.assertIsNone(stored_token.used_at)
            self.assertIsNotNone(stored_token.last_used_at)

    def test_mobile_review_post_resolves_and_prevents_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task = _runtime_task("TASK-M2", status=TaskStatus.MANUAL_REVIEW)
            task_service.create_tasks([task])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task])
            review = review_service.list_review_tasks()[0]

            with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "unit-test-secret"}, clear=False):
                token_result = ReviewTokenService(repository).create_token(
                    review.review_task_id,
                    token_subject="mobile_reviewer",
                )
                body = urlencode(
                    {
                        "runtime_db": str(db_path),
                        "token": token_result.raw_token,
                        "action": "approved",
                        "resolution_note": "mobile approved",
                        "resolution_payload_json": '{"source":"mobile"}',
                    }
                )
                first_status, first_headers, _ = self._call_app(
                    path=f"/mobile/review/{review.review_task_id}/resolve",
                    method="POST",
                    body=body,
                )
                second_status, _, second_body = self._call_app(
                    path=f"/mobile/review/{review.review_task_id}/resolve",
                    method="POST",
                    body=body,
                )

            self.assertEqual(first_status, "303 See Other")
            self.assertIn("resolved=1", first_headers["Location"])
            self.assertEqual(second_status, "200 OK")
            self.assertIn("链接已失效或无权访问该复核任务", second_body)

            stored_token = repository.get_review_token(token_result.review_token.token_id)
            resolved_review = review_service.get_review_task(review.review_task_id)
            resolved_task = task_service.get_task(task.task_id)
            history = task_service.list_status_history(task.task_id)
            self.assertIsNotNone(stored_token.used_at)
            self.assertEqual(resolved_review.resolved_by, "mobile_reviewer")
            self.assertEqual(resolved_task.task_status, TaskStatus.PENDING)
            self.assertTrue(any(item.metadata.get("actor_source") == "mobile_review_token" for item in history))

    def test_mobile_review_post_rejects_invalid_payload_and_expired_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task = _runtime_task("TASK-M3", status=TaskStatus.MANUAL_REVIEW)
            task_service.create_tasks([task])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task])
            review = review_service.list_review_tasks()[0]

            with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "unit-test-secret"}, clear=False):
                token_result = ReviewTokenService(repository).create_token(
                    review.review_task_id,
                    token_subject="mobile_reviewer",
                    allowed_actions=["approved", "expired"],
                )
                invalid_json_status, _, invalid_json_body = self._call_app(
                    path=f"/mobile/review/{review.review_task_id}/resolve",
                    method="POST",
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "token": token_result.raw_token,
                            "action": "approved",
                            "resolution_payload_json": "[]",
                        }
                    ),
                )
                expired_status, _, expired_body = self._call_app(
                    path=f"/mobile/review/{review.review_task_id}/resolve",
                    method="POST",
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "token": token_result.raw_token,
                            "action": "expired",
                            "resolution_payload_json": "{}",
                        }
                    ),
                )

            self.assertEqual(invalid_json_status, "200 OK")
            self.assertIn("JSON object", invalid_json_body)
            self.assertEqual(expired_status, "200 OK")
            self.assertIn("链接已失效或无权访问该复核任务", expired_body)

    def test_mobile_review_token_fails_after_web_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            task = _runtime_task("TASK-M4", status=TaskStatus.MANUAL_REVIEW)
            task_service.create_tasks([task])
            review_service = ReviewTaskService(repository, runtime_task_service=task_service)
            review_service.create_from_tasks([task])
            review = review_service.list_review_tasks()[0]

            with patch.dict("os.environ", {"REVIEW_TOKEN_SECRET": "unit-test-secret"}, clear=False):
                token_result = ReviewTokenService(repository).create_token(
                    review.review_task_id,
                    token_subject="mobile_reviewer",
                )
                review_service.resolve_review_task(
                    review_task_id=review.review_task_id,
                    status=ReviewTaskStatus.CANCELLED,
                    actor="admin",
                    actor_source="web_session",
                    source_task_status=TaskStatus.CANCELLED,
                )
                status, _, body = self._call_app(
                    path=f"/mobile/review/{review.review_task_id}/resolve",
                    method="POST",
                    body=urlencode(
                        {
                            "runtime_db": str(db_path),
                            "token": token_result.raw_token,
                            "action": "approved",
                            "resolution_payload_json": "{}",
                        }
                    ),
                )

            self.assertEqual(status, "200 OK")
            self.assertIn("链接已失效或无权访问该复核任务", body)


if __name__ == "__main__":
    unittest.main()
