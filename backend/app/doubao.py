from __future__ import annotations

import base64
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx

from .models import ModelConfig, OptimizerConfig
from .security import unprotect_secret


@dataclass(frozen=True)
class DoubaoResponse:
    parsed: dict[str, Any]
    raw_text: str
    raw_payload: dict[str, Any]
    upstream_status_code: int | None = None
    request_correlation_id: str | None = None
    attempt_count: int = 1
    output_budget: int | None = None
    reasoning_effort: str | None = None
    input_image_bytes: int | None = None


@dataclass(frozen=True)
class _UpstreamResponse:
    payload: dict[str, Any]
    status_code: int
    request_correlation_id: str | None


class DoubaoError(RuntimeError):
    technical_error_type = "non_retryable"
    retryable = False
    upstream_status_code: int | None = None
    request_correlation_id: str | None = None
    attempt_count = 0


class DoubaoHTTPError(DoubaoError):
    def __init__(self, status_code: int, headers: dict[str, str]):
        super().__init__(f"模型 API HTTP {status_code}")
        self.status_code = status_code
        self.upstream_status_code = status_code
        self.request_correlation_id = _request_correlation_id(headers)
        self.headers = headers
        self.technical_error_type = (
            "429"
            if status_code == 429
            else "provider5xx"
            if 500 <= status_code <= 599
            else "non_retryable"
        )
        self.retryable = self.technical_error_type != "non_retryable"


class DoubaoTransportError(DoubaoError):
    def __init__(self, error_type: str):
        if error_type not in {"timeout", "network"}:
            raise ValueError("不支持的传输错误类型")
        super().__init__(f"模型 API {error_type}")
        self.technical_error_type = error_type
        self.retryable = True


class DoubaoParseError(DoubaoError):
    def __init__(self, message: str, *, truncated: bool = False):
        super().__init__(message)
        self.technical_error_type = (
            "json_truncated" if truncated else "transient_parse"
        )
        self.retryable = True


_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-trace-id",
    "trace-id",
    "x-tt-logid",
)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")


def _request_correlation_id(headers: Mapping[str, str]) -> str | None:
    normalized = {str(key).lower(): str(value).strip() for key, value in headers.items()}
    for header_name in _REQUEST_ID_HEADERS:
        value = normalized.get(header_name)
        if value and _SAFE_REQUEST_ID.fullmatch(value):
            return value
    return None


