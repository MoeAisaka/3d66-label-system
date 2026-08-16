from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .asset_identity import (
    AssetIdentityError,
    IdentityVerificationEvidence,
    resolve_three_d_su_identity,
)
from .audit import append_audit_event, canonical_json
from .mechanism_profiles import MechanismProfileError, validate_mechanism_artifacts
from .models import (
    Asset,
    AssetVersion,
    CategoryEvaluationV3Config,
    ContentIngressEvent,
    ContentRecord,
    EvaluationCategoryProfile,
    EvaluationResult,
    HumanReview,
    LabelOutboxEvent,
    LabelRelease,
    MaterialPackage,
    MaterialPackageItem,
    PublishedLabel,
    ReviewPanel,
    SemanticTagFact,
    SourceIdentityVerification,
    TagDemandContract,
)
from .semantic_tag_contracts import (
    PLATFORM_SEMANTIC_CONTRACT_KEY,
    SemanticTagContractError,
    validate_semantic_field_result,
    validate_tag_demand_contract,
)


SCHEMA_VERSION = "content-ingress-v1"
INGRESS_SCHEMA_VERSIONS = {SCHEMA_VERSION, "content-ingress-v2"}
LABEL_SCHEMA_VERSION = "published-label-v1"
SEMANTIC_LABEL_SCHEMA_VERSION = "published-label-v2"
INGRESS_TYPES = {"content.created", "content.updated", "content.deleted"}


class LabelIntegrationConflict(ValueError):
    pass


class SemanticTagRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticExecutionRoute:
    contract_id: int
    contract_version: int
    contract_hash: str
    site_scope: str
    asset_scope: str
    locale: str
    category_key: str
    prompt_variant: str
    prompt_version: str
    model_version: str
    fields: Mapping[str, str]
    asset_version_id: int


def _active_platform_contract(db: Session) -> TagDemandContract | None:
    return db.scalar(
        select(TagDemandContract)
        .where(
            TagDemandContract.contract_key == PLATFORM_SEMANTIC_CONTRACT_KEY,
            TagDemandContract.status == "active",
        )
        .order_by(TagDemandContract.version.desc(), TagDemandContract.id.desc())
        .limit(1)
    )


