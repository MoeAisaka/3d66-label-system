# Platform Semantic Tag Demand Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 TPENG 标签实验台中建立平台级通用语义标签需求合同、Canonical 事实、字段级质量证据和可重建下游投影，并以国内 3D/SU 整体/单体作为第一条本地纵向验证切片。

**Architecture:** 以 `TagDemandContract` 冻结下游字段需求、类目适用性、执行变体和质量门槛；以不可变 `AssetVersion` 绑定素材版本；模型/规则输出先进入证据层，经标准化、实体映射和人工纠偏后，才进入 `semantic.*` Canonical 事实。现有 `LabelRelease` / `PublishedLabel` 继续作为正式事实发布轴，现有 `ProjectionContract` / manifest / reconciliation 继续作为数据库表与知识图谱的可重建消费投影轴，不新增旁路发布。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、SQLite 增量迁移、Pydantic 2、Pytest、React 19、TypeScript 7、TanStack Query、Vite 8。

## Global Constraints

- 统一产品名称为 **TPENG 标签实验台（LabelLab）**；“标签体系重构”是转型目标，不再建设独立平台。
- `space`、`object`、`style`、`material`、`structural_features`、`architectural_element`、`soft_decoration`、`hard_decoration`、`color` 是平台通用语义字段；`title` 是按类目和 locale 声明的本地化字段。
- 业务类目只能提供字段适用性、`category.<category_key>.*` 扩展、预处理、Prompt/模型/规则绑定、专用编辑视图和字段级质量门槛，不复制模型管理、纠偏、版本、发布、重跑或投影能力。
- Canonical 多值字段必须结构化保存 `value`、`entity_id`、`locale`、`rank`、`weight`、`weight_semantics` 和完整 provenance；禁止将逗号字符串作为事实主存储。
- `object`、`material` 使用 `weight_semantics=relative_importance_level`：每个值的 0～1 数值单独保留，`rank` 负责排序，不做总和归一化；同一实体重复出现时取最高等级，不累加。
- `not_applicable`、`not_detected`、`needs_review` 必须可区分；禁止使用空字符串折叠空值语义。
- 国内/海外、整体/单体通过 `site_scope`、`asset_scope`、`locale`、`category_key`、`prompt_variant`、`prompt_version`、`model_version` 参数路由，不复制四套流水线。
- 机制发布轴与标签事实发布轴保持独立；启用候选机制不自动发布标签事实，不自动覆盖存量。
- 知识图谱、搜索索引、向量索引和下游数据库表只消费正式发布事实；不得读取模型原始结果、候选机制、实验结果或人工处理过程。
- 本计划首轮只允许本地数据和 dry-run 投影；不得连接真实上游、真实业务数据库、真实知识图谱、真实模型或生产环境。
- 不修改钉钉原始需求文档；所有实现口径落在本仓库的 ADR、规格、计划、测试和项目状态中。
- 不做移动端适配；验收桌面视口为 `1440×900` 和 `1280×720`，并保留键盘焦点与 200% 缩放能力。
- 未签认标准词表、权重含义、字段适用性和两张下游表合同前，停止在“本地 schema + 模拟数据 + dry-run 投影”，不得创建外部写入适配器。

---

## File Structure

### New backend files

- `backend/app/semantic_tag_contracts.py`：平台字段定义、结构化值、空值语义、合同校验、Canonical hash。
- `backend/app/semantic_tag_mapping.py`：中英文标准值、实体映射、去重、主次排序、冲突检测。
- `backend/app/semantic_tag_quality.py`：字段级 Precision/Recall、映射覆盖率、冲突率和聚合指标。
- `backend/tests/test_semantic_tag_contracts.py`：平台 schema、适用性和执行变体合同测试。
- `backend/tests/test_semantic_tag_mapping.py`：标准化、实体映射、去重和冲突测试。
- `backend/tests/test_semantic_tag_quality.py`：字段级与平台级质量指标测试。
- `backend/tests/test_model_3d_su_semantic_slice.py`：国内 3D/SU 整体/单体第一纵向切片测试。

### Modified backend files

- `backend/app/models.py`：增加不可变 `AssetVersion`、`TagDemandContract`、`SemanticTagFact`、`SemanticQualityMetricSnapshot`。
- `backend/app/migrations/runner.py`：追加单一增量迁移和不可变/外键/唯一性保护。
- `backend/app/label_governance.py`：把已批准语义事实装配进 `published-label-v2`，保持旧 `published-label-v1` 可读。
- `backend/app/projection_contracts.py`：允许 `semantic.*` 与新增 provenance，保持候选/原始响应/人工过程禁止投影。
- `backend/app/main.py`：增加字段需求合同、字段适用性、质量指标和本地 dry-run API；不增加外部数据库写 API。
- `backend/app/schema_adapter.py`：把模型结果适配为证据候选，不直接生成正式事实。
- `backend/app/category_evaluation_v3_config_api.py`：在类目 profile payload 中暴露语义字段适用性摘要，不把语义合同塞入 v3 评分合同。
- `backend/tests/test_migration.py`：旧 SQLite 安全升级测试。
- `backend/tests/test_unified_label_platform.py`：`published-label-v2`、双发布轴和消费者边界测试。
- `backend/tests/test_projection_contracts.py`：国内/海外投影、结构化语义字段、版本对账和禁止字段测试。
- `backend/tests/test_category_evaluation_v3_config_api.py`：类目适用性摘要与 v3 机制隔离测试。
- `backend/tests/test_baseline_regression.py`：字段级指标证据与现有五档指标并存测试。

### New frontend files

- `frontend/src/pages/tag-demand-contracts-page.tsx`：列表型字段需求合同管理页，一级页只展示版本、状态、适用类目、执行变体和质量门槛摘要。
- `frontend/src/components/tag-demand-contract-drawer.tsx`：字段矩阵、空值语义、投影映射、版本证据的二级抽屉。
- `frontend/scripts/check-tag-demand-contract.ts`：路由、字段、抽屉和禁止平铺的静态合同检查。

### Modified frontend files

