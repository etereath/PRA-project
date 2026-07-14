"""Credential access for the standalone ShadowBot worker.

The worker deliberately keeps this module dependency-free.  A deployed ShadowBot
process may use a bundled Python runtime that does not contain pywin32, so the
Windows Credential Manager API is called through ``ctypes``.  This module never
enumerates credentials and never includes credential values in exceptions or
representations.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable, Mapping


_CREDENTIAL_TYPE_GENERIC = 1
_ERROR_ACCESS_DENIED = 5
_ERROR_FILE_NOT_FOUND = 1168
_ERROR_INVALID_PARAMETER = 87
_ERROR_NO_SUCH_LOGON_SESSION = 1312

_ERROR_MESSAGES = {
    "CREDENTIAL_TARGET_MISSING": "credential target is not configured",
    "CREDENTIAL_MANAGER_UNAVAILABLE": "Windows Credential Manager is unavailable",
    "CREDENTIAL_NOT_FOUND": "credential record was not found",
    "CREDENTIAL_ACCESS_DENIED": "credential record access was denied",
    "CREDENTIAL_FORMAT_INVALID": "credential record username or password format is invalid",
    "CREDENTIAL_READ_FAILED": "credential record could not be read",
}

# These codes are safe to cross the Worker/flow boundary. They describe the
# provider state without exposing a target, username, password, or blob.
SAFE_PROVIDER_ERROR_CODES = frozenset(_ERROR_MESSAGES)


class CredentialProviderError(RuntimeError):
    """Stable, deliberately non-sensitive credential provider failure."""

    def __init__(self, error_code: str, message: str | None = None) -> None:
        self.error_code = str(error_code)
        # ``code`` is kept as a small compatibility alias for callers that use
        # exception codes without depending on the message text.
        self.code = self.error_code
        # Ignore caller-provided text: provider errors may cross the worker
        # boundary and must remain safe even when a lower-level API supplies a
        # verbose message.  ``message`` is accepted for source compatibility.
        del message
        safe_message = _ERROR_MESSAGES.get(self.error_code, "credential provider failed")
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True, repr=False)
class LoginCredentials:
    """Runtime-only credentials; repr intentionally redacts both fields."""

    account: str
    password: str

    def __repr__(self) -> str:  # pragma: no cover - exercised indirectly by logs
        return "LoginCredentials(account=<redacted>, password=<redacted>)"


class _CredentialFileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _CredentialFileTime),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _windows_credential_reader(target: str) -> Mapping[str, Any]:
    """Read one Generic Credential by exact target using the native API."""

    if os.name != "nt":
        raise CredentialProviderError("CREDENTIAL_MANAGER_UNAVAILABLE")

    try:
        advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
        cred_read = advapi32.CredReadW
        cred_free = advapi32.CredFree
    except (AttributeError, OSError):
        raise CredentialProviderError("CREDENTIAL_MANAGER_UNAVAILABLE") from None

    credential_ptr = ctypes.POINTER(_Credential)()
    cred_read.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    cred_read.restype = wintypes.BOOL
    cred_free.argtypes = [ctypes.c_void_p]
    cred_free.restype = wintypes.BOOL

    if not cred_read(target, _CREDENTIAL_TYPE_GENERIC, 0, ctypes.byref(credential_ptr)):
        error = ctypes.get_last_error()
        if error == _ERROR_FILE_NOT_FOUND:
            raise CredentialProviderError("CREDENTIAL_NOT_FOUND")
        if error == _ERROR_ACCESS_DENIED:
            raise CredentialProviderError("CREDENTIAL_ACCESS_DENIED")
        if error in (_ERROR_INVALID_PARAMETER, _ERROR_NO_SUCH_LOGON_SESSION):
            raise CredentialProviderError("CREDENTIAL_MANAGER_UNAVAILABLE")
        raise CredentialProviderError("CREDENTIAL_READ_FAILED")

    try:
        record = credential_ptr.contents
        blob_size = int(record.CredentialBlobSize)
        blob = b""
        if blob_size:
            if not record.CredentialBlob:
                raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
            blob = ctypes.string_at(record.CredentialBlob, blob_size)
        return {"UserName": record.UserName, "CredentialBlob": blob, "CredentialBlobSize": blob_size}
    finally:
        cred_free(credential_ptr)


def _decode_account(value: Any) -> str:
    if isinstance(value, str):
        account = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            account = raw.decode("utf-16-le" if b"\x00" in raw else "utf-8")
        except UnicodeDecodeError:
            try:
                account = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID") from None
    else:
        raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
    account = account.rstrip("\x00").strip()
    if not account or any(ord(char) < 32 for char in account):
        raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
    return account


def _decode_password(value: Any, size: Any = None) -> str:
    if isinstance(value, str):
        password = value.rstrip("\x00")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if size is not None:
            try:
                requested_size = int(size)
            except (TypeError, ValueError):
                raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID") from None
            if requested_size < 0 or requested_size > len(raw):
                raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
            raw = raw[:requested_size]
        if not raw:
            raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
        try:
            if b"\x00" in raw:
                password = raw.decode("utf-16-le")
            else:
                password = raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            try:
                password = raw.decode("utf-16-le")
            except (UnicodeDecodeError, ValueError):
                raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID") from None
        password = password.rstrip("\x00")
    else:
        raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")

    if not password or "\x00" in password or any(ord(char) < 32 and char not in "\t\r\n" for char in password):
        raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
    return password


class WindowsCredentialManagerProvider:
    """Read one configured Generic Credential at login time."""

    def __init__(
        self,
        credential_target: str,
        *,
        credential_reader: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        target = str(credential_target or "").strip()
        if not target or any(ord(char) < 32 for char in target):
            raise CredentialProviderError("CREDENTIAL_TARGET_MISSING")
        self.credential_target = target
        self._credential_reader = credential_reader or _windows_credential_reader

    def get_login_credentials(self) -> LoginCredentials:
        try:
            record = self._credential_reader(self.credential_target)
        except CredentialProviderError:
            raise
        except Exception:
            raise CredentialProviderError("CREDENTIAL_READ_FAILED") from None
        if not isinstance(record, Mapping):
            raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID")
        try:
            account = _decode_account(record.get("UserName"))
            password = _decode_password(record.get("CredentialBlob"), record.get("CredentialBlobSize"))
        except CredentialProviderError:
            raise
        except Exception:
            raise CredentialProviderError("CREDENTIAL_FORMAT_INVALID") from None
        return LoginCredentials(account=account, password=password)


__all__ = [
    "CredentialProviderError",
    "LoginCredentials",
    "SAFE_PROVIDER_ERROR_CODES",
    "WindowsCredentialManagerProvider",
]