def resolve_semantic_execution_route(
    db: Session,
    *,
    content_record: ContentRecord,
    asset_version: AssetVersion | None,
    site_scope: str,
    asset_scope: str,
    locale: str,
    prompt_variant: str,
    prompt_version: str,
    model_version: str,
) -> SemanticExecutionRoute:
    if asset_version is None:
        raise SemanticTagRoutingError("素材版本缺失，不能创建语义标注路由")
    if content_record.asset_id is None or asset_version.asset_id != content_record.asset_id:
        raise SemanticTagRoutingError("素材版本与内容记录不一致")
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == content_record.category_key,
            EvaluationCategoryProfile.status == "active",
        )
    )
    if profile is None:
        raise SemanticTagRoutingError("类目 profile 未启用")
    contract = _active_platform_contract(db)
    if contract is None:
        raise SemanticTagRoutingError("平台语义标签需求合同未启用")
    try:
        definition = validate_tag_demand_contract(json.loads(contract.definition_json))
    except (json.JSONDecodeError, ValueError) as exc:
        raise SemanticTagRoutingError(f"平台语义标签需求合同无效：{exc}") from None
    matrix = definition.category_applicability.get(content_record.category_key)
    if matrix is None:
        raise SemanticTagRoutingError("类目缺少语义字段适用性矩阵")
    variants = [
        variant
        for variant in definition.execution_variants
        if (
            variant.site_scope == site_scope
            and variant.asset_scope == asset_scope
            and variant.locale == locale
            and variant.category_key == content_record.category_key
            and variant.prompt_variant == prompt_variant
            and variant.prompt_version == prompt_version
            and variant.model_version == model_version
        )
    ]
    if not variants:
        raise SemanticTagRoutingError("请求执行变体未在当前语义合同中声明")
    return SemanticExecutionRoute(
        contract_id=contract.id,
        contract_version=contract.version,
        contract_hash=contract.contract_hash,
        site_scope=site_scope,
        asset_scope=asset_scope,
        locale=locale,
        category_key=content_record.category_key,
        prompt_variant=prompt_variant,
        prompt_version=prompt_version,
        model_version=model_version,
        fields=MappingProxyType(dict(matrix)),
        asset_version_id=asset_version.id,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def approve_semantic_facts(
    db: Session,
    *,
    evaluation_id: int,
    actor: str,
) -> list[SemanticTagFact]:
    """Promote evidence candidates after completed human truth, append-only."""
    evaluation = db.get(EvaluationResult, evaluation_id)
    if evaluation is None:
        raise ValueError("评测结果不存在")
    panel = db.scalar(select(ReviewPanel).where(ReviewPanel.evaluation_id == evaluation_id))
    if panel is None or panel.status != "completed" or panel.final_review_id is None:
        raise ValueError("人工真值未完成，不能批准语义事实")
    final_review = db.get(HumanReview, panel.final_review_id)
    if final_review is None or final_review.decision not in {"approved", "corrected"}:
        raise ValueError("人工真值不是可批准状态")
    contract = _active_platform_contract(db)
    if contract is None:
        raise ValueError("平台语义标签需求合同未启用")
    try:
        definition = validate_tag_demand_contract(json.loads(contract.definition_json))
    except (json.JSONDecodeError, SemanticTagContractError) as exc:
        raise ValueError(f"平台语义标签需求合同无效：{exc}") from None
    precheck = json.loads(evaluation.precheck_json or "{}")
    route = precheck.get("semantic_route")
    if not isinstance(route, Mapping):
        raise ValueError("语义执行路由未冻结，不能批准语义事实")
    if (
        route.get("contract_id") != contract.id
        or route.get("contract_version") != contract.version
        or route.get("contract_hash") != contract.contract_hash
    ):
        raise ValueError("语义标签合同已漂移，不能将旧候选归属到当前合同")
    if route.get("category_key") != evaluation.job.category_key:
        raise ValueError("语义执行路由类目与评测类目不一致")
    route_asset_version_id = route.get("asset_version_id")
    if not isinstance(route_asset_version_id, int):
        raise ValueError("语义执行路由缺少冻结素材版本")
    asset_version = db.get(AssetVersion, route_asset_version_id)
    if asset_version is None or asset_version.asset_id != evaluation.asset_id:
        raise ValueError("冻结素材版本与评测素材不一致")
    matrix = definition.category_applicability.get(evaluation.job.category_key)
    if matrix is None:
        raise ValueError("当前类目缺少语义字段适用性矩阵")
    final_truth = json.loads(panel.final_truth_json or "{}")
    semantic_truth = final_truth.get("semantic")
    semantic_truth = semantic_truth if isinstance(semantic_truth, Mapping) else {}
    candidates = db.scalars(
        select(SemanticTagFact)
        .where(
            SemanticTagFact.source_evaluation_id == evaluation_id,
            SemanticTagFact.status == "candidate",
        )
        .order_by(SemanticTagFact.field_key.asc(), SemanticTagFact.fact_version.asc(), SemanticTagFact.id.asc())
    ).all()
    if not candidates:
        return []
    approved: list[SemanticTagFact] = []
    for candidate in candidates:
        if candidate.contract_id != contract.id:
            raise ValueError("候选语义事实合同与冻结执行合同不一致")
        if candidate.asset_version_id != asset_version.id:
            raise ValueError("候选语义事实素材版本与冻结执行版本不一致")
        field_definition = definition.semantic_schema.fields.get(candidate.field_key)
        field_status = matrix.get(candidate.field_key)
        if field_definition is None or field_status is None:
            raise ValueError(f"字段 {candidate.field_key} 未在当前合同中声明")
        existing_for_review = db.scalar(
            select(SemanticTagFact).where(
                SemanticTagFact.source_evaluation_id == evaluation_id,
                SemanticTagFact.source_review_id == final_review.id,
                SemanticTagFact.asset_version_id == candidate.asset_version_id,
                SemanticTagFact.field_key == candidate.field_key,
                SemanticTagFact.contract_id == contract.id,
                SemanticTagFact.status == "approved",
            )
        )
        if existing_for_review is not None:
            approved.append(existing_for_review)
            continue
        existing_approved = db.scalar(
            select(SemanticTagFact)
            .where(
                SemanticTagFact.asset_version_id == candidate.asset_version_id,
                SemanticTagFact.field_key == candidate.field_key,
                SemanticTagFact.status == "approved",
            )
            .order_by(SemanticTagFact.fact_version.desc(), SemanticTagFact.id.desc())
        )
        fact_version = (existing_approved.fact_version + 1) if existing_approved else candidate.fact_version + 1
        values = json.loads(candidate.values_json)
        evidence = json.loads(candidate.evidence_json)
        human_field = semantic_truth.get(candidate.field_key)
        if isinstance(human_field, Mapping):
            field_status = str(human_field.get("status") or field_status)
            if "values" in human_field:
                values = human_field.get("values")
        if not isinstance(values, list):
            raise ValueError(f"字段 {candidate.field_key} 的 values 必须是数组")
        canonical_values: list[dict[str, Any]] = []
        validation_values: list[dict[str, Any]] = []
        for index, raw_value in enumerate(values):
            if not isinstance(raw_value, Mapping):
                raise ValueError(f"字段 {candidate.field_key} 的值必须是对象")
            item = dict(raw_value)
            item.setdefault("value", str(item.get("entity_id") or "").strip())
            item.setdefault("locale", str(route.get("locale") or "zh"))
            item.setdefault("rank", index + 1)
            item.setdefault("source", "mixed")
            item.setdefault(
                "evidence_ref",
                str(evidence[index] if index < len(evidence) else f"evaluation:{evaluation_id}#semantic.{candidate.field_key}.{index}"),
            )
            item.setdefault("model_version", str(route.get("model_version") or "") or None)
            item.setdefault("prompt_version", str(route.get("prompt_version") or "") or None)
            item["normalization_version"] = candidate.normalization_version
            item["mapping_version"] = candidate.mapping_version
            item["review_status"] = "approved"
            canonical_values.append(item)
            validation_values.append(
                {
                    key: item.get(key)
                    for key in (
                        "value",
                        "entity_id",
                        "locale",
                        "rank",
                        "weight",
                        "source",
                        "evidence_ref",
                        "model_version",
                        "prompt_version",
                        "normalization_version",
                        "mapping_version",
                        "review_status",
                    )
                    if key in item
                }
            )
        try:
            validate_semantic_field_result(
                {"status": field_status, "values": validation_values}
            )
        except SemanticTagContractError as exc:
            raise ValueError(
                f"字段 {candidate.field_key} 不符合语义合同：{exc}"
            ) from None
        if field_definition.cardinality == "single" and len(canonical_values) > 1:
            raise ValueError(f"字段 {candidate.field_key} 为 single，最多只能有一个值")
        if len(canonical_values) > field_definition.max_values:
            raise ValueError(
                f"字段 {candidate.field_key} 的值数量超过合同 max_values"
            )
        values = canonical_values
        payload = {
            "asset_version_id": candidate.asset_version_id,
            "field_key": candidate.field_key,
            "fact_version": fact_version,
            "field_status": field_status,
            "values": values,
            "evidence": evidence,
            "source_evaluation_id": evaluation_id,
            "source_review_id": final_review.id,
            "contract_id": contract.id,
            "normalization_version": candidate.normalization_version,
            "mapping_version": candidate.mapping_version,
            "status": "approved",
        }
        row = SemanticTagFact(
            asset_version_id=candidate.asset_version_id,
            field_key=candidate.field_key,
            fact_version=fact_version,
            field_status=field_status,
            supersedes_fact_id=existing_approved.id if existing_approved else candidate.id,
            values_json=canonical_json(values),
            evidence_json=canonical_json(evidence),
            source_evaluation_id=evaluation_id,
            source_review_id=final_review.id,
            contract_id=contract.id,
            normalization_version=candidate.normalization_version,
            mapping_version=candidate.mapping_version,
            status="approved",
            payload_hash=_payload_hash(payload),
        )
        db.add(row)
        approved.append(row)
    append_audit_event(
        db,
        category="semantic_tag_fact",
        action="approved",
        subject_type="evaluation_result",
        subject_id=str(evaluation_id),
        actor=actor,
        payload={"fact_count": len(approved), "contract_id": contract.id, "review_id": final_review.id},
        event_key=f"semantic-tag-fact:approved:{evaluation_id}:{final_review.id}",
    )
    db.flush()
    return approved


def ingest_content_event(
    db: Session,
    *,
    event_id: str,
    schema_version: str,
    event_type: str,
    source_system: str,
    occurred_at: datetime,
    payload: dict[str, Any],
    received_by: str,
) -> tuple[ContentIngressEvent, ContentRecord, bool]:
    if schema_version not in INGRESS_SCHEMA_VERSIONS:
        raise ValueError("不支持的内容接入 schema_version")
    if event_type not in INGRESS_TYPES:
        raise ValueError("不支持的内容接入 event_type")
    content_id = str(payload.get("content_id", "")).strip()
    category_key = str(payload.get("category_key", "")).strip()
    source_version = str(payload.get("content_version", "")).strip()
    if not content_id or len(content_id) > 160:
        raise ValueError("payload.content_id 必须填写且长度不超过 160")
    if not category_key or len(category_key) > 40:
        raise ValueError("payload.category_key 必须填写且长度不超过 40")
    if not source_version or len(source_version) > 120:
        raise ValueError("payload.content_version 必须填写且长度不超过 120")
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == category_key,
            EvaluationCategoryProfile.status == "active",
        )
    )
    if profile is None:
        raise ValueError("payload.category_key 不是启用中的评测类目")
    asset_id = payload.get("asset_id")
    asset: Asset | None = None
    if asset_id is not None:
        if not isinstance(asset_id, int) or asset_id < 1:
            raise ValueError("payload.asset_id 必须为正整数")
        asset = db.get(Asset, asset_id)
        if asset is None:
            raise ValueError("payload.asset_id 对应素材不存在")
        if asset.category_key != category_key:
            raise ValueError("payload.asset_id 与 category_key 不一致")

    resolved_identity = None
    identity_verification: SourceIdentityVerification | None = None
    if schema_version == "content-ingress-v2":
        if category_key != "model_3d_su":
            raise ValueError("content-ingress-v2 当前只支持 model_3d_su 类目")
        identity_verification = db.scalar(
            select(SourceIdentityVerification).where(
                SourceIdentityVerification.contract_key
                == PLATFORM_SEMANTIC_CONTRACT_KEY,
                SourceIdentityVerification.source_system == source_system,
                SourceIdentityVerification.status == "approved",
            )
        )
        verification_evidence = None
        if identity_verification is not None:
            try:
                key_fields = tuple(json.loads(identity_verification.key_fields_json))
                verification_evidence = IdentityVerificationEvidence(
                    source_system=identity_verification.source_system,
                    key_fields=key_fields,
                    status=identity_verification.result,
                    evidence_hash=identity_verification.probe_hash,
                )
            except (TypeError, ValueError) as exc:
                raise LabelIntegrationConflict(
                    f"已批准身份签认证据无效：{exc}"
                ) from None
        try:
            resolved_identity = resolve_three_d_su_identity(
                source_system=source_system,
                payload=payload,
                verification=verification_evidence,
            )
        except AssetIdentityError as exc:
            if identity_verification is not None:
                raise LabelIntegrationConflict(str(exc)) from None
            raise ValueError(str(exc)) from None

    payload_hash = _payload_hash(payload)
    existing_event = db.scalar(
        select(ContentIngressEvent).where(ContentIngressEvent.event_id == event_id)
    )
    if existing_event is not None:
        if (
            existing_event.payload_hash != payload_hash
            or existing_event.schema_version != schema_version
            or existing_event.event_type != event_type
            or existing_event.source_system != source_system
            or _aware(existing_event.occurred_at) != _aware(occurred_at)
            or (
                resolved_identity is not None
                and existing_event.identity_hash != resolved_identity.identity_hash
            )
        ):
            raise LabelIntegrationConflict("同一 event_id 的内容接入载荷不一致")
        record = db.get(ContentRecord, existing_event.content_record_id)
        if record is None:
            raise RuntimeError("内容接入事件缺少本地内容投影")
        return existing_event, record, True

    record = db.scalar(
        select(ContentRecord).where(
            ContentRecord.source_system == source_system,
            ContentRecord.source_content_id == content_id,
        )
    )
    incoming_time = _aware(occurred_at)
    status = "deleted" if event_type == "content.deleted" else (
        "ready" if asset is not None or (record is not None and record.asset_id is not None)
        else "awaiting_material"
    )
    event_status = status if status == "awaiting_material" else "applied"
    if record is None:
        identity_values = (
            {
                "content_key": resolved_identity.content_key,
                "source_res_type": resolved_identity.res_type,
                "source_ll_id": resolved_identity.ll_id,
                "source_res_id": resolved_identity.res_id,
                "identity_status": resolved_identity.identity_status,
                "identity_hash": resolved_identity.identity_hash,
                "identity_verification_id": (
                    identity_verification.id
                    if identity_verification is not None
                    else None
                ),
            }
            if resolved_identity is not None
            else {}
        )
        record = ContentRecord(
            source_system=source_system,
            source_content_id=content_id,
            category_key=category_key,
            source_version=source_version,
            source_occurred_at=occurred_at,
            asset_id=asset.id if asset else None,
            status=status,
            **identity_values,
        )
        db.add(record)
        db.flush()
    elif incoming_time <= _aware(record.source_occurred_at):
        event_status = "stale"
    else:
        if resolved_identity is not None:
            if (
                record.identity_hash is not None
                and record.identity_hash != resolved_identity.identity_hash
            ):
                raise LabelIntegrationConflict("同一内容记录的源身份发生漂移")
            if record.identity_status == "pending_verification":
                record.content_key = resolved_identity.content_key
                record.source_res_type = resolved_identity.res_type
                record.source_ll_id = resolved_identity.ll_id
                record.source_res_id = resolved_identity.res_id
                record.identity_status = resolved_identity.identity_status
                record.identity_hash = resolved_identity.identity_hash
                record.identity_verification_id = (
                    identity_verification.id
                    if identity_verification is not None
                    else None
                )
        record.category_key = category_key
        record.source_version = source_version
        record.source_occurred_at = occurred_at
        if asset is not None:
            record.asset_id = asset.id
        record.status = status
        record.updated_at = datetime.now(timezone.utc)

    identity_snapshot = (
        {
            **resolved_identity.model_dump(mode="json"),
            "identity_verification_id": (
                identity_verification.id
                if identity_verification is not None
                else None
            ),
            "verification_evidence_hash": (
                identity_verification.probe_hash
                if identity_verification is not None
                else None
            ),
        }
        if resolved_identity is not None
        else None
    )
    event = ContentIngressEvent(
        event_id=event_id,
        schema_version=schema_version,
        event_type=event_type,
        source_system=source_system,
        occurred_at=occurred_at,
        payload_hash=payload_hash,
        payload_json=canonical_json(payload),
        identity_snapshot_json=(
            canonical_json(identity_snapshot) if identity_snapshot is not None else None
        ),
        identity_hash=(
            resolved_identity.identity_hash
            if resolved_identity is not None
            else None
        ),
        identity_verification_id=(
            identity_verification.id
            if identity_verification is not None
            else None
        ),
        content_record_id=record.id,
        status=event_status,
        received_by=received_by,
    )
    db.add(event)
    db.flush()
    append_audit_event(
        db,
        category="content_ingress",
        action=event_status,
        subject_type="content_ingress_event",
        subject_id=event_id,
        actor=received_by,
        payload={
            "content_key": (
                record.content_key
                if schema_version == "content-ingress-v2"
                else f"{source_system}:{content_id}"
            ),
            "category_key": category_key,
            "event_type": event_type,
            "source_version": source_version,
            "status": event_status,
            "identity_status": record.identity_status,
        },
        event_key=f"content-ingress:{event_id}",
    )
    return event, record, False


