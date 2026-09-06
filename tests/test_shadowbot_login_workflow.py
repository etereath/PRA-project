"""Unit-test the login state machine without loading the ShadowBot xbot runtime."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


FLOW_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "vertical_slice_read_price.py"


class SliceError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _load_login_helpers(**overrides):
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "_login_config_value",
        "_safe_login_markers",
        "_wait_for_manual_login_verification",
        "_attempt_automatic_login",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    clock = FakeClock()
    phases: list[str] = []

    def write_phase(_request, _result, phase, *_args):
        phases.append(phase)

    namespace = {
        "SliceError": SliceError,
        "TZ_SHANGHAI": timezone(timedelta(hours=8)),
        "datetime": datetime,
        "time": clock,
        "sleep": clock.sleep,
        "_now_iso": lambda: "2026-07-11T12:00:00+08:00",
        "_write_phase": write_phase,
        "_check_stop_before_submit": lambda _request, _result: None,
        "_collect_ui_state_labels": lambda _window: ["验证码"],
        "_login_page_state": lambda labels: ("VERIFICATION_REQUIRED", labels),
        "_find_element": lambda _window, _selector, _timeout: object(),
        "ELEMENTS": {"product_management": "商品管理"},
        "phases": phases,
        "clock": clock,
    }
    namespace.update(overrides)
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(FLOW_PATH), "exec"), namespace)
    return namespace


def test_single_auto_login_submission_transitions_to_manual_verification_without_secret_output():
    calls: list[tuple[str, str]] = []

    class SubmitElement:
        def click(self) -> None:
            calls.append(("submit", ""))

    class EmployeeModeElement:
        def click(self) -> None:
            calls.append(("employee_mode", ""))

    def find_element(_window, selector, _timeout):
        if selector == "employee":
            return EmployeeModeElement()
        if selector == "account":
            return object()
        if selector == "password":
            return object()
        if selector == "submit":
            return SubmitElement()
        if selector == "商品管理":
            return object()
        raise AssertionError(selector)

    def native_input(_element, value):
        calls.append(("input", value))

    namespace = _load_login_helpers(
        _find_element=find_element,
        _set_login_input_value=native_input,
        _collect_ui_state_labels=lambda _window: ["验证码"],
    )
    result: dict[str, object] = {}
    completed = namespace["_attempt_automatic_login"](
        object(),
        {},
        result,
        1,
        {
            "auto_enabled": True,
            "employee_mode_required": True,
            "employee_mode_selector": "employee",
            "employee_mode_wait_seconds": 0,
            "account_selector": "account",
            "password_selector": "password",
            "submit_selector": "submit",
            "post_submit_wait_seconds": 8,
            "verification_wait_seconds": 300,
        },
        SimpleNamespace(get_login_credentials=lambda: SimpleNamespace(account="seller", password="secret-password")),
        ["登录"],
    )

    assert completed is True
    assert calls.count(("submit", "")) == 1
    assert calls.index(("employee_mode", "")) < calls.index(("input", "seller"))
    assert namespace["phases"] == ["LOGIN_ACCOUNT_PASSWORD_SUBMITTED", "LOGIN_VERIFICATION_REQUIRED"]
    assert "secret-password" not in repr(result)
    assert "seller" not in repr(result)
    assert result["login"]["employee_mode_clicked"] is True
    assert result["login"]["verification_completed"] is True


def test_provider_error_code_crosses_login_boundary_only_when_allowlisted():
    class ProviderFailure(Exception):
        def __init__(self, error_code: str) -> None:
            super().__init__("secret provider detail must not cross the boundary")
            self.error_code = error_code

    config = {
        "auto_enabled": True,
        "employee_mode_required": False,
        "account_selector": "account",
        "password_selector": "password",
        "submit_selector": "submit",
        "post_submit_wait_seconds": 1,
        "verification_wait_seconds": 1,
    }

    def failing_provider(code: str):
        def get_login_credentials():
            raise ProviderFailure(code)

        return SimpleNamespace(get_login_credentials=get_login_credentials)

    for code, expected in (
        ("CREDENTIAL_NOT_FOUND", "CREDENTIAL_NOT_FOUND"),
        ("UNSAFE_PROVIDER_DETAIL", None),
    ):
        namespace = _load_login_helpers()
        result: dict[str, object] = {}
        try:
            namespace["_attempt_automatic_login"](
                object(), {}, result, 1, config, failing_provider(code), ["登录"]
            )
        except SliceError as exc:
            assert exc.code == "LOGIN_CREDENTIALS_UNAVAILABLE"
        else:
            raise AssertionError("expected provider failure")
        if expected is None:
            assert "provider_error_code" not in result
        else:
            assert result["provider_error_code"] == expected
        assert "secret provider detail" not in repr(result)


def test_unresolved_post_submit_login_state_uses_manual_verification_not_credential_rejection():
    class SubmitElement:
        def click(self) -> None:
            return None

    def find_element(_window, selector, _timeout):
        if selector == "submit":
            return SubmitElement()
        if selector == "商品管理":
            raise SliceError("ELEMENT_NOT_FOUND", "still waiting")
        return object()

    namespace = _load_login_helpers(
        _find_element=find_element,
        _set_login_input_value=lambda _element, _value: None,
        _collect_ui_state_labels=lambda _window: ["请输入您的账号", "请输入您的密码"],
        _login_page_state=lambda _labels: ("ACCOUNT_PASSWORD", ["请输入您的账号"]),
    )
    try:
        namespace["_attempt_automatic_login"](
            object(),
            {},
            {},
            1,
            {
                "auto_enabled": True,
                "employee_mode_required": False,
                "account_selector": "account",
                "password_selector": "password",
                "submit_selector": "submit",
                "post_submit_wait_seconds": 1,
                "verification_wait_seconds": 1,
            },
            SimpleNamespace(get_login_credentials=lambda: SimpleNamespace(account="seller", password="secret-password")),
            ["登录"],
        )
    except SliceError as exc:
        assert exc.code == "LOGIN_VERIFICATION_TIMEOUT"
    else:
        raise AssertionError("expected manual verification timeout")
    assert namespace["phases"] == ["LOGIN_ACCOUNT_PASSWORD_SUBMITTED", "LOGIN_VERIFICATION_REQUIRED"]


def test_manual_verification_timeout_and_stop_are_safe_pre_submit_failures():
    def no_product(*_args):
        raise SliceError("ELEMENT_NOT_FOUND", "not yet logged in")

    timeout = _load_login_helpers(_find_element=no_product)
    timeout["clock"].now = 10.0
    try:
        timeout["_wait_for_manual_login_verification"](
            object(), {}, {}, 1, {"verification_wait_seconds": 1}, ["验证码"]
        )
    except SliceError as exc:
        assert exc.code == "LOGIN_VERIFICATION_TIMEOUT"
        assert exc.retryable is False
    else:
        raise AssertionError("expected timeout")
    assert timeout["phases"] == ["LOGIN_VERIFICATION_REQUIRED"]

    stop = _load_login_helpers(
        _find_element=no_product,
        _check_stop_before_submit=lambda _request, _result: (_ for _ in ()).throw(
            SliceError("WORKER_STOP_REQUESTED", "stop requested", True)
        ),
    )
    try:
        stop["_wait_for_manual_login_verification"](
            object(), {}, {}, 1, {"verification_wait_seconds": 300}, ["验证码"]
        )
    except SliceError as exc:
        assert exc.code == "WORKER_STOP_REQUESTED"
        assert exc.retryable is True
    else:
        raise AssertionError("expected stop")
    assert stop["phases"] == ["LOGIN_VERIFICATION_REQUIRED"]


def test_manual_verification_accepts_homepage_that_appears_at_deadline():
    calls = {"product_management": 0}

    def product_after_deadline(_window, _selector, _timeout):
        calls["product_management"] += 1
        if calls["product_management"] == 1:
            raise SliceError("ELEMENT_NOT_FOUND", "still waiting")
        return object()

    namespace = _load_login_helpers(_find_element=product_after_deadline)
    result: dict[str, object] = {}
    completed = namespace["_wait_for_manual_login_verification"](
        object(), {}, result, 1, {"verification_wait_seconds": 1}, ["验证码"]
    )

    assert completed is True
    assert calls["product_management"] == 2
    assert result["login"]["verification_completed"] is True
