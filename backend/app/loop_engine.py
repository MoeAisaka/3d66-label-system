from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit


MAX_BUSINESS_ROUNDS = 3

ROUND_KIND = {
    1: "base",
    2: "targeted_recheck",
    3: "arbitration",
}

PROBLEM_DIMENSION_FIELDS = (
    "problem_dimensions",
    "low_confidence_dimensions",
    "ab_conflict_dimensions",
    "schema_error_dimensions",
    "enum_error_dimensions",
    "cross_field_error_dimensions",
)

RULE_SENTINELS = {
    "schema_valid": "__schema__",
    "enum_valid": "__enum__",
    "cross_field_valid": "__cross_field__",
}

SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "cookie",
    "credentials",
    "encrypted_api_key",
    "password",
    "proxy_authorization",
    "provider_payload",
    "raw_payload",
    "raw_response",
    "raw_response_a",
    "raw_response_b",
    "secret",
    "set_cookie",
    "session_token",
    "token",
}
_SENSITIVE_NORMALIZED_KEYS = {
    "".join(character for character in key if character.isalnum())
    for key in SENSITIVE_KEYS
} | {
    "accesskey",
    "accesskeyid",
    "apikey",
    "auth",
    "authentication",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "credential",
    "encryptedapikey",
    "idtoken",
    "passwd",
    "privatekey",
    "proxyauthorization",
    "pwd",
    "refreshtoken",
    "secretkey",
    "sessionid",
    "signature",
    "signingkey",
    "xapikey",
}
_SENSITIVE_KEY_SUFFIXES = (
    "accesstoken",
    "apikey",
    "authtoken",
    "authorization",
    "bearertoken",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "idtoken",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessiontoken",
    "signingkey",
    "token",
)
_AUTH_HEADER_VALUE_RE = re.compile(
    r"(?i)(?:^|[\s;,])(?:authorization|proxy-authorization|"
    r"x-api-key|api-key|cookie|set-cookie)\s*[:=]\s*\S+"
)
_STRING_SECRET_ALIAS_VALUE_RE = re.compile(
    r"""(?ix)
    (?<![a-z0-9_-])
    (?:
        authorization[-_]?header
        | proxy[-_]?authorization
        | client[-_]?secret
        | x[-_]?auth[-_]?token
        | x[-_]?api[-_]?key
        | api[-_]?key
        | access[-_]?token
        | refresh[-_]?token
        | session(?:[-_]?(?:cookie|id|token))?
        | set[-_]?cookie
        | cookie
        | password
        | passwd
    )
    ["']?\s*(?:[:=]|/)\s*
    (?:"[^"]+"|'[^']+'|[^\s,;&}\]]+)
    """
)
_AUTH_SCHEME_CANDIDATE_RE = re.compile(
    r"(?i)(?:^|[\s:;,])(?P<scheme>bearer|basic)\s+"
    r"(?P<token>[A-Za-z0-9._~+/=-]{4,})"
)
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>'\"]+")
_URL_SECRET_QUERY_KEYS = {
    "accesskey",
    "accesskeyid",
    "accesstoken",
    "apikey",
    "auth",
    "authorization",
    "authtoken",
    "clientsecret",
    "credential",
    "idtoken",
    "password",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "token",
    "xapikey",
}

REQUIRED_STABILITY_FLAGS = (
    "schema_valid",
    "enum_valid",
    "cross_field_valid",
    "confidence_threshold_met",
    "version_threshold_met",
    "freeze_threshold_met",
)
HUMAN_REVIEW_FIELDS = (
    "needs_human",
    "needs_review",
    "force_human",
    "review_required",
    "requires_human",
    "manual_review_required",
    "human_review_required",
)
TARGETED_RESULT_METADATA_FIELDS = {
    *REQUIRED_STABILITY_FLAGS,
    *HUMAN_REVIEW_FIELDS,
    "stable",
    "new_evidence",
    "consecutive_consistency",
    "result_version",
    "schema_version",
    "confidence",
    "confidence_threshold",
    "problem_dimensions",
    "low_confidence_dimensions",
    "ab_conflict_dimensions",
    "schema_error_dimensions",
    "enum_error_dimensions",
    "cross_field_error_dimensions",
    "conflicts",
}
TARGETED_DIMENSION_FIELDS = {
    "dimension_values",
    "suggested_values",
    "confidence_by_dimension",
    "evidence",
    "arbitration_evidence",
}
FORBIDDEN_FULL_RESULT_FIELDS = {
    "classification",
    "dimensions",
    "full_output",
    "full_result",
    "precheck",
    "aesthetic",
    "scoring",
}