def route_content_event_to_incremental_package(
    db: Session,
    *,
    event: ContentIngressEvent,
    record: ContentRecord,
    duplicate: bool,
    actor: str,
) -> tuple[MaterialPackage | None, bool, str]:
    """Build or reuse one local incremental package without queueing a job."""
    if event.event_type == "content.deleted" or event.status == "stale":
        return None, False, "ignored"
    if record.status != "ready" or record.asset_id is None:
        return None, False, "awaiting_material"

    mechanism = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == record.category_key,
            CategoryEvaluationV3Config.status == "active",
        )
    )
    try:
        if mechanism is None:
            raise MechanismProfileError(
                "active_profile_missing",
                "类目缺少现役评测机制",
            )
        validate_mechanism_artifacts(
            json.loads(mechanism.contract_json or "{}"),
            json.loads(mechanism.classification_map_json or "{}"),
            json.loads(mechanism.subcategory_dimensions_json or "{}"),
        )
    except (MechanismProfileError, TypeError, json.JSONDecodeError, ValueError):
        append_audit_event(
            db,
            category="content_ingress",
            action="blocked_profile",
            subject_type="content_ingress_event",
            subject_id=event.event_id,
            actor=actor,
            payload={
                "content_record_id": record.id,
                "category_key": record.category_key,
                "workflow_kind": "incremental",
            },
            event_key=f"content-ingress:{event.event_id}:blocked-profile",
        )
        return None, False, "blocked_profile"

    package_key = "ingress:" + hashlib.sha256(
        event.event_id.encode("utf-8")
    ).hexdigest()
    package = db.scalar(
        select(MaterialPackage).where(MaterialPackage.package_key == package_key)
    )
    if package is not None:
        item = db.scalar(
            select(MaterialPackageItem).where(
                MaterialPackageItem.package_id == package.id,
                MaterialPackageItem.asset_id == record.asset_id,
            )
        )
        if item is None:
            raise RuntimeError("内容接入素材包缺少冻结素材项")
        if duplicate:
            append_audit_event(
                db,
                category="content_ingress",
                action="duplicate_reused",
                subject_type="material_package",
                subject_id=package.id,
                actor=actor,
                payload={
                    "event_id": event.event_id,
                    "category_key": record.category_key,
                    "workflow_kind": "incremental",
                },
                event_key=f"content-ingress:{event.event_id}:duplicate-reused",
            )
        return package, False, "packaged"

    asset = db.get(Asset, record.asset_id)
    if asset is None or asset.status == "deleted":
        return None, False, "awaiting_material"
    if asset.category_key != record.category_key:
        raise ValueError("内容投影绑定素材与类目不一致")

    package = MaterialPackage(
        package_key=package_key,
        name=f"增量接入 · {record.category_key} · {event.event_id}",
        source="production_import",
        category_key=record.category_key,
        created_by=actor,
    )
    db.add(package)
    db.flush()
    db.add(
        MaterialPackageItem(
            package_id=package.id,
            asset_id=asset.id,
            original_name=asset.original_name,
            duplicate=duplicate,
            position=1,
        )
    )
    append_audit_event(
        db,
        category="content_ingress",
        action="incremental_package_created",
        subject_type="material_package",
        subject_id=package.id,
        actor=actor,
        payload={
            "event_id": event.event_id,
            "content_record_id": record.id,
            "asset_id": asset.id,
            "category_key": record.category_key,
            "workflow_kind": "incremental",
        },
        event_key=f"content-ingress:{event.event_id}:incremental-package",
    )
    return package, True, "packaged"