- `frontend/src/lib/types.ts`：合同、结构化值、适用性、字段指标和 dry-run manifest 类型。
- `frontend/src/lib/api.ts`：字段合同、质量指标和 dry-run API client。
- `frontend/src/App.tsx`：新增 `/workflow/governance/tag-demand-contracts` 路由。
- `frontend/src/pages/system-management-page.tsx`：新增“字段需求合同”常规入口。
- `frontend/src/pages/category-evaluation-v3-config-page.tsx`：只展示类目语义适用性摘要和进入合同抽屉的入口。
- `frontend/src/pages/baseline-regression-page.tsx`：在不移除现有 L1–L5 矩阵和聚合档位的前提下，增加字段级语义质量入口。
- `frontend/package.json`：增加 `contract:tag-demand` 脚本。
- `PROJECT_STATUS.md`：记录实际完成批次、验证结果、未启用外部写入和下一阶段 Gap。
- `docs/decisions/README.md`：索引新 ADR。
- `docs/decisions/0047-platform-semantic-tag-demand-contract.md`：冻结平台语义标签合同、事实主权、类目扩展和首切片边界。

---

### Task 1: Freeze the platform semantic field contract

**Files:**
- Create: `backend/app/semantic_tag_contracts.py`
- Create: `backend/tests/test_semantic_tag_contracts.py`
- Create: `docs/decisions/0047-platform-semantic-tag-demand-contract.md`
- Modify: `docs/decisions/README.md`

**Interfaces:**
- Consumes: 设计文档 `docs/superpowers/specs/2026-08-14-kg-semantic-tag-demand-design.md`。
- Produces: `SemanticTagSchema`、`TagDemandContractDefinition`、`validate_tag_demand_contract(payload: Mapping[str, Any]) -> TagDemandContractDefinition`、`canonical_contract_hash(definition: TagDemandContractDefinition) -> str`、`PLATFORM_SEMANTIC_FIELD_KEYS`。

- [ ] **Step 1: Write failing schema tests**

```python
def test_platform_contract_accepts_shared_semantic_fields_and_structured_values() -> None:
    contract = valid_contract()
    parsed = validate_tag_demand_contract(contract)
    assert parsed.schema_version == "tag-demand-contract-v1"
    assert set(parsed.semantic_schema.fields) == set(PLATFORM_SEMANTIC_FIELD_KEYS) | {"title"}
    assert parsed.semantic_schema.fields["object"].cardinality == "multi"


@pytest.mark.parametrize("status", ["not_applicable", "not_detected", "needs_review"])
def test_field_null_semantics_remain_distinct(status: str) -> None:
    value = validate_semantic_field_result({"status": status, "values": []})
    assert value.status == status


def test_contract_rejects_comma_joined_canonical_values() -> None:
    contract = valid_contract()
    contract["semantic_schema"]["fields"]["style"]["default_value"] = "现代,极简"
    with pytest.raises(SemanticTagContractError, match="结构化数组"):
        validate_tag_demand_contract(contract)
```

- [ ] **Step 2: Run the tests and confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_contracts.py -q`

Expected: collection fails because `app.semantic_tag_contracts` does not exist.

- [ ] **Step 3: Implement immutable Pydantic contract types**

```python
PLATFORM_SEMANTIC_FIELD_KEYS = (
    "space", "object", "style", "material", "structural_features",
    "architectural_element", "soft_decoration", "hard_decoration", "color",
)

class SemanticTagValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: str = Field(min_length=1, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)
    locale: Literal["zh", "en"]
    rank: int = Field(ge=1, le=100)
    weight: float | None = Field(default=None, ge=0, le=1)
    source: Literal["model", "rule", "human", "mixed"]
    evidence_ref: str = Field(min_length=1, max_length=320)
    model_version: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    normalization_version: str = Field(min_length=1, max_length=80)
    mapping_version: str = Field(min_length=1, max_length=80)
    review_status: Literal["candidate", "needs_review", "approved", "rejected"]

class SemanticFieldResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["required", "optional", "not_applicable", "not_detected", "needs_review"]
    values: Sequence[SemanticTagValue] = ()

SemanticApplicability = Literal["required", "optional", "not_applicable"]

class SemanticFieldDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    field_key: str
    cardinality: Literal["single", "multi"]
    localized: bool = True
    vocabulary_owner: str = Field(min_length=1, max_length=120)
    max_values: int = Field(ge=1, le=100)
    default_value: Sequence[SemanticTagValue] = ()

class FieldQualityGate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    min_precision: float = Field(ge=0, le=1)
    min_recall: float = Field(ge=0, le=1)
    min_mapping_coverage: float = Field(ge=0, le=1)
    max_conflict_rate: float = Field(ge=0, le=1)

class ProjectionTargetDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_key: Literal["domestic_material_tags", "overseas_material_tags", "knowledge_graph"]
    mode: Literal["dry_run"]
    locale: Literal["zh", "en"]

class ExecutionVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    site_scope: Literal["domestic", "overseas"]
    asset_scope: Literal["whole", "single", "other", "unknown"]
    locale: Literal["zh", "en"]
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    prompt_variant: Literal["whole", "single"]
    prompt_version: str
    model_version: str

class SemanticTagSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["semantic-tag-schema-v1"]
    fields: dict[str, SemanticFieldDefinition]

class TagDemandContractDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["tag-demand-contract-v1"]
    semantic_schema: SemanticTagSchema
    category_applicability: dict[str, dict[str, SemanticApplicability]]
    execution_variants: Sequence[ExecutionVariant]
    quality_gates: dict[str, FieldQualityGate]
    projection_targets: Sequence[ProjectionTargetDefinition]
```

Implement explicit validators so `not_applicable` and `not_detected` require an empty value list, `required` cannot publish empty values, ranks are unique, each relative-importance level remains within `0..1`, and no aggregate-weight normalization is applied.

- [ ] **Step 4: Add canonical hash and exact execution-variant validation**

```python
def canonical_contract_hash(definition: TagDemandContractDefinition) -> str:
    payload = definition.model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
