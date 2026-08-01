#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DIMENSIONS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_coherence",
    "visual_hierarchy",
    "detail_finish",
    "contemporary_relevance",
    "presentation_integrity",
)


def _precheck() -> dict[str, Any]:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "住宅设计",
            "primary_confidence": 0.96,
        },
        "image_quality": {
            "quality_severity": "normal",
            "confidence": 0.96,
            "evidence": ["隔离验收图片清晰"],
        },
        "media_form": {
            "real_photo": {"status": "yes", "confidence": 0.96},
            "rendering": {"status": "no", "confidence": 0.96},
            "ai_generated": {"status": "no", "confidence": 0.96},
            "professional_photography": {"status": "no", "confidence": 0.96},
            "casual_snapshot": {"status": "no", "confidence": 0.96},
            "documentary_record": {"status": "no", "confidence": 0.96},
            "collage_or_multiview": {"status": "no", "confidence": 0.96},
            "unfinished_scene": {"status": "no", "confidence": 0.96},
            "white_background_product": {"status": "no", "confidence": 0.96},
        },
    }


def _aesthetic() -> dict[str, Any]:
    return {
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": [f"{key} 的隔离验收证据"],
                "defects": [],
            }
            for key in DIMENSIONS
        },
        "decision_rules": {
            "hard_gate_triggered": False,
            "hard_gate_target": "none",
            "hard_gate_reasons": [],
            "level_cap": "none",
            "level_cap_reasons": [],
            "manual_review_required": False,
        },
        "overall_confidence": 0.96,
        "needs_review": False,
        "review_reasons": [],
    }


class State:
    def __init__(self, state_dir: Path, *, timeout_seconds: float = 12.0):
        self.state_dir = state_dir
        self.timeout_seconds = timeout_seconds
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.mode_file = self.state_dir / "mode.txt"
        self.requests_file = self.state_dir / "requests.jsonl"
        self.lock = threading.Lock()
        self.count = 0
        if not self.mode_file.exists():
            self.mode_file.write_text("ok\n", encoding="utf-8")

    def mode(self) -> str:
        try:
            return self.mode_file.read_text(encoding="utf-8").strip() or "ok"
        except OSError:
            return "ok"

    def record(self, payload: dict[str, Any]) -> int:
        with self.lock:
            self.count += 1
            count = self.count
            mode = self.mode()
            with self.requests_file.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "request": count,
                            "mode": mode,
                            "model": payload.get("model"),
                            "stage": _stage(payload),
                            "response_status": 503 if mode == "provider5xx" else 200,
                            "usage_included": mode not in {"missing_usage", "provider5xx"},
                            "response_delayed": mode == "timeout",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            return count


def _system_prompt(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            return str(message.get("content") or "")
    return ""


def _stage(payload: dict[str, Any]) -> str:
    system_prompt = _system_prompt(payload)
    if "纠偏诊断专家" in system_prompt:
        return "optimizer_diagnostic"
    if "提示词优化专家" in system_prompt:
        return "optimizer_synthesis"
    if "E2E_STAGE_A" in system_prompt:
        return "evaluation_stage_a"
    if "E2E_STAGE_B" in system_prompt:
        return "evaluation_stage_b"
    return "connection_test"


def _content(payload: dict[str, Any]) -> dict[str, Any] | str:
    stage = _stage(payload)
    if stage == "optimizer_diagnostic":
        return {
            "summary": "目标错例被高估，候选应收紧普通素材的等级锚点。",
            "patterns": ["普通表现被高估"],
            "prompt_risks": ["不得破坏稳定对照和锁定盲测"],
        }
    if stage == "optimizer_synthesis":
        return {
            "candidates": [
                {
                    "system_prompt": "E2E_STAGE_B_CANDIDATE：逐维评测并返回合法 JSON。",
                    "user_prompt": "根据 {{precheck_json}} 评测，规则 {{rubric_version}}。",
                    "change_note": "收紧普通素材高估，同时保持稳定对照。",
                }
            ]
        }
    if stage == "evaluation_stage_a":
        return _precheck()
    if stage == "evaluation_stage_b":
        return _aesthetic()
    return "连接成功"


def _handler(state: State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "3d66-mock-model/1.0"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-request-id", f"mock-{uuid.uuid4().hex[:12]}")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Expected when a timeout scenario closes the client connection.
                return

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(200, {"ok": True, "mode": state.mode(), "requests": state.count})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid_request"})
                return
            request_number = state.record(payload)
            mode = state.mode()
            if mode == "provider5xx":
                self._json(503, {"error": {"message": "injected provider failure"}})
                return
            if mode == "timeout":
                time.sleep(state.timeout_seconds)
            if mode == "invalid_json":
                content = "{invalid"
            else:
                content = json.dumps(_content(payload), ensure_ascii=False)
            response: dict[str, Any] = {
                "id": f"mock-response-{request_number}",
                "choices": [{"message": {"role": "assistant", "content": content}}],
            }
            if mode != "missing_usage":
                response["usage"] = {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                }
            self._json(200, response)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19091)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    state = State(
        args.state_dir.resolve(), timeout_seconds=args.timeout_seconds
    )
    server = ThreadingHTTPServer((args.host, args.port), _handler(state))
    print(f"mock model listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
