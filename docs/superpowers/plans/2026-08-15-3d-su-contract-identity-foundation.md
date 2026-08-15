# 3D/SU Field Contract and Asset Identity Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 2026-08-21 前交付 3D/SU 真实标签闭环的首个可执行底座：字段供给合同 v2、可验证资产身份、只读唯一性探查包和关闭失败的内容接入门禁。

**Architecture:** 保留现有 `content-ingress-v1` 和 `tag-demand-contract-v1` 的兼容行为，新建 v2 合同与身份解析纯模块。真实 `content_key` 只有在只读探查结果经过人工批准后生成；未签认、冲突或漂移的身份事件可以留存审计证据，但不能创建增量素材包或进入标签生产。当前批次只提供 SQL 生成器，不连接 DataWorks、不执行 SQL、不写外部数据库。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、SQLite 启动迁移、Pytest、React 19、TypeScript、TanStack Query、Vite。

## Global Constraints

- 基线固定为 `origin/main@850508a38240aa3108b2e59a3dc94fc4a1c90a09`。
- 工作分支固定为 `codex/tpeng-label-reconstruction-kickoff-20260815`，不得修改旧 `codex/label-mechanism-v1` 工作树。
- 本计划只覆盖 2026-08-17 至 2026-08-21 的字段合同和资产身份子项目；脚本运行时、完整标签生产、真实投影和 Badcase 回流分别编写后续计划。
- 3D/SU 外部身份候选键为 `source_system + res_type + ll_id`；`res_type=1` 表示 3D，`res_type=6` 表示 SU。
- `content_key` 只能在唯一性证据状态为 `verified` 时生成；未验证数据不得假定唯一。
- `ll_id + res_type` 若存在重复或映射多个 `res_id`，状态必须为 `conflict`，不得自动追加随机后缀规避冲突。
- 字段质量默认门槛保持 Precision ≥ 0.80、Recall ≥ 0.70、映射覆盖率 ≥ 0.90、冲突率 ≤ 0.10。
- 单体素材的 `space` 字段必须通过执行变体覆盖为 `not_applicable`，不能沿用 whole 素材的 `required`。
- 机制发布轴和标签事实发布轴继续独立；合同激活不启动模型、不发布标签、不执行投影。
- 不执行 DataWorks SQL、不申请权限、不调用真实模型、不写外部数据库、不推送 Codeup、不创建 MR、不合并、不部署。
- 管理员不能通过页面上传或执行 Python、JavaScript、SQL 或 Shell；本计划中的 SQL 工具只生成文本和哈希。
- 正式界面只验收桌面 `1440×900` 和 `1280×720`，不新增移动端需求。
- 后端测试从仓库根运行：`PYTHONPATH=.:backend <python> -m pytest backend/tests -q`，避免根目录 `scripts/` 与 `backend/app` 路径缺失。

---

## File Structure

### New backend files

- `backend/app/asset_identity.py`：3D/SU 身份输入、验证证据和确定性 `content_key` 解析纯函数。
- `backend/app/source_identity_probe.py`：生成只读 DataWorks 唯一性探查 SQL 和确定性查询哈希，不建立连接。
- `backend/tests/test_asset_identity.py`：身份格式、未验证、冲突、确定性哈希和内容键测试。
- `backend/tests/test_source_identity_probe.py`：表名安全、SQL 内容和哈希稳定性测试。
- `backend/tests/test_content_ingress_v2_identity.py`：v2 接入、人工签认、路由阻断、重复事件和冲突测试。

### New script and documentation files

- `scripts/generate_3d_su_identity_probe.py`：将探查包以 JSON 输出到 stdout；不读取凭据、不访问网络。
- `docs/contracts/3d-su-source-identity-v1.md`：源表、候选键、探查口径、人工签认和停止条件。
- `docs/contracts/3d-su-field-supply-v1.md`：逐字段供给路径、whole/single 适用性、Owner、SLA、质量门槛和回退口径。
- `frontend/src/components/content-identity-drawer.tsx`：内容接入身份证据二级抽屉。
- `frontend/scripts/check-content-identity-contract.ts`：前端身份和合同展示静态契约。

### Existing files to modify

- `backend/app/semantic_tag_contracts.py`：增加 v2 字段供给、源身份和变体级适用性合同。
- `backend/app/model_3d_su_category_seed.py`：生成不可自动激活的 3D/SU 合同 v2 草稿。
- `backend/app/models.py`：新增源身份签认模型和内容记录身份列。
- `backend/app/migrations/runner.py`：增加 migration 70，兼容旧 SQLite 数据库。
- `backend/app/label_governance.py`：保留 v1；增加 v2 身份冻结和路由门禁。
- `backend/app/main.py`：扩展请求/响应类型，增加身份签认 API 和 v2 集成状态。
- `backend/tests/test_semantic_tag_contracts.py`：v1 兼容与 v2 合同验证。
- `backend/tests/test_model_3d_su_semantic_slice.py`：whole/single 字段适用性和合同草稿版本。
- `backend/tests/test_asset_version_and_tag_contract_models.py`：migration 70、不可变证据和唯一索引。
- `backend/tests/test_content_ingress_incremental_routing.py`：证明 v1 路由完全不变。
- `backend/tests/test_tag_demand_contract_api.py`：v2 候选创建、未签认激活阻断和签认后激活。
- `frontend/src/lib/types.ts`：v2 合同和内容身份响应类型。
- `frontend/src/lib/api.ts`：身份签认和内容记录 API。
- `frontend/src/components/tag-demand-contract-drawer.tsx`：展示供给路径、身份合同和变体覆盖。
- `frontend/src/pages/tag-demand-contracts-page.tsx`：增加“身份签认状态”列。
- `frontend/src/pages/incremental-workspace-page.tsx`：展示最近接入身份状态并打开详情抽屉。
- `frontend/package.json`：登记 `contract:content-identity`。
- `PROJECT_STATUS.md`：记录本批实现、验证、未授权外部动作和下一计划。

---

### Task 1: Add versioned field-supply and source-identity contract types

**Files:**
- Modify: `backend/app/semantic_tag_contracts.py`
- Modify: `backend/tests/test_semantic_tag_contracts.py`

**Interfaces:**
- Consumes: existing `SemanticFieldDefinition`, `ExecutionVariant`, `TagDemandContractDefinition`, `validate_tag_demand_contract()`.
- Produces: `SourceIdentityContract`, `FieldSupplyDefinition`, `ExecutionVariant.field_applicability_overrides`, and v1/v2 compatible `TagDemandContractDefinition`.

- [ ] **Step 1: Add failing v2 contract fixtures**

Add this helper beside `valid_contract()` in `backend/tests/test_semantic_tag_contracts.py`:

