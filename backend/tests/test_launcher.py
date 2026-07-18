import json

from app import launcher


class FakeResponse:
    def __init__(self, payload: dict[str, str]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_existing_3d66_service_is_detected(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse({"service": "3d66-label-system"}),
    )
    assert launcher._service_is_running(8080) is True


def test_other_http_service_is_not_mistaken_for_3d66(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse({"service": "something-else"}),
    )
    assert launcher._service_is_running(8080) is False
