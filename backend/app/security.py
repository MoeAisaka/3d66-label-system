from __future__ import annotations

import base64
import binascii
import ctypes
import ctypes.util
import hashlib
import hmac
import os
import re
import secrets
import sys
from collections.abc import Mapping
from ctypes import wintypes
from typing import Final, Protocol


DEFAULT_ADMIN_PASSWORD_HASH = (
    "scrypt$16384$8$1$MF8Xt0Yd9OYKsOI3RsAVcA==$"
    "pURXfzh2eVCoBBhcCmlx6yyAQ-5BXuXLXcD9XSoHP6o="
)

MODEL_CONFIG_KEYCHAIN_ACCOUNT: Final = "model-config"
OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT: Final = "optimizer-config"
KEYCHAIN_SERVICE: Final = "com.3d66.label-system.api-keys"
KEYCHAIN_REFERENCE_PREFIX: Final = "keychain:v1:"
DPAPI_REFERENCE_PREFIX: Final = "dpapi:v1:"
DPAPI_MACHINE_REFERENCE_PREFIX: Final = "dpapi-machine:v1:"
DPAPI_SCOPE_ENV: Final = "API_KEY_DPAPI_SCOPE"
DPAPI_SCOPE_CURRENT_USER: Final = "current-user"
DPAPI_SCOPE_LOCAL_MACHINE: Final = "local-machine"

_ALLOWED_KEYCHAIN_ACCOUNTS: Final = frozenset(
    {
        MODEL_CONFIG_KEYCHAIN_ACCOUNT,
        OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    }
)
_ERR_SEC_SUCCESS: Final = 0
_ERR_SEC_DUPLICATE_ITEM: Final = -25299
_ERR_SEC_ITEM_NOT_FOUND: Final = -25300
_K_CF_STRING_ENCODING_UTF8: Final = 0x08000100
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x1
_CRYPTPROTECT_LOCAL_MACHINE: Final = 0x4


