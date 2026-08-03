from __future__ import annotations

import asyncio
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


def test_chat_text_accepts_natural_language_without_json_retry() -> None:
    client = _client("openai_chat")
    client.config = SimpleNamespace(
        protocol="openai_chat",
        provider="doubao",
        model_id="model-x",
        max_tokens=1000,
        temperature=0.2,
        structured_output=True,
        max_retries=3,
    )
    calls: list[dict[str, object]] = []

    async def fake_post(payload: dict[str, object]) -> dict[str, object]:
        calls.append(payload)
        return {"choices": [{"message": {"content": "这是一段自由结论。"}}]}

    client._post = fake_post  # type: ignore[method-assign]
    response = asyncio.run(client.chat_text("自由系统提示", "自由用户提示"))
    assert response.raw_text == "这是一段自由结论。"
    assert response.parsed == {}
    assert response.attempt_count == 1
    assert len(calls) == 1
    assert "response_format" not in calls[0]