class LoopContractError(ValueError):
    """Raised when a loop command violates a frozen workflow contract."""


@dataclass(frozen=True)
class LoopDecision:
    status: str
    machine_converged: bool
    needs_human: bool
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    next_round: int | None = None
    next_kind: str | None = None
    target_dimensions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "machine_converged": self.machine_converged,
            "needs_human": self.needs_human,
            "reason_codes": list(self.reason_codes),
            "evidence": self.evidence,
            "next_round": self.next_round,
            "next_kind": self.next_kind,
            "target_dimensions": list(self.target_dimensions),
        }


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def request_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _key_tokens(value: object) -> tuple[str, ...]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value))
    return tuple(
        token
        for token in re.split(r"[^a-z0-9]+", separated.lower())
        if token
    )


def _is_sensitive_key(value: object) -> bool:
    normalized = _normalized_key(value)
    tokens = set(_key_tokens(value))
    if normalized == "key":
        return False
    if (
        normalized in _SENSITIVE_NORMALIZED_KEYS
        or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)
    ):
        return True
    if any(
        root in normalized
        for root in (
            "apikey",
            "authorization",
            "authtoken",
            "clientsecret",
            "providerpayload",
            "proxyauthorization",
            "sessiontoken",
            "xauthtoken",
        )
    ) and any(
        container in normalized
        for container in (
            "backup",
            "container",
            "data",
            "header",
            "payload",
            "snapshot",
            "value",
        )
    ):
        return True
    if tokens & {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
    }:
        return True
    if "secret" in tokens and tokens & {
        "api",
        "auth",
        "client",
        "credential",
        "key",
        "token",
        "value",
        "backup",
        "container",
        "header",
        "payload",
    }:
        return True
    if "token" in tokens and tokens & {
        "access",
        "auth",
        "bearer",
        "client",
        "header",
        "id",
        "refresh",
        "session",
        "value",
        "x",
        "backup",
        "container",
        "payload",
    }:
        return True
    if "key" in tokens and tokens & {
        "access",
        "api",
        "auth",
        "private",
        "secret",
        "signing",
        "x",
    }:
        return True
    if "header" in tokens and tokens & {
        "api",
        "auth",
        "authorization",
        "cookie",
        "token",
    }:
        return True
    return bool(
        "payload" in tokens
        and tokens
        & {
            "auth",
            "backup",
            "credential",
            "provider",
            "raw",
            "request",
            "response",
            "secret",
            "token",
        }
    )


def _is_credential_auth_token(scheme: str, token: str) -> bool:
    """Recognize credential-shaped auth values without flagging prose."""
    normalized_scheme = scheme.lower()
    lowered_token = token.lower()
    if lowered_token in {
        "auth",
        "authentication",
        "authorization",
        "mechanism",
        "mode",
        "scheme",
        "token",
    }:
        return False

    if normalized_scheme == "basic":
        try:
            padded = token + ("=" * (-len(token) % 4))
            decoded = base64.b64decode(
                padded.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error, ValueError):
            decoded = b""
        if b":" in decoded:
            return True

    if re.fullmatch(r"(?i)sk-[a-z0-9_-]{3,}", token):
        return True
    if re.fullmatch(
        r"(?i)eyj[a-z0-9_-]{3,}\.[a-z0-9_-]{3,}\.[a-z0-9_-]{3,}",
        token,
    ):
        return True

    minimum_length = 8 if normalized_scheme == "basic" else 12
    if len(token) < minimum_length:
        return False
    if normalized_scheme == "bearer":
        return True
    has_digit = any(character.isdigit() for character in token)
    has_mixed_case = (
        any(character.islower() for character in token)
        and any(character.isupper() for character in token)
    )
    has_token_punctuation = any(
        character in "._~+/=" for character in token
    )
    has_multiple_hyphens = token.count("-") >= 2
    return (
        has_digit
        or has_mixed_case
        or has_token_punctuation
        or has_multiple_hyphens
    )


def contains_credential_auth_scheme(value: str) -> bool:
    return any(
        _is_credential_auth_token(
            match.group("scheme"),
            match.group("token"),
        )
        for match in _AUTH_SCHEME_CANDIDATE_RE.finditer(value)
    )


def _string_contains_auth_material(value: str) -> bool:
    if (
        _STRING_SECRET_ALIAS_VALUE_RE.search(value)
        or _AUTH_HEADER_VALUE_RE.search(value)
        or contains_credential_auth_scheme(value)
    ):
        return True
    for match in _URL_RE.finditer(value):
        candidate = match.group(0).rstrip(".,);]")
        try:
            parts = urlsplit(candidate)
        except ValueError:
            return True
        if parts.username is not None or parts.password is not None:
            return True
        for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
            if _normalized_key(key) in _URL_SECRET_QUERY_KEYS and item_value:
                return True
    return False