class SecretStorageError(RuntimeError):
    """A credential storage failure whose message never includes credential data."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "SECRET_STORAGE_FAILED",
        system_error: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.system_error = system_error


class SecretNotFoundError(SecretStorageError):
    """The requested credential reference is valid but has no stored secret."""


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    n, r, p = 16384, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "$".join(
        [
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


class _WindowsDPAPI:
    def __init__(self) -> None:
        try:
            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise SecretStorageError(
                "Windows DPAPI 初始化失败",
                reason="DPAPI_INIT_FAILED",
            ) from exc

        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        self._crypt_protect_data = crypt32.CryptProtectData
        self._crypt_unprotect_data = crypt32.CryptUnprotectData
        self._local_free = kernel32.LocalFree

    @staticmethod
    def _last_error_code() -> int:
        getter = getattr(ctypes, "get_last_error", None)
        return int(getter()) if getter is not None else 0

    def protect(self, cleartext: bytes, *, local_machine: bool = False) -> bytes:
        in_blob, _buffer = _to_blob(cleartext)
        out_blob = _DataBlob()
        flags = _CRYPTPROTECT_UI_FORBIDDEN
        if local_machine:
            flags |= _CRYPTPROTECT_LOCAL_MACHINE
        if not self._crypt_protect_data(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(out_blob),
        ):
            system_error = self._last_error_code()
            raise SecretStorageError(
                f"Windows DPAPI 加密失败（系统错误 {system_error}）",
                reason="DPAPI_PROTECT_FAILED",
                system_error=system_error,
            )
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            if out_blob.pbData:
                self._local_free(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    def unprotect(self, ciphertext: bytes) -> bytes:
        in_blob, _buffer = _to_blob(ciphertext)
        out_blob = _DataBlob()
        if not self._crypt_unprotect_data(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            system_error = self._last_error_code()
            raise SecretStorageError(
                f"Windows DPAPI 解密失败（系统错误 {system_error}）",
                reason="DPAPI_UNPROTECT_FAILED",
                system_error=system_error,
            )
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            if out_blob.pbData:
                self._local_free(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


class _CFDictionaryKeyCallBacks(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copy_description", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
        ("hash", ctypes.c_void_p),
    ]


class _CFDictionaryValueCallBacks(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copy_description", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
    ]


def _cf_constant(library: ctypes.CDLL, name: str) -> ctypes.c_void_p:
    try:
        value = ctypes.c_void_p.in_dll(library, name)
    except ValueError as exc:
        raise SecretStorageError("macOS Keychain 初始化失败") from exc
    if not value.value:
        raise SecretStorageError("macOS Keychain 初始化失败")
    return value


class _MacOSSecurityFramework:
    """Minimal ctypes binding for generic-password items in Security.framework."""

    def __init__(self) -> None:
        security_path = ctypes.util.find_library("Security")
        core_foundation_path = ctypes.util.find_library("CoreFoundation")
        if not security_path or not core_foundation_path:
            raise SecretStorageError("macOS Keychain 框架不可用")
        try:
            security = ctypes.CDLL(security_path)
            core_foundation = ctypes.CDLL(core_foundation_path)
        except OSError as exc:
            raise SecretStorageError("macOS Keychain 框架加载失败") from exc

        security.SecItemAdd.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        security.SecItemAdd.restype = ctypes.c_int32
        security.SecItemCopyMatching.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        security.SecItemCopyMatching.restype = ctypes.c_int32
        security.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        security.SecItemUpdate.restype = ctypes.c_int32
        security.SecItemDelete.argtypes = [ctypes.c_void_p]
        security.SecItemDelete.restype = ctypes.c_int32

        core_foundation.CFStringCreateWithBytes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_long,
            ctypes.c_uint32,
            ctypes.c_bool,
        ]
        core_foundation.CFStringCreateWithBytes.restype = ctypes.c_void_p
        core_foundation.CFDataCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_long,
        ]
        core_foundation.CFDataCreate.restype = ctypes.c_void_p
        core_foundation.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.POINTER(_CFDictionaryKeyCallBacks),
            ctypes.POINTER(_CFDictionaryValueCallBacks),
        ]
        core_foundation.CFDictionaryCreate.restype = ctypes.c_void_p
        core_foundation.CFDataGetLength.argtypes = [ctypes.c_void_p]
        core_foundation.CFDataGetLength.restype = ctypes.c_long
        core_foundation.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        core_foundation.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_uint8)
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        core_foundation.CFRelease.restype = None

        try:
            key_callbacks = _CFDictionaryKeyCallBacks.in_dll(
                core_foundation, "kCFTypeDictionaryKeyCallBacks"
            )
            value_callbacks = _CFDictionaryValueCallBacks.in_dll(
                core_foundation, "kCFTypeDictionaryValueCallBacks"
            )
        except ValueError as exc:
            raise SecretStorageError("macOS Keychain 初始化失败") from exc

        self._sec_item_add = security.SecItemAdd
        self._sec_item_copy_matching = security.SecItemCopyMatching
        self._sec_item_update = security.SecItemUpdate
        self._sec_item_delete = security.SecItemDelete
        self._cf_string_create_with_bytes = core_foundation.CFStringCreateWithBytes
        self._cf_data_create = core_foundation.CFDataCreate
        self._cf_dictionary_create = core_foundation.CFDictionaryCreate
        self._cf_data_get_length = core_foundation.CFDataGetLength
        self._cf_data_get_byte_ptr = core_foundation.CFDataGetBytePtr
        self._cf_release = core_foundation.CFRelease
        self._key_callbacks = key_callbacks
        self._value_callbacks = value_callbacks

        self._k_sec_class = _cf_constant(security, "kSecClass")
        self._k_sec_class_generic_password = _cf_constant(
            security, "kSecClassGenericPassword"
        )
        self._k_sec_attr_service = _cf_constant(security, "kSecAttrService")
        self._k_sec_attr_account = _cf_constant(security, "kSecAttrAccount")
        self._k_sec_value_data = _cf_constant(security, "kSecValueData")
        self._k_sec_return_data = _cf_constant(security, "kSecReturnData")
        self._k_sec_match_limit = _cf_constant(security, "kSecMatchLimit")
        self._k_sec_match_limit_one = _cf_constant(security, "kSecMatchLimitOne")
        self._k_cf_boolean_true = _cf_constant(core_foundation, "kCFBooleanTrue")

    @staticmethod
    def _pointer_value(value: int | ctypes.c_void_p) -> int:
        pointer = value.value if isinstance(value, ctypes.c_void_p) else value
        if not pointer:
            raise SecretStorageError("macOS Keychain 对象创建失败")
        return int(pointer)

    def _create_cf_string(self, value: str) -> ctypes.c_void_p:
        encoded = value.encode("utf-8")
        if not encoded:
            raise SecretStorageError("macOS Keychain 标识不能为空")
        buffer = (ctypes.c_uint8 * len(encoded)).from_buffer_copy(encoded)
        reference = self._cf_string_create_with_bytes(
            None,
            buffer,
            len(encoded),
            _K_CF_STRING_ENCODING_UTF8,
            False,
        )
        if not reference:
            raise SecretStorageError("macOS Keychain 字符串创建失败")
        return ctypes.c_void_p(reference)

    def _create_cf_data(self, value: bytes) -> ctypes.c_void_p:
        if not value:
            raise SecretStorageError("API Key 不能为空")
        buffer = (ctypes.c_uint8 * len(value)).from_buffer_copy(value)
        reference = self._cf_data_create(None, buffer, len(value))
        if not reference:
            raise SecretStorageError("macOS Keychain 数据创建失败")
        return ctypes.c_void_p(reference)

    def _create_dictionary(
        self,
        entries: list[tuple[ctypes.c_void_p, ctypes.c_void_p]],
        *,
        owned_values: list[ctypes.c_void_p],
    ) -> ctypes.c_void_p:
        try:
            keys = (ctypes.c_void_p * len(entries))(
                *(self._pointer_value(key) for key, _value in entries)
            )
            values = (ctypes.c_void_p * len(entries))(
                *(self._pointer_value(value) for _key, value in entries)
            )
            reference = self._cf_dictionary_create(
                None,
                keys,
                values,
                len(entries),
                ctypes.byref(self._key_callbacks),
                ctypes.byref(self._value_callbacks),
            )
            if not reference:
                raise SecretStorageError("macOS Keychain 查询创建失败")
            return ctypes.c_void_p(reference)
        finally:
            for owned_value in owned_values:
                self._cf_release(owned_value)

    def _identity_query(self, service: str, account: str) -> ctypes.c_void_p:
        service_ref = self._create_cf_string(service)
        try:
            account_ref = self._create_cf_string(account)
        except Exception:
            self._cf_release(service_ref)
            raise
        return self._create_dictionary(
            [
                (self._k_sec_class, self._k_sec_class_generic_password),
                (self._k_sec_attr_service, service_ref),
                (self._k_sec_attr_account, account_ref),
            ],
            owned_values=[service_ref, account_ref],
        )

    def add(self, service: str, account: str, secret: bytes) -> int:
        service_ref = self._create_cf_string(service)
        try:
            account_ref = self._create_cf_string(account)
        except Exception:
            self._cf_release(service_ref)
            raise
        try:
            secret_ref = self._create_cf_data(secret)
        except Exception:
            self._cf_release(service_ref)
            self._cf_release(account_ref)
            raise
        attributes = self._create_dictionary(
            [
                (self._k_sec_class, self._k_sec_class_generic_password),
                (self._k_sec_attr_service, service_ref),
                (self._k_sec_attr_account, account_ref),
                (self._k_sec_value_data, secret_ref),
            ],
            owned_values=[service_ref, account_ref, secret_ref],
        )
        try:
            return int(self._sec_item_add(attributes, None))
        finally:
            self._cf_release(attributes)

    def copy(self, service: str, account: str) -> tuple[int, bytes | None]:
        service_ref = self._create_cf_string(service)
        try:
            account_ref = self._create_cf_string(account)
        except Exception:
            self._cf_release(service_ref)
            raise
        query = self._create_dictionary(
            [
                (self._k_sec_class, self._k_sec_class_generic_password),
                (self._k_sec_attr_service, service_ref),
                (self._k_sec_attr_account, account_ref),
                (self._k_sec_return_data, self._k_cf_boolean_true),
                (self._k_sec_match_limit, self._k_sec_match_limit_one),
            ],
            owned_values=[service_ref, account_ref],
        )
        result = ctypes.c_void_p()
        try:
            status = int(self._sec_item_copy_matching(query, ctypes.byref(result)))
        finally:
            self._cf_release(query)
        if status != _ERR_SEC_SUCCESS:
            if result.value:
                self._cf_release(result)
            return status, None
        if not result.value:
            raise SecretStorageError("macOS Keychain 返回了空数据对象")
        try:
            length = int(self._cf_data_get_length(result))
            if length < 0:
                raise SecretStorageError("macOS Keychain 返回了无效数据")
            pointer = self._cf_data_get_byte_ptr(result)
            if length and not pointer:
                raise SecretStorageError("macOS Keychain 返回了无效数据")
            return status, ctypes.string_at(pointer, length) if length else b""
        finally:
            self._cf_release(result)

    def update(self, service: str, account: str, secret: bytes) -> int:
        query = self._identity_query(service, account)
        try:
            secret_ref = self._create_cf_data(secret)
            attributes = self._create_dictionary(
                [(self._k_sec_value_data, secret_ref)],
                owned_values=[secret_ref],
            )
            try:
                return int(self._sec_item_update(query, attributes))
            finally:
                self._cf_release(attributes)
        finally:
            self._cf_release(query)

    def delete(self, service: str, account: str) -> int:
        query = self._identity_query(service, account)
        try:
            return int(self._sec_item_delete(query))
        finally:
            self._cf_release(query)


class _MacOSBindings(Protocol):
    def add(self, service: str, account: str, secret: bytes) -> int: ...

    def copy(self, service: str, account: str) -> tuple[int, bytes | None]: ...

    def update(self, service: str, account: str, secret: bytes) -> int: ...

    def delete(self, service: str, account: str) -> int: ...


class _MacOSKeychain:
    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        bindings: _MacOSBindings | None = None,
    ) -> None:
        if not service:
            raise SecretStorageError("macOS Keychain service 不能为空")
        self._service = service
        self._bindings = bindings or _MacOSSecurityFramework()

    @staticmethod
    def _raise_status(operation: str, status: int) -> None:
        raise SecretStorageError(f"macOS Keychain {operation}失败（OSStatus {status}）")

    def set_secret(self, account: str, secret: str) -> None:
        if not account:
            raise SecretStorageError("macOS Keychain account 不能为空")
        if not secret or not secret.strip():
            raise SecretStorageError("API Key 不能为空")
        encoded = secret.encode("utf-8")

        status = self._bindings.update(self._service, account, encoded)
        if status == _ERR_SEC_SUCCESS:
            return
        if status != _ERR_SEC_ITEM_NOT_FOUND:
            self._raise_status("更新", status)

        status = self._bindings.add(self._service, account, encoded)
        if status == _ERR_SEC_SUCCESS:
            return
        if status == _ERR_SEC_DUPLICATE_ITEM:
            status = self._bindings.update(self._service, account, encoded)
            if status == _ERR_SEC_SUCCESS:
                return
        self._raise_status("写入", status)

    def get_secret(self, account: str) -> str:
        if not account:
            raise SecretStorageError("macOS Keychain account 不能为空")
        status, value = self._bindings.copy(self._service, account)
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            raise SecretNotFoundError("macOS Keychain 中没有对应的 API Key")
        if status != _ERR_SEC_SUCCESS:
            self._raise_status("读取", status)
        if value is None:
            raise SecretStorageError("macOS Keychain 返回了空数据")
        try:
            secret = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStorageError("macOS Keychain 数据不是有效 UTF-8") from exc
        if not secret:
            raise SecretStorageError("macOS Keychain 中的 API Key 为空")
        return secret

    def delete_secret(self, account: str, *, missing_ok: bool = True) -> bool:
        if not account:
            raise SecretStorageError("macOS Keychain account 不能为空")
        status = self._bindings.delete(self._service, account)
        if status == _ERR_SEC_SUCCESS:
            return True
        if status == _ERR_SEC_ITEM_NOT_FOUND and missing_ok:
            return False
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            raise SecretNotFoundError("macOS Keychain 中没有对应的 API Key")
        self._raise_status("删除", status)
        return False


def _get_windows_dpapi() -> _WindowsDPAPI:
    return _WindowsDPAPI()


def _get_macos_keychain() -> _MacOSKeychain:
    return _MacOSKeychain()


def _validate_account(account: str) -> str:
    if (
        account not in _ALLOWED_KEYCHAIN_ACCOUNTS
        and re.fullmatch(r"model-config-[1-9][0-9]*", account) is None
    ):
        raise SecretStorageError("不支持的 API Key account")
    return account


def _keychain_account_from_reference(reference: str) -> str:
    if not reference.startswith(KEYCHAIN_REFERENCE_PREFIX):
        raise SecretStorageError("不是受支持的 macOS Keychain 引用")
    account = reference.removeprefix(KEYCHAIN_REFERENCE_PREFIX)
    return _validate_account(account)


def _dpapi_scope(env: Mapping[str, str] | None = None) -> str:
    effective_env = os.environ if env is None else env
    raw = effective_env.get(DPAPI_SCOPE_ENV, DPAPI_SCOPE_CURRENT_USER)
    normalized = str(raw).strip().casefold().replace("_", "-")
    aliases = {
        "user": DPAPI_SCOPE_CURRENT_USER,
        "current-user": DPAPI_SCOPE_CURRENT_USER,
        "currentuser": DPAPI_SCOPE_CURRENT_USER,
        "machine": DPAPI_SCOPE_LOCAL_MACHINE,
        "local-machine": DPAPI_SCOPE_LOCAL_MACHINE,
        "localmachine": DPAPI_SCOPE_LOCAL_MACHINE,
    }
    scope = aliases.get(normalized)
    if scope is None:
        raise SecretStorageError(
            f"{DPAPI_SCOPE_ENV} 只允许 current-user 或 local-machine",
            reason="DPAPI_SCOPE_INVALID",
        )
    return scope


def _decode_dpapi_reference(reference: str) -> bytes:
    if reference.startswith(DPAPI_REFERENCE_PREFIX):
        payload = reference.removeprefix(DPAPI_REFERENCE_PREFIX)
    elif reference.startswith(DPAPI_MACHINE_REFERENCE_PREFIX):
        payload = reference.removeprefix(DPAPI_MACHINE_REFERENCE_PREFIX)
    else:
        payload = reference
    if not payload:
        raise SecretStorageError("DPAPI 密文为空")
    try:
        return base64.b64decode(payload.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise SecretStorageError("DPAPI 密文格式无效") from exc


def protect_secret(secret: str, *, account: str) -> str:
    if not isinstance(secret, str) or not secret or not secret.strip():
        raise SecretStorageError("API Key 不能为空")
    account = _validate_account(account)

    if sys.platform == "darwin":
        _get_macos_keychain().set_secret(account, secret)
        return f"{KEYCHAIN_REFERENCE_PREFIX}{account}"
    if sys.platform == "win32":
        scope = _dpapi_scope()
        encrypted = _get_windows_dpapi().protect(
            secret.encode("utf-8"),
            local_machine=scope == DPAPI_SCOPE_LOCAL_MACHINE,
        )
        prefix = (
            DPAPI_MACHINE_REFERENCE_PREFIX
            if scope == DPAPI_SCOPE_LOCAL_MACHINE
            else DPAPI_REFERENCE_PREFIX
        )
        return f"{prefix}{base64.b64encode(encrypted).decode('ascii')}"
    raise SecretStorageError(
        "当前操作系统不支持安全的 API Key 存储",
        reason="SECURE_STORAGE_PLATFORM_UNSUPPORTED",
    )


def unprotect_secret(reference: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise SecretStorageError("API Key 引用不能为空")

    if sys.platform == "darwin":
        if (
            reference.startswith(DPAPI_REFERENCE_PREFIX)
            or reference.startswith(DPAPI_MACHINE_REFERENCE_PREFIX)
            or ":" not in reference
        ):
            raise SecretStorageError("Windows DPAPI 密文不能在 macOS 上读取")
        account = _keychain_account_from_reference(reference)
        return _get_macos_keychain().get_secret(account)

    if sys.platform == "win32":
        if reference.startswith(KEYCHAIN_REFERENCE_PREFIX):
            raise SecretStorageError("macOS Keychain 引用不能在 Windows 上读取")
        if (
            ":" in reference
            and not reference.startswith(DPAPI_REFERENCE_PREFIX)
            and not reference.startswith(DPAPI_MACHINE_REFERENCE_PREFIX)
        ):
            raise SecretStorageError("未知的 API Key 引用格式")
        cleartext = _get_windows_dpapi().unprotect(_decode_dpapi_reference(reference))
        try:
            secret = cleartext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStorageError("DPAPI 解密结果不是有效 UTF-8") from exc
        if not secret:
            raise SecretStorageError("DPAPI 解密结果为空")
        return secret

    raise SecretStorageError(
        "当前操作系统不支持安全的 API Key 读取",
        reason="SECURE_STORAGE_PLATFORM_UNSUPPORTED",
    )


def probe_windows_dpapi() -> str:
    """Run an in-memory DPAPI round trip and return the configured scope."""
    if sys.platform != "win32":
        raise SecretStorageError(
            "Windows DPAPI 探针只能在原生 Windows Python 中运行",
            reason="SECURE_STORAGE_PLATFORM_UNSUPPORTED",
        )
    scope = _dpapi_scope()
    sentinel = secrets.token_bytes(32)
    dpapi = _get_windows_dpapi()
    encrypted = dpapi.protect(
        sentinel,
        local_machine=scope == DPAPI_SCOPE_LOCAL_MACHINE,
    )
    recovered = dpapi.unprotect(encrypted)
    if not hmac.compare_digest(sentinel, recovered):
        raise SecretStorageError(
            "Windows DPAPI 回环结果不一致",
            reason="DPAPI_ROUND_TRIP_MISMATCH",
        )
    return scope


def delete_secret(reference: str) -> bool:
    if sys.platform != "darwin":
        raise SecretStorageError("只有 macOS Keychain 引用支持显式删除")
    account = _keychain_account_from_reference(reference)
    return _get_macos_keychain().delete_secret(account)
