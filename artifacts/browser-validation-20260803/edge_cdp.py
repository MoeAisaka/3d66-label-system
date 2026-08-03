#!/usr/bin/env python3
"""Small page-level CDP client for the dedicated Edge validation profile."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import websocket


class PageCdp:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(
            ws_url,
            timeout=15,
            suppress_origin=True,
        )
        self.next_id = 1

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.ws.send(
            json.dumps(
                {"id": request_id, "method": method, "params": params or {}},
                ensure_ascii=False,
            )
        )
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            return message.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False))
        return result.get("result", {}).get("value")


def targets(port: int) -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
        return json.load(response)


def choose_target(items: list[dict[str, Any]], target_id: str | None, url_contains: str | None) -> dict[str, Any]:
    pages = [item for item in items if item.get("type") == "page"]
    if target_id:
        matches = [item for item in pages if item.get("id") == target_id]
    elif url_contains:
        matches = [item for item in pages if url_contains in str(item.get("url", ""))]
    else:
        matches = pages
    if not matches:
        raise SystemExit("No matching page target")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("list", "eval", "navigate", "screenshot", "set-files", "type"),
    )
    parser.add_argument("--port", type=int, default=19222)
    parser.add_argument("--target-id")
    parser.add_argument("--url-contains")
    parser.add_argument("--expression")
    parser.add_argument("--output")
    parser.add_argument("--selector")
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--text")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--device-scale-factor", type=float, default=1.0)
    args = parser.parse_args()

    items = targets(args.port)
    if args.action == "list":
        print(
            json.dumps(
                [
                    {key: item.get(key) for key in ("id", "type", "title", "url")}
                    for item in items
                    if item.get("type") == "page"
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    target = choose_target(items, args.target_id, args.url_contains)
    client = PageCdp(target["webSocketDebuggerUrl"])
    emulating = args.width is not None or args.height is not None
    if emulating:
        if args.width is None or args.height is None:
            raise SystemExit("--width and --height must be provided together")
        client.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": args.width,
                "height": args.height,
                "deviceScaleFactor": args.device_scale_factor,
                "mobile": True,
            },
        )
        client.evaluate("new Promise((resolve) => setTimeout(resolve, 350))")
    try:
        if args.action == "eval":
            expression = args.expression if args.expression is not None else sys.stdin.read()
            print(json.dumps(client.evaluate(expression), ensure_ascii=False, indent=2))
            return 0
        if args.action == "navigate":
            if not args.text:
                raise SystemExit("--text URL is required")
            client.call("Page.navigate", {"url": args.text})
            print("ok")
            return 0
        if args.action == "screenshot":
            if not args.output:
                raise SystemExit("--output is required")
            result = client.call(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": True},
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(base64.b64decode(result["data"]))
            print(output)
            return 0
        if args.action == "set-files":
            if not args.selector or not args.files:
                raise SystemExit("--selector and --files are required")
            root = client.call("DOM.getDocument", {"depth": 1})["root"]["nodeId"]
            node = client.call(
                "DOM.querySelector",
                {"nodeId": root, "selector": args.selector},
            )["nodeId"]
            if not node:
                raise SystemExit(f"No element matches {args.selector!r}")
            client.call(
                "DOM.setFileInputFiles",
                {
                    "nodeId": node,
                    "files": [str(Path(item).expanduser().resolve()) for item in args.files],
                },
            )
            print("ok")
            return 0
        if args.action == "type":
            if not args.selector or args.text is None:
                raise SystemExit("--selector and --text are required")
            root = client.call("DOM.getDocument", {"depth": 1})["root"]["nodeId"]
            node = client.call(
                "DOM.querySelector",
                {"nodeId": root, "selector": args.selector},
            )["nodeId"]
            if not node:
                raise SystemExit(f"No element matches {args.selector!r}")
            client.call("Page.bringToFront")
            quad = client.call("DOM.getContentQuads", {"nodeId": node})["quads"][0]
            x = sum(quad[0::2]) / 4
            y = sum(quad[1::2]) / 4
            client.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
            client.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
            client.call(
                "Input.dispatchKeyEvent",
                {
                    "type": "rawKeyDown",
                    "key": "a",
                    "code": "KeyA",
                    "modifiers": 4,
                    "commands": ["SelectAll"],
                },
            )
            client.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 4})
            client.call("Input.insertText", {"text": args.text})
            print("ok")
            return 0
    finally:
        if emulating:
            client.call("Emulation.clearDeviceMetricsOverride")
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