def _content_key(db: Session, evaluation: EvaluationResult, requested: str | None) -> str:
    if requested:
        value = requested.strip()
        if ":" not in value or len(value) > 320:
            raise ValueError("content_key 必须使用 source_system:content_id 格式")
        if value == f"asset:{evaluation.asset_id}":
            return value
        if value.startswith("asset:"):
            raise ValueError("asset content_key 与待发布评测的素材不一致")
        source_system, source_content_id = value.split(":", 1)
        record = db.scalar(
            select(ContentRecord).where(
                ContentRecord.source_system == source_system,
                ContentRecord.source_content_id == source_content_id,
            )
        )
        if record is None or record.status != "ready":
            raise ValueError("content_key 尚未接入完成本地素材绑定")
        if record.asset_id != evaluation.asset_id or record.category_key != evaluation.job.category_key:
            raise ValueError("content_key 与待发布评测的素材或类目不一致")
        return value
    record = db.scalar(select(ContentRecord).where(ContentRecord.asset_id == evaluation.asset_id))
    return (
        f"{record.source_system}:{record.source_content_id}"
        if record is not None
        else f"asset:{evaluation.asset_id}"
    )


def _approved_semantic_payload(
    db: Session,
    *,
    evaluation: EvaluationResult,
) -> tuple[AssetVersion, dict[str, Any]] | None:
    asset_version = db.scalar(
        select(AssetVersion)
        .where(AssetVersion.asset_id == evaluation.asset_id)
        .order_by(AssetVersion.version.desc(), AssetVersion.id.desc())
        .limit(1)
    )
    if asset_version is None:
        return None
    facts = db.scalars(
        select(SemanticTagFact)
        .where(
            SemanticTagFact.asset_version_id == asset_version.id,
            SemanticTagFact.source_evaluation_id == evaluation.id,
            SemanticTagFact.status == "approved",
        )
        .order_by(SemanticTagFact.field_key.asc(), SemanticTagFact.fact_version.desc(), SemanticTagFact.id.desc())
    ).all()
    latest_by_field: dict[str, SemanticTagFact] = {}
    for fact in facts:
        latest_by_field.setdefault(fact.field_key, fact)
    if not latest_by_field:
        return None
    semantic = {
        field_key: {
            "status": fact.field_status,
            "values": json.loads(fact.values_json),
            "evidence": json.loads(fact.evidence_json),
        }
        for field_key, fact in latest_by_field.items()
    }
    return asset_version, {
        "semantic": semantic,
        "semantic_contract_id": next(iter(latest_by_field.values())).contract_id,
        "normalization_version": next(iter(latest_by_field.values())).normalization_version,
        "mapping_version": next(iter(latest_by_field.values())).mapping_version,
    }