def _image_data_url(path: Path, mime_type: str | None = None) -> str:
    media_type = mime_type or mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _bounded_image_data_url(
    path: Path,
    mime_type: str | None,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    """Read and encode one immutable payload snapshot with a hard byte ceiling."""
    if max_bytes <= 0:
        raise ValueError("诊断图片总量超过安全请求上限")
    try:
        with path.open("rb") as image_file:
            raw = image_file.read(max_bytes + 1)
    except OSError as exc:
        raise ValueError("诊断图片不可读取") from exc
    if not raw:
        raise ValueError("诊断图片为空文件")
    if len(raw) > max_bytes:
        raise ValueError("诊断图片超过安全请求字节上限")
    media_type = mime_type or mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{media_type};base64,{encoded}", len(raw)


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
    raise DoubaoParseError("模型响应中没有可读取的文本内容")


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise DoubaoParseError(
                "模型未返回合法 JSON",
                truncated=first_error.pos >= max(0, len(cleaned) - 2),
            ) from first_error
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DoubaoParseError(
                "模型未返回合法 JSON",
                truncated=exc.pos >= max(0, len(cleaned[start : end + 1]) - 2),
            ) from exc
    if not isinstance(value, dict):
        raise DoubaoParseError("模型 JSON 顶层必须是对象")
    return value


class DoubaoClient:
    def __init__(self, config: ModelConfig | OptimizerConfig):
        if not config.encrypted_api_key:
            raise ValueError("尚未配置模型 API Key")
        self.config = config
        self.api_key = unprotect_secret(config.encrypted_api_key)

    @property
    def url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/{self.config.api_path.lstrip('/')}"

    async def _post(self, payload: dict[str, Any]) -> _UpstreamResponse:
        timeout = httpx.Timeout(float(self.config.timeout_seconds))
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    self.url, headers=headers, json=payload
                )
            except httpx.TimeoutException as exc:
                raise DoubaoTransportError("timeout") from exc
            except httpx.NetworkError as exc:
                raise DoubaoTransportError("network") from exc
            if response.is_error:
                raise DoubaoHTTPError(
                    response.status_code,
                    dict(response.headers),
                )
            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                error = DoubaoParseError(
                    "模型 API 返回无效 JSON",
                    truncated=exc.pos >= max(0, len(exc.doc) - 2),
                )
                error.upstream_status_code = response.status_code
                error.request_correlation_id = _request_correlation_id(
                    response.headers
                )
                raise error from exc
            if not isinstance(data, dict):
                error = DoubaoParseError(
                    "豆包 API 返回了无法识别的数据结构"
                )
                error.upstream_status_code = response.status_code
                error.request_correlation_id = _request_correlation_id(
                    response.headers
                )
                raise error
            return _UpstreamResponse(
                payload=data,
                status_code=response.status_code,
                request_correlation_id=_request_correlation_id(response.headers),
            )

    def _generation_options(
        self,
        payload: dict[str, Any],
        *,
        output_budget: int | None,
        reasoning_effort: str | None,
        structured_output: bool | None,
    ) -> tuple[int, str | None]:
        actual_budget = (
            self.config.max_tokens if output_budget is None else output_budget
        )
        if actual_budget < 1:
            raise ValueError("模型输出预算必须大于 0")
        actual_reasoning_effort = reasoning_effort
        if self.config.provider == "openai":
            payload["max_completion_tokens"] = actual_budget
            actual_reasoning_effort = actual_reasoning_effort or "high"
            payload["reasoning_effort"] = actual_reasoning_effort
        else:
            payload["temperature"] = self.config.temperature
            payload["max_tokens"] = actual_budget
            if actual_reasoning_effort is not None:
                payload["reasoning_effort"] = actual_reasoning_effort
        use_structured_output = (
            self.config.structured_output
            if structured_output is None
            else structured_output
        )
        if use_structured_output:
            payload["response_format"] = {"type": "json_object"}
        return actual_budget, actual_reasoning_effort

    def _attempts(self, max_attempts: int | None) -> int:
        if max_attempts is None:
            return max(1, self.config.max_retries + 1)
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts < 1
        ):
            raise ValueError("模型调用次数必须大于 0")
        return max_attempts

    @staticmethod
    def _unpack_upstream(
        response: _UpstreamResponse | dict[str, Any],
    ) -> tuple[dict[str, Any], int | None, str | None]:
        # 保持测试替身和既有私有扩展返回 dict 时的兼容性。
        if isinstance(response, _UpstreamResponse):
            return (
                response.payload,
                response.status_code,
                response.request_correlation_id,
            )
        return response, None, None

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        image_path: Path | None = None,
        mime_type: str | None = None,
        *,
        max_attempts: int | None = None,
        output_budget: int | None = None,
        reasoning_effort: str | None = None,
        structured_output: bool | None = None,
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
        }
        actual_budget, actual_reasoning_effort = self._generation_options(
            payload,
            output_budget=output_budget,
            reasoning_effort=reasoning_effort,
            structured_output=structured_output,
        )

        last_error: Exception | None = None
        attempts = self._attempts(max_attempts)
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
                upstream = await self._post(current_payload)
                raw, status_code, request_id = self._unpack_upstream(upstream)
                try:
                    raw_text = _extract_message_text(raw)
                    parsed = parse_json_text(raw_text)
                except DoubaoError as exc:
                    exc.upstream_status_code = status_code
                    exc.request_correlation_id = request_id
                    raise
                return DoubaoResponse(
                    parsed=parsed,
                    raw_text=raw_text,
                    raw_payload=raw,
                    upstream_status_code=status_code,
                    request_correlation_id=request_id,
                    attempt_count=attempt + 1,
                    output_budget=actual_budget,
                    reasoning_effort=actual_reasoning_effort,
                )
            except Exception as exc:  # API 与 JSON 错误都按配置重试
                if isinstance(exc, DoubaoError):
                    exc.attempt_count = attempt + 1
                last_error = exc
        if last_error is not None:
            raise last_error
        raise DoubaoError("模型调用失败")

    async def chat_json_images(
        self,
        system_prompt: str,
        samples: list[tuple[str, Path, str | None]],
        *,
        max_attempts: int | None = None,
        output_budget: int | None = None,
        reasoning_effort: str | None = None,
        structured_output: bool | None = None,
        max_image_count: int | None = None,
        max_single_image_bytes: int | None = None,
        max_total_image_bytes: int | None = None,
    ) -> DoubaoResponse:
        if max_image_count is not None and len(samples) > max_image_count:
            raise ValueError("诊断图片数量超过安全请求上限")
        remaining_bytes = max_total_image_bytes
        input_image_bytes = 0
        content: list[dict[str, Any]] = []
        for text, image_path, mime_type in samples:
            limits = [
                limit
                for limit in (remaining_bytes, max_single_image_bytes)
                if limit is not None
            ]
            image_url, image_bytes = _bounded_image_data_url(
                image_path,
                mime_type,
                max_bytes=(
                    min(limits) if limits else image_path.stat().st_size
                ),
            )
            input_image_bytes += image_bytes
            if remaining_bytes is not None:
                remaining_bytes -= image_bytes
            content.append({"type": "text", "text": text})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": "high"},
                }
            )
        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        actual_budget, actual_reasoning_effort = self._generation_options(
            payload,
            output_budget=output_budget,
            reasoning_effort=reasoning_effort,
            structured_output=structured_output,
        )

        last_error: Exception | None = None
        attempts = self._attempts(max_attempts)
        for attempt in range(attempts):
            try:
                upstream = await self._post(payload)
                raw, status_code, request_id = self._unpack_upstream(upstream)
                try:
                    raw_text = _extract_message_text(raw)
                    parsed = parse_json_text(raw_text)
                except DoubaoError as exc:
                    exc.upstream_status_code = status_code
                    exc.request_correlation_id = request_id
                    raise
                return DoubaoResponse(
                    parsed=parsed,
                    raw_text=raw_text,
                    raw_payload=raw,
                    upstream_status_code=status_code,
                    request_correlation_id=request_id,
                    attempt_count=attempt + 1,
                    output_budget=actual_budget,
                    reasoning_effort=actual_reasoning_effort,
                    input_image_bytes=input_image_bytes,
                )
            except Exception as exc:
                if isinstance(exc, DoubaoError):
                    exc.attempt_count = attempt + 1
                last_error = exc
        if last_error is not None:
            raise last_error
        raise DoubaoError("模型调用失败")

    async def test_connection(self) -> str:
        payload = {
            "model": self.config.model_id,
            "messages": [{"role": "user", "content": "只回复：连接成功"}],
        }
        if self.config.provider == "openai":
            payload["max_completion_tokens"] = 64
            payload["reasoning_effort"] = "low"
        else:
            payload["temperature"] = 0
            payload["max_tokens"] = 16
        upstream = await self._post(payload)
        raw, _status_code, _request_id = self._unpack_upstream(upstream)
        return _extract_message_text(raw).strip()