```

Also validate `domestic → zh`, `overseas → en`, `whole → prompt_variant=whole`, and `single → prompt_variant=single`; reject a required field missing from any declared category applicability matrix.

- [ ] **Step 5: Record ADR-0047**

The ADR must state that semantic fields are platform-wide, 3D/SU is only the first validation slice, `is_single` is a projection alias derived from `asset_scope`, and external projections cannot become fact owners.

- [ ] **Step 6: Run tests and documentation checks**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_contracts.py -q`

Expected: all tests pass.

Run: `rg -n "3D/SU 专属|实验台仅为评测工具|两套独立项目" PRODUCT.md PROJECT_STATUS.md docs/decisions docs/superpowers/specs docs/superpowers/plans`

Expected: no contradictory active-positioning statements in the new ADR or plan.

- [ ] **Step 7: Commit Task 1**

```bash
git add backend/app/semantic_tag_contracts.py backend/tests/test_semantic_tag_contracts.py docs/decisions/0047-platform-semantic-tag-demand-contract.md docs/decisions/README.md
git commit -m "feat: define platform semantic tag demand contract"
```

### Task 2: Add immutable asset versions and contract persistence

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/tests/test_migration.py`
- Create: `backend/tests/test_asset_version_and_tag_contract_models.py`

**Interfaces:**
- Consumes: `TagDemandContractDefinition` and canonical hash from Task 1.
- Produces: SQLAlchemy models `AssetVersion`, `TagDemandContract`, `SemanticTagFact`, `SemanticQualityMetricSnapshot`.

- [ ] **Step 1: Write failing migration/model tests**

```python
def test_asset_version_is_immutable_and_unique_per_asset_revision(db: Session) -> None:
    version = AssetVersion(asset_id=1, version=1, asset_sha256="a" * 64,
                           source_version="source-v1", snapshot_kind="materialized", created_by="tester")
    db.add(version)
    db.commit()
    version.asset_sha256 = "b" * 64
    with pytest.raises(IntegrityError):
        db.commit()


def test_tag_demand_contract_versions_are_append_only(db: Session) -> None:
    first = make_contract(version=1, status="active")
    db.add(first)
    db.commit()
    first.definition_json = "{}"
    with pytest.raises(IntegrityError):
        db.commit()
```

- [ ] **Step 2: Confirm red against the current schema**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_asset_version_and_tag_contract_models.py tests/test_migration.py -q`

Expected: fails because the four models and next migration are absent.

- [ ] **Step 3: Add the four focused models**

Use these persisted responsibilities:

```text
AssetVersion
  asset_id, version, asset_sha256, source_version, supersedes_id,
  snapshot_kind(materialized|deleted), created_by, created_at

TagDemandContract
  contract_key, version, status(draft|candidate|active|retired),
  definition_json, contract_hash, approved_by, approved_at,
  created_by, created_at

SemanticTagFact
  asset_version_id, field_key, fact_version, field_status, supersedes_fact_id,
  values_json, evidence_json, source_evaluation_id, source_review_id,
  contract_id, normalization_version, mapping_version,
  status(candidate|approved|rejected), payload_hash, created_at

SemanticQualityMetricSnapshot
  baseline_run_id, contract_id, category_key, site_scope, asset_scope,
  field_key, truth_count, predicted_count, true_positive_count,
  precision, recall, mapping_coverage, unmapped_rate, conflict_rate,
  null_semantics_accuracy, correction_rate, review_coverage,
  bilingual_consistency, reconciliation_rate, metrics_hash, created_at
```

Add relationships only where current code needs them; do not create cascading deletes from assets, published labels, reviews, or contracts.

- [ ] **Step 4: Append one migration after migration 67**

Create all four tables, indexes, foreign keys, unique constraints, JSON validity checks, and status checks. Add SQLite triggers that fully prevent update/delete of `asset_versions` and `semantic_tag_facts`; for `tag_demand_contracts`, allow only status and approval-audit transitions while rejecting changes to key, version, definition, hash, creator, or creation time.

- [ ] **Step 5: Add old-database upgrade coverage**

Start from a database whose `schema_migrations` ends at `add_projection_contract_registry`, run `run_migrations(connection)`, then assert the four tables exist and the previous `published_labels` row remains unchanged.

- [ ] **Step 6: Run focused migration tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_asset_version_and_tag_contract_models.py tests/test_migration.py -q`

Expected: all tests pass; the new migration name appears once in the applied migration list.

- [ ] **Step 7: Commit Task 2**

```bash
git add backend/app/models.py backend/app/migrations/runner.py backend/tests/test_migration.py backend/tests/test_asset_version_and_tag_contract_models.py
git commit -m "feat: persist asset versions and semantic tag contracts"
```

### Task 3: Expose versioned tag-demand contract APIs

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_tag_demand_contract_api.py`

**Interfaces:**
- Consumes: persisted `TagDemandContract` and Task 1 validation.
- Produces: `GET /api/tag-demand-contracts`, `POST /api/tag-demand-contracts`, `POST /api/tag-demand-contracts/{id}/activate`, `GET /api/tag-demand-contracts/{id}`.

- [ ] **Step 1: Write API tests for versioning, permissions and activation**

```python
def test_create_contract_appends_version_without_overwriting_active(client: TestClient) -> None:
    first = client.post("/api/tag-demand-contracts", json=valid_contract_request()).json()
    second = client.post("/api/tag-demand-contracts", json=valid_contract_request()).json()
    assert (first["version"], second["version"]) == (1, 2)
    assert first["contract_hash"] != ""


def test_activation_requires_admin_and_signed_input_matrix(non_admin_client: TestClient) -> None:
    response = non_admin_client.post("/api/tag-demand-contracts/1/activate")
    assert response.status_code == 403


def test_activation_rejects_missing_vocab_owner(client: TestClient) -> None:
    contract = valid_contract_request()
    contract["definition"]["fields"]["style"]["vocabulary_owner"] = ""
    created = client.post("/api/tag-demand-contracts", json=contract).json()
    response = client.post(f"/api/tag-demand-contracts/{created['id']}/activate")
    assert response.status_code == 409
```

