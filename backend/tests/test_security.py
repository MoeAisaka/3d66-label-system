from __future__ import annotations

from app.security import protect_secret, unprotect_secret


def test_windows_dpapi_round_trip() -> None:
    encrypted = protect_secret("demo-key-not-a-real-secret")
    assert encrypted != "demo-key-not-a-real-secret"
    assert unprotect_secret(encrypted) == "demo-key-not-a-real-secret"

