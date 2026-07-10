import ast
from pathlib import Path


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "vertical_slice_read_price.py"
)


def _load_replace_helper(fake_os, fake_time):
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_replace_file_with_retry"
    )
    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"os": fake_os, "time": fake_time}
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace["_replace_file_with_retry"]


class FakeOs:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def replace(self, _source, _destination):
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError(5, "access denied")


class FakeTime:
    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def test_phase_replace_retries_windows_sharing_collision():
    fake_os = FakeOs(failures=2)
    fake_time = FakeTime()
    replace = _load_replace_helper(fake_os, fake_time)

    replace("source", "destination")

    assert fake_os.calls == 3
    assert fake_time.sleeps == [0.05, 0.1]


def test_phase_replace_raises_after_retry_budget():
    fake_os = FakeOs(failures=3)
    fake_time = FakeTime()
    replace = _load_replace_helper(fake_os, fake_time)

    try:
        replace("source", "destination", max_attempts=3)
    except PermissionError:
        pass
    else:
        raise AssertionError("expected PermissionError")

    assert fake_os.calls == 3