def assert_safe_normalized_payload(value: Any, path: str = "$") -> None:
    """Reject dependency payloads and credential-shaped fields at the API boundary."""
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            tokens = set(_key_tokens(raw_key))
            if (
                _is_sensitive_key(raw_key)
                or (
                    isinstance(raw_key, str)
                    and _string_contains_auth_material(raw_key)
                )
                or (
                    "raw" in tokens
                    and tokens & {"payload", "request", "response"}
                )
            ):
                raise LoopContractError(
                    f"{path}.* 不允许包含原始或敏感字段"
                )
            assert_safe_normalized_payload(item, f"{path}.*")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_safe_normalized_payload(item, f"{path}[{index}]")
    elif isinstance(value, str) and _string_contains_auth_material(value):
        raise LoopContractError(f"{path} 不允许包含认证材料")


def _dimension_values(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        yield from (str(key).strip() for key in value if str(key).strip())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, str) and item.strip():
                yield item.strip()
            elif isinstance(item, Mapping):
                dimension = item.get("dimension") or item.get("field")
                if isinstance(dimension, str) and dimension.strip():
                    yield dimension.strip()


def problem_dimensions(result: Mapping[str, Any]) -> tuple[str, ...]:
    dimensions: set[str] = set()
    for field in PROBLEM_DIMENSION_FIELDS:
        dimensions.update(_dimension_values(result.get(field)))
    dimensions.update(_dimension_values(result.get("conflicts")))
    for field, sentinel in RULE_SENTINELS.items():
        if result.get(field) is False:
            dimensions.add(sentinel)
    return tuple(sorted(dimensions))


def conflict_dimensions(result: Mapping[str, Any]) -> tuple[str, ...]:
    dimensions = set(_dimension_values(result.get("ab_conflict_dimensions")))
    dimensions.update(_dimension_values(result.get("conflicts")))
    return tuple(sorted(dimensions))


def _attempt_result(attempt: Mapping[str, Any]) -> Mapping[str, Any]:
    result = attempt.get("normalized_result")
    if not isinstance(result, Mapping):
        raise LoopContractError("完成轮次必须包含 normalized_result 对象")
    return result


def _round(attempt: Mapping[str, Any]) -> int:
    value = attempt.get("round", attempt.get("business_round"))
    if not isinstance(value, int) or not 1 <= value <= MAX_BUSINESS_ROUNDS:
        raise LoopContractError("业务轮次只能是 1 至 3")
    return value


def _nonempty_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) and bool(value) else None


