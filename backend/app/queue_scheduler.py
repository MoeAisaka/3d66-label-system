from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence


QUEUE_CLASSES = (
    "validation",
    "interactive",
    "production_batch",
    "canary",
    "recovery",
)
MAX_RETRY_AFTER_SECONDS = 3600.0

DEFAULT_SHARES = {
    "production_batch": 50,
    "interactive": 20,
    "validation": 15,
    "canary": 10,
    "recovery": 5,
}


class QueueContractError(ValueError):
    """Raised when queue configuration or a job violates the queue contract."""


@dataclass(frozen=True)
class QueuePolicy:
    global_limit: int
    shares: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_SHARES))
    weights: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_SHARES))
    validation_boost: int = 10
    recovery_dispatch_interval: int = 4
    version: str = "queue-policy-v1"

    def __post_init__(self) -> None:
        if self.global_limit < 1:
            raise QueueContractError("global_limit 必须至少为 1")
        for name, values in (("shares", self.shares), ("weights", self.weights)):
            if set(values) != set(QUEUE_CLASSES):
                raise QueueContractError(f"{name} 必须完整包含五类队列")
            if any(not isinstance(value, int) or value <= 0 for value in values.values()):
                raise QueueContractError(f"{name} 必须是正整数")
        if sum(self.shares.values()) != 100:
            raise QueueContractError("shares 合计必须为 100")
        if self.validation_boost < 0:
            raise QueueContractError("validation_boost 不能为负数")
        if self.recovery_dispatch_interval < 1:
            raise QueueContractError("recovery_dispatch_interval 必须至少为 1")

    def frozen_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "global_limit": self.global_limit,
                "shares": dict(self.shares),
                "weights": dict(self.weights),
                "validation_boost": self.validation_boost,
                "recovery_dispatch_interval": self.recovery_dispatch_interval,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class QueueJob:
    id: int
    queue_class: str
    created_at: datetime


@dataclass(frozen=True)
class QueueCapacity:
    queue_class: str
    pending: int
    running: int
    reserved: int
    borrowed: int
    effective_limit: int
    weight: int
    effective_weight: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "queue_class": self.queue_class,
            "pending": self.pending,
            "running": self.running,
            "reserved": self.reserved,
            "borrowed": self.borrowed,
            "effective_limit": self.effective_limit,
            "weight": self.weight,
            "effective_weight": self.effective_weight,
        }


def _counts(values: Mapping[str, int] | None) -> dict[str, int]:
    values = values or {}
    result = {queue: int(values.get(queue, 0)) for queue in QUEUE_CLASSES}
    if any(value < 0 for value in result.values()):
        raise QueueContractError("队列计数不能为负数")
    return result


def reserved_capacities(policy: QueuePolicy) -> dict[str, int]:
    """Map percentages to integers while protecting core low-concurrency lanes."""
    total = policy.global_limit
    exact = {
        queue: total * policy.shares[queue] / 100
        for queue in QUEUE_CLASSES
    }
    result = {queue: int(exact[queue]) for queue in QUEUE_CLASSES}
    remaining = total - sum(result.values())
    order = sorted(
        QUEUE_CLASSES,
        key=lambda queue: (
            exact[queue] - result[queue],
            policy.shares[queue],
            -QUEUE_CLASSES.index(queue),
        ),
        reverse=True,
    )
    for queue in order[:remaining]:
        result[queue] += 1

    mandatory: tuple[str, ...]
    if total == 1:
        mandatory = ()
    elif total == 2:
        mandatory = ("interactive", "production_batch")
    elif total == 3:
        mandatory = ("validation", "interactive", "production_batch")
    elif total == 4:
        mandatory = (
            "validation",
            "interactive",
            "production_batch",
            "canary",
        )
    else:
        mandatory = QUEUE_CLASSES
    for queue in mandatory:
        if result[queue] > 0:
            continue
        donor = max(
            (
                candidate
                for candidate in QUEUE_CLASSES
                if result[candidate] > 0
                and (
                    candidate not in mandatory
                    or result[candidate] > 1
                )
            ),
            key=lambda candidate: (result[candidate], policy.shares[candidate]),
            default=None,
        )
        if donor is not None:
            result[donor] -= 1
            result[queue] += 1
    return result


def queue_capacities(
    policy: QueuePolicy,
    *,
    pending: Mapping[str, int] | None = None,
    running: Mapping[str, int] | None = None,
) -> dict[str, QueueCapacity]:
    pending_counts = _counts(pending)
    running_counts = _counts(running)
    reserved = reserved_capacities(policy)
    idle_reserved = sum(
        max(0, reserved[queue] - running_counts[queue])
        for queue in QUEUE_CLASSES
        if pending_counts[queue] == 0
    )
    capacities: dict[str, QueueCapacity] = {}
    for queue in QUEUE_CLASSES:
        floor = reserved[queue]
        # At tiny global limits, positive-weight lanes still receive turns.
        fairness_floor = 1 if pending_counts[queue] else 0
        effective = min(
            policy.global_limit,
            max(floor, fairness_floor) + idle_reserved,
        )
        if queue == "recovery":
            effective = min(effective, max(1, reserved[queue]))
        capacities[queue] = QueueCapacity(
            queue_class=queue,
            pending=pending_counts[queue],
            running=running_counts[queue],
            reserved=floor,
            borrowed=max(0, running_counts[queue] - floor),
            effective_limit=effective,
            weight=policy.weights[queue],
            effective_weight=policy.weights[queue]
            + (policy.validation_boost if queue == "validation" else 0),
        )
    return capacities


