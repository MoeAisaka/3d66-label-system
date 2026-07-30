#!/usr/bin/env python3
"""Validate and optionally send one production feedback event."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


URL_ENV = "LABELLAB_FEEDBACK_URL"
TOKEN_ENV = "LABELLAB_FEEDBACK_TOKEN"
REQUIRED_TOP_LEVEL = {
    "event_id",
    "schema_version",
    "event_type",
    "source_system",
    "occurred_at",
    "payload",
}
REQUIRED_PAYLOAD = {
    "production_case_id",
    "prompt_version",
    "severity",
    "model_output",
    "human_truth",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def load_event(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取事件文件：{exc.strerror or '读取失败'}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"事件文件不是合法 JSON（第 {exc.lineno} 行）") from None
    if not isinstance(value, dict):
        raise ValueError("事件 JSON 顶层必须是对象")
    missing = REQUIRED_TOP_LEVEL - value.keys()
    if missing:
        raise ValueError(f"事件缺少字段：{', '.join(sorted(missing))}")
    if value["schema_version"] != "production-feedback-v1":
        raise ValueError("不支持的 schema_version")
    if value["event_type"] != "human_correction_finalized":
        raise ValueError("不支持的 event_type")
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    missing_payload = REQUIRED_PAYLOAD - payload.keys()
    if missing_payload:
        raise ValueError(
            f"payload 缺少字段：{', '.join(sorted(missing_payload))}"
        )
    if payload["severity"] not in {"P0", "P1", "P2", "P3"}:
        raise ValueError("payload.severity 非法")
    if not isinstance(payload["model_output"], dict) or not isinstance(
        payload["human_truth"], dict
    ):
        raise ValueError("model_output 与 human_truth 必须是对象")
    return value


def safe_summary(event: dict[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    digest = hashlib.sha256(canonical_json(event).encode("utf-8")).hexdigest()
    return {
        "event_id": str(event["event_id"]),
        "schema_version": str(event["schema_version"]),
        "event_type": str(event["event_type"]),
        "source_system": str(event["source_system"]),
        "occurred_at": str(event["occurred_at"]),
        "payload_fields": sorted(str(key) for key in payload),
        "event_sha256": digest,
    }


def configured_destination() -> tuple[str, str]:
    url = os.getenv(URL_ENV, "").strip()
    token = os.getenv(TOKEN_ENV, "").strip()
    if not url or not token:
        raise ValueError(
            f"--send 需要同时配置 {URL_ENV} 与 {TOKEN_ENV}"
        )
    parsed = urllib.parse.urlsplit(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if parsed.scheme != "https" and not local_http:
        raise ValueError("发送 URL 必须使用 HTTPS；仅本机联调允许 HTTP")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("发送 URL 不得包含用户信息或 fragment")
    return url, token


def send_event(url: str, token: str, event: dict[str, Any]) -> int:
    body = canonical_json(event).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "LabelLab-production-feedback-example/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except urllib.error.URLError:
        raise ValueError("发送失败：网络或 TLS 连接异常") from None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验生产纠偏事件；默认 dry-run，仅 --send 才发送。"
    )
    parser.add_argument("event_file", type=Path, help="UTF-8 JSON 事件文件")
    parser.add_argument(
        "--send",
        action="store_true",
        help="使用环境变量中的 URL/token 发起 POST",
    )
    args = parser.parse_args()
    try:
        event = load_event(args.event_file)
        summary = safe_summary(event)
        if not args.send:
            print(canonical_json({"mode": "dry-run", "event": summary}))
            return 0
        url, token = configured_destination()
        status = send_event(url, token, event)
        print(canonical_json({"mode": "send", "status": status, "event": summary}))
        return 0 if 200 <= status < 300 else 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