- [ ] **Step 2: Confirm API tests fail**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_tag_demand_contract_api.py -q`

Expected: 404 responses for all new routes.

- [ ] **Step 3: Add request/response models**

Define `TagDemandContractCreateRequest` with `contract_key`, `definition`, `status=draft`; reject client-supplied version/hash/audit fields. API responses include the validated definition and never expose credentials or raw provider payloads.

- [ ] **Step 4: Implement append-only create/list/detail routes**

Create version `latest.version + 1`, calculate the server-owned hash, and return the current active contract marker separately from the version list.

- [ ] **Step 5: Implement explicit activation transaction**

Activation validates all required sign-off fields, retires the previous active row without mutating its definition, activates the selected candidate, appends an audit event, and does not create evaluation jobs, label releases, stock reruns, projection manifests, or outbox events.

- [ ] **Step 6: Run API tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_tag_demand_contract_api.py tests/test_unified_label_platform.py -q`

Expected: all tests pass and existing release APIs keep their prior behavior.

- [ ] **Step 7: Commit Task 3**

```bash
git add backend/app/main.py backend/tests/test_tag_demand_contract_api.py
git commit -m "feat: add versioned tag demand contract api"
```

### Task 4: Route content to one platform contract with category applicability

**Files:**
- Modify: `backend/app/label_governance.py`
- Modify: `backend/app/category_evaluation_v3_config_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_category_evaluation_v3_config_api.py`
- Create: `backend/tests/test_semantic_execution_routing.py`

**Interfaces:**
- Consumes: active `TagDemandContract`, active `EvaluationCategoryProfile`, `ContentRecord`, `AssetVersion`.
- Produces: `resolve_semantic_execution_route(db: Session, *, content_record: ContentRecord, asset_version: AssetVersion, site_scope: str, asset_scope: str, locale: str, prompt_variant: str, prompt_version: str, model_version: str) -> SemanticExecutionRoute` with the seven frozen variant fields.

- [ ] **Step 1: Write routing tests**

```python
@pytest.mark.parametrize(
    ("site_scope", "asset_scope", "locale", "prompt_variant"),
    [
        ("domestic", "whole", "zh", "whole"),
        ("domestic", "single", "zh", "single"),
        ("overseas", "whole", "en", "whole"),
        ("overseas", "single", "en", "single"),
    ],
)
def test_four_batches_share_one_contract(site_scope, asset_scope, locale, prompt_variant, db):
    record, version = content_fixture(db, category_key="model_3d_su")
    route = resolve_semantic_execution_route(
        db,
        content_record=record,
        asset_version=version,
        site_scope=site_scope,
        asset_scope=asset_scope,
        locale=locale,
        prompt_variant=prompt_variant,
        prompt_version=f"semantic-{prompt_variant}-v1",
        model_version="fixture-model-v1",
    )
    assert route.contract_id == active_contract.id
    assert route.site_scope == site_scope
    assert route.asset_scope == asset_scope
    assert route.locale == locale
    assert route.prompt_variant == prompt_variant


def test_non_applicable_fields_are_routed_without_model_work(db):
    record, version = content_fixture(db, category_key="proposal_text_pdf")
    route = resolve_semantic_execution_route(
        db,
        content_record=record,
        asset_version=version,
        site_scope="domestic",
        asset_scope="whole",
        locale="zh",
        prompt_variant="whole",
        prompt_version="proposal-semantic-whole-v1",
        model_version="fixture-model-v1",
    )
    assert route.fields["material"].status == "not_applicable"
```

- [ ] **Step 2: Confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_execution_routing.py -q`

Expected: `resolve_semantic_execution_route` is missing.

- [ ] **Step 3: Implement deterministic route resolution**

The resolver must fail closed when the contract is absent, the category profile is inactive, the asset version is missing, or a required field lacks an applicability rule. `prompt_version` and `model_version` come from the frozen run snapshot, not the current mutable registry.

- [ ] **Step 4: Expose read-only applicability summary in category profile payload**

Return:

```json
{
  "semantic_tag_applicability": {
    "contract_id": 12,
    "contract_version": 3,
    "field_counts": {"required": 4, "optional": 5, "not_applicable": 1},
    "fields": {"space": "required", "material": "optional"}
  }
}
```

Do not store this object inside `CategoryEvaluationV3Config.contract_json`; v3 remains the evaluation/scoring mechanism.

- [ ] **Step 5: Run route/profile tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_execution_routing.py tests/test_category_evaluation_v3_config_api.py -q`

Expected: all tests pass, and existing v3 revision hashes are unchanged by a tag-demand contract update.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/app/label_governance.py backend/app/category_evaluation_v3_config_api.py backend/app/main.py backend/tests/test_semantic_execution_routing.py backend/tests/test_category_evaluation_v3_config_api.py
git commit -m "feat: route semantic fields through category applicability"
```

### Task 5: Normalize model evidence and map standard entities

**Files:**
- Create: `backend/app/semantic_tag_mapping.py`
- Modify: `backend/app/schema_adapter.py`
- Create: `backend/tests/test_semantic_tag_mapping.py`

**Interfaces:**
- Consumes: `SemanticExecutionRoute`, provider result JSON, versioned vocabulary mapping.
- Produces: `normalize_semantic_candidates(*, route: SemanticExecutionRoute, provider_payload: Mapping[str, Any], evidence_prefix: str) -> SemanticCandidateBundle`、`map_standard_entities(*, bundle: SemanticCandidateBundle, mapping_registry: Mapping[str, Any], normalization_version: str, mapping_version: str) -> SemanticMappingResult`。

- [ ] **Step 1: Write mapping tests**

```python
def test_mapping_keeps_structured_rank_weight_and_bilingual_names() -> None:
    bundle = candidate_bundle(
        field_key="style",
        values=[candidate("现代简约", rank=1, weight=0.7)],
    )
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=fixture_mapping("style-map-v1"),
        normalization_version="semantic-normalization-v1",
        mapping_version="style-map-v1",
    )
    assert result.values[0].entity_id == "style.modern_minimal"
    assert result.values[0].localized_names == {"zh": "现代简约", "en": "Modern Minimal"}
    assert result.values[0].rank == 1
    assert result.values[0].weight == 0.7


