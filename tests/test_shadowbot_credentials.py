from pathlib import Path
import importlib.util
import sys
import ast


MODULE_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "shadowbot_credentials.py"
WORKER_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "shadowbot_queue_worker.py"
FLOW_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "vertical_slice_read_price.py"


def _module():
    spec = importlib.util.spec_from_file_location("shadowbot_credentials_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_windows_credential_provider_reads_username_and_utf16_password_without_logging_values():
    module = _module()
    provider = module.WindowsCredentialManagerProvider(
        "ShadowBot/Test",
        credential_reader=lambda _target: {
            "UserName": "seller-account",
            "CredentialBlob": "secret-password".encode("utf-16-le"),
        },
    )

    credentials = provider.get_login_credentials()

    assert credentials.account == "seller-account"
    assert credentials.password == "secret-password"
    assert "secret-password" not in str(provider.__dict__)


def test_windows_credential_provider_returns_safe_failure_when_record_is_missing():
    module = _module()
    provider = module.WindowsCredentialManagerProvider(
        "ShadowBot/Test",
        credential_reader=lambda _target: {"UserName": "seller-account", "CredentialBlob": b""},
    )

    try:
        provider.get_login_credentials()
    except module.CredentialProviderError as exc:
        assert "password" in str(exc)
    else:
        raise AssertionError("expected CredentialProviderError")


def test_credential_provider_has_stdlib_windows_api_fallback_for_embedded_python():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "CredReadW" in source
    assert 'ctypes.WinDLL("Advapi32"' in source
    assert "win32cred is unavailable" not in source


def test_credentials_are_injected_as_runtime_only_objects_not_request_json_fields():
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    flow_source = FLOW_PATH.read_text(encoding="utf-8")

    assert '"_credential_provider": self.credential_provider' in worker_source
    assert 'json.dumps(runtime_request, ensure_ascii=False)' in worker_source
    payload_start = flow_source.index("def _request_payload")
    payload_end = flow_source.index("def _as_int", payload_start)
    assert '"_credential_provider"' not in flow_source[payload_start:payload_end]
    assert '"CredentialBlob"' not in worker_source


def test_login_input_uses_native_element_apis_and_never_uses_clipboard():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_set_login_input_value"]
    assert len(selected) == 1
    namespace = {
        "sleep": lambda _seconds: None,
        "SliceError": RuntimeError,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(FLOW_PATH), "exec"), namespace)

    calls: list[tuple[str, tuple[object, ...]]] = []

    class NativeElement:
        def click(self):
            calls.append(("click", ()))

        def set_value(self, value):
            calls.append(("set_value", (value,)))

        def clipboard_input(self, *args):
            raise AssertionError("credential input must not use clipboard")

    namespace["_set_login_input_value"](NativeElement(), "secret-password")

    assert calls == [("click", ()), ("set_value", ("secret-password",))]
    assert "clipboard_input" not in ast.unparse(selected[0])
