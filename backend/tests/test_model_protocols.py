from __future__ import annotations

from types import SimpleNamespace

from app.doubao import DoubaoClient, _extract_message_text


def _client(protocol: str) -> DoubaoClient:
    client = object.__new__(DoubaoClient)
    client.api_key = "secret-value"
    client.config = SimpleNamespace(protocol=protocol, model_id="model-x")
    return client


def test_openai_chat_protocol_shape() -> None:
    client = _client("openai_chat")
    assert client._headers()["Authorization"] == "Bearer secret-value"
    payload = client._protocol_payload("system", "user")
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]


def test_openai_responses_protocol_shape() -> None:
    client = _client("openai_responses")
    payload = client._protocol_payload("system", "user")
    assert payload["input"][1] == {"role": "user", "content": "user"}


def test_anthropic_protocol_shape_and_response() -> None:
    client = _client("anthropic_messages")
    assert client._headers() == {
        "Content-Type": "application/json",
        "x-api-key": "secret-value",
        "anthropic-version": "2023-06-01",
    }
    payload = client._protocol_payload("system", "user")
    assert payload["system"] == "system"
    assert payload["messages"] == [{"role": "user", "content": "user"}]
    assert _extract_message_text({"content": [{"type": "text", "text": "ok"}]}) == "ok"