def build_label_snapshot(
    db: Session,
    *,
    evaluation_id: int,
    content_key: str | None,
) -> tuple[str, int, int, dict[str, Any]]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    if evaluation is None:
        raise ValueError("评测结果不存在")
    panel = db.scalar(select(ReviewPanel).where(ReviewPanel.evaluation_id == evaluation.id))
    if panel is None or panel.status != "completed" or panel.final_review_id is None:
        raise ValueError("评测尚未完成初审，不能形成正式标签")
    final_review = db.get(HumanReview, panel.final_review_id)
    if final_review is None or final_review.decision not in {"approved", "corrected"}:
        raise ValueError("人工真值不是可发布状态")
    if evaluation.job.category_key != evaluation.asset.category_key:
        raise ValueError("评测类目与素材类目不一致")
    precheck = json.loads(evaluation.precheck_json or "{}")
    aesthetic = json.loads(evaluation.aesthetic_json or "{}")
    truth = json.loads(panel.final_truth_json or "{}")
    final_level = truth.get("corrected_level") or evaluation.level
    final_score = truth.get("corrected_score")
    if final_score is None:
        final_score = evaluation.score
    if not final_level or final_score is None:
        raise ValueError("人工真值缺少正式等级或服务端分数")
    key_fields = truth.get("key_fields") or {}
    classification = dict(precheck.get("classification") or {})
    image_quality = dict(precheck.get("image_quality") or {})
    production_fields = dict(precheck.get("production_fields") or {})
    for field_key, value in key_fields.items():
        if field_key.startswith("classification."):
            classification[field_key.split(".", 1)[1]] = value
        elif field_key.startswith("image_quality."):
            image_quality[field_key.split(".", 1)[1]] = value
        elif field_key.startswith("production_fields."):
            production_fields[field_key.split(".", 1)[1]] = value
    media_form = key_fields.get("media_form", precheck.get("media_form", {}))
    semantic_result = _approved_semantic_payload(db, evaluation=evaluation)
    schema_version = SEMANTIC_LABEL_SCHEMA_VERSION if semantic_result else LABEL_SCHEMA_VERSION
    payload = {
        "schema_version": schema_version,
        "content_key": _content_key(db, evaluation, content_key),
        "category_key": evaluation.job.category_key,
        "level": final_level,
        "score": final_score,
        "classification": classification,
        "dimensions": truth.get("dimensions") or aesthetic.get("dimensions", {}),
        "key_fields": key_fields,
        "production_fields": production_fields,
        "image_quality": image_quality,
        "media_form": media_form,
        "provenance": {
            "evaluation_id": evaluation.id,
            "job_id": evaluation.job_id,
            "final_review_id": final_review.id,
            "strategy_bundle_id": evaluation.strategy_bundle_id,
            "model_id": evaluation.model_id,
            "prompt_a_version": evaluation.prompt_a_version,
            "prompt_b_version": evaluation.prompt_b_version,
            "rubric_version": evaluation.rubric_version,
            "engine_version": evaluation.engine_version,
        },
    }
    if semantic_result is not None:
        asset_version, semantic_meta = semantic_result
        semantic_route = precheck.get("semantic_route") if isinstance(precheck.get("semantic_route"), Mapping) else {}
        payload["semantic"] = semantic_meta["semantic"]
        payload["quality"] = {
            "level": final_level,
            "score": final_score,
            "dimensions": truth.get("dimensions") or aesthetic.get("dimensions", {}),
        }
        payload["governance"] = {
            "review_status": "approved",
            "contract_id": semantic_meta["semantic_contract_id"],
        }
        payload["provenance"].update({
            "asset_version_id": asset_version.id,
            "asset_id": evaluation.asset_id,
            "final_review_id": final_review.id,
            "normalization_version": semantic_meta["normalization_version"],
            "mapping_version": semantic_meta["mapping_version"],
            "site_scope": semantic_route.get("site_scope"),
            "asset_scope": semantic_route.get("asset_scope"),
            "tag_contract_version": (
                f"{semantic_route.get('contract_id')}:{semantic_route.get('contract_version')}"
                if semantic_route.get("contract_id") is not None and semantic_route.get("contract_version") is not None
                else str(semantic_meta["semantic_contract_id"])
            ),
        })
    return payload["content_key"], evaluation.id, final_review.id, payload