```python
def valid_contract_v2() -> dict:
    contract = valid_contract()
    contract["schema_version"] = "tag-demand-contract-v2"
    contract["source_identity"] = {
        "source_system": "aliyun_3d66_dw",
        "object_grain": "asset",
        "identity_fields": ["res_type", "ll_id"],
        "optional_disambiguator": "res_id",
        "version_field": "dt",
        "deletion_field": "is_delete",
        "uniqueness_status": "unverified",
        "verification_evidence_hash": None,
    }
    contract["field_supply"] = {
        key: {
            "field_key": key,
            "fact_namespace": "semantic",
            "object_grain": "asset",
            "production_method": "model" if key != "title" else "source_direct",
            "source_authority": "tpeng-label-platform",
            "owner": "tpeng-semantic-platform",
            "freshness_sla_hours": 24,
            "null_semantics": ["not_applicable", "not_detected", "unknown"],
            "rollback_strategy": "previous_release",
        }
        for key in contract["semantic_schema"]["fields"]
    }
    contract["execution_variants"].append({
        "site_scope": "domestic",
        "asset_scope": "single",
        "locale": "zh",
        "category_key": "model_3d_su",
        "prompt_variant": "single",
        "prompt_version": "prompt-single-v1",
        "model_version": "model-v1",
        "field_applicability_overrides": {"space": "not_applicable"},
    })
    return contract
```

Add exact assertions:

```python
def test_v2_contract_freezes_source_identity_and_field_supply() -> None:
    parsed = validate_tag_demand_contract(valid_contract_v2())
    assert parsed.schema_version == "tag-demand-contract-v2"
    assert parsed.source_identity.identity_fields == ("res_type", "ll_id")
    assert parsed.field_supply["style"].production_method == "model"
    assert parsed.execution_variants[1].field_applicability_overrides["space"] == "not_applicable"


def test_v2_contract_requires_supply_metadata_for_every_field() -> None:
    contract = valid_contract_v2()
    del contract["field_supply"]["material"]
    with pytest.raises(SemanticTagContractError, match="material.*供给路径"):
        validate_tag_demand_contract(contract)


def test_v2_verified_identity_requires_evidence_hash() -> None:
    contract = valid_contract_v2()
    contract["source_identity"]["uniqueness_status"] = "verified"
    with pytest.raises(SemanticTagContractError, match="verification_evidence_hash"):
        validate_tag_demand_contract(contract)


def test_v1_contract_remains_valid_without_v2_fields() -> None:
    assert validate_tag_demand_contract(valid_contract()).schema_version == "tag-demand-contract-v1"
```

- [ ] **Step 2: Run the focused tests and confirm red**

Run:

```bash
cd /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/tpeng-label-reconstruction-kickoff-20260815
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_semantic_tag_contracts.py -q
```

Expected: FAIL because v2 schema, `source_identity`, `field_supply`, and variant overrides are not defined.

- [ ] **Step 3: Implement immutable v2 contract types**

Add these exact public shapes in `backend/app/semantic_tag_contracts.py`:

```python
class SourceIdentityContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str = Field(min_length=1, max_length=120)
    object_grain: Literal["asset"]
    identity_fields: tuple[Literal["res_type", "ll_id"], ...]
    optional_disambiguator: Literal["res_id"] | None = None
    version_field: str = Field(min_length=1, max_length=80)
    deletion_field: str = Field(min_length=1, max_length=80)
    uniqueness_status: Literal["unverified", "verified", "conflict"]
    verification_evidence_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FieldSupplyDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_key: str
    fact_namespace: Literal["semantic", "quality", "governance"]
    object_grain: Literal["asset", "image", "text_fragment"]
    production_method: Literal["source_direct", "rule", "model", "human", "hybrid"]
    source_authority: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=120)
    freshness_sla_hours: int = Field(ge=1, le=8760)
    null_semantics: tuple[Literal["not_applicable", "not_detected", "unknown", "empty_valid"], ...]
    rollback_strategy: Literal["previous_release", "compensation_release"]
```

Update existing types:

```python
class ExecutionVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site_scope: Literal["domestic", "overseas"]
    asset_scope: Literal["whole", "single", "other", "unknown"]
    locale: Literal["zh", "en"]
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    prompt_variant: Literal["whole", "single"]
    prompt_version: str
    model_version: str
    field_applicability_overrides: dict[str, SemanticApplicability] = Field(
        default_factory=dict
    )


class TagDemandContractDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["tag-demand-contract-v1", "tag-demand-contract-v2"]
    semantic_schema: SemanticTagSchema
    category_applicability: dict[str, dict[str, SemanticApplicability]]
    execution_variants: tuple[ExecutionVariant, ...]
    quality_gates: dict[str, FieldQualityGate]
    projection_targets: tuple[ProjectionTargetDefinition, ...]
    source_identity: SourceIdentityContract | None = None
    field_supply: dict[str, FieldSupplyDefinition] = Field(default_factory=dict)
```

In `_validate_platform_contract()` enforce all of the following only for v2:

- every semantic field has exactly one matching `field_supply` row;
- no supply row references an undeclared field;
- `identity_fields` is exactly `("res_type", "ll_id")` for the 3D/SU contract;
- `verified` requires a 64-character evidence hash;
- `conflict` cannot carry a verification evidence hash;
- override fields must exist in the semantic schema;
- v1 remains byte-for-byte hash compatible.

Freeze `identity_fields`, `null_semantics`, `field_supply`, and variant overrides before returning.

- [ ] **Step 4: Run focused tests and confirm green**

Run the same command from Step 2.

Expected: all `test_semantic_tag_contracts.py` tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/semantic_tag_contracts.py backend/tests/test_semantic_tag_contracts.py
git commit -m "feat: version tag supply and source identity contracts"
```

---

### Task 2: Implement deterministic 3D/SU asset identity resolution

**Files:**
- Create: `backend/app/asset_identity.py`
- Create: `backend/tests/test_asset_identity.py`

**Interfaces:**
- Consumes: approved identity evidence from Task 4 as `IdentityVerificationEvidence`.
- Produces: `resolve_three_d_su_identity(source_system: str, payload: Mapping[str, Any], verification: IdentityVerificationEvidence | None) -> ResolvedAssetIdentity`.

- [ ] **Step 1: Write the failing pure-function tests**

Create `backend/tests/test_asset_identity.py`:

```python
from __future__ import annotations

import pytest

from app.asset_identity import (
    AssetIdentityError,
    IdentityVerificationEvidence,
    resolve_three_d_su_identity,
)


def _verified() -> IdentityVerificationEvidence:
    return IdentityVerificationEvidence(
        source_system="aliyun_3d66_dw",
        key_fields=("res_type", "ll_id"),
        status="verified",
        evidence_hash="a" * 64,
    )


def test_verified_3d_identity_builds_deterministic_content_key() -> None:
    result = resolve_three_d_su_identity(
        source_system="aliyun_3d66_dw",
        payload={"res_type": 1, "ll_id": "12345", "res_id": "r-9"},
        verification=_verified(),
    )
    assert result.content_key == "aliyun_3d66_dw:1:12345"
    assert result.identity_status == "verified"
    assert len(result.identity_hash) == 64


def test_unverified_identity_never_builds_content_key() -> None:
    result = resolve_three_d_su_identity(
        source_system="aliyun_3d66_dw",
        payload={"res_type": 6, "ll_id": "su-1"},
        verification=None,
    )
    assert result.content_key is None
    assert result.identity_status == "pending_verification"