def test_mapping_marks_unknown_values_without_discarding_evidence() -> None:
    bundle = candidate_bundle(field_key="material", values=[candidate("未知复合面")])
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=empty_mapping(),
        normalization_version="semantic-normalization-v1",
        mapping_version="material-map-v1",
    )
    assert result.unmapped_values == ["未知复合面"]
    assert result.field_status == "needs_review"
    assert result.evidence_refs


def test_duplicate_entities_merge_deterministically() -> None:
    bundle = candidate_bundle(
        field_key="object",
        values=[
            candidate("沙发", rank=1, weight=0.6),
            candidate("sofa", locale="en", rank=2, weight=0.4),
        ],
    )
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=fixture_mapping("object-map-v1"),
        normalization_version="semantic-normalization-v1",
        mapping_version="object-map-v1",
    )
    assert len(result.values) == 1
    assert result.values[0].weight == pytest.approx(0.6)
```

- [ ] **Step 2: Confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_mapping.py -q`

Expected: import failure for `app.semantic_tag_mapping`.

- [ ] **Step 3: Implement candidate normalization**

Reject strings where an array/object is required, trim Unicode whitespace, normalize locale aliases, preserve the raw evidence reference, and never mutate the provider raw response stored on the evaluation result.

- [ ] **Step 4: Implement versioned mapping and conflict detection**

Map by exact standard value first, then declared alias; never perform uncontrolled fuzzy mapping. Emit conflicts when two entity IDs claim the same rank, a single-value field maps to multiple entities, or rank/individual-level constraints violate the field contract. Duplicate aliases for one entity keep the highest relative-importance level and merge evidence without adding levels.

- [ ] **Step 5: Connect `schema_adapter.py` to evidence candidates only**

The adapter returns a `semantic_candidates` section in the normalized evaluation payload. It must not create `SemanticTagFact`, `LabelRelease`, `PublishedLabel`, or projection rows.

- [ ] **Step 6: Run mapping and existing adapter tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_mapping.py tests/test_category_worker_pipeline.py tests/test_category_evaluation_aggregator.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add backend/app/semantic_tag_mapping.py backend/app/schema_adapter.py backend/tests/test_semantic_tag_mapping.py
git commit -m "feat: normalize and map semantic tag evidence"
```

### Task 6: Persist human-approved semantic facts and publish `published-label-v2`

**Files:**
- Modify: `backend/app/label_governance.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_unified_label_platform.py`
- Create: `backend/tests/test_semantic_tag_fact_governance.py`

**Interfaces:**
- Consumes: mapped candidates, completed `ReviewPanel`, final `HumanReview`, active demand contract, `AssetVersion`.
- Produces: approved `SemanticTagFact` rows and a `published-label-v2` payload with `semantic`, `quality`, `governance`, and `provenance` namespaces.

- [ ] **Step 1: Write governance tests**

```python
def test_only_completed_human_truth_can_approve_semantic_facts(db: Session) -> None:
    with pytest.raises(ValueError, match="人工真值"):
        approve_semantic_facts(db, evaluation_id=pending_review.id, actor="reviewer")


def test_published_label_v2_contains_only_approved_structured_facts(client: TestClient) -> None:
    published = publish_semantic_fixture(client)
    label = published["release"]["label"]
    assert label["schema_version"] == "published-label-v2"
    assert label["semantic"]["style"]["values"][0]["entity_id"] == "style.modern"
    assert label["provenance"]["asset_version_id"]
    serialized = json.dumps(label, ensure_ascii=False)
    assert "raw_response" not in serialized
    assert "candidate" not in serialized
```

- [ ] **Step 2: Confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_fact_governance.py tests/test_unified_label_platform.py -q`

Expected: semantic fact approval and `published-label-v2` assertions fail.

- [ ] **Step 3: Implement candidate-to-approved fact promotion**

Require completed human review for `needs_review` fields. Allow deterministic auto-approved facts only where the active contract explicitly permits the production path and field threshold; still store evidence and versions. Promotion inserts a new immutable `approved` fact with `supersedes_fact_id` pointing to the prior approved fact; it never updates or deletes the old row.

- [ ] **Step 4: Extend `build_label_snapshot` without breaking v1 history**

New releases use:

```json
{
  "schema_version": "published-label-v2",
  "content_key": "content-hub:content-001",
  "asset_version_id": 15,
  "category_key": "model_3d_su",
  "semantic": {"space": {"status": "required", "values": []}},
  "quality": {"level": "L2", "score": 78, "dimensions": {}},
  "governance": {"review_status": "approved", "contract_id": 12},
  "provenance": {
    "asset_id": 7,
    "asset_version_id": 15,
    "asset_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "evaluation_id": 42,
    "final_review_id": 9,
    "strategy_bundle_id": 3,
    "model_id": "fixture-model-v1",
    "prompt_a_version": "model-3d-su-semantic-a-v1",
    "prompt_b_version": "model-3d-su-quality-b-v1",
    "normalization_version": "semantic-normalization-v1",
    "mapping_version": "kg-entity-map-v1"
  }
}
```

Keep consumer reads of existing `published-label-v1` rows unchanged.

- [ ] **Step 5: Verify release-axis isolation**

Add assertions that activating a mechanism or a demand contract creates zero `PublishedLabel` and zero `LabelOutboxEvent` rows; only explicit label release approval produces them.

- [ ] **Step 6: Run governance tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_fact_governance.py tests/test_unified_label_platform.py tests/test_category_evaluation_v3_revisions.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 6**

```bash
git add backend/app/label_governance.py backend/app/main.py backend/tests/test_semantic_tag_fact_governance.py backend/tests/test_unified_label_platform.py
git commit -m "feat: publish approved semantic facts"
```

### Task 7: Add field-level quality metrics without replacing L1-L5 evidence