def create_release(
    db: Session,
    *,
    release_key: str,
    evaluation_id: int,
    content_key: str | None,
    requested_by: str,
) -> tuple[LabelRelease, bool]:
    existing = db.scalar(select(LabelRelease).where(LabelRelease.release_key == release_key))
    if existing is not None:
        if (
            existing.evaluation_id != evaluation_id
            or (content_key is not None and existing.content_key != content_key)
        ):
            raise LabelIntegrationConflict("同一 release_key 的发布请求不一致")
        return existing, True
    key, eval_id, review_id, payload = build_label_snapshot(
        db, evaluation_id=evaluation_id, content_key=content_key
    )
    raw = canonical_json(payload)
    release = LabelRelease(
        release_key=release_key,
        content_key=key,
        category_key=str(payload["category_key"]),
        evaluation_id=eval_id,
        final_review_id=review_id,
        label_schema_version=str(payload["schema_version"]),
        label_payload_json=raw,
        payload_hash=_payload_hash(payload),
        status="pending_review",
        requested_by=requested_by,
    )
    db.add(release)
    db.flush()
    append_audit_event(
        db,
        category="label_release",
        action="requested",
        subject_type="label_release",
        subject_id=release.id,
        actor=requested_by,
        payload={"release_key": release_key, "content_key": key, "evaluation_id": eval_id},
        event_key=f"label-release:requested:{release_key}",
    )
    return release, False