@pytest.mark.parametrize("res_type", [0, 2, 9, "1"])
def test_unsupported_or_untyped_res_type_is_rejected(res_type) -> None:
    with pytest.raises(AssetIdentityError, match="res_type"):
        resolve_three_d_su_identity(
            source_system="aliyun_3d66_dw",
            payload={"res_type": res_type, "ll_id": "123"},
            verification=_verified(),
        )


def test_conflict_evidence_blocks_identity() -> None:
    evidence = _verified().model_copy(update={"status": "conflict"})
    with pytest.raises(AssetIdentityError, match="冲突"):
        resolve_three_d_su_identity(
            source_system="aliyun_3d66_dw",
            payload={"res_type": 1, "ll_id": "123"},
            verification=evidence,
        )
```

- [ ] **Step 2: Run tests and confirm red**

```bash
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_asset_identity.py -q
```

Expected: import failure for `app.asset_identity`.

- [ ] **Step 3: Implement the identity module**

Create these exact public types in `backend/app/asset_identity.py`:

```python
class AssetIdentityError(ValueError):
    pass


class IdentityVerificationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str
    key_fields: tuple[Literal["res_type", "ll_id"], ...]
    status: Literal["verified", "conflict"]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedAssetIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str
    res_type: Literal[1, 6]
    ll_id: str
    res_id: str | None
    content_key: str | None
    identity_status: Literal["pending_verification", "verified"]
    identity_hash: str


def resolve_three_d_su_identity(
    *,
    source_system: str,
    payload: Mapping[str, Any],
    verification: IdentityVerificationEvidence | None,
) -> ResolvedAssetIdentity:
    normalized_source = str(source_system).strip()
    if not normalized_source or len(normalized_source) > 120:
        raise AssetIdentityError("source_system 必须填写且长度不超过 120")

    raw_res_type = payload.get("res_type")
    if isinstance(raw_res_type, bool) or not isinstance(raw_res_type, int):
        raise AssetIdentityError("res_type 必须是整数 1 或 6")
    if raw_res_type not in (1, 6):
        raise AssetIdentityError("res_type 只支持 1（3D）或 6（SU）")

    ll_id = str(payload.get("ll_id", "")).strip()
    if not ll_id or len(ll_id) > 160:
        raise AssetIdentityError("ll_id 必须填写且长度不超过 160")
    raw_res_id = payload.get("res_id")
    res_id = None if raw_res_id is None else str(raw_res_id).strip()
    if res_id == "":
        res_id = None
    if res_id is not None and len(res_id) > 160:
        raise AssetIdentityError("res_id 长度不能超过 160")

    identity_payload = {
        "source_system": normalized_source,
        "res_type": raw_res_type,
        "ll_id": ll_id,
        "res_id": res_id,
    }
    canonical = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    identity_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    if verification is None:
        return ResolvedAssetIdentity(
            **identity_payload,
            content_key=None,
            identity_status="pending_verification",
            identity_hash=identity_hash,
        )
    if verification.source_system != normalized_source:
        raise AssetIdentityError("身份签认 source_system 与事件不一致")
    if verification.key_fields != ("res_type", "ll_id"):
        raise AssetIdentityError("身份签认 key_fields 必须为 res_type + ll_id")
    if verification.status == "conflict":
        raise AssetIdentityError("源身份唯一性存在冲突")

    return ResolvedAssetIdentity(
        **identity_payload,
        content_key=f"{normalized_source}:{raw_res_type}:{ll_id}",
        identity_status="verified",
        identity_hash=identity_hash,
    )
```

Implementation rules:

- require integer `res_type` exactly `1` or `6`;
- normalize `ll_id` and optional `res_id` with `str(value).strip()`, reject blank values;
- canonical identity payload is `{"source_system", "res_type", "ll_id", "res_id"}` sorted JSON;
- `identity_hash = sha256(canonical_json.encode("utf-8")).hexdigest()`;
- verification source and key fields must exactly match;
- unverified returns `content_key=None`;
- verified returns `f"{source_system}:{res_type}:{ll_id}"`;
- conflict raises `AssetIdentityError` before any persistence.

- [ ] **Step 4: Run tests and confirm green**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/asset_identity.py backend/tests/test_asset_identity.py
git commit -m "feat: resolve verified 3d su asset identities"
```

---

### Task 3: Generate a read-only DataWorks identity probe package

**Files:**
- Create: `backend/app/source_identity_probe.py`
- Create: `backend/tests/test_source_identity_probe.py`
- Create: `scripts/generate_3d_su_identity_probe.py`
- Create: `docs/contracts/3d-su-source-identity-v1.md`

**Interfaces:**
- Consumes: approved table identifier only; no credentials or clients.
- Produces: `build_three_d_su_identity_probe(table_name: str) -> SourceIdentityProbeBundle` and CLI JSON `{table_name, queries, probe_hash}`.

- [ ] **Step 1: Write failing SQL generator tests**

Create `backend/tests/test_source_identity_probe.py` with these assertions:

```python
import pytest

from app.source_identity_probe import SourceIdentityProbeError, build_three_d_su_identity_probe


def test_probe_contains_count_null_duplicate_and_res_id_queries() -> None:
    bundle = build_three_d_su_identity_probe("aliyun_3d66_dw.dim_res_info_union")
    assert set(bundle.queries) == {"scope", "nulls", "duplicates", "res_id_conflicts"}
    assert "res_type IN (1, 6)" in bundle.queries["scope"]
    assert "GROUP BY res_type, ll_id" in bundle.queries["duplicates"]
    assert "COUNT(DISTINCT res_id)" in bundle.queries["res_id_conflicts"]
    assert len(bundle.probe_hash) == 64


def test_probe_hash_is_stable() -> None:
    first = build_three_d_su_identity_probe("aliyun_3d66_dw.dim_res_info_union")
    second = build_three_d_su_identity_probe("aliyun_3d66_dw.dim_res_info_union")
    assert first.probe_hash == second.probe_hash


@pytest.mark.parametrize("table", ["x;DROP TABLE y", "x y", "`secret`", "x/../y"])
def test_probe_rejects_unsafe_identifiers(table: str) -> None:
    with pytest.raises(SourceIdentityProbeError, match="表名"):
        build_three_d_su_identity_probe(table)
```

- [ ] **Step 2: Run the test and confirm red**

```bash
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_source_identity_probe.py -q
```

Expected: import failure for `app.source_identity_probe`.

- [ ] **Step 3: Implement deterministic SQL generation**

Expose these types:

```python
class SourceIdentityProbeError(ValueError):
    pass


class SourceIdentityProbeBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    table_name: str
    queries: dict[str, str]
    probe_hash: str


def build_three_d_su_identity_probe(table_name: str) -> SourceIdentityProbeBundle:
    if not _TABLE_PATTERN.fullmatch(table_name):
        raise SourceIdentityProbeError("表名必须是 project.table 格式的安全标识符")
    queries = {
        "scope": (
            f"SELECT res_type, COUNT(*) AS row_count "
            f"FROM {table_name} "
            "WHERE res_type IN (1, 6) "
            "GROUP BY res_type ORDER BY res_type"
        ),
        "nulls": (
            "SELECT res_type, "
            "SUM(CASE WHEN ll_id IS NULL OR TRIM(CAST(ll_id AS STRING)) = '' THEN 1 ELSE 0 END) AS ll_id_blank_count, "
            "SUM(CASE WHEN res_id IS NULL OR TRIM(CAST(res_id AS STRING)) = '' THEN 1 ELSE 0 END) AS res_id_blank_count "
            f"FROM {table_name} WHERE res_type IN (1, 6) GROUP BY res_type ORDER BY res_type"
        ),
        "duplicates": (
            "SELECT res_type, ll_id, COUNT(*) AS row_count "
            f"FROM {table_name} "
            "WHERE res_type IN (1, 6) AND ll_id IS NOT NULL "
            "GROUP BY res_type, ll_id HAVING COUNT(*) > 1 "
            "ORDER BY row_count DESC, res_type, ll_id"
        ),
        "res_id_conflicts": (
            "SELECT res_type, ll_id, COUNT(DISTINCT res_id) AS res_id_count "
            f"FROM {table_name} "
            "WHERE res_type IN (1, 6) AND ll_id IS NOT NULL "
            "GROUP BY res_type, ll_id HAVING COUNT(DISTINCT res_id) > 1 "
            "ORDER BY res_id_count DESC, res_type, ll_id"
        ),
    }
    canonical = json.dumps(
        {"table_name": table_name, "queries": queries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return SourceIdentityProbeBundle(
        table_name=table_name,
        queries=queries,
        probe_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
```

The file imports `hashlib`, `json`, `re`, `BaseModel`, and `ConfigDict`, and defines `_TABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")`.

Accept only `^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$`. Generate exactly four `SELECT` statements:

1. counts grouped by `res_type` for `res_type IN (1, 6)`;
2. null/blank counts for `ll_id` and `res_id`;
3. duplicates grouped by `res_type, ll_id` with `HAVING COUNT(*) > 1`;
4. key-to-resource conflicts with `HAVING COUNT(DISTINCT res_id) > 1`.

Sort query keys before hashing canonical JSON. Do not import SQLAlchemy engines, HTTP clients, environment credentials, or DataWorks SDKs.

- [ ] **Step 4: Add a stdout-only CLI**

Create `scripts/generate_3d_su_identity_probe.py`:

```python
from __future__ import annotations

import argparse
import json

from backend.app.source_identity_probe import build_three_d_su_identity_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default="aliyun_3d66_dw.dim_res_info_union")
    args = parser.parse_args()
    bundle = build_three_d_su_identity_probe(args.table)
    print(json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The script must not accept a token, endpoint, SQL execution flag, or output path.

- [ ] **Step 5: Document the probe and approval contract**

In `docs/contracts/3d-su-source-identity-v1.md`, record:

- authoritative candidate table `aliyun_3d66_dw.dim_res_info_union`;
- `res_type=1/6` meanings;
- candidate key `res_type + ll_id`;
- required outputs for counts, blanks, duplicates and `res_id` conflicts;
- verification result is `verified` only when duplicates and conflicts are both zero for the signed data window;
- SQL execution requires separate read-only authorization;
- no production DML is part of the probe;
- evidence stored by hash and summary only, not credentials or full sensitive result dumps.

- [ ] **Step 6: Verify tests and CLI output**

```bash
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_source_identity_probe.py -q
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python scripts/generate_3d_su_identity_probe.py | python3 -m json.tool >/dev/null
```

Expected: tests pass and CLI emits valid JSON without network activity.

- [ ] **Step 7: Commit Task 3**

```bash
git add backend/app/source_identity_probe.py backend/tests/test_source_identity_probe.py scripts/generate_3d_su_identity_probe.py docs/contracts/3d-su-source-identity-v1.md
git commit -m "feat: generate read only 3d su identity probes"
```

---

### Task 4: Persist identity evidence without rewriting legacy records

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/tests/test_asset_version_and_tag_contract_models.py`

**Interfaces:**
- Consumes: identity hashes from Task 2 and probe hashes from Task 3.
- Produces: `SourceIdentityVerification` ORM rows plus explicit identity columns on `ContentRecord` and `ContentIngressEvent`.

- [ ] **Step 1: Write migration 70 persistence tests**

Extend `backend/tests/test_asset_version_and_tag_contract_models.py` with:

```python
def test_migration_70_preserves_legacy_content_records_as_unverified(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        record = ContentRecord(
            source_system="legacy",
            source_content_id="1",
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=datetime.now(timezone.utc),
            status="awaiting_material",
        )
        db.add(record)
        db.commit()
        assert record.identity_status == "legacy_unverified"
        assert record.content_key is None


def test_verified_content_key_is_unique_when_present(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        first = ContentRecord(
            source_system="aliyun_3d66_dw",
            source_content_id="source-1",
            content_key="aliyun_3d66_dw:1:12345",
            source_res_type=1,
            source_ll_id="12345",
            identity_status="verified",
            identity_hash="a" * 64,
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=now,
            status="awaiting_material",
        )
        second = ContentRecord(
            source_system="aliyun_3d66_dw",
            source_content_id="source-2",
            content_key="aliyun_3d66_dw:1:12345",
            source_res_type=1,
            source_ll_id="12345",
            identity_status="verified",
            identity_hash="b" * 64,
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=now,
            status="awaiting_material",
        )
        db.add(first)
        db.commit()
        db.add(second)
        with pytest.raises(IntegrityError, match="content_key"):
            db.commit()
    engine.dispose()
```

Add this second concrete test:

```python
def test_only_one_approved_identity_verification_exists_per_source_contract(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        def row(probe_hash: str) -> SourceIdentityVerification:
            return SourceIdentityVerification(
                contract_key="semantic-platform",
                source_system="aliyun_3d66_dw",
                key_fields_json='["res_type","ll_id"]',
                result="verified",
                probe_hash=probe_hash,
                data_window="2026-08-01/2026-08-15",
                scoped_row_count=100,
                duplicate_key_count=0,
                res_id_conflict_count=0,
                status="approved",
                created_by="test",
                approved_by="test",
                approved_at=datetime.now(timezone.utc),
            )
        db.add(row("c" * 64))
        db.commit()
        db.add(row("d" * 64))
        with pytest.raises(IntegrityError, match="source_identity_verifications"):
            db.commit()
    engine.dispose()
```

Add this raw legacy-upgrade test to `backend/tests/test_migration.py` so migration 70 runs against a table that predates the new ORM columns:

```python
def test_migration_70_upgrades_legacy_content_identity_without_backfill(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-identity.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE content_records (
                id INTEGER PRIMARY KEY,
                source_system VARCHAR(120) NOT NULL,
                source_content_id VARCHAR(160) NOT NULL,
                category_key VARCHAR(40) NOT NULL,
                source_version VARCHAR(120) NOT NULL,
                source_occurred_at DATETIME NOT NULL,
                asset_id INTEGER,
                status VARCHAR(30) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE(source_system, source_content_id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO content_records (
                id, source_system, source_content_id, category_key,
                source_version, source_occurred_at, status, created_at, updated_at
            ) VALUES (
                1, 'legacy', '42', 'model_3d_su',
                'v1', CURRENT_TIMESTAMP, 'awaiting_material', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        run_migrations(connection)
        row = connection.exec_driver_sql(
            "SELECT content_key, identity_status, identity_hash FROM content_records WHERE id = 1"
        ).one()
        assert row.content_key is None
        assert row.identity_status == "legacy_unverified"
        assert row.identity_hash is None
```