**Files:**
- Create: `backend/app/semantic_tag_quality.py`
- Create: `backend/tests/test_semantic_tag_quality.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Consumes: locked golden truth, predicted/mapped semantic fields, review outcomes, projection reconciliation.
- Produces: `compute_semantic_quality_metrics(*, truth_by_asset: Mapping[str, Mapping[str, set[str]]], predicted_by_asset: Mapping[str, Mapping[str, set[str]]], mapping_stats: Mapping[str, Mapping[str, int]], review_stats: Mapping[str, Mapping[str, int]], reconciliation_stats: Mapping[str, int]) -> SemanticQualityReport`、`GET /api/baseline-regressions/{run_id}/semantic-metrics`。

- [ ] **Step 1: Write metric-definition tests**

```python
def test_field_precision_and_recall_use_entity_ids() -> None:
    report = compute_semantic_quality_metrics(
        truth_by_asset={"asset-v1": {"style": {"style.modern", "style.minimal"}}},
        predicted_by_asset={"asset-v1": {"style": {"style.modern", "style.luxury"}}},
        mapping_stats={"style": {"candidate": 2, "mapped": 2, "unmapped": 0, "conflicted": 0, "evaluated": 1}},
        review_stats={"style": {"corrected": 0, "reviewed": 1, "required": 1, "null_truth": 0, "null_correct": 0, "bilingual": 0, "bilingual_consistent": 0}},
        reconciliation_stats={"expected": 1, "matched": 1},
    )
    assert report.fields["style"].precision == pytest.approx(0.5)
    assert report.fields["style"].recall == pytest.approx(0.5)


def test_platform_macro_and_micro_metrics_are_both_reported() -> None:
    report = semantic_quality_fixture_report()
    assert 0 <= report.macro_precision <= 1
    assert 0 <= report.micro_recall <= 1


def test_empty_denominator_serializes_as_none() -> None:
    report = compute_semantic_quality_metrics(
        truth_by_asset={},
        predicted_by_asset={},
        mapping_stats={"material": {"candidate": 0, "mapped": 0, "unmapped": 0, "conflicted": 0, "evaluated": 0}},
        review_stats={"material": {"corrected": 0, "reviewed": 0, "required": 0, "null_truth": 0, "null_correct": 0, "bilingual": 0, "bilingual_consistent": 0}},
        reconciliation_stats={"expected": 0, "matched": 0},
    )
    assert report.fields["material"].precision is None
```

- [ ] **Step 2: Confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_quality.py -q`

Expected: quality module missing.

- [ ] **Step 3: Implement exact metric formulas**

```text
precision = true_positive_count / predicted_count
recall = true_positive_count / truth_count
mapping_coverage = mapped_candidate_count / candidate_count
unmapped_rate = unmapped_candidate_count / candidate_count
conflict_rate = conflicted_asset_field_count / evaluated_asset_field_count
null_semantics_accuracy = correctly_classified_null_status / truth_null_status_count
correction_rate = human_corrected_field_count / reviewed_field_count
review_coverage = reviewed_field_count / review_required_field_count
bilingual_consistency = consistent_bilingual_entity_count / bilingual_entity_count
reconciliation_rate = matched_projection_row_count / expected_projection_row_count
```

Return `None` for zero denominators and preserve raw counts for audit.

- [ ] **Step 4: Persist append-only metric snapshots**

Write one snapshot per baseline run × contract × category × variant × field and one `_aggregate` row. Hash the complete metric payload; never overwrite historical snapshots when mappings or contracts change.

- [ ] **Step 5: Add read API and regression coexistence tests**

Assert the new API returns field metrics while `/api/baseline-regressions/{id}/metrics` still returns the existing L1–L5 matrix, exact-level accuracy, adjacent-level accuracy, recommendation band, regular band, and filter band.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_semantic_tag_quality.py tests/test_baseline_regression.py tests/test_quality_metrics_api.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 7**

```bash
git add backend/app/semantic_tag_quality.py backend/tests/test_semantic_tag_quality.py backend/app/main.py backend/tests/test_baseline_regression.py
git commit -m "feat: add semantic field quality metrics"
```

### Task 8: Extend projection contracts for domestic/overseas semantic tables

**Files:**
- Modify: `backend/app/projection_contracts.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_projection_contracts.py`

**Interfaces:**
- Consumes: `PublishedLabel` v1/v2 rows, `ProjectionContract`, `ProjectionManifest`.
- Produces: dry-run manifests for `domestic_material_tags` and `overseas_material_tags`, derived `is_single`, structured-to-compatibility serialization, version reconciliation.

- [ ] **Step 1: Write failing projection tests**

```python
def test_domestic_projection_reads_only_published_semantic_facts(client: TestClient) -> None:
    manifest = create_domestic_manifest(client)
    row = manifest["rows"][0]
    assert row["space"] == "客厅"
    assert row["object"] == "沙发_0.7,茶几_0.3"
    assert row["is_single"] == 0
    assert "candidate" not in json.dumps(manifest, ensure_ascii=False)


def test_overseas_projection_uses_entity_localized_names(client: TestClient) -> None:
    row = create_overseas_manifest(client)["rows"][0]
    assert row["style"] == "Modern Minimal"
    assert row["title"] == "Modern living room"


def test_projection_manifest_tracks_asset_contract_mapping_and_model_versions(client: TestClient) -> None:
    manifest = create_domestic_manifest(client)
    assert manifest["input_versions"]["asset_versions"]
    assert manifest["input_versions"]["tag_contract_versions"]
    assert manifest["input_versions"]["mapping_versions"]
    assert manifest["input_versions"]["model_versions"]
```

- [ ] **Step 2: Confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_projection_contracts.py -q`

Expected: semantic source roots and target roles are rejected by current validation.

- [ ] **Step 3: Extend allowed source paths safely**

Allow `semantic.<field>.status`, `semantic.<field>.values`, `quality.*`, `governance.*`, and the new versioned provenance fields. Continue rejecting `raw_response`, `candidate`, `human_review`, `manual_process`, credentials, tokens, and any unregistered root.

- [ ] **Step 4: Add deterministic projection transforms**

Implement named transforms in the contract instead of executable expressions:

```text
semantic_primary_name(locale)
semantic_weighted_names(locale, separator=",")
asset_scope_to_is_single
null_semantics_to_empty_string
```

The manifest keeps Canonical structured values and the rendered compatibility row so both can be hashed and audited.

- [ ] **Step 5: Keep environments local/test only**

Register built-in local table aliases for the two dry-run outputs. Do not add MySQL/Postgres credentials, network clients, SQL execution, or production environment values.

- [ ] **Step 6: Run projection tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_projection_contracts.py tests/test_unified_label_platform.py -q`

