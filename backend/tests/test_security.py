from __future__ import annotations

import base64
import sys
import uuid

import pytest

from app import security


FAKE_SECRET_V1 = "obvious-fake-api-key-第一版"
FAKE_SECRET_V2 = "obvious-fake-api-key-第二版"


class FakeMacOSSecurityFramework:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str, str]] = []

    def add(self, service: str, account: str, secret: bytes) -> int:
        self.calls.append(("add", service, account))
        key = (service, account)
        if key in self.items:
            return security._ERR_SEC_DUPLICATE_ITEM
        self.items[key] = secret
        return security._ERR_SEC_SUCCESS

    def copy(self, service: str, account: str) -> tuple[int, bytes | None]:
        self.calls.append(("copy", service, account))
        value = self.items.get((service, account))
        if value is None:
            return security._ERR_SEC_ITEM_NOT_FOUND, None
        return security._ERR_SEC_SUCCESS, value

    def update(self, service: str, account: str, secret: bytes) -> int:
        self.calls.append(("update", service, account))
        key = (service, account)
        if key not in self.items:
            return security._ERR_SEC_ITEM_NOT_FOUND
        self.items[key] = secret
        return security._ERR_SEC_SUCCESS

    def delete(self, service: str, account: str) -> int:
        self.calls.append(("delete", service, account))
        key = (service, account)
        if key not in self.items:
            return security._ERR_SEC_ITEM_NOT_FOUND
        del self.items[key]
        return security._ERR_SEC_SUCCESS


class FakeDPAPI:
    def __init__(self) -> None:
        self.protected: list[bytes] = []
        self.unprotected: list[bytes] = []

    def protect(self, cleartext: bytes) -> bytes:
        self.protected.append(cleartext)
        return b"\x00fake-dpapi-ciphertext\xff"

    def unprotect(self, ciphertext: bytes) -> bytes:
        self.unprotected.append(ciphertext)
        return FAKE_SECRET_V1.encode("utf-8")


def _install_fake_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> FakeMacOSSecurityFramework:
    bindings = FakeMacOSSecurityFramework()
    keychain = security._MacOSKeychain(bindings=bindings)
    monkeypatch.setattr(security.sys, "platform", "darwin")
    monkeypatch.setattr(security, "_get_macos_keychain", lambda: keychain)
    return bindings


def test_macos_references_are_stable_distinct_and_contain_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = _install_fake_keychain(monkeypatch)

    model_reference = security.protect_secret(
        FAKE_SECRET_V1,
        account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    )
    optimizer_reference = security.protect_secret(
        FAKE_SECRET_V2,
        account=security.OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    )

    assert model_reference == "keychain:v1:model-config"
    assert optimizer_reference == "keychain:v1:optimizer-config"
    assert model_reference != optimizer_reference
    assert FAKE_SECRET_V1 not in model_reference
    assert FAKE_SECRET_V2 not in optimizer_reference
    assert len(bindings.items) == 2
    assert security.unprotect_secret(model_reference) == FAKE_SECRET_V1
    assert security.unprotect_secret(optimizer_reference) == FAKE_SECRET_V2


def test_macos_same_account_updates_in_place_and_only_new_utf8_value_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = _install_fake_keychain(monkeypatch)
    account = security.MODEL_CONFIG_KEYCHAIN_ACCOUNT

    first_reference = security.protect_secret(FAKE_SECRET_V1, account=account)
    second_reference = security.protect_secret(FAKE_SECRET_V2, account=account)

    assert first_reference == second_reference
    assert len(bindings.items) == 1
    assert security.unprotect_secret(second_reference) == FAKE_SECRET_V2
    assert FAKE_SECRET_V1.encode("utf-8") not in bindings.items.values()
    assert [call[0] for call in bindings.calls[:3]] == ["update", "add", "update"]


def test_macos_delete_removes_the_referenced_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_keychain(monkeypatch)
    reference = security.protect_secret(
        FAKE_SECRET_V1,
        account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    )

    assert security.delete_secret(reference) is True
    assert security.delete_secret(reference) is False
    with pytest.raises(security.SecretNotFoundError):
        security.unprotect_secret(reference)


def test_macos_duplicate_add_race_retries_as_update() -> None:
    class DuplicateRaceBindings(FakeMacOSSecurityFramework):
        def __init__(self) -> None:
            super().__init__()
            self.update_count = 0

        def update(self, service: str, account: str, secret: bytes) -> int:
            self.update_count += 1
            if self.update_count == 1:
                return security._ERR_SEC_ITEM_NOT_FOUND
            self.items[(service, account)] = secret
            return security._ERR_SEC_SUCCESS

        def add(self, service: str, account: str, secret: bytes) -> int:
            return security._ERR_SEC_DUPLICATE_ITEM

    bindings = DuplicateRaceBindings()
    keychain = security._MacOSKeychain(bindings=bindings)

    keychain.set_secret("isolated-race-account", FAKE_SECRET_V1)

    assert bindings.update_count == 2
    assert keychain.get_secret("isolated-race-account") == FAKE_SECRET_V1