def publish_release(db: Session, *, release: LabelRelease, actor: str) -> tuple[PublishedLabel, bool]:
    if release.status == "published":
        published = db.scalar(select(PublishedLabel).where(PublishedLabel.release_id == release.id))
        if published is None:
            raise RuntimeError("已发布 release 缺少发布标签")
        return published, True
    if release.status not in {"pending_review", "approved"}:
        raise ValueError("当前发布版本不在可审批状态")
    if release.evaluation_id is not None:
        build_label_snapshot(db, evaluation_id=release.evaluation_id, content_key=release.content_key)
    now = datetime.now(timezone.utc)
    release.status = "published"
    release.approved_by = actor
    release.approved_at = now
    release.published_at = now
    current = db.scalar(
        select(PublishedLabel).where(
            PublishedLabel.content_key == release.content_key,
            PublishedLabel.status == "published",
        ).order_by(PublishedLabel.version.desc(), PublishedLabel.id.desc())
    )
    version = (current.version + 1) if current else 1
    if current is not None:
        current.status = "superseded"
        current.superseded_at = now
    label = PublishedLabel(
        release_id=release.id,
        content_key=release.content_key,
        category_key=release.category_key,
        version=version,
        label_schema_version=release.label_schema_version,
        label_payload_json=release.label_payload_json,
        payload_hash=release.payload_hash,
        status="published",
        published_at=now,
    )
    db.add(label)
    db.flush()
    payload = {
        "schema_version": "label-change-event-v1",
        "operation": "rolled_back" if release.source_release_id else "published",
        "content_key": release.content_key,
        "version": version,
        "label": json.loads(label.label_payload_json),
        "release_id": release.id,
        "payload_hash": label.payload_hash,
    }
    operation = payload["operation"]
    outbox = LabelOutboxEvent(
        event_id=f"label-release:{release.id}:{operation}",
        release_id=release.id,
        published_label_id=label.id,
        content_key=release.content_key,
        operation=operation,
        payload_hash=_payload_hash(payload),
        payload_json=canonical_json(payload),
    )
    db.add(outbox)
    append_audit_event(
        db,
        category="label_release",
        action=operation,
        subject_type="label_release",
        subject_id=release.id,
        actor=actor,
        payload={"content_key": release.content_key, "version": version, "outbox_event_id": outbox.event_id},
        event_key=f"label-release:{release.id}:{operation}",
    )
    return label, False