- [ ] **Step 2: Run migration tests and confirm red**

```bash
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_asset_version_and_tag_contract_models.py backend/tests/test_migration.py -q
```

Expected: model/column/migration assertions fail because migration 70 is absent.

- [ ] **Step 3: Add ORM fields and verification model**

Add `SourceIdentityVerification` to `backend/app/models.py`:

```python
class SourceIdentityVerification(Base):
    __tablename__ = "source_identity_verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_key: Mapped[str] = mapped_column(String(120), index=True)
    source_system: Mapped[str] = mapped_column(String(120), index=True)
    key_fields_json: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(20), index=True)  # verified/conflict
    probe_hash: Mapped[str] = mapped_column(String(64), index=True)
    data_window: Mapped[str] = mapped_column(String(120))
    scoped_row_count: Mapped[int] = mapped_column(Integer)
    duplicate_key_count: Mapped[int] = mapped_column(Integer)
    res_id_conflict_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True)  # draft/approved/superseded/rejected
    created_by: Mapped[str] = mapped_column(String(80))
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add checks for enumerations, non-negative counts and 64-character hash, plus a partial unique index for one approved row per `(contract_key, source_system)`.

Add nullable identity fields to `ContentRecord`: `content_key`, `source_res_type`, `source_ll_id`, `source_res_id`, `identity_status`, `identity_hash`, `identity_verification_id`.

Add frozen evidence fields to `ContentIngressEvent`: `identity_snapshot_json`, `identity_hash`, `identity_verification_id`.

- [ ] **Step 4: Implement migration 70**

Add `_migration_070_add_source_identity_verification()` and register it after migration 69. Requirements:

- create `source_identity_verifications` if absent;
- add all content identity columns only when missing;
- default existing `content_records.identity_status` to `legacy_unverified`;
- leave all legacy `content_key` values null;
- create partial unique index `uq_content_records_verified_key` on non-null `content_key`;
- create immutability triggers preventing changes to non-null `content_key`, `identity_hash`, and event identity snapshots;
- migration is idempotent across repeated startup.

- [ ] **Step 5: Run focused migration tests**

Run the Step 2 command.

Expected: all focused migration tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/app/models.py backend/app/migrations/runner.py backend/tests/test_asset_version_and_tag_contract_models.py backend/tests/test_migration.py
git commit -m "feat: persist source identity verification evidence"
```

---

### Task 5: Add identity approval API and fail-closed content-ingress v2

**Files:**
- Modify: `backend/app/label_governance.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_content_ingress_v2_identity.py`
- Modify: `backend/tests/test_content_ingress_incremental_routing.py`
- Modify: `backend/tests/test_tag_demand_contract_api.py`

**Interfaces:**
- Consumes: `resolve_three_d_su_identity()` and `SourceIdentityVerification`.
- Produces: `/api/source-identity-verifications`, `/approve`, `content-ingress-v2`, `routing_status=blocked_identity|packaged`.

- [ ] **Step 1: Write failing API and routing tests**

Create `backend/tests/test_content_ingress_v2_identity.py` using the same in-memory engine and token override pattern as `test_content_ingress_incremental_routing.py`.

Define these exact helpers first:

```python
def _v2_payload(
    *,
    event_id: str,
    asset_id: int,
    res_type: int = 1,
    ll_id: str = "12345",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "schema_version": "content-ingress-v2",
        "event_type": "content.created",
        "source_system": "aliyun_3d66_dw",
        "occurred_at": datetime(2026, 8, 18, tzinfo=timezone.utc).isoformat(),
        "payload": {
            "content_id": f"{res_type}:{ll_id}",
            "content_version": "2026-08-18",
            "category_key": "model_3d_su",
            "asset_id": asset_id,
            "res_type": res_type,
            "ll_id": ll_id,
            "res_id": f"res-{ll_id}",
        },
    }


def _verification_payload(*, result: str = "verified") -> dict[str, object]:
    return {
        "contract_key": "semantic-platform",
        "source_system": "aliyun_3d66_dw",
        "key_fields": ["res_type", "ll_id"],
        "result": result,
        "probe_hash": "a" * 64,
        "data_window": "2026-08-01/2026-08-15",
        "scoped_row_count": 100,
        "duplicate_key_count": 0 if result == "verified" else 1,
        "res_id_conflict_count": 0,
    }


def _create_v2_contract(client: TestClient) -> dict[str, object]:
    from tests.test_semantic_tag_contracts import valid_contract_v2

    response = client.post(
        "/api/tag-demand-contracts",
        json={
            "contract_key": "semantic-platform",
            "definition": valid_contract_v2(),
            "status": "draft",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve_identity(client: TestClient) -> dict[str, object]:
    _create_v2_contract(client)
    created = client.post(
        "/api/source-identity-verifications",
        json=_verification_payload(),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/api/source-identity-verifications/{created.json()['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    return approved.json()
```

`_fixture_model_3d_su()` must create an in-memory SQLite engine, run migrations, add an admin user and active `ModelConfig`, seed `model_3d_su`, add one `Asset(category_key="model_3d_su")`, override `get_db` and `current_user`, and return `(engine, db, client, asset)`. `_headers(monkeypatch)` must set a dedicated test `content_ingress_token` and return its Bearer header. `_close()` must clear dependency overrides, close the session and dispose the engine.

Then add these required tests:

```python
def test_v2_event_without_approved_identity_is_stored_but_not_packaged(monkeypatch) -> None:
    engine, db, client, asset = _fixture_model_3d_su()
    try:
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_v2_payload(event_id="evt-pending", asset_id=asset.id),
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"]["identity_status"] == "pending_verification"
        assert response.json()["content"]["content_key"] is None
        assert response.json()["routing_status"] == "blocked_identity"
        assert response.json()["material_package_id"] is None
    finally:
        _close(engine, db)


def test_admin_approval_allows_new_v2_event_to_package(monkeypatch) -> None:
    engine, db, client, asset = _fixture_model_3d_su()
    try:
        _approve_identity(client)
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_v2_payload(event_id="evt-verified", asset_id=asset.id),
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"]["content_key"] == "aliyun_3d66_dw:1:12345"
        assert response.json()["routing_status"] == "packaged"
    finally:
        _close(engine, db)


def test_conflict_probe_cannot_be_approved_as_verified() -> None:
    engine, db, client, _asset = _fixture_model_3d_su()
    try:
        _create_v2_contract(client)
        created = client.post(
            "/api/source-identity-verifications",
            json=_verification_payload(result="conflict"),
        )
        assert created.status_code == 201, created.text
        response = client.post(
            f"/api/source-identity-verifications/{created.json()['id']}/approve"
        )
        assert response.status_code == 409
    finally:
        _close(engine, db)


def test_same_v2_event_id_with_changed_identity_returns_409(monkeypatch) -> None:
    engine, db, client, asset = _fixture_model_3d_su()
    headers = _headers(monkeypatch)
    try:
        _approve_identity(client)
        first_payload = _v2_payload(
            event_id="evt-identity-drift",
            asset_id=asset.id,
            res_type=1,
            ll_id="12345",
        )
        second_payload = _v2_payload(
            event_id="evt-identity-drift",
            asset_id=asset.id,
            res_type=1,
            ll_id="99999",
        )
        first = client.post("/api/content-ingress/events", headers=headers, json=first_payload)
        second = client.post("/api/content-ingress/events", headers=headers, json=second_payload)
        assert first.status_code == 200, first.text
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "INGRESS_EVENT_CONFLICT"
    finally:
        _close(engine, db)
```

