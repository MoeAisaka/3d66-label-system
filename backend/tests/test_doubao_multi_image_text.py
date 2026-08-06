from __future__ import annotations

import asyncio
from types import SimpleNamespace

from PIL import Image

from app.doubao import DoubaoClient


def _client() -> DoubaoClient:
    client = object.__new__(DoubaoClient)
    client.api_key = "secret-value"
    client.config = SimpleNamespace(
        protocol="openai_chat",
        provider="doubao",
        model_id="model-x",
        max_tokens=1000,
        temperature=0.2,
        structured_output=True,
        max_retries=0,
    )
    return client


def test_chat_text_images_sends_individual_pages_without_structured_rewrite(tmp_path) -> None:
    first = tmp_path / "1.png"
    second = tmp_path / "2.png"
    Image.new("RGB", (10, 12), "white").save(first)
    Image.new("RGB", (12, 10), "black").save(second)
    client = _client()
    calls: list[dict[str, object]] = []

    async def fake_post(payload: dict[str, object]) -> dict[str, object]:
        calls.append(payload)
        return {
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {
                "prompt_tokens": 321,
                "completion_tokens": 45,
                "total_tokens": 366,
            },
        }

    client._post = fake_post  # type: ignore[method-assign]
    response = asyncio.run(client.chat_text_images(
        "system",
        "global user context",
        [("第1页", first, "image/png"), ("第2页", second, "image/png")],
        image_detail="low",
        max_image_count=16,
    ))

    assert response.parsed == {"status": "ok"}
    assert response.input_tokens == 321
    assert response.output_tokens == 45
    assert response.total_tokens == 366
    assert len(calls) == 1
    assert "response_format" not in calls[0]
    content = calls[0]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "global user context"}
    assert [item["type"] for item in content] == [
        "text", "text", "image_url", "text", "image_url",
    ]
    assert content[2]["image_url"]["detail"] == "low"