def _dimension_result_values(result: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in ("dimension_values", "suggested_values"):
        field_value = result.get(field)
        if isinstance(field_value, Mapping):
            values.update({str(key): item for key, item in field_value.items()})
    return values


def _evidence_value(result: Mapping[str, Any]) -> Any:
    arbitration = result.get("arbitration_evidence")
    if arbitration not in (None, {}, []):
        return arbitration
    return result.get("evidence")


def canonical_evidence_fingerprint(
    result: Mapping[str, Any],
    *,
    target_dimensions: Sequence[str] = (),
) -> str | None:
    evidence = _evidence_value(result)
    if evidence in (None, {}, [], ""):
        return None
    targets = tuple(sorted(set(str(item) for item in target_dimensions)))
    if targets and isinstance(evidence, Mapping):
        evidence = {
            target: evidence[target]
            for target in targets
            if target in evidence
        }
        if not evidence:
            return None
    return request_fingerprint(evidence)


def canonical_result_fingerprint(
    result: Mapping[str, Any],
    *,
    target_dimensions: Sequence[str] = (),
) -> str:
    values = _dimension_result_values(result)
    targets = tuple(sorted(set(str(item) for item in target_dimensions)))
    if targets:
        values = {key: values[key] for key in targets if key in values}
    return request_fingerprint(
        {
            "dimension_values": values,
            "evidence": (
                {
                    key: value
                    for key, value in (
                        _evidence_value(result) or {}
                    ).items()
                    if not targets or key in targets
                }
                if isinstance(_evidence_value(result), Mapping)
                else _evidence_value(result)
            ),
            "flags": {
                field: result.get(field)
                for field in REQUIRED_STABILITY_FLAGS + HUMAN_REVIEW_FIELDS
                if field in result
            },
        }
    )


def _requires_human(result: Mapping[str, Any]) -> bool:
    return any(result.get(field) is True for field in HUMAN_REVIEW_FIELDS)


def _adjacent_consistency(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    target_dimensions: Sequence[str],
) -> tuple[bool, tuple[str, ...]]:
    previous_values = _dimension_result_values(previous)
    current_values = _dimension_result_values(current)
    targets = tuple(sorted(set(str(item) for item in target_dimensions)))
    if not targets:
        targets = tuple(sorted(set(previous_values) & set(current_values)))
    if not targets or any(
        target not in previous_values or target not in current_values
        for target in targets
    ):
        return False, ()
    conflicts = tuple(
        target
        for target in targets
        if canonical_json(previous_values[target])
        != canonical_json(current_values[target])
    )
    return not conflicts, conflicts


def _new_evidence(
    attempts: Sequence[Mapping[str, Any]],
) -> tuple[bool, str | None]:
    current_result = _attempt_result(attempts[-1])
    if current_result.get("new_evidence") is False:
        return False, "CLIENT_DECLARED_NO_NEW_EVIDENCE"
    targets = tuple(
        _dimension_values(attempts[-1].get("target_dimensions"))
    )
    current = canonical_evidence_fingerprint(
        current_result,
        target_dimensions=targets,
    )
    if current is None:
        return False, "MISSING_EVIDENCE"
    if len(attempts) == 1:
        return True, None
    current_result_fingerprint = canonical_result_fingerprint(
        current_result,
        target_dimensions=targets,
    )
    previous_results = [
        _attempt_result(attempt) for attempt in attempts[:-1]
    ]
    if any(
        canonical_result_fingerprint(
            previous_result,
            target_dimensions=targets,
        )
        == current_result_fingerprint
        for previous_result in previous_results
    ):
        return False, "DUPLICATE_RESULT"
    if any(
        previous is not None and previous == current
        for previous in (
            canonical_evidence_fingerprint(
                previous_result,
                target_dimensions=targets,
            )
            for previous_result in previous_results
        )
    ):
        return False, "DUPLICATE_EVIDENCE"
    return True, None


def _server_problem_dimensions(result: Mapping[str, Any]) -> tuple[str, ...]:
    dimensions = set(problem_dimensions(result))
    confidence_by_dimension = result.get("confidence_by_dimension")
    if isinstance(confidence_by_dimension, Mapping):
        for dimension, value in confidence_by_dimension.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0.7:
                dimensions.add(str(dimension))
    return tuple(sorted(dimensions))


def _stable(
    attempts: Sequence[Mapping[str, Any]],
    *,
    problems: Sequence[str],
    conflicts: Sequence[str],
    consistency: bool,
) -> bool:
    result = _attempt_result(attempts[-1])
    if len(attempts) < 2 or _round(attempts[-1]) == 1:
        return False
    if not result or _requires_human(result) or problems or conflicts:
        return False
    if not all(result.get(flag) is True for flag in REQUIRED_STABILITY_FLAGS):
        return False
    if not _dimension_result_values(result):
        return False
    if canonical_evidence_fingerprint(result) is None:
        return False
    return consistency


def _round_evidence(
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    summaries = []
    for index, attempt in enumerate(attempts):
        result = _attempt_result(attempt)
        targets = tuple(
            _dimension_values(attempt.get("target_dimensions"))
        )
        consistency = False
        calculated_conflicts: tuple[str, ...] = ()
        if index > 0:
            consistency, calculated_conflicts = _adjacent_consistency(
                _attempt_result(attempts[index - 1]),
                result,
                target_dimensions=targets,
            )
        server_conflicts = tuple(
            sorted(
                set(calculated_conflicts)
                | set(conflict_dimensions(result))
            )
        )
        evidence_is_new, evidence_reason = _new_evidence(
            attempts[: index + 1]
        )
        summaries.append(
            {
                "round": _round(attempt),
                "kind": attempt.get("kind") or ROUND_KIND[_round(attempt)],
                "target_dimensions": list(
                    _dimension_values(attempt.get("target_dimensions"))
                ),
                "problem_dimensions": list(
                    _server_problem_dimensions(result)
                ),
                "conflict_dimensions": list(server_conflicts),
                "server_consistency": consistency,
                "server_new_evidence": evidence_is_new,
                "server_evidence_reason": evidence_reason,
                "evidence_fingerprint": (
                    canonical_evidence_fingerprint(
                        result,
                        target_dimensions=targets,
                    )
                ),
                "result_fingerprint": canonical_result_fingerprint(
                    result,
                    target_dimensions=targets,
                ),
                "evidence": result.get("evidence", {}),
                "arbitration_evidence": result.get(
                    "arbitration_evidence", {}
                ),
                "suggested_values": result.get("suggested_values", {}),
            }
        )
    return {"rounds": summaries}


def _stop(
    *,
    status: str,
    reason: str,
    attempts: Sequence[Mapping[str, Any]],
) -> LoopDecision:
    return LoopDecision(
        status=status,
        machine_converged=status == "machine_converged",
        needs_human=status == "needs_human",
        reason_codes=(reason,),
        evidence=_round_evidence(attempts),
    )


def decide_next_step(
    attempts: Sequence[Mapping[str, Any]],
) -> LoopDecision:
    """Return the only valid next transition for completed business rounds.

    The function has no I/O or clock dependency. Callers persist the returned
    decision and create a ``waiting_result`` attempt instead of pretending a
    model was invoked.
    """
    if not attempts:
        raise LoopContractError("至少需要一个已完成轮次")
    ordered = sorted(attempts, key=_round)
    rounds = [_round(attempt) for attempt in ordered]
    if rounds != list(range(1, len(ordered) + 1)):
        raise LoopContractError("轮次必须从 1 开始且连续，禁止越序")
    if len(ordered) > MAX_BUSINESS_ROUNDS:
        raise LoopContractError("最多三轮，禁止第四轮")

    current = ordered[-1]
    current_round = _round(current)
    result = _attempt_result(current)
    problems = _server_problem_dimensions(result)
    consistency = False
    calculated_conflicts: tuple[str, ...] = ()
    if len(ordered) > 1:
        consistency, calculated_conflicts = _adjacent_consistency(
            _attempt_result(ordered[-2]),
            result,
            target_dimensions=tuple(
                _dimension_values(current.get("target_dimensions"))
            ),
        )
    # Client-declared conflicts may only make the decision more conservative.
    # They can never establish consistency or convergence.
    conflicts = tuple(
        sorted(set(calculated_conflicts) | set(conflict_dimensions(result)))
    )

    if _requires_human(result):
        return _stop(
            status="needs_human",
            reason="FORCE_HUMAN",
            attempts=ordered,
        )

    has_new_evidence, evidence_reason = _new_evidence(ordered)
    if not has_new_evidence:
        return _stop(
            status="needs_human",
            reason=(
                "NO_NEW_EVIDENCE"
                if evidence_reason == "CLIENT_DECLARED_NO_NEW_EVIDENCE"
                else evidence_reason or "NO_NEW_EVIDENCE"
            ),
            attempts=ordered,
        )

    if _stable(
        ordered,
        problems=problems,
        conflicts=conflicts,
        consistency=consistency,
    ):
        reason = {
            1: "ROUND1_STABLE",
            2: "ROUND2_RESOLVED",
            3: "ROUND3_RESOLVED",
        }[current_round]
        return _stop(
            status="machine_converged",
            reason=reason,
            attempts=ordered,
        )

    if current_round == 1:
        targets = problems
        if not targets:
            return _stop(
                status="needs_human",
                reason="UNLOCATABLE_UNSTABLE_RESULT",
                attempts=ordered,
            )
        return LoopDecision(
            status="waiting_result",
            machine_converged=False,
            needs_human=False,
            reason_codes=("TARGETED_RECHECK_REQUIRED",),
            evidence=_round_evidence(ordered),
            next_round=2,
            next_kind=ROUND_KIND[2],
            target_dimensions=targets,
        )

    if current_round == 2:
        if not conflicts:
            return _stop(
                status="needs_human",
                reason="UNRESOLVED_NON_CONFLICT",
                attempts=ordered,
            )
        return LoopDecision(
            status="waiting_result",
            machine_converged=False,
            needs_human=False,
            reason_codes=("ARBITRATION_REQUIRED",),
            evidence=_round_evidence(ordered),
            next_round=3,
            next_kind=ROUND_KIND[3],
            target_dimensions=conflicts,
        )

    return _stop(
        status="needs_human",
        reason="ROUND3_FORCED_HUMAN",
        attempts=ordered,
    )


def validate_submission_scope(
    *,
    business_round: int,
    expected_kind: str,
    expected_dimensions: Sequence[str],
    submitted_kind: str,
    submitted_dimensions: Sequence[str],
) -> None:
    if business_round not in ROUND_KIND:
        raise LoopContractError("最多三轮，禁止第四轮")
    if submitted_kind != expected_kind or submitted_kind != ROUND_KIND[business_round]:
        raise LoopContractError("轮次 kind 与冻结流程不一致")
    expected = tuple(sorted(set(expected_dimensions)))
    submitted = tuple(sorted(set(submitted_dimensions)))
    if business_round == 1:
        if submitted:
            raise LoopContractError("round1 是基础判定，不能指定局部维度")
        return
    if not submitted or any(
        dimension.lower() in {"*", "all", "__all__", "full"}
        for dimension in submitted
    ):
        raise LoopContractError("round2/round3 必须只提交冻结的问题维度")
    if submitted != expected:
        raise LoopContractError("目标维度与上一轮冻结的问题维度不一致")


def validate_result_scope(
    *,
    business_round: int,
    target_dimensions: Sequence[str],
    normalized_result: Mapping[str, Any],
) -> None:
    if business_round == 1:
        return
    allowed = {str(item) for item in target_dimensions}
    unexpected_fields = sorted(
        set(normalized_result)
        - TARGETED_RESULT_METADATA_FIELDS
        - TARGETED_DIMENSION_FIELDS
    )
    forbidden = sorted(set(normalized_result) & FORBIDDEN_FULL_RESULT_FIELDS)
    if forbidden or unexpected_fields:
        fields = forbidden or unexpected_fields
        raise LoopContractError(
            "专项复判结果包含未允许字段：" + ",".join(fields)
        )

    reported = set(problem_dimensions(normalized_result))
    dimension_fields = {
        "dimension_values",
        "suggested_values",
        "confidence_by_dimension",
        "evidence",
        "arbitration_evidence",
    }
    for field in dimension_fields:
        value = normalized_result.get(field)
        if isinstance(value, Mapping):
            reported.update(str(key) for key in value)
        elif value not in (None, {}, []):
            raise LoopContractError(f"{field} 必须是按目标维度索引的对象")
    conflicts = normalized_result.get("conflicts")
    if conflicts not in (None, []):
        if not isinstance(conflicts, Sequence) or isinstance(
            conflicts, (str, bytes)
        ):
            raise LoopContractError("conflicts 必须是冲突对象列表")
        reported.update(_dimension_values(conflicts))
    unexpected = sorted(reported - allowed)
    if unexpected:
        raise LoopContractError(
            "专项复判结果包含未冻结维度：" + ",".join(unexpected)
        )
    values = _dimension_result_values(normalized_result)
    if not values or set(values) != allowed:
        raise LoopContractError("专项复判必须且只能返回全部目标维度值")
    if business_round == 2:
        if normalized_result.get("suggested_values") not in (None, {}):
            raise LoopContractError("round2 只能返回 dimension_values")
        if not isinstance(
            normalized_result.get("dimension_values"), Mapping
        ):
            raise LoopContractError("round2 必须返回 dimension_values")
        evidence = normalized_result.get("evidence")
        if not isinstance(evidence, Mapping) or not evidence:
            raise LoopContractError("round2 必须返回按目标维度索引的新证据")
        if normalized_result.get("arbitration_evidence") not in (None, {}):
            raise LoopContractError("round2 不能夹带仲裁证据")
    else:
        if normalized_result.get("dimension_values") not in (None, {}):
            raise LoopContractError("round3 只能返回 suggested_values")
        if not isinstance(
            normalized_result.get("suggested_values"), Mapping
        ):
            raise LoopContractError("round3 必须返回 suggested_values")
        arbitration = normalized_result.get("arbitration_evidence")
        if not isinstance(arbitration, Mapping) or not arbitration:
            raise LoopContractError("round3 必须返回按冲突维度索引的仲裁证据")
        if normalized_result.get("evidence") not in (None, {}):
            raise LoopContractError("round3 只允许仲裁证据")


def normalize_targeted_model_result(
    raw_result: Mapping[str, Any],
    *,
    business_round: int,
    target_dimensions: Sequence[str],
) -> dict[str, Any]:
    """Convert a targeted provider response into server-owned loop metadata."""
    targets = tuple(sorted(set(str(item) for item in target_dimensions)))
    raw_values = raw_result.get("dimension_values")
    if not isinstance(raw_values, Mapping):
        raw_values = raw_result.get("suggested_values")
    if not isinstance(raw_values, Mapping):
        raw_values = {
            target: raw_result[target]
            for target in targets
            if target in raw_result
        }
    values = {
        target: raw_values[target]
        for target in targets
        if target in raw_values
    }
    evidence_field = (
        "arbitration_evidence" if business_round == 3 else "evidence"
    )
    raw_evidence = raw_result.get(evidence_field)
    if not isinstance(raw_evidence, Mapping):
        raw_evidence = {}
    evidence = {
        target: raw_evidence[target]
        for target in targets
        if target in raw_evidence
    }
    raw_confidence = raw_result.get("confidence_by_dimension")
    confidence_by_dimension = (
        {
            target: raw_confidence[target]
            for target in targets
            if target in raw_confidence
            and isinstance(raw_confidence[target], (int, float))
            and not isinstance(raw_confidence[target], bool)
        }
        if isinstance(raw_confidence, Mapping)
        else {}
    )
    schema_valid = set(values) == set(targets) and set(evidence) == set(targets)
    enum_valid = schema_valid and all(
        value is not None and not isinstance(value, (bytes, bytearray))
        for value in values.values()
    )
    cross_field_valid = schema_valid and all(
        evidence.get(target) not in (None, "", [], {})
        for target in targets
    )
    confidence_threshold_met = (
        set(confidence_by_dimension) == set(targets)
        and all(value >= 0.7 for value in confidence_by_dimension.values())
    )
    normalized: dict[str, Any] = {
        (
            "suggested_values"
            if business_round == 3
            else "dimension_values"
        ): values,
        evidence_field: evidence,
        "confidence_by_dimension": confidence_by_dimension,
        "schema_valid": schema_valid,
        "enum_valid": enum_valid,
        "cross_field_valid": cross_field_valid,
        "confidence_threshold_met": confidence_threshold_met,
        "version_threshold_met": True,
        "freeze_threshold_met": True,
    }
    if raw_result.get("new_evidence") is False:
        normalized["new_evidence"] = False
    for field in HUMAN_REVIEW_FIELDS:
        if raw_result.get(field) is True:
            normalized[field] = True
    assert_safe_normalized_payload(normalized)
    validate_result_scope(
        business_round=business_round,
        target_dimensions=targets,
        normalized_result=normalized,
    )
    return normalized


def _flatten_dimension_values(
    value: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = f"{prefix}.{raw_key}" if prefix else str(raw_key)
        if isinstance(item, Mapping):
            if "grade" in item:
                flattened[str(raw_key)] = item["grade"]
            elif "status" in item:
                flattened[str(raw_key)] = item["status"]
            else:
                flattened.update(
                    _flatten_dimension_values(item, prefix=key)
                )
        elif isinstance(item, (str, int, float, bool)) or item is None:
            flattened[key] = item
    return flattened


def normalize_base_evaluation_result(
    *,
    precheck: Mapping[str, Any],
    aesthetic: Mapping[str, Any] | None,
    scoring: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a secret-safe, comparable round-1 result from evaluated fields."""
    values = _flatten_dimension_values(precheck)
    dimensions = (
        aesthetic.get("dimensions")
        if isinstance(aesthetic, Mapping)
        else None
    )
    if isinstance(dimensions, Mapping):
        values.update(_flatten_dimension_values(dimensions))
    evidence: dict[str, Any] = {}
    for dimension, item in (dimensions or {}).items():
        if not isinstance(item, Mapping):
            continue
        candidate = (
            item.get("evidence")
            or item.get("reason")
            or item.get("description")
        )
        if candidate not in (None, "", [], {}):
            evidence[str(dimension)] = candidate
    for field in ("evidence", "quality_evidence", "review_reasons"):
        candidate = precheck.get(field)
        if candidate not in (None, "", [], {}):
            evidence[f"precheck.{field}"] = candidate
    confidence = scoring.get("confidence")
    confidence_valid = (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and confidence >= 0.7
    )
    normalized: dict[str, Any] = {
        "dimension_values": values,
        "evidence": evidence,
        "schema_valid": bool(precheck) and bool(values),
        "enum_valid": bool(values)
        and all(value is not None for value in values.values()),
        "cross_field_valid": bool(precheck.get("classification")),
        "confidence_threshold_met": confidence_valid,
        "version_threshold_met": True,
        "freeze_threshold_met": True,
    }
    if scoring.get("needs_review") is True or (
        isinstance(aesthetic, Mapping)
        and aesthetic.get("needs_review") is True
    ):
        normalized["review_required"] = True
    if not evidence:
        normalized["review_required"] = True
    assert_safe_normalized_payload(normalized)
    return normalized


def enqueue_loop_evaluation_job(
    db: Any,
    *,
    loop_run: Any,
    attempt: Any,
    queue_class: str = "interactive",
) -> Any:
    """Idempotently create the only initial EvaluationJob for a loop round."""
    from sqlalchemy import select

    from .models import EvaluationJob, PromptVersion

    existing = db.scalar(
        select(EvaluationJob).where(
            EvaluationJob.loop_attempt_id == attempt.id,
            EvaluationJob.technical_attempt == 0,
        )
    )
    if existing is not None:
        return existing
    bundle = loop_run.strategy_bundle
    prompt_a_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "A",
            PromptVersion.version == bundle.prompt_a_version,
        )
    ).all()
    if len(prompt_a_matches) != 1:
        raise LoopContractError(
            "StrategyBundle 缺少唯一冻结的 A 阶段 PromptVersion"
        )
    prompt_b = None
    if bundle.prompt_b_version is not None:
        prompt_b_matches = db.scalars(
            select(PromptVersion).where(
                PromptVersion.stage == "B",
                PromptVersion.version == bundle.prompt_b_version,
            )
        ).all()
        if len(prompt_b_matches) != 1:
            raise LoopContractError(
                "StrategyBundle 缺少唯一冻结的 B 阶段 PromptVersion"
            )
        prompt_b = prompt_b_matches[0]
    job = EvaluationJob(
        asset_id=loop_run.asset_id,
        prompt_a_id=prompt_a_matches[0].id,
        prompt_b_id=prompt_b.id if prompt_b is not None else None,
        strategy_bundle_id=bundle.id,
        loop_attempt_id=attempt.id,
        queue_class=queue_class,
        origin_queue_class=queue_class,
        technical_attempt=0,
        batch_key=f"loop:{loop_run.id}",
        status="queued",
        stage="waiting",
    )
    db.add(job)
    db.flush()
    return job


def advance_loop_attempt(
    db: Any,
    *,
    loop_run: Any,
    attempt: Any,
    normalized_result: Mapping[str, Any],
    result_idempotency_key: str,
    result_fingerprint: str,
    technical_attempt: int,
    cost: float | None = None,
    latency_ms: int | None = None,
    next_queue_class: str = "interactive",
) -> LoopDecision:
    """Complete one round and atomically create the next round and its job."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from .models import LoopAttempt

    assert_safe_normalized_payload(normalized_result)
    if attempt.status == "completed":
        if (
            attempt.result_idempotency_key == result_idempotency_key
            and attempt.result_fingerprint == result_fingerprint
        ):
            return decide_next_step(
                [
                    {
                        "round": item.business_round,
                        "kind": item.kind,
                        "target_dimensions": json.loads(
                            item.target_dimensions_json
                        ),
                        "normalized_result": json.loads(
                            item.normalized_result_json or "{}"
                        ),
                    }
                    for item in loop_run.attempts
                    if item.status == "completed"
                ]
            )
        raise LoopContractError("完成的 LoopAttempt 不可变")
    previous_attempts = db.scalars(
        select(LoopAttempt)
        .where(
            LoopAttempt.loop_run_id == loop_run.id,
            LoopAttempt.status == "completed",
            LoopAttempt.id != attempt.id,
        )
        .order_by(LoopAttempt.business_round.asc())
    ).all()
    decision_attempts = [
        {
            "round": item.business_round,
            "kind": item.kind,
            "target_dimensions": json.loads(
                item.target_dimensions_json
            ),
            "normalized_result": json.loads(
                item.normalized_result_json or "{}"
            ),
        }
        for item in previous_attempts
    ]
    decision_attempts.append(
        {
            "round": attempt.business_round,
            "kind": attempt.kind,
            "target_dimensions": json.loads(
                attempt.target_dimensions_json
            ),
            "normalized_result": dict(normalized_result),
        }
    )
    decision = decide_next_step(
        decision_attempts
    )
    round_summaries = decision.evidence.get("rounds", [])
    server_conflicts = (
        round_summaries[-1].get("conflict_dimensions", [])
        if round_summaries
        else []
    )
    attempt.normalized_result_json = canonical_json(normalized_result)
    attempt.conflict_json = canonical_json(server_conflicts)
    attempt.status = "completed"
    attempt.technical_attempt = technical_attempt
    attempt.cost = cost
    attempt.latency_ms = latency_ms
    attempt.result_idempotency_key = result_idempotency_key
    attempt.result_fingerprint = result_fingerprint
    attempt.completed_at = datetime.now(timezone.utc)
    db.flush()
    loop_run.status = decision.status
    loop_run.decision_json = canonical_json(decision.as_dict())
    if decision.next_round is None:
        loop_run.completed_at = datetime.now(timezone.utc)
        return decision
    loop_run.current_round = decision.next_round
    next_attempt = LoopAttempt(
        business_round=decision.next_round,
        kind=decision.next_kind or ROUND_KIND[decision.next_round],
        target_dimensions_json=canonical_json(
            list(decision.target_dimensions)
        ),
        input_evidence_json=canonical_json(decision.evidence),
        status="waiting_result",
    )
    loop_run.attempts.append(next_attempt)
    db.flush()
    enqueue_loop_evaluation_job(
        db,
        loop_run=loop_run,
        attempt=next_attempt,
        queue_class=next_queue_class,
    )
    return decision
