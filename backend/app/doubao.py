from __future__ import annotations

import base64
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .models import ModelConfig
from .security import unprotect_secret


@dataclass(frozen=True)
class DoubaoResponse:
    parsed: dict[str, Any]
    raw_text: str
    raw_payload: dict[str, Any]


def _image_data_url(path: Path, mime_type: str | None = None) -> str:
    media_type = mime_type or mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _extract_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
    output = payload.get("output") or []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                return str(content.get("text", ""))
    raise ValueError("模型响应中没有可读取的文本内容")


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型未返回合法 JSON")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型 JSON 顶层必须是对象")
    return value


class DoubaoClient:
    def __init__(self, config: ModelConfig):
        if not config.encrypted_api_key:
            raise ValueError("尚未配置豆包 API Key")
        self.config = config
        self.api_key = unprotect_secret(config.encrypted_api_key)

    @property
    def url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/{self.config.api_path.lstrip('/')}"

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = httpx.Timeout(float(self.config.timeout_seconds))
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            if response.is_error:
                detail = response.text[:1200]
                raise RuntimeError(f"豆包 API 返回 {response.status_code}: {detail}")
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("豆包 API 返回了无法识别的数据结构")
            return data

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        image_path: Path | None = None,
        mime_type: str | None = None,
    ) -> DoubaoResponse:
        content: str | list[dict[str, Any]]
        if image_path:
            content = [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(image_path, mime_type)}},
            ]
        else:
            content = user_prompt
        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.structured_output:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        attempts = max(1, self.config.max_retries + 1)
        for attempt in range(attempts):
            current_payload = dict(payload)
            if attempt > 0:
                retry_messages = list(current_payload["messages"])
                retry_messages.append(
                    {
                        "role": "user",
                        "content": "上次输出无法通过 JSON 校验。请严格只返回一个合法 JSON 对象，不要包含 Markdown。",
                    }
                )
                current_payload["messages"] = retry_messages
            try:
                raw = await self._post(current_payload)
                raw_text = _extract_message_text(raw)
                return DoubaoResponse(parsed=parse_json_text(raw_text), raw_text=raw_text, raw_payload=raw)
            except Exception as exc:  # API 与 JSON 错误都按配置重试
                last_error = exc
        raise RuntimeError(str(last_error) if last_error else "模型调用失败")

    async def test_connection(self) -> str:
        payload = {
            "model": self.config.model_id,
            "messages": [{"role": "user", "content": "只回复：连接成功"}],
            "temperature": 0,
            "max_tokens": 16,
        }
        raw = await self._post(payload)
        return _extract_message_text(raw).strip()
