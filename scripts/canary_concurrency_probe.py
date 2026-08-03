#!/usr/bin/env python3
"""Real-provider concurrency / rate-limit canary for the label lab.

Purpose
-------
Hit the *real* Doubao (Volcengine Ark) endpoint with the platform's actual
"call A" precheck prompt across a sweep of concurrency levels, and measure:

  - per-request wall latency (P50 / P90 / P95 / max)
  - success / 429 / 5xx / timeout / parse-error counts
  - Retry-After values returned on 429
  - throughput (successful requests per second)
  - token usage (input/output/total) when reported

Safety
------
- The API key is read ONLY from the DOUBAO_API_KEY environment variable.
  It is never written to disk and never printed. The output JSON records
  key presence and a short fingerprint (sha256 first 8 hex), not the key.
- This script performs real outbound calls to the configured provider.
  It is intended to be run manually by the Owner with an explicit image
  budget and concurrency sweep.

Usage
-----
    DOUBAO_API_KEY=ark-xxxx \
    .venv/bin/python scripts/canary_concurrency_probe.py \
        --images <dir_or_files...> \
        --levels 1 2 4 6 8 10 \
        --requests-per-level 12 \
        --out /path/to/report.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Make the backend package importable when run from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.doubao import (  # noqa: E402
    DoubaoClient,
    DoubaoError,
    DoubaoHTTPError,
    DoubaoParseError,
    DoubaoTransportError,
)
from app.prompt_loader import load_prompt_pairs  # noqa: E402


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class _FakeConfig:
    """Minimal ModelConfig stand-in so DoubaoClient can run standalone.

    DoubaoClient only reads these attributes; it does not touch the DB.
    encrypted_api_key holds the RAW key here and unprotect_secret is bypassed
    by monkeypatching (see main) so we never need the DPAPI/Keychain master key.
    """

    encrypted_api_key: str
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    api_path: str = "/chat/completions"
    model_id: str = "doubao-seed-2-0-lite-260215"
    protocol: str = "openai_chat"
    provider: str = "doubao"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_seconds: int = 120
    max_retries: int = 0  # canary measures raw single attempts, no hidden retries
    structured_output: bool = True


@dataclass
class RequestOutcome:
    ok: bool
    latency_ms: float
    status_code: int | None = None
    error_type: str | None = None
    retry_after: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    correlation_id: str | None = None


@dataclass
class LevelResult:
    concurrency: int
    total: int
    ok: int
    http_429: int
    http_5xx: int
    timeout: int
    network: int
    parse_error: int
    other_error: int
    wall_seconds: float
    throughput_rps: float
    latency_ms_p50: float | None
    latency_ms_p90: float | None
    latency_ms_p95: float | None
    latency_ms_max: float | None
    retry_after_values: list[str] = field(default_factory=list)
    avg_total_tokens: float | None = None


def _collect_images(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.suffix.lower() in IMAGE_SUFFIXES:
                    out.append(child)
        elif p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            out.append(p)
    return out


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


async def _one_call(
    client: DoubaoClient,
    system_a: str,
    user_a: str,
    image_path: Path,
) -> RequestOutcome:
    start = time.perf_counter()
    try:
        resp = await client.chat_json(
            system_a,
            user_a,
            image_path=image_path,
            max_attempts=1,
            output_budget=2048,
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        return RequestOutcome(
            ok=True,
            latency_ms=elapsed,
            status_code=resp.upstream_status_code,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            total_tokens=resp.total_tokens,
            correlation_id=resp.request_correlation_id,
        )
    except DoubaoHTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        retry_after = None
        try:
            retry_after = exc.headers.get("retry-after") or exc.headers.get("Retry-After")
        except Exception:
            retry_after = None
        return RequestOutcome(
            ok=False,
            latency_ms=elapsed,
            status_code=exc.status_code,
            error_type=exc.technical_error_type,
            retry_after=retry_after,
            correlation_id=exc.request_correlation_id,
        )
    except DoubaoTransportError as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return RequestOutcome(
            ok=False, latency_ms=elapsed, error_type=exc.technical_error_type
        )
    except DoubaoParseError as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return RequestOutcome(
            ok=False,
            latency_ms=elapsed,
            status_code=exc.upstream_status_code,
            error_type=exc.technical_error_type,
        )
    except DoubaoError as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return RequestOutcome(
            ok=False, latency_ms=elapsed, error_type=getattr(exc, "technical_error_type", "unknown")
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - start) * 1000.0
        return RequestOutcome(ok=False, latency_ms=elapsed, error_type=f"unexpected:{type(exc).__name__}")


async def _run_level(
    client: DoubaoClient,
    system_a: str,
    user_a: str,
    images: list[Path],
    concurrency: int,
    n_requests: int,
) -> LevelResult:
    sem = asyncio.Semaphore(concurrency)
    outcomes: list[RequestOutcome] = []

    async def _guarded(idx: int) -> RequestOutcome:
        async with sem:
            img = images[idx % len(images)]
            return await _one_call(client, system_a, user_a, img)

    wall_start = time.perf_counter()
    tasks = [asyncio.create_task(_guarded(i)) for i in range(n_requests)]
    for coro in asyncio.as_completed(tasks):
        outcomes.append(await coro)
    wall_seconds = time.perf_counter() - wall_start

    ok = [o for o in outcomes if o.ok]
    lat_ok = [o.latency_ms for o in ok]
    total_tokens = [o.total_tokens for o in ok if o.total_tokens]
    return LevelResult(
        concurrency=concurrency,
        total=len(outcomes),
        ok=len(ok),
        http_429=sum(1 for o in outcomes if o.status_code == 429),
        http_5xx=sum(1 for o in outcomes if o.status_code and 500 <= o.status_code <= 599),
        timeout=sum(1 for o in outcomes if o.error_type == "timeout"),
        network=sum(1 for o in outcomes if o.error_type == "network"),
        parse_error=sum(1 for o in outcomes if o.error_type in {"json_truncated", "transient_parse"}),
        other_error=sum(
            1
            for o in outcomes
            if not o.ok
            and o.status_code != 429
            and not (o.status_code and 500 <= o.status_code <= 599)
            and o.error_type not in {"timeout", "network", "json_truncated", "transient_parse"}
        ),
        wall_seconds=round(wall_seconds, 3),
        throughput_rps=round(len(ok) / wall_seconds, 3) if wall_seconds > 0 else 0.0,
        latency_ms_p50=round(_percentile(lat_ok, 0.50), 1) if lat_ok else None,
        latency_ms_p90=round(_percentile(lat_ok, 0.90), 1) if lat_ok else None,
        latency_ms_p95=round(_percentile(lat_ok, 0.95), 1) if lat_ok else None,
        latency_ms_max=round(max(lat_ok), 1) if lat_ok else None,
        retry_after_values=[o.retry_after for o in outcomes if o.retry_after],
        avg_total_tokens=round(statistics.mean(total_tokens), 1) if total_tokens else None,
    )


async def _amain(args: argparse.Namespace) -> int:
    api_key = os.environ.get("DOUBAO_API_KEY", "").strip()
    if not api_key:
        print("ERROR: DOUBAO_API_KEY env var is required and was empty.", file=sys.stderr)
        return 2

    # Bypass DPAPI/Keychain: we hold the raw key, so unprotect returns it as-is.
    import app.doubao as doubao_mod

    doubao_mod.unprotect_secret = lambda ref: ref  # type: ignore[assignment]

    images = _collect_images(args.images)
    if not images:
        print("ERROR: no images found in the provided paths.", file=sys.stderr)
        return 2

    pairs = load_prompt_pairs(REPO_ROOT / "prompts" / "3d66-aesthetic-v2.1.md")
    system_a = pairs["A"].system
    user_a = pairs["A"].user

    config = _FakeConfig(
        encrypted_api_key=api_key,
        base_url=args.base_url,
        model_id=args.model_id,
        timeout_seconds=args.timeout,
    )
    client = DoubaoClient(config)

    key_fp = hashlib.sha256(api_key.encode()).hexdigest()[:8]
    report: dict[str, Any] = {
        "schema": "canary-concurrency-probe-v1",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": "doubao",
        "base_url": args.base_url,
        "model_id": args.model_id,
        "api_key_present": True,
        "api_key_fingerprint": key_fp,
        "image_count": len(images),
        "requests_per_level": args.requests_per_level,
        "timeout_seconds": args.timeout,
        "levels": [],
    }

    print(f"Canary start: {len(images)} images, levels={args.levels}, "
          f"{args.requests_per_level} req/level, model={args.model_id}, keyfp={key_fp}")

    for level in args.levels:
        print(f"  -> concurrency={level} ...", flush=True)
        result = await _run_level(
            client, system_a, user_a, images, level, args.requests_per_level
        )
        report["levels"].append(asdict(result))
        print(
            f"     ok={result.ok}/{result.total} 429={result.http_429} "
            f"5xx={result.http_5xx} timeout={result.timeout} "
            f"p50={result.latency_ms_p50}ms p95={result.latency_ms_p95}ms "
            f"rps={result.throughput_rps}",
            flush=True,
        )
        if args.cooldown > 0 and level != args.levels[-1]:
            await asyncio.sleep(args.cooldown)

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report written: {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", nargs="+", required=True, help="Image files or directories.")
    parser.add_argument("--levels", nargs="+", type=int, default=[1, 2, 4, 6, 8, 10])
    parser.add_argument("--requests-per-level", type=int, default=12)
    parser.add_argument("--base-url", default="https://ark.cn-beijing.volces.com/api/v3")
    parser.add_argument("--model-id", default="doubao-seed-2-0-lite-260215")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--cooldown", type=float, default=3.0, help="Seconds to sleep between levels.")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
