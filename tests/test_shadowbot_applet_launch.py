"""Unit tests for the safe WeChat mini-program URI launch fallback."""

from __future__ import annotations

import ast
from pathlib import Path


FLOW_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "vertical_slice_read_price.py"


def _load_helpers(win32):
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "SliceError",
        "_get_and_prepare_window",
        "_validate_applet_uri",
        "_launch_applet_uri",
        "_get_or_open_and_prepare_window",
    }
    nodes = [
        node
        for node in tree.body
        if (isinstance(node, ast.ClassDef) and node.name in wanted)
        or (isinstance(node, ast.FunctionDef) and node.name in wanted)
    ]
    namespace = {
        "APPLET_LAUNCH_TIMEOUT_DEFAULT": 20,
        "APPLET_URI_PREFIXES": ("weixin://launchapplet/",),
        "win32": win32,
        "sleep": lambda _seconds: None,
        "_now_iso": lambda: "2026-07-12T12:00:00+08:00",
        "os": object(),
    }
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(FLOW_PATH), "exec"), namespace)
    return namespace


class FakeWindow:
    def set_state(self, _value):
        pass

    def move(self, **_kwargs):
        pass

    def resize(self, **_kwargs):
        pass

    def activate(self):
        pass


class FakeWin32:
    def __init__(self, unavailable_before_ready=0):
        self.unavailable_before_ready = unavailable_before_ready
        self.calls = 0
        self.window = FakeWindow()

    def get(self, _title):
        self.calls += 1
        if self.calls <= self.unavailable_before_ready:
            raise RuntimeError("window missing")
        return self.window


def test_existing_window_is_reused_without_opening_uri():
    fake_win32 = FakeWin32()
    helpers = _load_helpers(fake_win32)
    opened: list[str] = []

    window, audit = helpers["_get_or_open_and_prepare_window"](
        "蚂蚁花团供应商", 0, 0, 562, 1056, "", uri_launcher=opened.append
    )

    assert window is fake_win32.window
    assert opened == []
    assert audit["source"] == "EXISTING_WINDOW"
    assert audit["uri_opened"] is False


def test_missing_window_opens_only_allowed_wechat_uri_then_retries():
    fake_win32 = FakeWin32(unavailable_before_ready=1)
    helpers = _load_helpers(fake_win32)
    opened: list[str] = []
    uri = "weixin://launchapplet/?app_id=wx8821c13ff3ff02"

    window, audit = helpers["_get_or_open_and_prepare_window"](
        "蚂蚁花团供应商", 0, 0, 562, 1056, uri, launch_timeout_seconds=1, uri_launcher=opened.append
    )

    assert window is fake_win32.window
    assert opened == [uri]
    assert audit["source"] == "URI_LAUNCHED"
    assert audit["uri_opened"] is True


def test_missing_window_rejects_non_wechat_uri_before_launching():
    fake_win32 = FakeWin32(unavailable_before_ready=99)
    helpers = _load_helpers(fake_win32)
    opened: list[str] = []

    try:
        helpers["_get_or_open_and_prepare_window"](
            "蚂蚁花团供应商", 0, 0, 562, 1056, "https://example.invalid", uri_launcher=opened.append
        )
    except helpers["SliceError"] as exc:
        assert exc.code == "APPLET_URI_INVALID"
        assert exc.retryable is False
    else:
        raise AssertionError("expected APPLET_URI_INVALID")

    assert opened == []