@pytest.mark.parametrize("status", [-50, -25291])
def test_macos_osstatus_errors_never_include_the_secret(status: int) -> None:
    class FailingBindings(FakeMacOSSecurityFramework):
        def update(self, service: str, account: str, secret: bytes) -> int:
            return status

    keychain = security._MacOSKeychain(bindings=FailingBindings())

    with pytest.raises(security.SecretStorageError) as error:
        keychain.set_secret("isolated-error-account", FAKE_SECRET_V1)

    assert str(status) in str(error.value)
    assert FAKE_SECRET_V1 not in str(error.value)


def test_empty_or_whitespace_secret_is_rejected_before_platform_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security.sys, "platform", "darwin")

    for value in ("", " \t "):
        with pytest.raises(security.SecretStorageError, match="不能为空"):
            security.protect_secret(
                value,
                account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
            )


def test_unknown_account_and_unknown_reference_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security.sys, "platform", "darwin")

    with pytest.raises(security.SecretStorageError, match="account"):
        security.protect_secret(FAKE_SECRET_V1, account="unexpected-account")
    with pytest.raises(security.SecretStorageError, match="account"):
        security.unprotect_secret("keychain:v1:unexpected-account")
    with pytest.raises(security.SecretStorageError, match="不是受支持"):
        security.unprotect_secret("vault:v9:model-config")


def test_wrong_platform_references_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.sys, "platform", "darwin")
    with pytest.raises(security.SecretStorageError, match="Windows DPAPI"):
        security.unprotect_secret("dpapi:v1:ZmFrZQ==")
    with pytest.raises(security.SecretStorageError, match="Windows DPAPI"):
        security.unprotect_secret("ZmFrZS1sZWdhY3k=")

    monkeypatch.setattr(security.sys, "platform", "win32")
    with pytest.raises(security.SecretStorageError, match="macOS Keychain"):
        security.unprotect_secret("keychain:v1:model-config")
    with pytest.raises(security.SecretStorageError, match="未知"):
        security.unprotect_secret("vault:v9:model-config")


def test_other_platforms_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.sys, "platform", "linux")

    with pytest.raises(security.SecretStorageError, match="不支持"):
        security.protect_secret(
            FAKE_SECRET_V1,
            account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
        )
    with pytest.raises(security.SecretStorageError, match="不支持"):
        security.unprotect_secret("keychain:v1:model-config")


def test_non_windows_dpapi_guard_rejects_before_loading_windows_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows_library_calls: list[str] = []

    def fail_if_windows_library_is_loaded(name: str, **_kwargs: object) -> object:
        windows_library_calls.append(name)
        raise AssertionError("non-Windows test must not load a Windows library")

    monkeypatch.setattr(security.sys, "platform", "linux")
    monkeypatch.setattr(
        security.ctypes,
        "WinDLL",
        fail_if_windows_library_is_loaded,
        raising=False,
    )

    with pytest.raises(security.SecretStorageError, match="只能在 Windows"):
        security._get_windows_dpapi()
    with pytest.raises(security.SecretStorageError, match="只能在 Windows"):
        security._WindowsDPAPI()

    assert windows_library_calls == []


def test_windows_new_dpapi_prefix_and_legacy_unprefixed_ciphertext_are_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dpapi = FakeDPAPI()
    monkeypatch.setattr(security.sys, "platform", "win32")
    monkeypatch.setattr(security, "_get_windows_dpapi", lambda: dpapi)

    reference = security.protect_secret(
        FAKE_SECRET_V1,
        account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    )
    legacy = reference.removeprefix(security.DPAPI_REFERENCE_PREFIX)

    assert reference.startswith("dpapi:v1:")
    assert FAKE_SECRET_V1 not in reference
    assert security.unprotect_secret(reference) == FAKE_SECRET_V1
    assert security.unprotect_secret(legacy) == FAKE_SECRET_V1
    assert dpapi.protected == [FAKE_SECRET_V1.encode("utf-8")]
    expected_ciphertext = base64.b64decode(legacy.encode("ascii"), validate=True)
    assert dpapi.unprotected == [expected_ciphertext, expected_ciphertext]


def test_invalid_dpapi_ciphertext_is_rejected_without_calling_dpapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dpapi = FakeDPAPI()
    monkeypatch.setattr(security.sys, "platform", "win32")
    monkeypatch.setattr(security, "_get_windows_dpapi", lambda: dpapi)

    with pytest.raises(security.SecretStorageError, match="格式无效"):
        security.unprotect_secret("dpapi:v1:not valid base64!")

    assert dpapi.unprotected == []


