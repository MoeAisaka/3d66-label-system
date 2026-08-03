"""Immutable, secret-safe strategy definitions for evaluation runs."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .loop_engine import contains_credential_auth_scheme
from .dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
    SPACE_SCHEMA_KEY,
)
from .models import (
    DimensionRoutePolicy,
    DimensionSchema,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
)


REDACTED = "[REDACTED]"
LEGACY_STRATEGY_SCHEMA_VERSION = "strategy-bundle-v1"
STRATEGY_SCHEMA_VERSION = "strategy-bundle-v2"
ROUTED_STRATEGY_SCHEMA_VERSION = "strategy-bundle-v3"
DIMENSION_ROUTE_POLICY_ID = "space-static-by-scoring-profile-v1"
RESOLVED_SCHEMA_CONTRACT_VERSION = "dimension-resolution-v1"
ROUTED_SCHEMA_CONTRACT_VERSION = "dimension-route-resolution-v2"
DIMENSION_SCHEMA_SET_FORMAT_VERSION = "dimension-schema-set-v1"
LABEL_FIELD_SET_FORMAT_VERSION = "label-field-set-snapshot-v1"
EVALUATION_PROFILE_SET_FORMAT_VERSION = "evaluation-profile-set-v1"
ROUTE_POLICY_SNAPSHOT_FORMAT_VERSION = "dimension-route-policy-snapshot-v1"

_ENDPOINT_KEYS = {
    "apibase",
    "apipath",
    "baseurl",
    "endpoint",
    "endpointurl",
    "uri",
    "url",
}
_SECRET_KEYS = {
    "accesskey",
    "accesskeyid",
    "apikey",
    "auth",
    "authentication",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "encryptedapikey",
    "password",
    "passwd",
    "privatekey",
    "proxyauthorization",
    "pwd",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessionid",
    "sessiontoken",
    "setcookie",
    "signature",
    "sig",
    "token",
    "xapikey",
}
_SECRET_SUFFIXES = (
    "accesstoken",
    "apikey",
    "auth",
    "authentication",
    "authorization",
    "clientsecret",
    "credential",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessiontoken",
    "signature",
    "token",
)
_AUTH_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|x-api-key|api-key|cookie|set-cookie)"
    r"(\s*[:=]\s*)([^\r\n]+)"
)
_AUTH_SCHEME_RE = re.compile(
    r"(?i)\b(bearer|basic)(\s+)([A-Za-z0-9._~+/=-]{4,})"
)
_PATH_SECRET_RE = re.compile(
    r"""(?ix)
    (
        /
        (?:
            authorization[-_]?header
            | client[-_]?secret
            | x[-_]?auth[-_]?token
            | x[-_]?api[-_]?key
            | api[-_]?key
            | auth
            | cookie
            | credential
            | password
            | secret
            | token
        )
        /
    )
    ([^/?#]+)
    """
)
_COMMON_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{8,}|AKIA[A-Z0-9]{12,}|"
    r"eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})\b"
)
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>'\"]+")
_SERIALIZED_SECRET_KEY_RE = re.compile(
    r"""(?ix)
    ["']?
    (?:
        api[-_\s]?key
        | authorization(?:[-_\s]?(?:header|value))?
        | proxy[-_\s]?authorization
        | client[-_\s]?secret
        | access[-_\s]?token
        | refresh[-_\s]?token
        | session[-_\s]?token
        | x[-_\s]?(?:api[-_\s]?key|auth[-_\s]?token)
        | cookie
        | password
        | credential
    )
    ["']?\s*(?:[:=]|/)
    """
)


class StrategySecretError(ValueError):
    """Raised with a value-safe message when a strategy cannot be sanitized."""


def _canonical_json(obj: Any) -> str:
    """Return byte-stable UTF-8 JSON for hashing and persistence."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _key_tokens(key: object) -> tuple[str, ...]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return tuple(
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if token
    )


def _is_secret_key(key: object) -> bool:
    normalized = _normalized_key(key)
    tokens = set(_key_tokens(key))
    if normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES):
        return True
    if any(
        root in normalized
        for root in (
            "apikey",
            "authorization",
            "authtoken",
            "clientsecret",
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
    return bool(
        "header" in tokens
        and tokens & {"api", "auth", "authorization", "cookie", "token"}
    )


def _redact_auth_material(value: str) -> str:
    value = _AUTH_HEADER_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        value,
    )
    value = _AUTH_SCHEME_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{REDACTED}"
            if contains_credential_auth_scheme(match.group(0))
            else match.group(0)
        ),
        value,
    )
    return _COMMON_CREDENTIAL_RE.sub(REDACTED, value)


def _is_explicit_placeholder_credential(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    return (
        "sentinel" in re.split(r"[^a-z0-9]+", normalized)
        or normalized in {"snapshot-user", "snapshot-pass"}
    )


def _endpoint_contains_secret_material(value: str) -> bool:
    try:
        parts = urlsplit(value.strip())
        _ = parts.port
    except (TypeError, ValueError):
        raise StrategySecretError(
            "StrategyBundle endpoint 无法安全解析"
        ) from None
    if parts.username is not None or parts.password is not None:
        return True
    if _PATH_SECRET_RE.search(parts.path):
        return True
    for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_secret_key(key) and item_value:
            return True
        if (
            _AUTH_HEADER_RE.search(item_value)
            or contains_credential_auth_scheme(item_value)
            or _COMMON_CREDENTIAL_RE.search(item_value)
            or _SERIALIZED_SECRET_KEY_RE.search(item_value)
        ):
            return True
    return False


def _endpoint_contains_only_placeholder_credentials(value: str) -> bool:
    try:
        parts = urlsplit(value.strip())
        _ = parts.port
    except (TypeError, ValueError):
        return False
    credentials: list[str] = []
    if parts.username is not None:
        credentials.append(parts.username)
    if parts.password is not None:
        credentials.append(parts.password)
    if _PATH_SECRET_RE.search(parts.path) is not None:
        return False
    for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_secret_key(key) and item_value:
            credentials.append(item_value)
        elif (
            _AUTH_HEADER_RE.search(item_value)
            or contains_credential_auth_scheme(item_value)
            or _COMMON_CREDENTIAL_RE.search(item_value)
            or _SERIALIZED_SECRET_KEY_RE.search(item_value)
        ):
            return False
    return bool(credentials) and all(
        _is_explicit_placeholder_credential(item)
        for item in credentials
    )


def _prompt_contains_only_placeholder_credentials(value: str) -> bool:
    matches = list(_AUTH_HEADER_RE.finditer(value))
    if not matches:
        return False
    for match in matches:
        credential = match.group(3).strip()
        scheme_match = re.match(
            r"(?i)^(?:bearer|basic)\s+(\S+)$",
            credential,
        )
        if scheme_match is not None:
            credential = scheme_match.group(1)
        if not _is_explicit_placeholder_credential(credential):
            return False
    without_headers = _AUTH_HEADER_RE.sub("", value)
    try:
        return _sanitize_text(without_headers) == without_headers
    except StrategySecretError:
        return False


def _sanitize_embedded_urls(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        matched = match.group(0)
        candidate = matched.rstrip(".,);]")
        if not _endpoint_contains_secret_material(candidate):
            return matched
        return _sanitize_endpoint(candidate) + matched[len(candidate) :]

    return _URL_RE.sub(replace, value)


def _sanitize_text(value: str, *, endpoint: bool = False) -> str:
    stripped = value.strip()
    if endpoint and stripped.startswith(("{", "[")):
        raise StrategySecretError(
            "StrategyBundle endpoint 包含无法安全解析的结构"
        )
    if endpoint:
        sanitized = _sanitize_endpoint(value)
    else:
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                if _SERIALIZED_SECRET_KEY_RE.search(value):
                    raise StrategySecretError(
                        "StrategyBundle 定义包含无法安全解析的凭据容器"
                    ) from exc
            else:
                if isinstance(decoded, (dict, list)):
                    sanitized_decoded = _redact_secrets(decoded)
                    if sanitized_decoded == decoded:
                        return value
                    return _canonical_json(sanitized_decoded)
        sanitized = _sanitize_embedded_urls(value)
        sanitized = _redact_auth_material(sanitized)
    if _COMMON_CREDENTIAL_RE.search(sanitized):
        raise StrategySecretError("StrategyBundle 定义包含无法安全脱敏的凭据")
    if (
        not endpoint
        and _contains_unredacted_secret_assignment(sanitized)
    ):
        raise StrategySecretError(
            "StrategyBundle 定义包含无法安全脱敏的凭据容器"
        )
    return sanitized


def _contains_unredacted_secret_assignment(value: str) -> bool:
    for match in _SERIALIZED_SECRET_KEY_RE.finditer(value):
        assigned = value[match.end() :].lstrip()
        if assigned.startswith(("\"", "'")):
            assigned = assigned[1:].lstrip()
        if not assigned.startswith(REDACTED):
            return True
    return False


def _sanitize_endpoint(value: str) -> str:
    """Keep endpoint identity while removing userinfo and secret query values."""
    value = value.strip()
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if (
            scheme in {"http", "https"}
            and (not parts.netloc or parts.hostname is None)
        ):
            raise ValueError("absolute endpoint requires a hostname")
        if ("://" in value or "@" in value) and not parts.netloc:
            raise ValueError("endpoint authority cannot be parsed")
        netloc = parts.netloc
        if netloc:
            # urlsplit exposes credentials via username/password. Reconstructing
            # from hostname/port guarantees they never survive the snapshot.
            hostname = parts.hostname
            if hostname:
                normalized_host = hostname.lower()
                if ":" in normalized_host and not normalized_host.startswith("["):
                    normalized_host = f"[{normalized_host}]"
                port = parts.port
                default_port = (scheme == "https" and port == 443) or (
                    scheme == "http" and port == 80
                )
                netloc = normalized_host + (
                    f":{port}" if port is not None and not default_port else ""
                )
            else:
                netloc = netloc.rsplit("@", 1)[-1].lower()

        safe_path = _PATH_SECRET_RE.sub(
            lambda match: f"{match.group(1)}{REDACTED}",
            parts.path,
        )
        safe_path = _sanitize_text(safe_path)
        query_items = []
        for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
            query_items.append(
                (
                    key,
                    REDACTED
                    if _is_secret_key(key)
                    else _sanitize_text(item_value),
                )
            )
        query_items.sort(key=lambda item: (item[0], item[1]))
        sanitized = urlunsplit(
            (
                scheme,
                netloc,
                safe_path,
                urlencode(query_items),
                "",
            )
        )
    except (TypeError, ValueError):
        raise StrategySecretError(
            "StrategyBundle endpoint 无法安全解析"
        ) from None
    return _redact_auth_material(sanitized)


def _redact_secrets(value: Any, *, key_hint: object | None = None) -> Any:
    """Recursively redact credentials from arbitrary snapshot-shaped data."""
    normalized_hint = _normalized_key(key_hint) if key_hint is not None else ""
    if key_hint is not None and _is_secret_key(key_hint):
        return REDACTED
    if isinstance(value, dict):
        return {
            key: _redact_secrets(item, key_hint=key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item, key_hint=key_hint) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        return _sanitize_text(
            value,
            endpoint=normalized_hint in _ENDPOINT_KEYS,
        )
    return value


def _assert_no_sensitive_material(value: Any) -> None:
    """Fail closed unless a final persisted structure is already fully safe."""
    if _redact_secrets(value) != value:
        raise StrategySecretError(
            "StrategyBundle 定义未通过最终安全校验"
        )


def _build_model_config_snapshot(config: ModelConfig) -> dict[str, Any]:
    """Capture every non-secret model setting that can affect an evaluation."""
    for endpoint in (config.base_url, config.api_path):
        if (
            _endpoint_contains_secret_material(endpoint)
            and not _endpoint_contains_only_placeholder_credentials(endpoint)
        ):
            raise StrategySecretError(
                "StrategyBundle 模型配置包含凭据材料"
            )
    _assert_no_sensitive_material(
        {
            "name": config.name,
            "provider": config.provider,
            "model_id": config.model_id,
        }
    )
    snapshot = {
        "name": config.name,
        "provider": config.provider,
        "protocol": getattr(config, "protocol", None) or "openai_chat",
        "base_url": config.base_url,
        "api_path": config.api_path,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "max_concurrency": config.max_concurrency,
        "structured_output": config.structured_output,
        "high_risk_review_enabled": config.high_risk_review_enabled,
    }
    return _redact_secrets(snapshot)


def build_model_config_snapshot(config: ModelConfig) -> dict[str, Any]:
    """Public safe snapshot used by Job-level execution contracts."""
    return _build_model_config_snapshot(config)


def _build_prompt_definition(prompt: PromptVersion) -> dict[str, Any]:
    """Capture prompt identity and exact persisted contents."""
    if prompt.id is None:
        raise ValueError("PromptVersion 必须先持久化，才能创建 StrategyBundle")
    definition = {
        "id": prompt.id,
        "category_key": prompt.category_key,
        "pipeline_scope": prompt.pipeline_scope,
        "stage": prompt.stage,
        "version": prompt.version,
        "name": prompt.name,
        "rubric_version": prompt.rubric_version,
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
    }
    sanitized = _redact_secrets(definition)
    if sanitized != definition:
        changed_prompt_fields = [
            field
            for field in ("system_prompt", "user_prompt")
            if sanitized[field] != definition[field]
        ]
        if (
            not changed_prompt_fields
            or any(
                not _prompt_contains_only_placeholder_credentials(
                    definition[field]
                )
                for field in changed_prompt_fields
            )
            or any(
                sanitized[field] != definition[field]
                for field in (
                    "id",
                    "stage",
                    "version",
                    "name",
                    "rubric_version",
                )
            )
        ):
            raise StrategySecretError(
                "StrategyBundle 定义未通过最终安全校验"
            )
        return sanitized
    return definition


def _build_sampling_policy_definition(
    sampling_policy: SamplingPolicy | None,
) -> dict[str, Any] | None:
    if sampling_policy is None:
        return None
    if sampling_policy.id is None:
        raise ValueError("SamplingPolicy 必须先持久化，才能创建 StrategyBundle")
    return {
        "id": sampling_policy.id,
        "revision": sampling_policy.revision,
        "sample_rate": sampling_policy.sample_rate,
        "low_confidence_threshold": sampling_policy.low_confidence_threshold,
        "medium_confidence_threshold": sampling_policy.medium_confidence_threshold,
        "cold_start_required_count": sampling_policy.cold_start_required_count,
        "high_level_required_from": sampling_policy.high_level_required_from,
    }


def _load_dimension_contract(
    db: Session,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze both behavior-compatible space revisions into a stable set."""
    schemas = db.scalars(
        select(DimensionSchema)
        .where(
            DimensionSchema.schema_key == SPACE_SCHEMA_KEY,
            DimensionSchema.version.in_(
                (HISTORICAL_DEFAULT_VERSION, ACTIVE_V13_VERSION)
            ),
            DimensionSchema.status == "published",
        )
        .order_by(DimensionSchema.version.asc())
    ).all()
    by_version = {schema.version: schema for schema in schemas}
    expected_versions = {
        HISTORICAL_DEFAULT_VERSION,
        ACTIVE_V13_VERSION,
    }
    if set(by_version) != expected_versions:
        raise ValueError(
            "创建 StrategyBundle 前必须存在两个已发布的空间维度兼容修订"
        )

    entries: list[dict[str, Any]] = []
    label_sets: dict[str, dict[str, Any]] = {}
    for version in sorted(expected_versions):
        schema = by_version[version]
        try:
            definition = json.loads(schema.definition_json)
        except json.JSONDecodeError as exc:
            raise ValueError("DimensionSchema definition_json 已损坏") from exc
        if (
            not isinstance(definition, dict)
            or _compute_canonical_hash(definition)
            != schema.canonical_hash
        ):
            raise ValueError("DimensionSchema 规范哈希无法复算")
        output_contract = definition.get("output_contract")
        if not isinstance(output_contract, dict):
            raise ValueError("DimensionSchema 缺少 output_contract")
        label_set_id = output_contract.get("label_field_set_id")
        label_fields = output_contract.get("label_fields_snapshot")
        if (
            not isinstance(label_set_id, str)
            or not label_set_id
            or not isinstance(label_fields, list)
        ):
            raise ValueError("DimensionSchema 标签字段集合不完整")
        label_sets[label_set_id] = {
            "label_field_set_id": label_set_id,
            "label_fields_snapshot": label_fields,
        }
        entries.append(
            {
                "schema_key": schema.schema_key,
                "version": schema.version,
                "schema_type": schema.schema_type,
                "family_key": schema.family_key,
                "canonical_hash": schema.canonical_hash,
                "definition": definition,
            }
        )

    dimension_set = {
        "format_version": DIMENSION_SCHEMA_SET_FORMAT_VERSION,
        "schemas": entries,
    }
    label_set = {
        "format_version": LABEL_FIELD_SET_FORMAT_VERSION,
        "sets": [
            label_sets[key]
            for key in sorted(label_sets)
        ],
    }
    return dimension_set, label_set


def build_dimension_route_policy_snapshot(
    policy: DimensionRoutePolicy,
) -> dict[str, Any]:
    """Freeze one registered route policy with its complete definition."""
    if policy.id is None:
        raise ValueError(
            "DimensionRoutePolicy 必须先持久化，才能创建 v3 Bundle"
        )
    try:
        definition = json.loads(policy.definition_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "DimensionRoutePolicy definition_json 已损坏"
        ) from exc
    if (
        not isinstance(definition, dict)
        or _compute_canonical_hash(definition) != policy.canonical_hash
    ):
        raise ValueError("DimensionRoutePolicy 规范哈希无法复算")
    if (
        definition.get("policy_key") != policy.policy_key
        or definition.get("policy_version") != policy.version
    ):
        raise ValueError("DimensionRoutePolicy 身份与定义不一致")
    return {
        "format_version": ROUTE_POLICY_SNAPSHOT_FORMAT_VERSION,
        "id": policy.id,
        "policy_key": policy.policy_key,
        "version": policy.version,
        "status": policy.status,
        "canonical_hash": policy.canonical_hash,
        "definition": definition,
    }


def build_frozen_evaluation_profile(
    *,
    profile_key: str,
    schema: DimensionSchema,
    prompt_b: PromptVersion | None,
) -> dict[str, Any]:
    """Freeze one schema, optional B prompt and label-field contract."""
    if not profile_key.strip():
        raise ValueError("EvaluationProfile 键不能为空")
    if schema.id is None:
        raise ValueError(
            "DimensionSchema 必须先持久化，才能创建 v3 Bundle"
        )
    try:
        definition = json.loads(schema.definition_json)
    except json.JSONDecodeError as exc:
        raise ValueError("DimensionSchema definition_json 已损坏") from exc
    if (
        not isinstance(definition, dict)
        or _compute_canonical_hash(definition) != schema.canonical_hash
    ):
        raise ValueError("DimensionSchema 规范哈希无法复算")
    output_contract = definition.get("output_contract")
    if not isinstance(output_contract, dict):
        raise ValueError("DimensionSchema 缺少 output_contract")
    label_field_set_id = output_contract.get("label_field_set_id")
    label_fields = output_contract.get("label_fields_snapshot")
    if label_field_set_id is None and label_fields is None:
        label_field_set_id = (
            f"{schema.schema_key}-label-fields-empty-v1"
        )
        label_fields = []
    if (
        not isinstance(label_field_set_id, str)
        or not label_field_set_id
        or not isinstance(label_fields, list)
    ):
        raise ValueError("DimensionSchema 标签字段集合不完整")
    label_field_set = {
        "format_version": LABEL_FIELD_SET_FORMAT_VERSION,
        "label_field_set_id": label_field_set_id,
        "label_fields_snapshot": label_fields,
    }
    label_field_set["canonical_hash"] = _compute_canonical_hash(
        label_field_set
    )

    prompt_definition = (
        _build_prompt_definition(prompt_b)
        if prompt_b is not None
        else None
    )
    if prompt_b is not None and prompt_b.stage != "B":
        raise ValueError("EvaluationProfile 只能冻结 B 阶段提示词")
    frozen_prompt = (
        {
            **prompt_definition,
            "canonical_hash": _compute_canonical_hash(prompt_definition),
        }
        if prompt_definition is not None
        else None
    )

    source_gate = definition.get("release_gate")
    if not isinstance(source_gate, dict):
        source_gate = {}
    blocked_reasons = [
        str(item)
        for item in source_gate.get("blocked_reasons", [])
        if isinstance(item, str) and item
    ]
    if frozen_prompt is None and "prompt_contract_missing" not in (
        blocked_reasons
    ):
        blocked_reasons.append("prompt_contract_missing")
    publishing_blocked = bool(
        source_gate.get("publishing_blocked", False)
        or blocked_reasons
    )
    release_gate = {
        "minimum_calibration_samples": int(
            source_gate.get("minimum_calibration_samples", 0)
        ),
        "target_calibration_samples": int(
            source_gate.get("target_calibration_samples", 0)
        ),
        "completed_calibration_samples": int(
            source_gate.get("completed_calibration_samples", 0)
        ),
        "required_sample_roles": [
            str(item)
            for item in source_gate.get("required_sample_roles", [])
            if isinstance(item, str) and item
        ],
        "status": str(source_gate.get("status", "not_applicable")),
        "publishing_blocked": publishing_blocked,
        "blocked_reasons": sorted(set(blocked_reasons)),
    }
    profile = {
        "profile_key": profile_key.strip(),
        "family_key": schema.family_key,
        "status": schema.status,
        "dimension_schema": {
            "id": schema.id,
            "schema_key": schema.schema_key,
            "version": schema.version,
            "schema_type": schema.schema_type,
            "family_key": schema.family_key,
            "status": schema.status,
            "canonical_hash": schema.canonical_hash,
            "definition": definition,
        },
        "prompt_b": frozen_prompt,
        "label_field_set": label_field_set,
        "release_gate": release_gate,
    }
    profile["canonical_hash"] = _compute_canonical_hash(profile)
    return profile


def build_evaluation_profile_set(
    *,
    profiles: list[dict[str, Any]],
    execution_context: str,
    default_profile_key: str,
) -> dict[str, Any]:
    """Build and validate the byte-stable candidate set frozen before A."""
    if execution_context not in {"calibration", "production"}:
        raise ValueError("execution_context 只允许 calibration 或 production")
    profile_map: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("EvaluationProfile 必须是对象")
        key = profile.get("profile_key")
        if not isinstance(key, str) or not key or key in profile_map:
            raise ValueError("EvaluationProfile 键为空或重复")
        stored_hash = profile.get("canonical_hash")
        definition_without_hash = {
            item_key: item_value
            for item_key, item_value in profile.items()
            if item_key != "canonical_hash"
        }
        if (
            not isinstance(stored_hash, str)
            or stored_hash
            != _compute_canonical_hash(definition_without_hash)
        ):
            raise ValueError(f"EvaluationProfile {key} 规范哈希无效")
        profile_map[key] = deepcopy(profile)
    if not profile_map:
        raise ValueError("EvaluationProfile 集合不能为空")
    if default_profile_key not in profile_map:
        raise ValueError("默认 EvaluationProfile 未包含在冻结集合中")
    profile_set = {
        "format_version": EVALUATION_PROFILE_SET_FORMAT_VERSION,
        "execution_context": execution_context,
        "default_profile_key": default_profile_key,
        "profiles": {
            key: profile_map[key]
            for key in sorted(profile_map)
        },
    }
    profile_set["canonical_hash"] = _compute_canonical_hash(profile_set)
    return profile_set


def _validate_routed_bundle_contract(
    *,
    route_policy_snapshot: dict[str, Any],
    profile_set: dict[str, Any],
) -> None:
    if (
        route_policy_snapshot.get("format_version")
        != ROUTE_POLICY_SNAPSHOT_FORMAT_VERSION
    ):
        raise ValueError("未知的冻结路由策略快照版本")
    policy_definition = route_policy_snapshot.get("definition")
    policy_hash = route_policy_snapshot.get("canonical_hash")
    if (
        not isinstance(policy_definition, dict)
        or not isinstance(policy_hash, str)
        or _compute_canonical_hash(policy_definition) != policy_hash
    ):
        raise ValueError("冻结路由策略规范哈希无效")
    if (
        profile_set.get("format_version")
        != EVALUATION_PROFILE_SET_FORMAT_VERSION
    ):
        raise ValueError("未知的 EvaluationProfile 集合版本")
    stored_set_hash = profile_set.get("canonical_hash")
    set_without_hash = {
        key: value
        for key, value in profile_set.items()
        if key != "canonical_hash"
    }
    if (
        not isinstance(stored_set_hash, str)
        or stored_set_hash != _compute_canonical_hash(set_without_hash)
    ):
        raise ValueError("EvaluationProfile 集合规范哈希无效")
    profiles = profile_set.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("EvaluationProfile 集合为空")

    schema_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for profile_key, profile in profiles.items():
        if (
            not isinstance(profile_key, str)
            or not isinstance(profile, dict)
            or profile.get("profile_key") != profile_key
        ):
            raise ValueError("EvaluationProfile 集合身份不一致")
        profile_hash = profile.get("canonical_hash")
        profile_without_hash = {
            key: value
            for key, value in profile.items()
            if key != "canonical_hash"
        }
        if (
            not isinstance(profile_hash, str)
            or profile_hash
            != _compute_canonical_hash(profile_without_hash)
        ):
            raise ValueError(
                f"EvaluationProfile {profile_key} 规范哈希无效"
            )
        schema = profile.get("dimension_schema")
        if not isinstance(schema, dict):
            raise ValueError(
                f"EvaluationProfile {profile_key} 缺少维度规则"
            )
        schema_definition = schema.get("definition")
        if (
            not isinstance(schema_definition, dict)
            or schema.get("canonical_hash")
            != _compute_canonical_hash(schema_definition)
        ):
            raise ValueError(
                f"EvaluationProfile {profile_key} 的 Schema 哈希无效"
            )
        identity = (
            str(schema.get("schema_key")),
            str(schema.get("version")),
            str(schema.get("canonical_hash")),
        )
        if identity in schema_index:
            raise ValueError("EvaluationProfile 集合重复冻结同一 Schema")
        schema_index[identity] = profile

        label_set = profile.get("label_field_set")
        if not isinstance(label_set, dict):
            raise ValueError(
                f"EvaluationProfile {profile_key} 缺少标签字段集合"
            )
        label_hash = label_set.get("canonical_hash")
        label_without_hash = {
            key: value
            for key, value in label_set.items()
            if key != "canonical_hash"
        }
        if (
            not isinstance(label_hash, str)
            or label_hash != _compute_canonical_hash(label_without_hash)
        ):
            raise ValueError(
                f"EvaluationProfile {profile_key} 的标签哈希无效"
            )
        prompt_b = profile.get("prompt_b")
        if prompt_b is not None:
            if not isinstance(prompt_b, dict) or prompt_b.get("stage") != "B":
                raise ValueError(
                    f"EvaluationProfile {profile_key} 的 B 提示词无效"
                )
            prompt_hash = prompt_b.get("canonical_hash")
            prompt_without_hash = {
                key: value
                for key, value in prompt_b.items()
                if key != "canonical_hash"
            }
            if (
                not isinstance(prompt_hash, str)
                or prompt_hash
                != _compute_canonical_hash(prompt_without_hash)
            ):
                raise ValueError(
                    f"EvaluationProfile {profile_key} 的 B 哈希无效"
                )
        gate = profile.get("release_gate")
        if (
            not isinstance(gate, dict)
            or not isinstance(gate.get("publishing_blocked"), bool)
            or not isinstance(gate.get("blocked_reasons"), list)
        ):
            raise ValueError(
                f"EvaluationProfile {profile_key} 的发布门禁无效"
            )

    routes = policy_definition.get("family_routes")
    if not isinstance(routes, dict) or not routes:
        raise ValueError("冻结路由策略缺少 family_routes")
    for family_key, route in routes.items():
        schema_ref = route.get("schema_ref") if isinstance(route, dict) else None
        if not isinstance(schema_ref, dict):
            raise ValueError(f"素材族 {family_key} 缺少冻结 Schema 引用")
        identity = (
            str(schema_ref.get("schema_key")),
            str(schema_ref.get("version")),
            str(schema_ref.get("canonical_hash")),
        )
        if identity not in schema_index:
            raise ValueError(f"素材族 {family_key} 命中未冻结 Profile")

    execution_context = profile_set.get("execution_context")
    if execution_context == "production":
        if (
            route_policy_snapshot.get("status") != "published"
            or policy_definition.get("activation_scope")
            == "calibration_only"
        ):
            raise ValueError("生产 Bundle 只能冻结已发布的生产路由策略")
        for profile_key, profile in profiles.items():
            gate = profile["release_gate"]
            if (
                profile.get("status") != "published"
                or profile.get("prompt_b") is None
                or gate.get("publishing_blocked") is not False
                or gate.get("blocked_reasons")
            ):
                raise ValueError(
                    f"生产 Bundle 的 Profile {profile_key} 未通过发布门禁"
                )
    elif execution_context != "calibration":
        raise ValueError("EvaluationProfile 集合缺少合法执行上下文")


def validate_routed_bundle_contract(
    *,
    route_policy_snapshot: dict[str, Any],
    profile_set: dict[str, Any],
) -> None:
    """Public fail-closed validator for v3 route/profile snapshots."""
    _validate_routed_bundle_contract(
        route_policy_snapshot=route_policy_snapshot,
        profile_set=profile_set,
    )


def _profile_sets_for_legacy_columns(
    profile_set: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    profiles = profile_set["profiles"]
    schema_entries: dict[tuple[str, str], dict[str, Any]] = {}
    label_entries: dict[str, dict[str, Any]] = {}
    for profile in profiles.values():
        schema = deepcopy(profile["dimension_schema"])
        schema_entries[(schema["schema_key"], schema["version"])] = schema
        label_set = deepcopy(profile["label_field_set"])
        label_entries[label_set["label_field_set_id"]] = label_set
    return (
        {
            "format_version": DIMENSION_SCHEMA_SET_FORMAT_VERSION,
            "schemas": [
                schema_entries[key]
                for key in sorted(schema_entries)
            ],
        },
        {
            "format_version": LABEL_FIELD_SET_FORMAT_VERSION,
            "sets": [
                label_entries[key]
                for key in sorted(label_entries)
            ],
        },
    )


def _dimension_contract_is_active(db: Session) -> bool:
    """Keep pre-migration/test databases on the readable v1 contract."""
    if db.get_bind().dialect.name != "sqlite":
        return True
    connection = db.connection()
    migrations_table = connection.exec_driver_sql(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).first()
    if migrations_table is None:
        return False
    return (
        connection.exec_driver_sql(
            "SELECT 1 FROM schema_migrations WHERE version = 26"
        ).first()
        is not None
    )


def _routed_contract_is_active(db: Session) -> bool:
    """Require migration 29 before persisting strategy-bundle-v3."""
    if db.get_bind().dialect.name != "sqlite":
        return True
    connection = db.connection()
    migrations_table = connection.exec_driver_sql(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).first()
    if migrations_table is None:
        return False
    return (
        connection.exec_driver_sql(
            "SELECT 1 FROM schema_migrations WHERE version = 29"
        ).first()
        is not None
    )


def _build_canonical_definition(
    *,
    schema_version: str,
    model_id: str,
    model_config_snapshot: dict[str, Any],
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    rubric_version: str,
    engine_version: str,
    sampling_policy: SamplingPolicy | None,
    risk_review_version: str | None,
    agent_plan_version: str,
    dimension_route_policy_id: str | None = None,
    dimension_schema_set: dict[str, Any] | None = None,
    label_field_set: dict[str, Any] | None = None,
    resolved_schema_contract_version: str | None = None,
    dimension_route_policy_snapshot: dict[str, Any] | None = None,
    evaluation_profile_set_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = {
        "schema_version": schema_version,
        "model_id": model_id,
        "model_config": model_config_snapshot,
        "prompt_a": _build_prompt_definition(prompt_a),
        "prompt_b": _build_prompt_definition(prompt_b) if prompt_b else None,
        "rubric_version": rubric_version,
        "engine_version": engine_version,
        "sampling_policy": _build_sampling_policy_definition(sampling_policy),
        "risk_review_version": risk_review_version,
        "agent_plan_version": agent_plan_version,
    }
    if schema_version in {
        STRATEGY_SCHEMA_VERSION,
        ROUTED_STRATEGY_SCHEMA_VERSION,
    }:
        if (
            not dimension_route_policy_id
            or not isinstance(dimension_schema_set, dict)
            or not isinstance(label_field_set, dict)
            or not resolved_schema_contract_version
        ):
            raise ValueError("strategy-bundle-v2 缺少完整维度合同")
        definition.update(
            {
                "dimension_route_policy_id": dimension_route_policy_id,
                "dimension_schema_set": dimension_schema_set,
                "label_field_set": label_field_set,
                "resolved_schema_contract_version": (
                    resolved_schema_contract_version
                ),
            }
        )
        if schema_version == ROUTED_STRATEGY_SCHEMA_VERSION:
            if (
                prompt_b is not None
                or not isinstance(dimension_route_policy_snapshot, dict)
                or not isinstance(evaluation_profile_set_snapshot, dict)
            ):
                raise ValueError(
                    "strategy-bundle-v3 缺少冻结路由或评审配置集合"
                )
            definition.update(
                {
                    "dimension_route_policy_snapshot": (
                        dimension_route_policy_snapshot
                    ),
                    "evaluation_profile_set_snapshot": (
                        evaluation_profile_set_snapshot
                    ),
                }
            )
        elif (
            dimension_route_policy_snapshot is not None
            or evaluation_profile_set_snapshot is not None
        ):
            raise ValueError(
                "strategy-bundle-v2 不允许携带 v3 冻结配置"
            )
    elif schema_version != LEGACY_STRATEGY_SCHEMA_VERSION:
        raise ValueError("不支持的 StrategyBundle 快照版本")
    return _redact_secrets(definition)


def _compute_canonical_hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(definition).encode("utf-8")).hexdigest()


def _assert_strategy_identity_is_safe(
    definition: dict[str, Any],
    *,
    model_config: ModelConfig,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    rubric_version: str,
    engine_version: str,
    risk_review_version: str | None,
    agent_plan_version: str,
) -> None:
    prompt_a_definition = definition["prompt_a"]
    prompt_b_definition = definition["prompt_b"]
    expected = (
        model_config.model_id,
        prompt_a.version,
        prompt_b.version if prompt_b else None,
        rubric_version,
        engine_version,
        risk_review_version,
        agent_plan_version,
    )
    sanitized = (
        definition["model_id"],
        prompt_a_definition["version"],
        prompt_b_definition["version"] if prompt_b_definition else None,
        definition["rubric_version"],
        definition["engine_version"],
        definition["risk_review_version"],
        definition["agent_plan_version"],
    )
    if sanitized != expected:
        raise StrategySecretError(
            "StrategyBundle 身份字段包含凭据材料，已拒绝创建"
        )


def _bundle_values(
    *,
    canonical_hash: str,
    strategy_schema_version: str,
    model_config: ModelConfig,
    model_config_snapshot: dict[str, Any],
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    rubric_version: str,
    engine_version: str,
    risk_review_version: str | None,
    sampling_policy: SamplingPolicy | None,
    agent_plan_version: str,
    dimension_route_policy_id: str | None,
    dimension_schema_set: dict[str, Any] | None,
    label_field_set: dict[str, Any] | None,
    resolved_schema_contract_version: str | None,
    dimension_route_policy_snapshot: dict[str, Any] | None = None,
    evaluation_profile_set_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "canonical_hash": canonical_hash,
        "strategy_schema_version": strategy_schema_version,
        "model_id": model_config.model_id,
        "model_config_snapshot": _canonical_json(model_config_snapshot),
        "prompt_a_version": prompt_a.version,
        "prompt_b_version": prompt_b.version if prompt_b else None,
        "rubric_version": rubric_version,
        "engine_version": engine_version,
        "sampling_policy_revision": (
            sampling_policy.revision if sampling_policy else None
        ),
        "risk_review_version": risk_review_version,
        "agent_plan_version": agent_plan_version,
        "dimension_route_policy_id": dimension_route_policy_id,
        "dimension_schema_set_snapshot": (
            _canonical_json(dimension_schema_set)
            if dimension_schema_set is not None
            else None
        ),
        "label_field_set_snapshot": (
            _canonical_json(label_field_set)
            if label_field_set is not None
            else None
        ),
        "resolved_schema_contract_version": (
            resolved_schema_contract_version
        ),
        "dimension_route_policy_snapshot": (
            _canonical_json(dimension_route_policy_snapshot)
            if dimension_route_policy_snapshot is not None
            else None
        ),
        "evaluation_profile_set_snapshot": (
            _canonical_json(evaluation_profile_set_snapshot)
            if evaluation_profile_set_snapshot is not None
            else None
        ),
    }


def _insert_bundle_if_absent(
    db: Session,
    values: dict[str, Any],
) -> None:
    """Atomically insert only across the canonical-hash conflict target."""
    statement = (
        sqlite_insert(StrategyBundle)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[StrategyBundle.canonical_hash]
        )
    )
    db.execute(statement)


def _is_canonical_hash_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return (
        "uq_strategy_canonical_hash" in message
        or "strategy_bundles.canonical_hash" in message
    )


def get_or_create_bundle(
    db: Session,
    model_config: ModelConfig,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    rubric_version: str,
    engine_version: str,
    risk_review_version: str | None,
    sampling_policy: SamplingPolicy | None,
    agent_plan_version: str = "controlled-agent-plan-v1",
) -> StrategyBundle:
    """Reuse an identical immutable definition or persist a new bundle."""
    model_config_snapshot = _build_model_config_snapshot(model_config)
    legacy_shape_for_secret_gate = _build_canonical_definition(
        schema_version=LEGACY_STRATEGY_SCHEMA_VERSION,
        model_id=model_config.model_id,
        model_config_snapshot=model_config_snapshot,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=rubric_version,
        engine_version=engine_version,
        sampling_policy=sampling_policy,
        risk_review_version=risk_review_version,
        agent_plan_version=agent_plan_version,
    )
    _assert_strategy_identity_is_safe(
        legacy_shape_for_secret_gate,
        model_config=model_config,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=rubric_version,
        engine_version=engine_version,
        risk_review_version=risk_review_version,
        agent_plan_version=agent_plan_version,
    )
    _assert_no_sensitive_material(legacy_shape_for_secret_gate)

    dimension_contract_active = _dimension_contract_is_active(db)
    if dimension_contract_active:
        dimension_schema_set, label_field_set = (
            _load_dimension_contract(db)
        )
        strategy_schema_version = STRATEGY_SCHEMA_VERSION
        dimension_route_policy_id = DIMENSION_ROUTE_POLICY_ID
        resolved_schema_contract_version = (
            RESOLVED_SCHEMA_CONTRACT_VERSION
        )
    else:
        dimension_schema_set = None
        label_field_set = None
        strategy_schema_version = LEGACY_STRATEGY_SCHEMA_VERSION
        dimension_route_policy_id = None
        resolved_schema_contract_version = None
    definition = _build_canonical_definition(
        schema_version=strategy_schema_version,
        model_id=model_config.model_id,
        model_config_snapshot=model_config_snapshot,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=rubric_version,
        engine_version=engine_version,
        sampling_policy=sampling_policy,
        risk_review_version=risk_review_version,
        agent_plan_version=agent_plan_version,
        dimension_route_policy_id=dimension_route_policy_id,
        dimension_schema_set=dimension_schema_set,
        label_field_set=label_field_set,
        resolved_schema_contract_version=resolved_schema_contract_version,
    )
    _assert_strategy_identity_is_safe(
        definition,
        model_config=model_config,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=rubric_version,
        engine_version=engine_version,
        risk_review_version=risk_review_version,
        agent_plan_version=agent_plan_version,
    )
    _assert_no_sensitive_material(definition)
    canonical_hash = _compute_canonical_hash(definition)

    existing = db.scalar(
        select(StrategyBundle).where(StrategyBundle.canonical_hash == canonical_hash)
    )
    if existing is not None:
        return existing

    values = _bundle_values(
        canonical_hash=canonical_hash,
        strategy_schema_version=strategy_schema_version,
        model_config=model_config,
        model_config_snapshot=model_config_snapshot,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=rubric_version,
        engine_version=engine_version,
        risk_review_version=risk_review_version,
        sampling_policy=sampling_policy,
        agent_plan_version=agent_plan_version,
        dimension_route_policy_id=dimension_route_policy_id,
        dimension_schema_set=dimension_schema_set,
        label_field_set=label_field_set,
        resolved_schema_contract_version=resolved_schema_contract_version,
    )
    _assert_no_sensitive_material(values)
    if db.get_bind().dialect.name == "sqlite":
        _insert_bundle_if_absent(db, values)
    else:
        try:
            with db.begin_nested():
                bundle = StrategyBundle(**values)
                db.add(bundle)
                db.flush()
        except IntegrityError as exc:
            if not _is_canonical_hash_conflict(exc):
                raise
        else:
            return bundle

    bundle = db.scalar(
        select(StrategyBundle).where(
            StrategyBundle.canonical_hash == canonical_hash
        )
    )
    if bundle is None:
        raise RuntimeError("StrategyBundle 原子创建后无法回查")
    return bundle


def get_or_create_routed_bundle(
    *,
    db: Session,
    model_config: ModelConfig,
    prompt_a: PromptVersion,
    route_policy: DimensionRoutePolicy,
    evaluation_profile_set: dict[str, Any],
    engine_version: str,
    risk_review_version: str | None,
    sampling_policy: SamplingPolicy | None,
    rubric_version: str = "routed-profile-set-v1",
    agent_plan_version: str = "controlled-agent-plan-v1",
) -> StrategyBundle:
    """Persist a v3 bundle that freezes all A-after-route candidates."""
    if not _routed_contract_is_active(db):
        raise ValueError(
            "strategy-bundle-v3 需要先应用迁移 29"
        )
    if prompt_a.stage != "A":
        raise ValueError("strategy-bundle-v3 必须冻结 A 阶段提示词")
    route_policy_snapshot = build_dimension_route_policy_snapshot(
        route_policy
    )
    _validate_routed_bundle_contract(
        route_policy_snapshot=route_policy_snapshot,
        profile_set=evaluation_profile_set,
    )
    dimension_schema_set, label_field_set = (
        _profile_sets_for_legacy_columns(evaluation_profile_set)
    )
    model_config_snapshot = _build_model_config_snapshot(model_config)
    route_policy_id = (
        f"{route_policy.policy_key}@{route_policy.version}"
    )
    definition = _build_canonical_definition(
        schema_version=ROUTED_STRATEGY_SCHEMA_VERSION,
        model_id=model_config.model_id,
        model_config_snapshot=model_config_snapshot,
        prompt_a=prompt_a,
        prompt_b=None,
        rubric_version=rubric_version,
        engine_version=engine_version,
        sampling_policy=sampling_policy,
        risk_review_version=risk_review_version,
        agent_plan_version=agent_plan_version,
        dimension_route_policy_id=route_policy_id,
        dimension_schema_set=dimension_schema_set,
        label_field_set=label_field_set,
        resolved_schema_contract_version=(
            ROUTED_SCHEMA_CONTRACT_VERSION
        ),
        dimension_route_policy_snapshot=route_policy_snapshot,
        evaluation_profile_set_snapshot=evaluation_profile_set,
    )
    _assert_strategy_identity_is_safe(
        definition,
        model_config=model_config,
        prompt_a=prompt_a,
        prompt_b=None,
        rubric_version=rubric_version,
        engine_version=engine_version,
        risk_review_version=risk_review_version,
        agent_plan_version=agent_plan_version,
    )
    _assert_no_sensitive_material(definition)
    canonical_hash = _compute_canonical_hash(definition)
    existing = db.scalar(
        select(StrategyBundle).where(
            StrategyBundle.canonical_hash == canonical_hash
        )
    )
    if existing is not None:
        return existing

    values = _bundle_values(
        canonical_hash=canonical_hash,
        strategy_schema_version=ROUTED_STRATEGY_SCHEMA_VERSION,
        model_config=model_config,
        model_config_snapshot=model_config_snapshot,
        prompt_a=prompt_a,
        prompt_b=None,
        rubric_version=rubric_version,
        engine_version=engine_version,
        risk_review_version=risk_review_version,
        sampling_policy=sampling_policy,
        agent_plan_version=agent_plan_version,
        dimension_route_policy_id=route_policy_id,
        dimension_schema_set=dimension_schema_set,
        label_field_set=label_field_set,
        resolved_schema_contract_version=(
            ROUTED_SCHEMA_CONTRACT_VERSION
        ),
        dimension_route_policy_snapshot=route_policy_snapshot,
        evaluation_profile_set_snapshot=evaluation_profile_set,
    )
    _assert_no_sensitive_material(values)
    if db.get_bind().dialect.name == "sqlite":
        _insert_bundle_if_absent(db, values)
    else:
        try:
            with db.begin_nested():
                bundle = StrategyBundle(**values)
                db.add(bundle)
                db.flush()
        except IntegrityError as exc:
            if not _is_canonical_hash_conflict(exc):
                raise
        else:
            return bundle
    bundle = db.scalar(
        select(StrategyBundle).where(
            StrategyBundle.canonical_hash == canonical_hash
        )
    )
    if bundle is None:
        raise RuntimeError("v3 StrategyBundle 原子创建后无法回查")
    return bundle


def build_strategy_snapshot(
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    sampling_policy: SamplingPolicy | None,
) -> str:
    """Build the complete, deterministic and recursively redacted run snapshot."""
    model_config_snapshot = json.loads(bundle.model_config_snapshot)
    schema_version = (
        bundle.strategy_schema_version
        or LEGACY_STRATEGY_SCHEMA_VERSION
    )
    dimension_schema_set = (
        json.loads(bundle.dimension_schema_set_snapshot)
        if bundle.dimension_schema_set_snapshot is not None
        else None
    )
    label_field_set = (
        json.loads(bundle.label_field_set_snapshot)
        if bundle.label_field_set_snapshot is not None
        else None
    )
    dimension_route_policy_snapshot = (
        json.loads(bundle.dimension_route_policy_snapshot)
        if bundle.dimension_route_policy_snapshot is not None
        else None
    )
    evaluation_profile_set_snapshot = (
        json.loads(bundle.evaluation_profile_set_snapshot)
        if bundle.evaluation_profile_set_snapshot is not None
        else None
    )
    definition = _build_canonical_definition(
        schema_version=schema_version,
        model_id=bundle.model_id,
        model_config_snapshot=model_config_snapshot,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=bundle.rubric_version,
        engine_version=bundle.engine_version,
        sampling_policy=sampling_policy,
        risk_review_version=bundle.risk_review_version,
        agent_plan_version=bundle.agent_plan_version,
        dimension_route_policy_id=bundle.dimension_route_policy_id,
        dimension_schema_set=dimension_schema_set,
        label_field_set=label_field_set,
        resolved_schema_contract_version=(
            bundle.resolved_schema_contract_version
        ),
        dimension_route_policy_snapshot=(
            dimension_route_policy_snapshot
        ),
        evaluation_profile_set_snapshot=(
            evaluation_profile_set_snapshot
        ),
    )
    if _compute_canonical_hash(definition) != bundle.canonical_hash:
        raise ValueError(
            "当前策略定义与 StrategyBundle 不一致；请创建新的不可变 Bundle"
        )

    safe_definition = _redact_secrets(definition)
    snapshot = {
        "bundle_id": bundle.id,
        "canonical_hash": bundle.canonical_hash,
        **safe_definition,
    }
    return _canonical_json(snapshot)


def build_evaluation_strategy_snapshot(
    *,
    db: Session,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    sampling_policy: SamplingPolicy | None,
    aesthetic: dict[str, Any] | None,
    dimension_schema_key: str | None = None,
    dimension_schema_version: str | None = None,
) -> str:
    """Resolve one frozen DimensionSchema for a persisted evaluation result."""
    snapshot = json.loads(
        build_strategy_snapshot(
            bundle,
            prompt_a,
            prompt_b,
            sampling_policy,
        )
    )
    if snapshot["schema_version"] == LEGACY_STRATEGY_SCHEMA_VERSION:
        return _canonical_json(snapshot)

    selected = resolve_frozen_dimension_entry(
        bundle=bundle,
        aesthetic=aesthetic,
        schema_key=dimension_schema_key,
        version=dimension_schema_version,
    )
    schema = db.scalar(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == selected["schema_key"],
            DimensionSchema.version == selected["version"],
            DimensionSchema.canonical_hash == selected["canonical_hash"],
            DimensionSchema.status.in_(("published", "retired")),
        )
    )
    if schema is None:
        raise ValueError("冻结 DimensionSchema 在注册表中不存在")
    definition = json.loads(schema.definition_json)
    if (
        selected.get("definition") != definition
        or _compute_canonical_hash(definition) != schema.canonical_hash
    ):
        raise ValueError("冻结 DimensionSchema 与注册表定义不一致")

    prompt_b_hash = (
        _compute_canonical_hash(_build_prompt_definition(prompt_b))
        if prompt_b is not None
        else None
    )
    scoring_profile = (
        aesthetic.get("scoring_profile")
        if isinstance(aesthetic, dict)
        else None
    )
    explicit_dimension_contract = (
        dimension_schema_key is not None
        and dimension_schema_version is not None
    )
    route_decision = {
        "policy_id": bundle.dimension_route_policy_id,
        "family_key": schema.family_key,
        "dimension_schema_id": schema.id,
        "dimension_schema_key": schema.schema_key,
        "dimension_schema_version": schema.version,
        "dimension_schema_hash": schema.canonical_hash,
        "input": {"scoring_profile": scoring_profile},
        "reason": (
            "explicit_category_contract_override"
            if explicit_dimension_contract
            else "scoring_profile_matches_active_v1_3"
            if schema.version == ACTIVE_V13_VERSION
            else "historical_default_compatibility"
        ),
        "needs_review": False,
    }
    resolution = {
        "resolved_dimension_schema_id": schema.id,
        "resolved_dimension_schema_key": schema.schema_key,
        "resolved_dimension_schema_version": schema.version,
        "resolved_dimension_schema_hash": schema.canonical_hash,
        "resolved_dimensions_snapshot": definition,
        "resolved_prompt_b_hash": prompt_b_hash,
        "route_decision_snapshot": route_decision,
    }
    snapshot.update(resolution)
    snapshot["resolved_snapshot_hash"] = _compute_canonical_hash(
        resolution
    )
    _assert_no_sensitive_material(snapshot)
    return _canonical_json(snapshot)


def resolve_frozen_dimension_entry(
    *,
    bundle: StrategyBundle,
    aesthetic: dict[str, Any] | None,
    schema_key: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Resolve one definition only from the bundle's immutable candidate set."""
    schema_version = (
        bundle.strategy_schema_version
        or LEGACY_STRATEGY_SCHEMA_VERSION
    )
    if schema_version == LEGACY_STRATEGY_SCHEMA_VERSION:
        raise ValueError(
            "strategy-bundle-v1 没有冻结 DimensionSchema"
        )
    if schema_version != STRATEGY_SCHEMA_VERSION:
        raise ValueError("不支持的 StrategyBundle 快照版本")
    try:
        dimension_set = json.loads(
            bundle.dimension_schema_set_snapshot or ""
        )
    except json.JSONDecodeError as exc:
        raise ValueError("StrategyBundle 冻结维度集合已损坏") from exc
    entries = (
        dimension_set.get("schemas")
        if isinstance(dimension_set, dict)
        else None
    )
    if not isinstance(entries, list):
        raise ValueError("StrategyBundle 缺少冻结维度集合")

    if bool(schema_key) != bool(version):
        raise ValueError("冻结维度身份必须同时包含 schema_key 和 version")
    scoring_profile = (
        aesthetic.get("scoring_profile")
        if isinstance(aesthetic, dict)
        else None
    )
    resolved_schema_key = schema_key or SPACE_SCHEMA_KEY
    resolved_version = version or (
        ACTIVE_V13_VERSION
        if scoring_profile == "space_aesthetic_v1.3"
        else HISTORICAL_DEFAULT_VERSION
    )
    selected = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("schema_key") == resolved_schema_key
            and entry.get("version") == resolved_version
        ),
        None,
    )
    if selected is None:
        raise ValueError("冻结维度集合缺少所需空间兼容修订")
    definition = selected.get("definition")
    if (
        not isinstance(definition, dict)
        or _compute_canonical_hash(definition)
        != selected.get("canonical_hash")
    ):
        raise ValueError("冻结 DimensionSchema 规范哈希无法复算")
    return deepcopy(selected)


def safe_strategy_snapshot_payload(
    snapshot: str | dict[str, Any],
) -> dict[str, Any]:
    """Return a recursively redacted strategy snapshot safe for API responses."""
    if isinstance(snapshot, str):
        try:
            payload = json.loads(snapshot)
        except json.JSONDecodeError as exc:
            raise ValueError("StrategyBundle 快照不是有效 JSON") from exc
    else:
        payload = snapshot
    if not isinstance(payload, dict):
        raise ValueError("StrategyBundle 快照必须是 JSON 对象")
    return _redact_secrets(payload)