def rollback_release(
    db: Session,
    *,
    target: PublishedLabel,
    rollback_key: str,
    actor: str,
) -> tuple[LabelRelease, PublishedLabel, bool]:
    existing = db.scalar(select(LabelRelease).where(LabelRelease.release_key == rollback_key))
    if existing is not None:
        if existing.source_release_id != target.release_id:
            raise LabelIntegrationConflict("同一 rollback_key 的回滚目标不一致")
        published = db.scalar(select(PublishedLabel).where(PublishedLabel.release_id == existing.id))
        if published is None:
            raise RuntimeError("回滚 release 缺少发布标签")
        return existing, published, True
    if target.status == "published":
        raise LabelIntegrationConflict("目标标签已是当前生效版本，无需回滚")
    release = LabelRelease(
        release_key=rollback_key,
        content_key=target.content_key,
        category_key=target.category_key,
        source_release_id=target.release_id,
        label_schema_version=target.label_schema_version,
        label_payload_json=target.label_payload_json,
        payload_hash=target.payload_hash,
        status="approved",
        requested_by=actor,
        approved_by=actor,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    published, _ = publish_release(db, release=release, actor=actor)
    return release, published, False


def release_payload(release: LabelRelease, published: PublishedLabel | None = None) -> dict[str, Any]:
    return {
        "id": release.id,
        "release_key": release.release_key,
        "content_key": release.content_key,
        "category_key": release.category_key,
        "evaluation_id": release.evaluation_id,
        "final_review_id": release.final_review_id,
        "source_release_id": release.source_release_id,
        "status": release.status,
        "label_schema_version": release.label_schema_version,
        "payload_hash": release.payload_hash,
        "label": json.loads(release.label_payload_json),
        "requested_by": release.requested_by,
        "requested_at": release.requested_at,
        "approved_by": release.approved_by,
        "approved_at": release.approved_at,
        "published_at": release.published_at,
        "published_label_id": published.id if published else None,
        "published_version": published.version if published else None,
        "is_current": published.status == "published" if published else None,
    }