def test_config_request_repr_masks_secret_and_storage_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from app import main
    from pydantic import SecretStr

    payload = main.ModelConfigUpdate(
        name="fake",
        base_url="https://example.invalid",
        api_path="/chat/completions",
        model_id="fake-model",
        api_key=FAKE_SECRET_V1,
        temperature=0.1,
        max_tokens=1024,
        timeout_seconds=30,
        max_retries=0,
        max_concurrency=1,
    )
    assert FAKE_SECRET_V1 not in repr(payload)
    assert (
        main.ModelConfigUpdate.model_json_schema()["properties"]["api_key"]["maxLength"]
        == 1000
    )

    def fail_storage(_secret: str, *, account: str) -> str:
        raise security.SecretStorageError("sanitized storage failure")

    monkeypatch.setattr(main, "protect_secret", fail_storage)
    with pytest.raises(HTTPException) as error:
        main._protected_api_key(
            SecretStr(FAKE_SECRET_V1),
            account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
        )
    assert error.value.detail == "API Key 安全存储失败"
    assert FAKE_SECRET_V1 not in str(error.value)


def test_model_and_optimizer_config_writes_use_different_stable_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main
    from app.models import ModelConfig, OptimizerConfig

    class FakeSession:
        def __init__(self, config: ModelConfig | OptimizerConfig) -> None:
            self.config = config
            self.committed = False

        def scalar(self, _statement: object) -> ModelConfig | OptimizerConfig:
            return self.config

        def add(self, _value: object) -> None:
            raise AssertionError("existing config should be reused")

        def commit(self) -> None:
            self.committed = True

    accounts: list[str] = []

    def fake_protected_api_key(_value: object, *, account: str) -> str:
        accounts.append(account)
        return f"{security.KEYCHAIN_REFERENCE_PREFIX}{account}"

    monkeypatch.setattr(main, "_protected_api_key", fake_protected_api_key)
    model = ModelConfig()
    optimizer = OptimizerConfig()
    model_db = FakeSession(model)
    optimizer_db = FakeSession(optimizer)

    main.update_model_config(
        main.ModelConfigUpdate(
            name="fake-model-config",
            base_url="https://example.invalid",
            api_path="/chat/completions",
            model_id="fake-model",
            api_key=FAKE_SECRET_V1,
            temperature=0.1,
            max_tokens=1024,
            timeout_seconds=30,
            max_retries=0,
            max_concurrency=1,
        ),
        _user=object(),
        db=model_db,  # type: ignore[arg-type]
    )
    main.update_optimizer_config(
        main.OptimizerConfigUpdate(
            name="fake-optimizer-config",
            base_url="https://example.invalid",
            api_path="/chat/completions",
            model_id="fake-optimizer",
            api_key=FAKE_SECRET_V2,
        ),
        _user=object(),
        db=optimizer_db,  # type: ignore[arg-type]
    )

    assert accounts == [
        security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
        security.OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    ]
    assert model.encrypted_api_key == "keychain:v1:model-config"
    assert optimizer.encrypted_api_key == "keychain:v1:optimizer-config"
    assert model_db.committed is True
    assert optimizer_db.committed is True


def test_numbered_model_config_accounts_are_allowed_but_other_accounts_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dpapi = FakeDPAPI()
    monkeypatch.setattr(security.sys, "platform", "win32")
    monkeypatch.setattr(security, "_get_windows_dpapi", lambda: dpapi)
    reference = security.protect_secret(
        FAKE_SECRET_V1, account="model-config-17"
    )
    assert reference.startswith(security.DPAPI_REFERENCE_PREFIX)
    with pytest.raises(security.SecretStorageError, match="不支持"):
        security.protect_secret(FAKE_SECRET_V1, account="model-config-0")


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 验证真实 DPAPI")
def test_windows_dpapi_real_round_trip_and_legacy_compatibility() -> None:
    reference = security.protect_secret(
        FAKE_SECRET_V1,
        account=security.MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    )

    assert reference.startswith(security.DPAPI_REFERENCE_PREFIX)
    assert security.unprotect_secret(reference) == FAKE_SECRET_V1
    assert (
        security.unprotect_secret(
            reference.removeprefix(security.DPAPI_REFERENCE_PREFIX)
        )
        == FAKE_SECRET_V1
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="仅在 macOS 验证真实登录 Keychain")
def test_macos_keychain_real_isolated_round_trip_update_and_cleanup() -> None:
    service = f"com.3d66.label-system.pytest.{uuid.uuid4().hex}"
    account = "isolated-integration-account"
    keychain = security._MacOSKeychain(service=service)

    try:
        keychain.delete_secret(account, missing_ok=True)
    except security.SecretStorageError as exc:
        if "OSStatus -50" in str(exc):
            pytest.skip("当前执行沙箱不允许访问登录 Keychain")
        raise

    try:
        keychain.set_secret(account, FAKE_SECRET_V1)
        assert keychain.get_secret(account) == FAKE_SECRET_V1

        keychain.set_secret(account, FAKE_SECRET_V2)
        assert keychain.get_secret(account) == FAKE_SECRET_V2
        assert keychain.get_secret(account) != FAKE_SECRET_V1
    finally:
        keychain.delete_secret(account, missing_ok=True)

    with pytest.raises(security.SecretNotFoundError):
        keychain.get_secret(account)
