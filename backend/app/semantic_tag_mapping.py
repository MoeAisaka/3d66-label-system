from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .label_governance import SemanticExecutionRoute


@dataclass(frozen=True)
class SemanticCandidate:
    value: str
    locale: str
    rank: int
    weight: float | None
    evidence_ref: str


@dataclass(frozen=True)
class SemanticCandidateBundle:
    field_key: str
    values: tuple[SemanticCandidate, ...]


@dataclass(frozen=True)
class SemanticMappedValue:
    entity_id: str
    localized_names: Mapping[str, str]
    rank: int
    weight: float | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class SemanticMappingResult:
    field_key: str
    values: tuple[SemanticMappedValue, ...]
    unmapped_values: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    conflicts: tuple[str, ...]
    field_status: str


def candidate(
    value: str,
    *,
    locale: str = "zh",
    rank: int = 1,
    weight: float | None = None,
    evidence_ref: str | None = None,
) -> SemanticCandidate:
    return SemanticCandidate(
        value=value,
        locale=locale,
        rank=rank,
        weight=weight,
        evidence_ref=evidence_ref or f"evidence:{value}",
    )


def candidate_bundle(*, field_key: str, values: list[SemanticCandidate]) -> SemanticCandidateBundle:
    return SemanticCandidateBundle(field_key=field_key, values=tuple(values))


def _candidate_from_payload(
    field_key: str,
    item: Any,
    *,
    index: int,
    locale: str,
    evidence_prefix: str,
) -> SemanticCandidate:
    if isinstance(item, str):
        value = item.strip()
        payload: Mapping[str, Any] = {}
    elif isinstance(item, Mapping):
        value = str(item.get("value") or item.get("name") or "").strip()
        payload = item
    else:
        raise ValueError(f"{field_key} 的候选值必须是字符串或对象")
    if not value:
        raise ValueError(f"{field_key} 的候选值不能为空")
    raw_rank = payload.get("rank", index + 1)
    if not isinstance(raw_rank, int) or raw_rank < 1:
        raise ValueError(f"{field_key}.rank 必须是正整数")
    raw_weight = payload.get("weight")
    if raw_weight is not None:
        if not isinstance(raw_weight, (int, float)) or isinstance(raw_weight, bool) or not 0 <= float(raw_weight) <= 1:
            raise ValueError(f"{field_key}.weight 必须在 0 至 1 之间")
        weight = float(raw_weight)
    else:
        weight = None
    evidence = payload.get("evidence_ref")
    if not evidence:
        evidence_items = payload.get("evidence")
        if isinstance(evidence_items, list) and evidence_items:
            evidence = str(evidence_items[0]).strip()
        else:
            evidence = f"{evidence_prefix}#semantic.{field_key}.{index}"
    return SemanticCandidate(
        value=value,
        locale=str(payload.get("locale") or locale),
        rank=raw_rank,
        weight=weight,
        evidence_ref=str(evidence),
    )


def normalize_semantic_candidates(
    *,
    route: SemanticExecutionRoute,
    provider_payload: Mapping[str, Any],
    evidence_prefix: str,
) -> dict[str, SemanticCandidateBundle]:
    semantic = provider_payload.get("semantic")
    if semantic is None:
        semantic = provider_payload.get("semantic_candidates")
    if not isinstance(semantic, Mapping):
        return {}
    bundles: dict[str, SemanticCandidateBundle] = {}
    for field_key, raw_values in semantic.items():
        if not isinstance(field_key, str) or field_key not in route.fields:
            continue
        if isinstance(raw_values, str):
            raise ValueError(f"{field_key} 必须是数组或对象")
        items = [raw_values] if isinstance(raw_values, Mapping) else raw_values
        if not isinstance(items, list):
            raise ValueError(f"{field_key} 必须是数组或对象")
        values = tuple(
            _candidate_from_payload(
                field_key,
                item,
                index=index,
                locale=route.locale,
                evidence_prefix=evidence_prefix,
            )
            for index, item in enumerate(items)
        )
        bundles[field_key] = SemanticCandidateBundle(field_key=field_key, values=values)
    return bundles


def _registry_values(mapping_registry: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    values = mapping_registry.get("values")
    if not isinstance(values, Mapping):
        raise ValueError("实体映射 registry.values 必须是对象")
    return values


def map_standard_entities(
    *,
    bundle: SemanticCandidateBundle,
    mapping_registry: Mapping[str, Any],
    normalization_version: str,
    mapping_version: str,
) -> SemanticMappingResult:
    del normalization_version, mapping_version
    if mapping_registry.get("field_key") not in {None, bundle.field_key}:
        raise ValueError("实体映射 field_key 与候选字段不一致")
    values = _registry_values(mapping_registry)
    alias_index: dict[str, Mapping[str, Any]] = {}
    for canonical, definition in values.items():
        if not isinstance(definition, Mapping):
            continue
        alias_index[str(canonical).casefold()] = definition
        for alias in definition.get("aliases") or []:
            alias_index[str(alias).casefold()] = definition
    mapped: dict[str, SemanticMappedValue] = {}
    unmapped: list[str] = []
    evidence_refs: list[str] = []
    conflicts: list[str] = []
    for item in bundle.values:
        evidence_refs.append(item.evidence_ref)
        definition = alias_index.get(item.value.casefold())
        if definition is None:
            unmapped.append(item.value)
            continue
        entity_id = str(definition.get("entity_id") or "").strip()
        names = definition.get("names")
        if not entity_id or not isinstance(names, Mapping):
            conflicts.append(f"{item.value}:mapping_definition_invalid")
            continue
        existing = mapped.get(entity_id)
        if existing is None:
            mapped[entity_id] = SemanticMappedValue(
                entity_id=entity_id,
                localized_names={str(key): str(value) for key, value in names.items()},
                rank=item.rank,
                weight=item.weight,
                evidence_refs=(item.evidence_ref,),
            )
            continue
        merged_weight = None
        if existing.weight is not None or item.weight is not None:
            weights = [
                float(weight)
                for weight in (existing.weight, item.weight)
                if weight is not None
            ]
            # Relative-importance levels are not additive. Duplicate aliases
            # must not inflate the level; keep the strongest observed evidence.
            merged_weight = max(weights)
        mapped[entity_id] = SemanticMappedValue(
            entity_id=existing.entity_id,
            localized_names=existing.localized_names,
            rank=min(existing.rank, item.rank),
            weight=merged_weight,
            evidence_refs=tuple(dict.fromkeys((*existing.evidence_refs, item.evidence_ref))),
        )
    return SemanticMappingResult(
        field_key=bundle.field_key,
        values=tuple(sorted(mapped.values(), key=lambda item: (item.rank, item.entity_id))),
        unmapped_values=tuple(unmapped),
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        conflicts=tuple(conflicts),
        field_status="needs_review" if unmapped or conflicts else "optional",
    )


def candidate_payload(value: SemanticCandidate) -> dict[str, Any]:
    return {
        "value": value.value,
        "locale": value.locale,
        "rank": value.rank,
        "weight": value.weight,
        "evidence_ref": value.evidence_ref,
    }
