from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.doubao import DoubaoClient


def _client(
    *,
    provider: str = "doubao",
    thinking_mode: str = "auto",
    protocol: str = "openai_chat",
) -> DoubaoClient:
    client = object.__new__(DoubaoClient)
    client.config = SimpleNamespace(
        protocol=protocol,
        provider=provider,
        model_id="model-x",
        max_tokens=1000,
        temperature=0.2,
        structured_output=True,
        thinking_mode=thinking_mode,
    )
    return client


def test_doubao_auto_does_not_override_provider_default() -> None:
    payload: dict[str, object] = {}
    _, trace_reasoning, trace_thinking = _client()._generation_options(
        payload,
        output_budget=None,
        reasoning_effort="high",
        structured_output=False,
    )
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload
    assert trace_reasoning == "high"
    assert trace_thinking == "auto"


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_doubao_explicit_thinking_mode_uses_ark_payload(mode: str) -> None:
    payload: dict[str, object] = {}
    _, _, trace_thinking = _client(thinking_mode=mode)._generation_options(
        payload,
        output_budget=512,
        reasoning_effort=None,
        structured_output=False,
    )
    assert payload["thinking"] == {"type": mode}
    assert "reasoning_effort" not in payload
    assert trace_thinking == mode


def test_openai_keeps_reasoning_effort_and_ignores_doubao_payload() -> None:
    payload: dict[str, object] = {}
    _, trace_reasoning, trace_thinking = _client(
        provider="openai", thinking_mode="disabled"
    )._generation_options(
        payload,
        output_budget=None,
        reasoning_effort="low",
        structured_output=False,
    )
    assert payload["reasoning_effort"] == "low"
    assert "thinking" not in payload
    assert trace_reasoning == "low"
    assert trace_thinking == "disabled"


def test_unknown_thinking_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="thinking_mode"):
        _client(thinking_mode="sometimes")._generation_options(
            {},
            output_budget=None,
            reasoning_effort=None,
            structured_output=False,
        )


def test_doubao_thinking_mapping_is_protocol_independent() -> None:
    payload: dict[str, object] = {}
    _client(thinking_mode="disabled", protocol="openai_responses")._generation_options(
        payload,
        output_budget=512,
        reasoning_effort="high",
        structured_output=False,
    )
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
