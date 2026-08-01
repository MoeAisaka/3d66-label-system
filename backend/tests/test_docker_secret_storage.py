from __future__ import annotations

import stat

from app import security


def test_linux_file_aead_round_trip_survives_reload(tmp_path, monkeypatch) -> None:
    key_path = tmp_path / "data" / "secrets" / "master.key"
    monkeypatch.setenv(security.FILE_AEAD_KEY_ENV, str(key_path))
    monkeypatch.setattr(security.sys, "platform", "linux")
    reference = security.protect_secret("test-api-key", account="model-config-9")
    assert reference.startswith("file-aead:v1:model-config-9:")
    assert "test-api-key" not in reference
    assert security.unprotect_secret(reference) == "test-api-key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_file_aead_binds_ciphertext_to_account(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(security.FILE_AEAD_KEY_ENV, str(tmp_path / "master.key"))
    monkeypatch.setattr(security.sys, "platform", "linux")
    reference = security.protect_secret("test-api-key", account="model-config-9")
    tampered = reference.replace("model-config-9", "model-config-8", 1)
    try:
        security.unprotect_secret(tampered)
    except security.SecretStorageError as error:
        assert error.reason == "FILE_AEAD_DECRYPT_FAILED"
    else:
        raise AssertionError("tampered account must not decrypt")
