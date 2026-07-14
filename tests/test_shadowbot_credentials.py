from pathlib import Path
import importlib.util
import sys
import ast
import subprocess


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


def test_credential_provider_error_codes_are_stable_and_redacted():
    module = _module()

    for record in (
        {"UserName": "seller-account", "CredentialBlob": b"\xff"},
        {"UserName": "", "CredentialBlob": "secret-password"},
        {"UserName": "seller-account", "CredentialBlob": b"secret-password", "CredentialBlobSize": 999},
    ):
        provider = module.WindowsCredentialManagerProvider("ShadowBot/Test", credential_reader=lambda _target, record=record: record)
        try:
            provider.get_login_credentials()
        except module.CredentialProviderError as exc:
            assert exc.error_code == "CREDENTIAL_FORMAT_INVALID"
            assert "seller-account" not in str(exc)
            assert "secret-password" not in str(exc)
            assert "CredentialBlob" not in str(exc)
        else:
            raise AssertionError("expected stable format error")

    try:
        module.WindowsCredentialManagerProvider("")
    except module.CredentialProviderError as exc:
        assert exc.error_code == "CREDENTIAL_TARGET_MISSING"
    else:
        raise AssertionError("expected missing-target error")

    credentials = module.LoginCredentials("seller-account", "secret-password")
    assert "seller-account" not in repr(credentials)
    assert "secret-password" not in repr(credentials)


def test_credential_provider_has_stdlib_windows_api_fallback_for_embedded_python():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "CredReadW" in source
    assert 'ctypes.WinDLL("Advapi32"' in source
    assert "win32cred is unavailable" not in source


def test_machine_local_worker_config_is_ignored_but_example_remains_visible():
    root = MODULE_PATH.parents[2]
    local_config = "shadowbot/test2/shadowbot_worker_config.json"
    example_config = "shadowbot/test2/shadowbot_worker_config.example.json"
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", local_config],
        cwd=root,
        check=False,
    )
    visible_example = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", example_config],
        cwd=root,
        check=False,
    )
    assert ignored.returncode == 0
    assert visible_example.returncode != 0


def test_credentials_are_injected_as_runtime_only_objects_not_request_json_fields():
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    flow_source = FLOW_PATH.read_text(encoding="utf-8")

    assert '"_credential_provider": self.credential_provider' in worker_source
    assert 'json.dumps(runtime_request, ensure_ascii=False)' in worker_source
    payload_start = flow_source.index("def _request_payload")
    payload_end = flow_source.index("def _as_int", payload_start)
    assert '"_credential_provider"' not in flow_source[payload_start:payload_end]
    assert '"CredentialBlob"' not in worker_source
    assert '"_provider_error_code": self.credential_provider_error_code' in worker_source
    assert "SAFE_PROVIDER_ERROR_CODES" in worker_source
    assert "provider_error_code" in flow_source


def test_result_and_phase_snapshots_drop_credential_fields():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "_safe_login_phase_snapshot",
        "_safe_output_payload",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "_SAFE_LOGIN_PHASE_FIELDS": frozenset({"account_password_submitted"}),
        "_SENSITIVE_OUTPUT_KEYS": frozenset({"account", "password", "credentialblob"}),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(FLOW_PATH), "exec"), namespace)

    login = {
        "account": "seller-account",
        "password": "secret-password",
        "account_password_submitted": True,
    }
    assert namespace["_safe_login_phase_snapshot"](login) == {"account_password_submitted": True}
    safe = namespace["_safe_output_payload"]({"login": login, "CredentialBlob": "secret-password"})
    assert safe == {"login": {"account_password_submitted": True}}


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