Expected: all tests pass; reconciliation drift leaves `PublishedLabel` and `SemanticTagFact` unchanged.

- [ ] **Step 7: Commit Task 8**

```bash
git add backend/app/projection_contracts.py backend/app/main.py backend/tests/test_projection_contracts.py
git commit -m "feat: add semantic tag dry run projections"
```

### Task 9: Build the list-first contract management UI

**Files:**
- Create: `frontend/src/pages/tag-demand-contracts-page.tsx`
- Create: `frontend/src/components/tag-demand-contract-drawer.tsx`
- Create: `frontend/scripts/check-tag-demand-contract.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/system-management-page.tsx`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: Task 3 contract APIs.
- Produces: `/workflow/governance/tag-demand-contracts` list view and detail drawer.

- [ ] **Step 1: Add TypeScript contract types and API methods**

```typescript
export type SemanticApplicability = "required" | "optional" | "not_applicable"

export type TagDemandContract = {
  id: number
  contract_key: string
  version: number
  status: "draft" | "candidate" | "active" | "retired"
  contract_hash: string
  definition: TagDemandContractDefinition
  created_by: string
  created_at: string
}
```

Add `listTagDemandContracts`, `getTagDemandContract`, `createTagDemandContract`, and `activateTagDemandContract` methods. Activation remains a deliberate admin action with a confirmation dialog.

- [ ] **Step 2: Write the static frontend contract check first**

The script must assert the route exists, the system-management entry exists, field details are rendered by `TagDemandContractDrawer`, the page does not render every field matrix inline, and the active version/status/hash are visible in the primary list.

- [ ] **Step 3: Run the check and confirm red**

Run: `cd frontend && node --experimental-strip-types scripts/check-tag-demand-contract.ts`

Expected: fails because route/page/drawer do not exist.

- [ ] **Step 4: Build the dense list page**

Primary columns: contract name, version, status, platform field count, applicable category count, execution variants, quality gate summary, updated time, actions. Actions: view details, clone new version, activate candidate. Do not place full vocabularies, mappings, JSON, history, or projection manifests on the first level.

- [ ] **Step 5: Build the detail drawer**

Sections: platform fields, category applicability matrix, execution variants, null semantics, quality thresholds, projection mapping, provenance/version evidence, and activation audit. Use a wide desktop drawer; no mobile layout branch.

- [ ] **Step 6: Add route and regular management entry**

Add the route to `App.tsx` and a normal entry under system governance. Do not add a second top-level navigation shortcut.

- [ ] **Step 7: Run frontend checks**

Run: `cd frontend && npm run contract:tag-demand && npm run lint && npm run build`

Expected: all commands exit 0; only the pre-existing Vite chunk-size warning is allowed.

- [ ] **Step 8: Commit Task 9**

```bash
git add frontend/src/pages/tag-demand-contracts-page.tsx frontend/src/components/tag-demand-contract-drawer.tsx frontend/scripts/check-tag-demand-contract.ts frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/App.tsx frontend/src/pages/system-management-page.tsx frontend/package.json
git commit -m "feat: add tag demand contract management ui"
```

### Task 10: Surface semantic quality evidence in category and regression workspaces

**Files:**
- Modify: `frontend/src/pages/category-evaluation-v3-config-page.tsx`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/semantic-quality-drawer.tsx`
- Modify: `frontend/scripts/check-tag-demand-contract.ts`

**Interfaces:**
- Consumes: Task 4 applicability summary and Task 7 semantic metrics API.
- Produces: compact applicability summary on the v3 config page and field-quality drawer on baseline regression.

- [ ] **Step 1: Extend the frontend contract check**

Assert the category page shows contract version plus required/optional/not-applicable counts, and baseline regression keeps `baseline-five-level-confusion-matrix`, the three aggregate bands, and a separate “语义字段质量” drawer entry.

- [ ] **Step 2: Run the check and confirm red**

Run: `cd frontend && npm run contract:tag-demand`

Expected: fails because the summary and drawer entry are missing.

- [ ] **Step 3: Add the category applicability summary**

Display only contract version/hash prefix, field counts, and “查看字段合同” action. Do not duplicate the editable field matrix inside v3 mechanism configuration.

- [ ] **Step 4: Add semantic quality drawer to baseline regression**

Show overall macro/micro Precision/Recall and a field table with Precision, Recall, mapping coverage, unmapped rate, conflict rate, null-semantics accuracy, correction rate, and review coverage. Zero denominators display `—`.

- [ ] **Step 5: Preserve current level metrics**

Do not remove or rename exact-level accuracy, adjacent-level accuracy, recommendation `L1/L2`, regular `L3/L4`, filter `L5`, or the complete 5×5 L1–L5 matrix.

- [ ] **Step 6: Run frontend verification**

Run: `cd frontend && npm run contract:tag-demand && npm run test:baseline-level-metrics && npm run lint && npm run build`

Expected: all commands exit 0.

- [ ] **Step 7: Commit Task 10**

```bash
git add frontend/src/pages/category-evaluation-v3-config-page.tsx frontend/src/pages/baseline-regression-page.tsx frontend/src/components/semantic-quality-drawer.tsx frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/scripts/check-tag-demand-contract.ts
git commit -m "feat: show semantic quality evidence"
```

### Task 11: Implement the domestic 3D/SU vertical slice with local fixtures

**Files:**
- Create: `backend/tests/test_model_3d_su_semantic_slice.py`
- Modify: `backend/app/seed.py`
- Modify: `backend/app/model_3d_su_seed.py`
- Modify: `backend/tests/test_model_3d_su_seed.py`
- Create: `backend/fixtures/semantic/model_3d_su_domestic_v1.json`

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces: one local draft demand-contract seed plus an explicit admin-activation fixture and whole/single dry-run flow for `category_key=model_3d_su`.

- [ ] **Step 1: Write end-to-end local slice tests**

```python
@pytest.mark.parametrize(("asset_scope", "expected_is_single"), [("whole", 0), ("single", 1)])
def test_domestic_3d_su_slice_reaches_dry_run_projection(asset_scope, expected_is_single, client):
    result = run_fixture_slice(client, asset_scope=asset_scope)
    assert result["route"]["category_key"] == "model_3d_su"
    assert result["published_label"]["schema_version"] == "published-label-v2"
    assert result["projection_row"]["is_single"] == expected_is_single
    assert result["reconciliation"]["status"] == "matched"


