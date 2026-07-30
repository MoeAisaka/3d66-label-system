"""Immutable, secret-safe strategy definitions for evaluation runs."""
from __future__ import annotations

import hashlib
import json
import re
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
    DimensionSchema,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
)


REDACTED = "[REDACTED]"
LEGACY_STRATEGY_SCHEMA_VERSION = "strategy-bundle-v1"
STRATEGY_SCHEMA_VERSION = "strategy-bundle-v2"
DIMENSION_ROUTE_POLICY_ID = "space-static-by-scoring-profile-v1"
RESOLVED_SCHEMA_CONTRACT_VERSION = "dimension-resolution-v1"
DIMENSION_SCHEMA_SET_FORMAT_VERSION = "dimension-schema-set-v1"
LABEL_FIELD_SET_FORMAT_VERSION = "label-field-set-snapshot-v1"

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


def _build_prompt_definition(prompt: PromptVersion) -> dict[str, Any]:
    """Capture prompt identity and exact persisted contents."""
    if prompt.id is None:
        raise ValueError("PromptVersion 必须先持久化，才能创建 StrategyBundle")
    definition = {
        "id": prompt.id,
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
    if schema_version == STRATEGY_SCHEMA_VERSION:
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

    dimension_set = snapshot.get("dimension_schema_set")
    entries = (
        dimension_set.get("schemas")
        if isinstance(dimension_set, dict)
        else None
    )
    if not isinstance(entries, list):
        raise ValueError("StrategyBundle 缺少冻结维度集合")

    scoring_profile = (
        aesthetic.get("scoring_profile")
        if isinstance(aesthetic, dict)
        else None
    )
    resolved_version = (
        ACTIVE_V13_VERSION
        if scoring_profile == "space_aesthetic_v1.3"
        else HISTORICAL_DEFAULT_VERSION
    )
    selected = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("schema_key") == SPACE_SCHEMA_KEY
            and entry.get("version") == resolved_version
        ),
        None,
    )
    if selected is None:
        raise ValueError("冻结维度集合缺少所需空间兼容修订")

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
    route_decision = {
        "policy_id": bundle.dimension_route_policy_id,
        "family_key": schema.family_key,
        "dimension_schema_id": schema.id,
        "dimension_schema_key": schema.schema_key,
        "dimension_schema_version": schema.version,
        "dimension_schema_hash": schema.canonical_hash,
        "input": {"scoring_profile": scoring_profile},
        "reason": (
            "scoring_profile_matches_active_v1_3"
            if resolved_version == ACTIVE_V13_VERSION
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