Extend the existing v1 test file with one assertion that its content key remains `upstream-sim:asset-1` and package behavior is unchanged.

- [ ] **Step 2: Run API tests and confirm red**

```bash
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_content_ingress_v2_identity.py backend/tests/test_content_ingress_incremental_routing.py backend/tests/test_tag_demand_contract_api.py -q
```

Expected: v2 schema and verification routes are missing.

- [ ] **Step 3: Add verification request models and endpoints**

In `backend/app/main.py`, add:

```python
class SourceIdentityVerificationCreateRequest(BaseModel):
    contract_key: str = Field(min_length=1, max_length=120)
    source_system: str = Field(min_length=1, max_length=120)
    key_fields: tuple[Literal["res_type", "ll_id"], ...]
    result: Literal["verified", "conflict"]
    probe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_window: str = Field(min_length=1, max_length=120)
    scoped_row_count: int = Field(ge=0)
    duplicate_key_count: int = Field(ge=0)
    res_id_conflict_count: int = Field(ge=0)
```

Add admin-only routes:

- `GET /api/source-identity-verifications`;
- `POST /api/source-identity-verifications` creates `draft` only;
- `POST /api/source-identity-verifications/{id}/approve` requires result `verified`, zero duplicate/conflict counts, exact key fields, and a matching candidate/draft v2 contract source identity;
- `POST /api/source-identity-verifications/{id}/reject` records actor and audit event.

When approving a new verification, mark the previous approved verification for the same `(contract_key, source_system)` as `superseded` in the same transaction. Approval must never execute SQL or activate a tag contract.

Add a version-append endpoint:

- `POST /api/tag-demand-contracts/{contract_id}/bind-source-identity-verification`;
- source contract must be v2 and `draft` or `candidate`;
- verification must be approved and match source system and key fields;
- create a new `candidate` contract version with `uniqueness_status=verified` and `verification_evidence_hash=probe_hash`;
- leave the source contract row byte-identical;
- return the newly appended candidate without activating it.

Add this assertion to `backend/tests/test_tag_demand_contract_api.py`:

```python
def _definition_v2() -> dict[str, object]:
    from tests.test_semantic_tag_contracts import valid_contract_v2

    return valid_contract_v2()


def _create_and_approve_identity_verification(client: TestClient) -> dict[str, object]:
    created = client.post(
        "/api/source-identity-verifications",
        json={
            "contract_key": "semantic-platform",
            "source_system": "aliyun_3d66_dw",
            "key_fields": ["res_type", "ll_id"],
            "result": "verified",
            "probe_hash": "a" * 64,
            "data_window": "2026-08-01/2026-08-15",
            "scoped_row_count": 100,
            "duplicate_key_count": 0,
            "res_id_conflict_count": 0,
        },
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/api/source-identity-verifications/{created.json()['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def test_binding_identity_verification_appends_candidate_without_mutating_source() -> None:
    with _context() as fixture:
        client = fixture["client"]
        app.dependency_overrides[current_user] = _as_user(fixture["admin"])
        draft = client.post(
            "/api/tag-demand-contracts",
            json={
                "contract_key": "semantic-platform",
                "definition": _definition_v2(),
                "status": "draft",
            },
        ).json()
        verification = _create_and_approve_identity_verification(client)
        bound = client.post(
            f"/api/tag-demand-contracts/{draft['id']}/bind-source-identity-verification",
            json={"verification_id": verification["id"]},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["version"] == draft["version"] + 1
        assert bound.json()["status"] == "candidate"
        source_identity = bound.json()["definition"]["source_identity"]
        assert source_identity["uniqueness_status"] == "verified"
        assert source_identity["verification_evidence_hash"] == verification["probe_hash"]
        original = client.get(f"/api/tag-demand-contracts/{draft['id']}").json()
        assert original["definition"]["source_identity"]["uniqueness_status"] == "unverified"
```

- [ ] **Step 4: Add `content-ingress-v2` compatibility**

Change `ContentIngressRequest.schema_version` to `Literal["content-ingress-v1", "content-ingress-v2"]`.

In `ingest_content_event()`:

- preserve current v1 parsing and content key behavior;
- for v2 `model_3d_su`, require `res_type`, `ll_id`, and optional `res_id` in payload;
- load the approved verification for the source and contract;
- call `resolve_three_d_su_identity()`;
- freeze identity JSON/hash/verification id on event;
- persist explicit identity fields on record;
- return `blocked_identity` before `route_content_event_to_incremental_package()` when `content_key is None`;
- return HTTP 409 for conflict evidence or duplicate event identity drift;
- never backfill legacy records as verified during request handling.

- [ ] **Step 5: Gate v2 contract activation on approved identity evidence**

In `activate_tag_demand_contract()` add this check only for `tag-demand-contract-v2`:

```python
if definition.source_identity.uniqueness_status != "verified":
    raise HTTPException(status_code=409, detail="源身份唯一性尚未签认")
approved = db.scalar(
    select(SourceIdentityVerification).where(
        SourceIdentityVerification.contract_key == contract.contract_key,
        SourceIdentityVerification.source_system == definition.source_identity.source_system,
        SourceIdentityVerification.status == "approved",
        SourceIdentityVerification.probe_hash == definition.source_identity.verification_evidence_hash,
    )
)
if approved is None:
    raise HTTPException(status_code=409, detail="字段合同引用的身份签认证据不存在或已失效")
if definition.source_identity.verification_evidence_hash != approved.probe_hash:
    raise HTTPException(status_code=409, detail="字段合同与身份签认证据不一致")
```

v1 activation remains unchanged.

- [ ] **Step 6: Run focused API tests**

Run the Step 2 command.

Expected: all focused tests pass and v1 behavior remains green.

- [ ] **Step 7: Commit Task 5**

```bash
git add backend/app/label_governance.py backend/app/main.py backend/tests/test_content_ingress_v2_identity.py backend/tests/test_content_ingress_incremental_routing.py backend/tests/test_tag_demand_contract_api.py
git commit -m "feat: gate 3d su ingress on verified identities"
```

---

### Task 6: Generate a safe 3D/SU contract v2 draft with single-asset applicability

**Files:**
- Modify: `backend/app/model_3d_su_category_seed.py`
- Modify: `backend/tests/test_model_3d_su_semantic_slice.py`
- Modify: `backend/tests/test_model_3d_su_seed.py`
- Create: `docs/contracts/3d-su-field-supply-v1.md`

