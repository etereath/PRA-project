import ast
from pathlib import Path


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _load_helper(win32):
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "SliceError"
        )
        or (
            isinstance(node, ast.FunctionDef)
            and node.name == "_get_and_prepare_window"
        )
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"win32": win32, "sleep": lambda _seconds: None}
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace["_get_and_prepare_window"], namespace["SliceError"]


class FakeWindow:
    def __init__(self):
        self.calls = []

    def set_state(self, value):
        self.calls.append(("set_state", value))

    def move(self, **kwargs):
        self.calls.append(("move", kwargs))

    def resize(self, **kwargs):
        self.calls.append(("resize", kwargs))

    def activate(self):
        self.calls.append(("activate", None))


class FlakyWin32:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.window = FakeWindow()

    def get(self, _title):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("invalid window handle")
        return self.window


def test_window_prepare_retries_transient_invalid_handle():
    fake = FlakyWin32(failures=1)
    prepare, _slice_error = _load_helper(fake)

    window = prepare("蚂蚁花团供应商", 0, 0, 562, 1056)

    assert window is fake.window
    assert fake.calls == 2
    assert fake.window.calls == [
        ("set_state", "restore"),
        ("move", {"x": 0, "y": 0}),
        ("resize", {"width": 562, "height": 1056}),
        ("activate", None),
    ]


def test_window_prepare_returns_explicit_retryable_error_after_retries():
    fake = FlakyWin32(failures=3)
    prepare, slice_error = _load_helper(fake)

    try:
        prepare("蚂蚁花团供应商", 0, 0, 562, 1056)
    except slice_error as exc:
        assert exc.code == "WINDOW_NOT_AVAILABLE"
        assert exc.retryable is True
        assert "invalid window handle" in exc.message
    else:
        raise AssertionError("expected WINDOW_NOT_AVAILABLE")

    assert fake.calls == 3