class DeterministicQueueScheduler:
    """Deterministic deficit calculation with explicitly serializable state.

    Production claimers restore and persist this state in the same database
    transaction as the job claim. These fields are not a process-global claim.
    """

    def __init__(
        self,
        policy: QueuePolicy,
        *,
        deficits: Mapping[str, int] | None = None,
        dispatch_count: int = 0,
        last_recovery_dispatch: int | None = None,
    ):
        self.policy = policy
        raw_deficits = deficits or {}
        self._deficit = {
            queue: int(raw_deficits.get(queue, 0))
            for queue in QUEUE_CLASSES
        }
        if dispatch_count < 0:
            raise QueueContractError("dispatch_count 不能为负数")
        self._dispatch_count = int(dispatch_count)
        self._last_recovery_dispatch = last_recovery_dispatch

    @property
    def deficits(self) -> dict[str, int]:
        return dict(self._deficit)

    def export_state(self) -> dict[str, Any]:
        return {
            "deficits": dict(self._deficit),
            "dispatch_count": self._dispatch_count,
            "last_recovery_dispatch": self._last_recovery_dispatch,
        }

    def snapshot(
        self,
        *,
        pending: Mapping[str, int] | None = None,
        running: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        capacities = queue_capacities(
            self.policy,
            pending=pending,
            running=running,
        )
        return {
            "policy_version": self.policy.version,
            "global_limit": self.policy.global_limit,
            "global_running": sum(_counts(running).values()),
            "shares": dict(self.policy.shares),
            "weights": dict(self.policy.weights),
            "validation_boost": self.policy.validation_boost,
            "queues": [
                {
                    **capacities[queue].as_dict(),
                    "deficit": self._deficit[queue],
                }
                for queue in QUEUE_CLASSES
            ],
        }

    def _eligible(
        self,
        *,
        pending: Mapping[str, int],
        running: Mapping[str, int],
    ) -> list[str]:
        if sum(running.values()) >= self.policy.global_limit:
            return []
        capacities = queue_capacities(
            self.policy,
            pending=pending,
            running=running,
        )
        eligible = [
            queue
            for queue in QUEUE_CLASSES
            if pending[queue] > 0
            and running[queue] < capacities[queue].effective_limit
        ]
        if not eligible:
            return []

        # An active owner reclaims future slots without preempting borrowed work.
        if sum(running.values()) > 0:
            reclaiming = [
                queue
                for queue in eligible
                if capacities[queue].reserved > running[queue]
            ]
            if reclaiming:
                eligible = reclaiming

        if "recovery" in eligible:
            only_recovery = eligible == ["recovery"]
            interval_met = (
                self._last_recovery_dispatch is None
                or self._dispatch_count - self._last_recovery_dispatch
                >= self.policy.recovery_dispatch_interval
            )
            if not only_recovery and not interval_met:
                eligible.remove("recovery")
        return eligible

    def choose_queue(
        self,
        *,
        pending: Mapping[str, int] | None,
        running: Mapping[str, int] | None,
    ) -> str | None:
        pending_counts = _counts(pending)
        running_counts = _counts(running)
        eligible = self._eligible(
            pending=pending_counts,
            running=running_counts,
        )
        if not eligible:
            return None
        total_weight = sum(
            self.policy.weights[queue]
            + (self.policy.validation_boost if queue == "validation" else 0)
            for queue in QUEUE_CLASSES
        )
        for queue in eligible:
            self._deficit[queue] += self.policy.weights[queue]
            if queue == "validation":
                self._deficit[queue] += self.policy.validation_boost
        selected = max(
            eligible,
            key=lambda queue: (
                self._deficit[queue],
                -QUEUE_CLASSES.index(queue),
            ),
        )
        self._deficit[selected] -= total_weight
        if selected == "recovery":
            self._last_recovery_dispatch = self._dispatch_count
        self._dispatch_count += 1
        return selected

    def choose_job(
        self,
        jobs: Sequence[QueueJob],
        *,
        running: Mapping[str, int] | None = None,
    ) -> QueueJob | None:
        grouped: dict[str, list[QueueJob]] = {
            queue: [] for queue in QUEUE_CLASSES
        }
        for job in jobs:
            if job.queue_class not in grouped:
                raise QueueContractError(f"未知队列：{job.queue_class}")
            grouped[job.queue_class].append(job)
        selected_queue = self.choose_queue(
            pending={queue: len(grouped[queue]) for queue in QUEUE_CLASSES},
            running=running,
        )
        if selected_queue is None:
            return None
        return min(
            grouped[selected_queue],
            key=lambda job: (job.created_at, job.id),
        )


@dataclass(frozen=True)
class TechnicalFailure:
    error_type: str
    retryable: bool
    priority: str
    retry_after_seconds: float | None = None


RETRYABLE_ERROR_TYPES = {
    "timeout",
    "network",
    "429",
    "provider5xx",
    "json_truncated",
    "transient_parse",
}

DIMENSION_CONTRACT_ERROR_TYPES = {
    "dimension_contract_incomplete",
    "dimension_contract_missing",
    "dimension_contract_ambiguous",
    "dimension_contract_not_published",
    "dimension_contract_invalid",
    "dimension_contract_not_executable",
}


def bounded_retry_after_seconds(value: object) -> float | None:
    """Accept only finite non-negative delay seconds and cap provider input."""
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def classify_technical_failure(
    error: BaseException | str,
    *,
    status_code: int | None = None,
    retry_after_seconds: float | None = None,
) -> TechnicalFailure:
    explicit_type = (
        getattr(error, "technical_error_type", None)
        if not isinstance(error, str)
        else None
    )
    explicit_retryable = (
        getattr(error, "retryable", None)
        if not isinstance(error, str)
        else None
    )
    if status_code is None and not isinstance(error, str):
        status_code = getattr(error, "status_code", None)
    if status_code == 429:
        error_type = "429"
    elif status_code is not None and 500 <= status_code <= 599:
        error_type = "provider5xx"
    elif explicit_type in (
        RETRYABLE_ERROR_TYPES
        | DIMENSION_CONTRACT_ERROR_TYPES
        | {"non_retryable"}
    ):
        error_type = str(explicit_type)
    elif isinstance(error, TimeoutError):
        error_type = "timeout"
    elif isinstance(error, ConnectionError):
        error_type = "network"
    elif isinstance(error, json.JSONDecodeError):
        error_type = (
            "json_truncated"
            if error.pos >= max(0, len(error.doc) - 2)
            else "transient_parse"
        )
    else:
        error_type = "non_retryable"
    retryable = (
        bool(explicit_retryable)
        if explicit_retryable is not None
        else error_type in RETRYABLE_ERROR_TYPES
    )
    return TechnicalFailure(
        error_type=error_type,
        retryable=retryable,
        priority="retry" if retryable else "P0",
        retry_after_seconds=(
            bounded_retry_after_seconds(retry_after_seconds)
            if error_type == "429"
            else None
        ),
    )


def retry_delay_seconds(
    technical_attempt: int,
    *,
    jitter_key: int | str = 0,
    retry_after_seconds: float | None = None,
    base_seconds: float = 1.0,
) -> float:
    if technical_attempt not in (1, 2):
        raise QueueContractError("自动技术重试仅允许 attempt 1 或 2")
    bounded_retry_after = bounded_retry_after_seconds(
        retry_after_seconds
    )
    if bounded_retry_after is not None:
        return bounded_retry_after
    seed = sum(ord(character) for character in str(jitter_key)) % 1000
    jitter = (seed / 1000.0) * base_seconds
    return base_seconds * (2 ** (technical_attempt - 1)) + jitter


@dataclass(frozen=True)
class BreakerDecision:
    state: str
    failure_count: int
    window_started_at: datetime | None
    opened_at: datetime | None
    cooldown_until: datetime | None
    reason: str | None


def record_breaker_failure(
    *,
    state: str = "closed",
    failure_count: int = 0,
    window_started_at: datetime | None = None,
    now: datetime | None = None,
    retryable: bool = True,
    threshold: int = 3,
    window_seconds: int = 60,
    cooldown_seconds: int = 300,
) -> BreakerDecision:
    if threshold < 1 or window_seconds < 1 or cooldown_seconds < 1:
        raise QueueContractError("breaker 参数必须为正数")
    current = now or datetime.now(timezone.utc)
    if state == "open":
        return BreakerDecision(
            state="open",
            failure_count=failure_count,
            window_started_at=window_started_at,
            opened_at=current,
            cooldown_until=current + timedelta(seconds=cooldown_seconds),
            reason="ALREADY_OPEN",
        )
    if (
        window_started_at is None
        or current - window_started_at > timedelta(seconds=window_seconds)
    ):
        window_started_at = current
        failure_count = 0
    next_count = failure_count + 1
    should_open = not retryable or next_count >= threshold
    return BreakerDecision(
        state="open" if should_open else "closed",
        failure_count=next_count,
        window_started_at=window_started_at,
        opened_at=current if should_open else None,
        cooldown_until=(
            current + timedelta(seconds=cooldown_seconds)
            if should_open
            else None
        ),
        reason=(
            "NON_RETRYABLE_P0"
            if not retryable
            else "SHORT_WINDOW_FAILURE_THRESHOLD"
            if should_open
            else None
        ),
    )