**Interfaces:**
- Consumes: v2 contract models from Task 1.
- Produces: deterministic draft `model-3d-su-semantic` v2 contract; never automatically active.

- [ ] **Step 1: Write failing seed and applicability tests**

Add assertions:

```python
def test_model_3d_su_contract_v2_declares_source_and_supply_paths() -> None:
    contract = build_model_3d_su_semantic_contract()
    assert contract["schema_version"] == "tag-demand-contract-v2"
    assert contract["source_identity"]["identity_fields"] == ["res_type", "ll_id"]
    assert contract["source_identity"]["uniqueness_status"] == "unverified"
    assert set(contract["field_supply"]) == set(contract["semantic_schema"]["fields"])


def test_single_variant_marks_space_not_applicable() -> None:
    contract = build_model_3d_su_semantic_contract()
    single = next(item for item in contract["execution_variants"] if item["asset_scope"] == "single")
    whole = next(item for item in contract["execution_variants"] if item["asset_scope"] == "whole")
    assert single["field_applicability_overrides"]["space"] == "not_applicable"
    assert whole["field_applicability_overrides"] == {}


def test_seed_appends_v2_draft_without_activating_or_overwriting_v1() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        seed_model_3d_su(db, settings)
        db.commit()
        first_rows = db.scalars(
            select(TagDemandContract)
            .where(TagDemandContract.contract_key == MODEL_3D_SU_SEMANTIC_CONTRACT_KEY)
            .order_by(TagDemandContract.version)
        ).all()
        assert len(first_rows) == 1
        assert first_rows[0].status == "draft"
        assert json.loads(first_rows[0].definition_json)["schema_version"] == "tag-demand-contract-v2"
        first_hash = first_rows[0].contract_hash

        seed_model_3d_su(db, settings)
        db.commit()
        second_rows = db.scalars(
            select(TagDemandContract)
            .where(TagDemandContract.contract_key == MODEL_3D_SU_SEMANTIC_CONTRACT_KEY)
            .order_by(TagDemandContract.version)
        ).all()
        assert len(second_rows) == 1
        assert second_rows[0].contract_hash == first_hash
        assert second_rows[0].approved_by is None
```

Import `MODEL_3D_SU_SEMANTIC_CONTRACT_KEY` from `app.model_3d_su_category_seed` for this test.

- [ ] **Step 2: Run focused tests and confirm red**

```bash
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest backend/tests/test_model_3d_su_semantic_slice.py backend/tests/test_model_3d_su_seed.py -q
```

Expected: contract remains v1 and single `space` is not overridden.

- [ ] **Step 3: Build the v2 draft**

Update `build_model_3d_su_semantic_contract()` to include:

- v2 source identity with `uniqueness_status=unverified` and no evidence hash;
- a `field_supply` row for every field;
- whole and single execution variants;
- `space=not_applicable` only in the single variant override;
- existing quality gates unchanged;
- projection target remains `dry_run`.

Replace the current “return any existing row” seed behavior with deterministic hash lookup:

- if an identical v2 row exists, reuse it;
- if only v1 exists, append v2 as `draft`;
- never retire or activate an existing contract;
- never rewrite operator-owned rows;
- repeated startup does not add versions.

- [ ] **Step 4: Run focused tests and confirm green**

Before running tests, create `docs/contracts/3d-su-field-supply-v1.md` with one row for each current platform field:

| Field | Namespace | Whole | Single | Production method | Authority | Owner | SLA | Default gate | Rollback |
|---|---|---|---|---|---|---|---|---|---|
| `space` | semantic | required | not_applicable | hybrid | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `object` | semantic | required | required | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `style` | semantic | required | required | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `material` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `structural_features` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `architectural_element` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `soft_decoration` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `hard_decoration` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `color` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `title` | semantic | optional | optional | source_direct | upstream source + label platform normalization | content data Owner | 24h | P≥0.80/R≥0.70 | previous release |

The document must also state that these are the currently implemented fields, not the final downstream sign-off; any added field requires a new contract version, and any weaker gate requires Owner approval.

Run the Step 2 command.

Expected: all focused tests pass.

- [ ] **Step 5: Commit Task 6**

```bash
git add backend/app/model_3d_su_category_seed.py backend/tests/test_model_3d_su_semantic_slice.py backend/tests/test_model_3d_su_seed.py docs/contracts/3d-su-field-supply-v1.md
git commit -m "feat: draft 3d su supply contract v2"
```

---

### Task 7: Expose identity and field-supply evidence in the desktop UI

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/tag-demand-contract-drawer.tsx`
- Modify: `frontend/src/pages/tag-demand-contracts-page.tsx`
- Create: `frontend/src/components/content-identity-drawer.tsx`
- Modify: `frontend/src/pages/incremental-workspace-page.tsx`
- Create: `frontend/scripts/check-content-identity-contract.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: v2 contract payload and content record payload from Task 5.
- Produces: desktop-only read/approval evidence; no SQL execution or contract auto-activation controls.

- [ ] **Step 1: Add a failing static frontend contract**

Create `frontend/scripts/check-content-identity-contract.ts` to read source files and assert these literal contracts exist:

```typescript
assertContains(types, 'uniqueness_status: "unverified" | "verified" | "conflict"')
assertContains(types, 'identity_status: "legacy_unverified" | "pending_verification" | "verified" | "conflict"')
assertContains(drawer, "源身份合同")
assertContains(drawer, "字段供给路径")
assertContains(identityDrawer, "候选复合键")
assertContains(identityDrawer, "签认证据哈希")
assertContains(incrementalPage, "身份待签认")
assertContains(contractPage, "身份签认")
assertContains(contractPage, "绑定到候选合同")
assertContains(contractPage, "管理员可签认")
```

Use the same small `assertContains()` helper style as `frontend/scripts/check-tag-demand-contract.ts`.

- [ ] **Step 2: Register and run the contract to confirm red**

Add to `frontend/package.json`:

```json
"contract:content-identity": "node --experimental-strip-types scripts/check-content-identity-contract.ts"
```

Run:

```bash
cd frontend
npm run contract:content-identity
```

Expected: FAIL because new types and components are missing.

- [ ] **Step 3: Extend TypeScript types and API clients**

Add v2 optional fields to `TagDemandContractDefinition` and define:

```typescript
export type SourceIdentityVerification = {
  id: number
  contract_key: string
  source_system: string
  key_fields: ["res_type", "ll_id"]
  result: "verified" | "conflict"
  probe_hash: string
  data_window: string
  scoped_row_count: number
  duplicate_key_count: number
  res_id_conflict_count: number
  status: "draft" | "approved" | "superseded" | "rejected"
  created_by: string
  approved_by: string | null
  created_at: string
  approved_at: string | null
}


export type ContentIdentityRecord = {
  id: number
  source_system: string
  content_id: string
  content_key: string | null
  category_key: string
  content_version: string
  source_res_type: 1 | 6 | null
  source_ll_id: string | null
  source_res_id: string | null
  identity_status: "legacy_unverified" | "pending_verification" | "verified" | "conflict"
  identity_hash: string | null
  identity_verification_id: number | null
  status: string
  updated_at: string
}
```

Add API methods:

```typescript
export const sourceIdentityApi = {
  list: () => api<{ items: SourceIdentityVerification[] }>("/api/source-identity-verifications"),
  approve: (id: number) => api<SourceIdentityVerification>(`/api/source-identity-verifications/${id}/approve`, { method: "POST" }),
  bindContract: (contractId: number, verificationId: number) => api<TagDemandContract>(
    `/api/tag-demand-contracts/${contractId}/bind-source-identity-verification`,
    { method: "POST", ...jsonBody({ verification_id: verificationId }) },
  ),
}

export const contentIngressApi = {
  list: () => api<{ items: ContentIdentityRecord[] }>("/api/content-ingress/records"),
}
```

- [ ] **Step 4: Add contract and identity evidence views**

In `TagDemandContractDrawer`, add read-only sections:

- “源身份合同”：source system, object grain, candidate key, version/delete fields, uniqueness status, evidence hash;
- “字段供给路径”：field, namespace, production method, authority, Owner, SLA, null semantics, rollback;
- “执行变体覆盖”：show `single → space: not_applicable` separately from category defaults.

In `TagDemandContractsPage`, add a secondary “身份签认” drawer rather than another primary page:

- list verification rows for the selected contract key and source system;
- show probe hash, data window, scoped rows, duplicate keys, `res_id` conflicts, status and actor;
- admins can approve a `draft` verification only when both conflict counts are zero;
- after approval, admins can click “绑定到候选合同”, which calls `sourceIdentityApi.bindContract()` and opens the appended candidate version;
- non-admin users see the same evidence without approve/bind actions and with the copy “管理员可签认”;
- the drawer must state that approval does not execute SQL, activate the contract, start a model, or publish facts.

Create `ContentIdentityDrawer` with:

- candidate composite key;
- `content_key` or “身份待签认”;
- identity status and hash;
- source/res type/ll_id/res_id;
- verification id and evidence hash when available;
- explicit note that pending/conflict records cannot start production.

Do not display raw SQL result rows, tokens, credentials, or full upstream payloads.

- [ ] **Step 5: Add identity state to incremental workspace**

Fetch `/api/content-ingress/records`, show the five most recent records, and open the drawer from a single “查看身份” action. The primary page only shows source, category, content version, identity status, routing consequence and update time.

Use text plus badge; do not rely on color alone. Keep the existing incremental workflow stepper and package actions unchanged.

- [ ] **Step 6: Run frontend contracts, lint and build**

```bash
cd frontend
npm run contract:tag-demand
npm run contract:content-identity
npm run contract:dual-workspaces
npm run lint
npm run build
```

Expected: all commands exit 0; the existing chunk-size warning is allowed.

- [ ] **Step 7: Commit Task 7**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/tag-demand-contract-drawer.tsx frontend/src/pages/tag-demand-contracts-page.tsx frontend/src/components/content-identity-drawer.tsx frontend/src/pages/incremental-workspace-page.tsx frontend/scripts/check-content-identity-contract.ts frontend/package.json
git commit -m "feat: show 3d su identity and supply evidence"
```

---

### Task 8: Run the first-week acceptance gate and record the next stop point

**Files:**
- Modify: `PROJECT_STATUS.md`
- Test: all focused and full suites listed below

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: a clean, reviewable local branch that is ready for Owner review but performs no external effects.

- [ ] **Step 1: Run all focused backend tests**

```bash
cd /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/tpeng-label-reconstruction-kickoff-20260815
PYTHONPATH=.:backend /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python -m pytest \
  backend/tests/test_semantic_tag_contracts.py \
  backend/tests/test_asset_identity.py \
  backend/tests/test_source_identity_probe.py \
  backend/tests/test_asset_version_and_tag_contract_models.py \
  backend/tests/test_content_ingress_v2_identity.py \
  backend/tests/test_content_ingress_incremental_routing.py \
  backend/tests/test_tag_demand_contract_api.py \
  backend/tests/test_model_3d_su_semantic_slice.py \
  backend/tests/test_model_3d_su_seed.py -q
```

Expected: 0 failed.

- [ ] **Step 2: Run the complete backend suite with isolated data**

```bash
data_dir=$(mktemp -d /tmp/labellab-identity-full.XXXXXX)
DATA_DIR="$data_dir" PYTHONPATH=.:backend \
  /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/labellab/backend/.venv312/bin/python \
  -X utf8 -m pytest backend/tests -q
```

Expected: all tests pass; only existing dependency deprecation warnings are allowed.

- [ ] **Step 3: Run all relevant frontend gates**

```bash
cd frontend
npm run contract:tag-demand
npm run contract:content-identity
npm run contract:information-architecture
npm run contract:dual-workspaces
npm run lint
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 4: Verify the SQL tool remains read-only**

```bash
cd ..
if rg -n '(^|[[:space:]])(import|from)[[:space:]]+(requests|httpx|urllib|odps|dataworks)|\.execute\(|\b(INSERT|UPDATE|DELETE)\b' \
  backend/app/source_identity_probe.py scripts/generate_3d_su_identity_probe.py; then
  exit 1
fi
```

Expected: no network/client/execution imports or DML statements. The documentation may mention `DELETE` only as source deletion semantics; runtime files must not.

- [ ] **Step 5: Update project status**

Add a dated section to `PROJECT_STATUS.md` recording:

- field contract v2 and exact compatibility boundary;
- identity candidate key and verification requirement;
- migration 70 behavior for legacy rows;
- v2 event fail-closed routing;
- frontend evidence views;
- focused/full test counts and frontend commands;
- generated SQL was not executed;
- no DataWorks access, permission application, external DML, real model call, push, merge or deployment occurred;
- next implementation plan is “脚本注册与工作流编排”。

- [ ] **Step 6: Verify scope and diff**

```bash
git diff --check
git status --short
git diff --stat origin/main...HEAD
```

Expected: only files named in this plan are modified; no `.env`, database, generated SQL result, image, log, `dist`, `node_modules`, token, or credential file is tracked.

- [ ] **Step 7: Commit the acceptance receipt**

```bash
git add PROJECT_STATUS.md
git commit -m "docs: record 3d su identity foundation acceptance"
```

- [ ] **Step 8: Stop before external effects**

Do not push, create an MR, merge, deploy, execute the generated SQL, apply for permissions, call a real model, or write a real table. Report the local commit range, test evidence, remaining external gates, and rollback point to the Owner.

---

## Follow-up Plans After This One

Only after Task 8 is reviewed and accepted, create these separate plans in dependency order:

1. `2026-08-21-script-registry-workflow-runtime.md`：脚本注册、工作流定义、冻结快照、五队列和检查点恢复；
2. `2026-09-04-3d-su-label-production-human-ai-loop.md`：真实模型路由、语义/美感/治理候选、人工真值、AI 机制候选和双人工门；
3. `2026-09-18-real-projection-consumer-feedback.md`：经审批的单写者投影、消费者检查点、对账、Badcase 回流和回退；
4. `2026-09-25-september-closure-acceptance.md`：故障注入、1000 单元吞吐、联合验收和 9 月 30 日证据包。

The dates in these filenames are planning checkpoints, not authorization to execute external actions.