def test_3d_su_quality_extension_does_not_replace_platform_semantic_fields(client):
    result = run_fixture_slice(client, asset_scope="whole")
    assert "semantic" in result["published_label"]
    assert "quality" in result["published_label"]
    assert result["published_label"]["quality"]["level"] in {"L1", "L2", "L3", "L4"}
```

- [ ] **Step 2: Confirm red**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_model_3d_su_semantic_slice.py -q`

Expected: fixture/seed/semantic flow is missing.

- [ ] **Step 3: Add a minimal deterministic fixture**

Include two local assets: one whole-space and one single-object sample. Each fixture includes stable asset SHA-256, model evidence, mapping inputs, human-approved truth, expected platform fields, and expected domestic projection row. Do not include real business data or provider credentials.

- [ ] **Step 4: Seed only the platform contract and category applicability**

Seed an operator-reviewable draft local contract with the nine platform fields and `title`. The test fixture must activate it through the explicit admin API after sign-off fields are present; the seed itself must never activate a candidate. Reuse the existing `model_3d_su` prompt/v3 mechanism seed; do not clone prompt/model/version management.

- [ ] **Step 5: Run the full slice through existing release/projection services**

The test helper must call the same service functions used by API routes: route, normalize, map, approve fact, create release, approve/publish, build manifest, local reconcile. It must not insert `PublishedLabel` or `LocalProjectionRow` directly.

- [ ] **Step 6: Run 3D/SU and platform regression tests**

Run: `cd backend && PYTHONPATH=. python -m pytest tests/test_model_3d_su_semantic_slice.py tests/test_model_3d_su_seed.py tests/test_semantic_tag_contracts.py tests/test_projection_contracts.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 11**

```bash
git add backend/tests/test_model_3d_su_semantic_slice.py backend/app/seed.py backend/app/model_3d_su_seed.py backend/tests/test_model_3d_su_seed.py backend/fixtures/semantic/model_3d_su_domestic_v1.json
git commit -m "feat: add domestic 3d su semantic slice"
```

### Task 12: Final verification, browser acceptance and status handoff

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/discussion/tpeng-labellab-gap-register-20260813.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified local implementation, explicit external-integration Gap list, no deployment or external write.

- [ ] **Step 1: Run backend focused suites**

Run:

```bash
cd backend
PYTHONPATH=. python -m pytest \
  tests/test_semantic_tag_contracts.py \
  tests/test_asset_version_and_tag_contract_models.py \
  tests/test_tag_demand_contract_api.py \
  tests/test_semantic_execution_routing.py \
  tests/test_semantic_tag_mapping.py \
  tests/test_semantic_tag_fact_governance.py \
  tests/test_semantic_tag_quality.py \
  tests/test_model_3d_su_semantic_slice.py \
  tests/test_projection_contracts.py -q
```

Expected: exit 0.

- [ ] **Step 2: Run complete backend suite**

Run: `cd backend && TEST_DATA_DIR=$(mktemp -d) && DATA_DIR="$TEST_DATA_DIR" PYTHONPATH=. python -m pytest tests -q`

Expected: exit 0; only pre-existing dependency warnings are allowed.

- [ ] **Step 3: Run frontend verification**

Run: `cd frontend && npm run contract:tag-demand && npm run contract:information-architecture && npm run test:baseline-level-metrics && npm run lint && npm run build`

Expected: exit 0; only the existing Vite main-chunk warning is allowed.

- [ ] **Step 4: Verify migration integrity on a copied SQLite database**

Run migrations against a temporary copy, then execute `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and query the latest `schema_migrations` row. Expected: `ok`, zero foreign-key rows, and the new semantic contract migration present exactly once.

- [ ] **Step 5: Perform desktop browser acceptance**

At `1440×900` and `1280×720`, verify:

```text
系统管理 → 字段需求合同列表 → 详情抽屉
类目评测 v3 → 语义适用性摘要 → 字段合同
存量回归 → 原有 L1–L5 指标 → 语义字段质量抽屉
投影治理 → 国内/海外 dry-run manifest → 对账结果
```

Acceptance: no white screen, no horizontal document overflow, no compressed single-character columns, no console error, and no duplicated top-level entry.

- [ ] **Step 6: Verify forbidden side effects**

Inspect database and logs. Expected:

```text
external_writes_enabled = false
no queued/processing real evaluation job
no running stock rerun
no automatic mechanism activation
no automatic label publication
no external database connection
no real model invocation
```

- [ ] **Step 7: Update project status and Gap register**

Record completed local capabilities, exact test/build/browser results, migration number, commits, and these remaining gated items:

```text
standard vocabularies and entity owners
0.7/0.5/0.3 relative-importance semantics and maximum value counts
real upstream asset-version events
domestic/overseas database table contracts and least-privilege accounts
knowledge-graph consumer SLA and badcase callback
overseas model/language/cost acceptance
production canary, rollback and observation plan
```

- [ ] **Step 8: Run repository checks**

Run: `git diff --check && git status --short && git log --oneline -15`

Expected: no whitespace errors; only intended files are changed.

- [ ] **Step 9: Commit Task 12**

```bash
git add PROJECT_STATUS.md docs/discussion/tpeng-labellab-gap-register-20260813.md
git commit -m "docs: record semantic tag contract verification"
```

## Release, rollback and stop conditions

- This plan does not authorize push, merge, deployment, external database writes, real model calls, or production activation.
- Before any later Codeup merge, rebase/merge the latest combined `origin/main`, rerun full backend/frontend verification, and use the repository's protected deployment flow.
- Database rollback is restore-from-snapshot plus code rollback; never delete semantic rows or rewrite immutable contract history.
- Stop immediately if a projection can mutate Canonical facts, a candidate/experiment becomes consumer-visible, a contract activation triggers label publication, version hashes cannot reconcile, or an external write path appears before its separate authorization.
