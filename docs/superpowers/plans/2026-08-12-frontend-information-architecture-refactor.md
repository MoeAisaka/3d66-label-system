# LabelLab 前端信息架构与评测机制编辑器重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变双发布轴、权限和生产边界的前提下，完成高级设置、V3 合同配置和存量回归三页重构，修复 Proposal PDF 白屏，增加受控机制插件、图像规则加分/维度上限、版本展示和安全降级闭环。

**Architecture:** 前端以共享工作区组件承载一级主线与二级抽屉/Dialog，V3 编辑器通过 `profile_type` 注册表加载受控插件；后端用同一机制解析与校验注册表路由标准图像合同和 Proposal PDF 合同。现有单行 `CategoryEvaluationV3Config` 继续作为运行时 active 投影，编辑保存改为创建不可变候选 revision，禁止直接覆盖 active；图像规则的新数学只在候选合同显式含新字段时启用新 bridge/composition 版本，旧冻结合同继续走既有路径。存量回归只重组现有 API 和纠偏内核，不新增整轮发布状态。

**Tech Stack:** React 19、TypeScript 7、React Router 7、TanStack Query、Radix Dialog、Tailwind CSS 4、Vite 8、FastAPI、Pydantic 2、SQLAlchemy 2、Pytest、Docker Compose。

## Global Constraints

- 已批准设计规格：`docs/superpowers/specs/2026-08-12-frontend-information-architecture-refactor-design.md`，基线 `main@ca829b72c212ae7aa68a9d5c4add75fbd01966d6`。
- 当前分支固定为 `codex/frontend-information-architecture-v1`；只在隔离工作树实施。
- 产品定位统一为 **TPENG 标签实验台（LabelLab）**，标签体系重构不是并列项目线。
- 视觉主色固定 `#CCED46`；白底/浅灰、微软雅黑、无霓虹光晕、无普通面板重阴影、无紫蓝渐变。
- 桌面主基准 `1440×900`，最低验收 `1280×720`；不新增移动端适配和移动端验收。
- 本批只重构高级设置、V3 合同配置、存量回归；其余路由只产出信息架构审计。
- 本批只实现 `text-proposal-additive-v1` 专用编辑器；3D、SU 只使用注册与安全降级基础，不实现专用编辑器。
- 未知或损坏机制必须只读降级，禁止白屏、猜测结构或写回合同。
- Proposal PDF 保持三分项加法，不使用图像机制的 `bonus_rules` 或 `dimension_score_cap`。
- 机制发布轴与标签事实发布轴继续独立；保存候选合同不自动发布、不自动重跑、不写正式标签事实。
- 本批不新增候选 V3 revision 到 `EvaluationPackage/MechanismRelease` 的激活绑定；只建立安全候选存储、读取和审计，后续必须经 Owner 冻结后接入既有人工发布门禁，禁止用旧 `status` 接口绕过门禁。
- 不执行生产发布、存量批量重跑、凭据清理；只允许合并 Codeup `main` 并部署内网测试环境 `192.168.1.35:8081`。
- 旧冻结合同、旧 fixture、旧回放结果、steps、evidence 和 cap reasons 必须逐字段保持不变。
- 前端版本格式固定为 `LabelLab v0.2.0 · build <7位Git短SHA>`；测试环境不得显示 `build dev`。
- 每个任务先写失败测试，再写最小实现，再运行定向测试；每个任务独立提交且不混入依赖目录、构建产物或数据文件。

## File and Interface Map

### Shared frontend framework

- Create `frontend/src/components/workspace-page.tsx`: `WorkspacePageHeader`、`StatusSummaryStrip`、`SecondaryDrawer`、`ConfirmDialog`、`InlineDisclosure`。
- Create `frontend/src/components/route-error-state.tsx`: `RouteErrorState` 与 React error boundary。
- Create `frontend/src/lib/app-version.ts`: `APP_VERSION`、`BUILD_SHA`、`formatAppVersion()`。
- Modify `frontend/src/components/app-shell.tsx`: 移除高级设置顶部重复入口并展示版本。
- Modify `frontend/src/pages/system-management-page.tsx`: 把底座预览和 V3 合同配置都放入“评测方案”。
- Modify `frontend/vite.config.ts`、`frontend/src/vite-env.d.ts`、`frontend/package.json`、`frontend/package-lock.json`、`Dockerfile`、`docker-compose.yml`、`scripts/deploy-test-server.sh`: 注入并验收构建 SHA。

### Mechanism profile registry

- Create `backend/app/mechanism_profiles.py`: 后端 profile 解析、描述、校验、规则镜像和媒介开关接口。
- Modify `backend/app/category_evaluation_v3_config_api.py`: GET/validate 与后续候选创建共用 profile registry，读取未知 profile 仍成功。
- Create `frontend/src/features/mechanism-config/types.ts`: V3 详情、草稿、profile 描述和插件接口。
- Create `frontend/src/features/mechanism-config/registry.tsx`: 前端受控插件注册表。
- Create `frontend/src/features/mechanism-config/mechanism-editor-boundary.tsx`: 插件异常安全降级。
- Create `frontend/src/features/mechanism-config/unknown-mechanism-summary.tsx`: 只读摘要和 JSON 抽屉。
- Create `frontend/src/features/mechanism-config/image-rule-editor.tsx`: 从现有大页面抽出的标准图像编辑器。
- Refactor `frontend/src/pages/category-evaluation-v3-config-page.tsx`: 只负责列表、revision 加载、候选创建和插件调度。
- Modify `frontend/scripts/check-level-scale-thinking-controls.ts`: 等级档位继续作为结构化合同字段，但落库改为整份候选 revision，不再要求专用 PUT。

### Candidate mechanism revisions

- Create `backend/app/category_evaluation_v3_revisions.py`: 候选 revision 创建、读取、现役投影并发校验和安全 bundle 解析。
- Modify `backend/app/models.py`、`backend/app/migrations/runner.py`: 新增工件不可变 `CategoryEvaluationV3Revision` 与现役投影指针，逐行回填现有合同。
- Modify `backend/app/category_evaluation_v3_config_api.py`: 新增 revision 列表/详情/候选创建接口，封住 active 原地覆盖与直接激活。
- Modify `backend/app/proposal_text_seed.py`: 只维护 active 种子，不覆盖或拒绝合法候选 revision。
- Frontend route and plugins consume `runtime_revision`、`selected_revision` and `candidate_revisions`; every create-candidate action appends a new revision.

### Proposal PDF editor

- Modify `backend/app/proposal_text_contract.py`: 把当前固定值校验改为结构化、范围化、可版本化校验，仍接受当前冻结合同。
- Create `frontend/src/features/mechanism-config/proposal-text-contract.ts`: 路径级深拷贝 patch 和 Proposal 摘要。
- Create `frontend/src/features/mechanism-config/proposal-text-editor.tsx`: 六步专用编辑工作台。

### Image rule bonus/cap engine

- Modify `backend/app/category_evaluation_contract.py`: `BonusRule`、统一规则校验和命中类型。
- Modify `backend/app/dimension_composition.py`: 识别旧 grade fallback、旧 deduction 模式和显式新 rule 模式。
- Modify `backend/app/dimension_deduction_bridge.py`: 新 prompt/output、bonus/cap composition 和版本证据。
- Modify `backend/app/worker_v3_authoritative.py`、`backend/app/worker.py`: 新模式路由和 provider warning 人工复核标记。
- Modify `backend/app/node_correction_api.py`: `hit_bonus_rules` 的追加式纠偏和冻结重算。
- Modify `frontend/src/lib/node-correction.ts`、`frontend/src/pages/node-correction-editor.tsx`: 正负规则统一展示与编辑。

### Baseline regression information architecture

- Create `frontend/src/features/baseline-regression/baseline-set-dialog.tsx`。
- Create `frontend/src/features/baseline-regression/run-config-drawer.tsx`。
- Create `frontend/src/features/baseline-regression/metrics-drawer.tsx`。
- Create `frontend/src/features/baseline-regression/run-history-drawer.tsx`。
- Create `frontend/src/features/baseline-regression/correction-workbench.tsx`。
- Refactor `frontend/src/pages/baseline-regression-page.tsx`: 保留数据编排，把主操作链与二级内容分开。

### Contracts and docs

- Create `frontend/scripts/check-information-architecture-contract.ts`。
- Create `frontend/scripts/check-mechanism-editor-contract.ts`。
- Create `docs/discussion/frontend-information-architecture-audit-20260812.md`。
- Create `docs/decisions/0044-frontend-workspaces-and-mechanism-profile-plugins.md`。
- Modify `docs/decisions/README.md`、`PROJECT_STATUS.md`。

---

### Task 1: Restore the executable baseline and add the shared desktop shell

**Files:**
- Create: `frontend/src/components/workspace-page.tsx`
- Create: `frontend/src/components/route-error-state.tsx`
- Create: `frontend/src/lib/app-version.ts`
- Create: `frontend/scripts/check-information-architecture-contract.ts`
- Modify: `frontend/src/components/app-shell.tsx`
- Modify: `frontend/src/pages/system-management-page.tsx`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `scripts/deploy-test-server.sh`

**Interfaces:**
- Produces: `formatAppVersion(version?: string, sha?: string): string`。
- Produces: `SecondaryDrawerProps { open; onOpenChange; title; description?; children; footer? }`。
- Produces: `RouteErrorStateProps { title; message; onRetry?; backTo? }`。
- Later tasks consume the shared drawer/Dialog and error state without duplicating Radix behavior.

- [ ] **Step 1: Re-establish the branch, Codeup remote, and dependencies**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git merge-base --is-ancestor ca829b72c212ae7aa68a9d5c4add75fbd01966d6 HEAD
git remote set-url origin git@codeup.aliyun.com:3d66/tepeng/3d66.label-system.git
git fetch origin main
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

Expected: branch is `codex/frontend-information-architecture-v1`; `HEAD` contains `caa4666`; origin points to Codeup; dependency installation succeeds. Do not copy `.venv312` or `node_modules` from rollback/sync snapshots.

- [ ] **Step 2: Run the clean baseline before modifying application code**

Run:

```bash
DATA_DIR="$(mktemp -d)" .venv312/bin/python -m pytest backend/tests -q -k 'not test_macos_keychain_real_isolated_round_trip_update_and_cleanup'
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: backend passes with only the known real-Keychain test deselected; frontend lint and build pass. Stop and investigate any other failure.

- [ ] **Step 3: Write the failing shell and version contract**

Add `frontend/scripts/check-information-architecture-contract.ts` with assertions equivalent to:

```ts
assert.doesNotMatch(appShell, /高级设置首页/)
assert.doesNotMatch(appShell, /active\.to === advancedWorkflowDomain\.to/)
assert.match(systemManagement, /类目评测底座预览/)
assert.match(systemManagement, /类目评测 v3 合同配置/)
assert.match(appShell, /<AppVersion/)
assert.equal(formatAppVersion("0.2.0", "caa46663608c"), "LabelLab v0.2.0 · build caa4666")
assert.equal(formatAppVersion("0.2.0", ""), "LabelLab v0.2.0 · build dev")
```

Add the package script:

```json
"contract:information-architecture": "node --experimental-strip-types scripts/check-information-architecture-contract.ts"
```

- [ ] **Step 4: Run the contract to verify it fails**

Run: `npm --prefix frontend run contract:information-architecture`

Expected: FAIL because `AppVersion` and the shared workspace components do not exist and the repeated advanced tabs are still present.

- [ ] **Step 5: Implement the shared components and build identity**

Implement `frontend/src/lib/app-version.ts` around compile-time constants:

```ts
export const APP_VERSION = __LABEL_LAB_VERSION__
export const BUILD_SHA = __LABEL_LAB_BUILD_SHA__

export function formatAppVersion(version = APP_VERSION, sha = BUILD_SHA) {
  const build = sha.trim() ? sha.trim().slice(0, 7) : "dev"
  return `LabelLab v${version} · build ${build}`
}
```

Define those constants in `vite.config.ts` from `frontend/package.json` and `process.env.LABEL_LAB_BUILD_SHA`. Bump both package files to `0.2.0`. Add `ARG LABEL_LAB_BUILD_SHA=dev` and `ENV LABEL_LAB_BUILD_SHA=$LABEL_LAB_BUILD_SHA` to the frontend Docker build stage. Pass the current checked-out short SHA as the Docker Compose build arg; make `compose_up()` calculate it from the server Git checkout for both normal deployment and rollback.

Implement `SecondaryDrawer` and `ConfirmDialog` with Radix `Dialog.Root`, `Dialog.Overlay`, `Dialog.Content`, `Dialog.Title`, `Dialog.Description`, an explicit close button, Esc behavior and focus return. Keep the shared component free of business fields.

Remove `advancedWorkflowDomain.tabs`; render the sticky secondary navigation only when `active.tabs.length > 0`. Place `<AppVersion />` above the signed-in user block. Add the preview entry before the V3 entry in `managementGroups[0].entries`.

- [ ] **Step 6: Run focused verification**

Run:

```bash
npm --prefix frontend run contract:information-architecture
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

Expected: all pass; production build made without `LABEL_LAB_BUILD_SHA` may show `build dev`, which is allowed locally.

- [ ] **Step 7: Commit the shell foundation**

```bash
git add frontend/src/components/workspace-page.tsx frontend/src/components/route-error-state.tsx frontend/src/lib/app-version.ts frontend/scripts/check-information-architecture-contract.ts frontend/src/components/app-shell.tsx frontend/src/pages/system-management-page.tsx frontend/vite.config.ts frontend/src/vite-env.d.ts frontend/package.json frontend/package-lock.json Dockerfile docker-compose.yml scripts/deploy-test-server.sh
git commit -m "feat: add LabelLab desktop workspace shell"
```

### Task 2: Add the backend mechanism profile registry and safe read/validation routing

**Files:**
- Create: `backend/app/mechanism_profiles.py`
- Create: `backend/tests/test_mechanism_profiles.py`
- Modify: `backend/app/category_evaluation_v3_config_api.py`
- Modify: `backend/tests/test_category_evaluation_v3_config_api.py`

**Interfaces:**
- Produces: `MechanismProfileResolution(profile_type, source, supported, editable, reason)`.
- Produces: `describe_mechanism_profile(contract: Any) -> MechanismProfileResolution`, which never raises and is safe for GET responses.
- Produces: `validate_mechanism_artifacts(contract, classification_map, subcategory_dimensions) -> str`, returning the resolved profile type or raising a coded `MechanismProfileError`.
- Produces: `extract_profile_rule_mirror(profile_type, subcategory_dimensions) -> dict[str, Any]` and `profile_media_penalty_enabled(profile_type, contract) -> bool`.
- V3 detail responses add `mechanism_profile` without changing stored JSON.

- [ ] **Step 1: Write failing registry unit tests**

Create tests covering exact resolution behavior:

```python
def test_explicit_proposal_profile_wins() -> None:
    result = describe_mechanism_profile(proposal_contract())
    assert result.profile_type == "text-proposal-additive-v1"
    assert result.source == "explicit"
    assert result.supported is True

def test_legacy_image_contract_resolves_without_rewrite() -> None:
    contract = image_contract()
    assert "profile_type" not in contract
    result = describe_mechanism_profile(contract)
    assert result.profile_type == "image-rule-deduction-v1"
    assert result.source == "legacy_image_shape"
    assert "profile_type" not in contract

def test_unknown_explicit_profile_is_readable_but_not_writable() -> None:
    contract = {"profile_type": "future-3d-v1", "category_key": "3d_model"}
    result = describe_mechanism_profile(contract)
    assert result.supported is False
    with pytest.raises(MechanismProfileError) as excinfo:
        validate_mechanism_artifacts(contract, {}, {})
    assert excinfo.value.code == "profile_type_unsupported"
```

- [ ] **Step 2: Write failing API tests for Proposal and unknown profiles**

Add tests asserting:

```python
proposal = client.get(f"{_BASE}/proposal_text_pdf")
assert proposal.status_code == 200
assert proposal.json()["mechanism_profile"] == {
    "profile_type": "text-proposal-additive-v1",
    "source": "explicit",
    "supported": True,
    "editable": True,
    "reason": None,
}

unknown = client.get(f"{_BASE}/future_3d")
assert unknown.status_code == 200
assert unknown.json()["mechanism_profile"]["supported"] is False

validate = client.post(f"{_BASE}/future_3d/validate", json=unknown_body)
assert validate.status_code == 200
assert validate.json()["ok"] is False
assert validate.json()["errors"][0]["code"] == "profile_type_unsupported"
```

Also assert Proposal validation no longer invokes the image `tracks[*].key`, `classification_map` or `subcategory_dimensions-v1` validators.

- [ ] **Step 3: Run tests to verify current behavior fails**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_mechanism_profiles.py backend/tests/test_category_evaluation_v3_config_api.py -q
```

Expected: FAIL because the registry and response field do not exist and Proposal is routed through image validation.

- [ ] **Step 4: Implement the registry as the single backend routing source**

Use a frozen dataclass and coded exception:

```python
@dataclass(frozen=True)
class MechanismProfileResolution:
    profile_type: str | None
    source: Literal["explicit", "legacy_image_shape", "unresolved"]
    supported: bool
    editable: bool
    reason: str | None = None

class MechanismProfileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
```

Register exactly `image-rule-deduction-v1` and `text-proposal-additive-v1`. Image validation reuses `validate_category_evaluation_contract`, `validate_classification_map` and `validate_subcategory_dimensions`. Proposal validation reuses `validate_proposal_text_contract` and requires matching profile markers in `classification_map` and `subcategory_dimensions`; its rule mirror is `{}` and media penalty is `False`.

Only infer the image profile when `schema_version == "evaluation-category-profile-v3"` and `track_classification.tracks` is a non-empty array. Never infer an unknown object-shaped mechanism.

- [ ] **Step 5: Route V3 reads and validation through the registry**

Add `mechanism_profile: dict[str, Any]` to `V3ConfigDetail`. `_detail()` calls the non-throwing descriptor. `_collect_validation_errors()` catches `MechanismProfileError` and produces a single stable target/code/message before profile-specific validation.

Use the registry helpers in `_collect_validation_errors()` and in the existing create/update derivation points so the later candidate service has one validation source. Keep GET list and GET detail readable for unsupported profiles. Task 3 replaces those legacy mutations with immutable candidate creation; this task does not define activation semantics.

- [ ] **Step 6: Run focused backend verification**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_mechanism_profiles.py backend/tests/test_category_evaluation_v3_config_api.py backend/tests/test_proposal_text_contract.py backend/tests/test_proposal_text_integration.py -q
git diff --check
```

Expected: all pass; existing image read/validate behavior remains unchanged and Proposal no longer enters image validators.

- [ ] **Step 7: Commit backend profile routing**

```bash
git add backend/app/mechanism_profiles.py backend/app/category_evaluation_v3_config_api.py backend/tests/test_mechanism_profiles.py backend/tests/test_category_evaluation_v3_config_api.py
git commit -m "feat: route evaluation configs by mechanism profile"
```

### Task 3: Persist immutable candidate V3 revisions without changing the active runtime

**Files:**
- Create: `backend/app/category_evaluation_v3_revisions.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/category_evaluation_v3_config_api.py`
- Modify: `backend/app/seed.py`
- Modify: `backend/app/proposal_text_seed.py`
- Create: `backend/tests/test_category_evaluation_v3_revisions.py`
- Modify: `backend/tests/test_category_evaluation_v3_config_api.py`
- Modify: `backend/tests/test_inspiration_seed_persistence.py`
- Modify: `backend/tests/test_proposal_text_integration.py`
- Modify: `backend/tests/test_migration.py`

**Interfaces:**
- Produces artifact-immutable `CategoryEvaluationV3Revision` rows with `category_key`, monotonically increasing `revision`, `status in {draft,candidate,active,retired}`, `parent_revision_id`, full contract artifacts, `contract_hash`, derived rule mirror/media flag and creator audit fields. Lifecycle status may change only through separately audited seed/release transitions; identity and artifact fields never mutate.
- `CategoryEvaluationV3Config.projected_revision_id` points to the revision currently copied into the existing projection; worker reads and the projection's existing `draft|active|retired` behavior remain unchanged.
- Produces `create_candidate_revision(db, projected, parent_revision, artifacts, expected_projected_revision, expected_projected_hash, actor) -> CategoryEvaluationV3Revision`.
- Produces `revision_bundle(revision) -> dict[str, Any]` for later regression/package binding; this task does not activate it.
- API `POST /api/category-evaluation/v3-config/{category_key}/revisions` always creates a candidate and never mutates `CategoryEvaluationV3Config`.
- Existing active `PUT /{category_key}`, `PUT /{category_key}/level-scale` and `PUT /{category_key}/status` return coded `409 active_projection_immutable`; dry-run validate remains available.

- [ ] **Step 1: Write failing migration and immutability tests**

Add migration assertions equivalent to:

```python
columns = table_columns(connection, "category_evaluation_v3_revisions")
assert {"category_key", "revision", "status", "parent_revision_id", "contract_json", "contract_hash"} <= columns
assert "projected_revision_id" in table_columns(connection, "category_evaluation_v3_configs")
```

Create active, draft and retired configs before migration 63, run migrations, and assert each gets one revision with matching lifecycle status, exact three JSON artifacts/hash and a projection pointer. Assert `UNIQUE(category_key, revision)` and the database trigger rejects changing frozen identity/artifact fields or deleting a revision.

- [ ] **Step 2: Write failing candidate lifecycle API tests**

Seed an active config, then assert:

```python
active = client.get(f"{_BASE}/inspiration_image").json()
candidate_body = {**_valid_body(), "parent_revision_id": active["projected_revision_id"], "expected_projected_revision": active["revision"], "expected_projected_contract_hash": active["contract_hash"]}
created = client.post(f"{_BASE}/inspiration_image/revisions", json=candidate_body)
assert created.status_code == 201
assert created.json()["status"] == "candidate"
assert created.json()["revision"] == active["revision"] + 1
assert client.get(f"{_BASE}/inspiration_image").json()["contract_hash"] == active["contract_hash"]
```

Also cover candidate history ordering, revision detail, editing a selected candidate into a child revision, stale projection revision/hash conflicts, foreign/stale parent rejection, duplicate idempotent payload behavior, cross-category body rejection and unknown-profile write failure. Assert the three legacy mutation endpoints return `409 active_projection_immutable` and do not change the runtime row.

- [ ] **Step 3: Run the tests to verify current behavior fails**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_category_evaluation_v3_revisions.py backend/tests/test_category_evaluation_v3_config_api.py backend/tests/test_migration.py -q
```

Expected: FAIL because the revision table, projection pointer and candidate endpoints do not exist and legacy PUT still mutates the projected row.

- [ ] **Step 4: Add migration 63 and the immutable model**

Migration 63 must:

```text
create category_evaluation_v3_revisions
add projected_revision_id to category_evaluation_v3_configs
backfill exactly one matching-status revision from every existing config row
set projected_revision_id to the backfilled row
create unique category/revision and status indexes
create no-delete and frozen-artifact UPDATE triggers
```

Backfill preserves the existing revision number rather than renumbering history. The revision status constraint is `draft|candidate|active|retired`; only one revision may be active per category via a partial unique index. Do not rebuild or rename `category_evaluation_v3_configs`; existing worker queries stay byte-compatible.

- [ ] **Step 5: Implement one candidate-creation service**

`create_candidate_revision()` must validate through `validate_mechanism_artifacts()`, compare `expected_projected_revision` and `expected_projected_contract_hash`, verify `parent_revision_id` belongs to the same category and is either the projected revision or an existing candidate descendant, derive mirrors through the profile registry, then assign:

```python
next_revision = max(existing revision for category, default=projected.revision) + 1
status = "candidate"
parent_revision_id = request.parent_revision_id
```

The full artifacts are canonicalized once and frozen. Every request is still guarded by the current projected revision/hash, so a runtime projection change makes an open editor stale even when its parent is a candidate. Retry with the same category, parent, contract hash, classification map and dimensions returns the existing candidate; a conflicting retry returns coded `409 candidate_revision_conflict`. There is no activation function in this task.

- [ ] **Step 6: Replace active writes with revision reads and candidate creation**

Add `GET /{category_key}/revisions`, `GET /{category_key}/revisions/{revision}` and `POST /{category_key}/revisions`. Detail/list responses expose `projected_revision_id`, selected revision status and candidate count. Keep `POST /validate` and `POST /{category_key}/validate` write-free.

Return `409 active_projection_immutable` from legacy full-update, level-scale update and status endpoints with the message “现役合同只能通过已批准机制发布原子切换，请先创建候选版本”. Do not silently redirect old PUT payloads because callers must notice the lifecycle change.

- [ ] **Step 7: Make all startup seeds revision-aware**

For `seed.py` and `proposal_text_seed.py`:

```text
new installation: create the projection and a matching draft/active revision together
known frozen seed upgrade: for an active projection, atomically retire the previous active revision, insert the new seed active revision, update the projection and pointer
same seed: no-op
operator candidate revisions: ignore; never validate them as the active seed and never overwrite/delete them
unknown projected contract: retain current fail-closed behavior
```

Add tests where a valid Proposal/operator candidate exists before a second seed call; startup must succeed and candidate JSON/hash must remain unchanged. Existing active frozen fixtures and runtime reads must keep their current values.

- [ ] **Step 8: Run focused revision and seed verification**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_category_evaluation_v3_revisions.py backend/tests/test_category_evaluation_v3_config_api.py backend/tests/test_inspiration_seed_persistence.py backend/tests/test_proposal_text_integration.py backend/tests/test_worker_v3_authoritative.py backend/tests/test_migration.py -q
git diff --check
```

Expected: all pass; candidate creation never changes `v3_authoritative_category()` output and seeds preserve candidates.

- [ ] **Step 9: Commit candidate revision storage**

```bash
git add backend/app/category_evaluation_v3_revisions.py backend/app/models.py backend/app/migrations/runner.py backend/app/category_evaluation_v3_config_api.py backend/app/seed.py backend/app/proposal_text_seed.py backend/tests/test_category_evaluation_v3_revisions.py backend/tests/test_category_evaluation_v3_config_api.py backend/tests/test_inspiration_seed_persistence.py backend/tests/test_proposal_text_integration.py backend/tests/test_migration.py
git commit -m "feat: store immutable candidate mechanism revisions"
```

### Task 4: Split the V3 frontend into registered plugins with a no-white-screen fallback

**Files:**
- Create: `frontend/src/features/mechanism-config/types.ts`
- Create: `frontend/src/features/mechanism-config/registry.tsx`
- Create: `frontend/src/features/mechanism-config/mechanism-editor-boundary.tsx`
- Create: `frontend/src/features/mechanism-config/unknown-mechanism-summary.tsx`
- Create: `frontend/src/features/mechanism-config/image-rule-editor.tsx`
- Create: `frontend/scripts/check-mechanism-editor-contract.ts`
- Modify: `frontend/src/pages/category-evaluation-v3-config-page.tsx`
- Modify: `frontend/scripts/check-level-scale-thinking-controls.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: `MechanismEditorPlugin { profileType; canEdit; Editor; buildSummary }`.
- Produces: `getMechanismEditorPlugin(profileType: string | null): MechanismEditorPlugin | null`.
- Produces: `MechanismEditorProps { draft; runtimeRevision; selectedRevision; busy; banner; errors; onPatch; onValidate; onCreateCandidate }`.
- The page owns network/revision state; plugins edit a clone of the selected revision and can only request validation or candidate creation. There is no lifecycle-status callback.

- [ ] **Step 1: Write the failing frontend registry contract**

The new script must import pure registry helpers and inspect page source:

```ts
assert.equal(getMechanismEditorPlugin("image-rule-deduction-v1")?.profileType, "image-rule-deduction-v1")
assert.equal(getMechanismEditorPlugin("text-proposal-additive-v1")?.profileType, "text-proposal-additive-v1")
assert.equal(getMechanismEditorPlugin("future-3d-v1"), null)
assert.doesNotMatch(pageSource, /tracks\.map/)
assert.doesNotMatch(pageSource, /onStatus/)
assert.doesNotMatch(pageSource, /\/level-scale.*PUT/)
assert.doesNotMatch(levelScaleContractSource, /expected_revision/)
assert.match(levelScaleContractSource, /创建候选版本/)
assert.match(pageSource, /MechanismEditorBoundary/)
assert.match(pageSource, /创建候选版本/)
assert.match(unknownSource, /当前版本不支持结构化编辑/)
assert.match(unknownSource, /查看完整 JSON/)
```

Add:

```json
"contract:mechanism-editor": "node --experimental-strip-types scripts/check-mechanism-editor-contract.ts"
```

- [ ] **Step 2: Run the contract to verify it fails**

Run: `npm --prefix frontend run contract:mechanism-editor`

Expected: FAIL because the page still directly calls image array methods and no plugin registry exists.

- [ ] **Step 3: Extract shared V3 types and the image editor**

Move `ConfigSummary`、`ConfigDetail`、`ConfigRevision`、`Editable`、`ValidationErrorItem` and `MechanismEditorProps` to `types.ts`. Move `V3ConfigEditor` and its image-only field editors into `image-rule-editor.tsx` while replacing status controls with read-only revision badges. Keep the current level-scale field editor, but remove its “仅保存等级档位” mutation; it patches the full draft and is persisted only by “创建候选版本”. Update `check-level-scale-thinking-controls.ts` to assert the field contract and candidate action instead of `/level-scale` plus `expected_revision`. The image plugin must not know list-query or endpoint details.

- [ ] **Step 4: Implement the registry and safety boundary**

Use an explicit record:

```tsx
const PLUGINS: Record<string, MechanismEditorPlugin> = {
  "image-rule-deduction-v1": imageRulePlugin,
  "text-proposal-additive-v1": proposalTextPlugin,
}
```

At this task, the Proposal plugin may render a read-only “专用编辑器将在下一任务启用” component but must already be registered and must never enter the image editor. `MechanismEditorBoundary` must be a class error boundary that catches render errors and renders `UnknownMechanismSummary` with retry and JSON access.

- [ ] **Step 5: Refactor the route page to dispatch safely**

The route page loads runtime detail plus revision history, selects the projected revision by default, then resolves `selectedRevision.mechanism_profile.profile_type` and renders:

```tsx
<MechanismEditorBoundary detail={selectedRevision} onRetry={() => reload(detail.category_key)}>
  {plugin ? <plugin.Editor {...editorProps} /> : <UnknownMechanismSummary detail={selectedRevision} />}
</MechanismEditorBoundary>
```

Do not call `tracks.map`, `Object.keys(subcategory_dimensions)` or image field editors in the route page. “创建候选版本” calls the Task 3 endpoint with `parent_revision_id=selectedRevision.id` and the current runtime projection revision/hash as concurrency guards, then labels the result “候选，未发布”; it never calls legacy PUT, level-scale PUT or status PUT. Load errors render `RouteErrorState`, while the application shell remains visible.

- [ ] **Step 6: Run frontend verification**

Run:

```bash
npm --prefix frontend run contract:mechanism-editor
npm --prefix frontend run contract:v3-only
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

Expected: all pass; Proposal and future profile fixtures do not throw during summary rendering.

- [ ] **Step 7: Commit the plugin shell**

```bash
git add frontend/src/features/mechanism-config frontend/src/pages/category-evaluation-v3-config-page.tsx frontend/scripts/check-mechanism-editor-contract.ts frontend/scripts/check-level-scale-thinking-controls.ts frontend/package.json frontend/package-lock.json
git commit -m "refactor: split V3 config editors by mechanism profile"
```

### Task 5: Make Proposal PDF a lossless, editable profile

**Files:**
- Create: `frontend/src/features/mechanism-config/proposal-text-contract.ts`
- Create: `frontend/src/features/mechanism-config/proposal-text-editor.tsx`
- Modify: `frontend/src/features/mechanism-config/registry.tsx`
- Modify: `backend/app/proposal_text_contract.py`
- Modify: `backend/tests/test_proposal_text_contract.py`
- Modify: `backend/tests/test_category_evaluation_v3_config_api.py`
- Modify: `frontend/scripts/check-mechanism-editor-contract.ts`

**Interfaces:**
- Produces: `patchProposalContract(contract, path, value): Json` that clones the full object and changes only the addressed path.
- Produces: `validate_proposal_text_contract(contract) -> dict[str, Any]` accepting versioned operator-authored values within explicit safe bounds.
- The runtime aggregator continues consuming `track_classification.tracks[scoring_track]` and `grade_bands` from the frozen contract.

- [ ] **Step 1: Write failing backend tests for editable Proposal values**

Add tests for a candidate derived from the frozen fixture:

```python
def test_contract_accepts_operator_version_and_track_cap_changes() -> None:
    candidate = contract()
    candidate["spec_version"] = "proposal-text-v3-owner-edit-20260812"
    candidate["call_a_version"] = "proposal-text-a-v3-owner-edit-20260812"
    candidate["call_b_version"] = "proposal-text-b-v3-owner-edit-20260812"
    candidate["track_classification"]["tracks"]["A"]["visual_max"] = 44
    candidate["track_classification"]["tracks"]["A"]["narrative_max"] = 46
    assert validate_proposal_text_contract(candidate)["spec_version"] == candidate["spec_version"]

@pytest.mark.parametrize("path,value", [
    (("pdf_input_channel", "call_a", "batch_size"), 0),
    (("track_classification", "tracks", "A", "visual_max"), 101),
    (("grade_bands", "L2"), [80, 70]),
])
def test_contract_rejects_unsafe_operator_values(path, value) -> None:
    candidate = contract()
    target = candidate
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_proposal_text_contract(candidate)
```

Add an API round-trip test that adds an unknown extension field, creates a candidate revision, reloads that revision and asserts byte-equivalent JSON value preservation for that field while the active projection stays unchanged.

- [ ] **Step 2: Run backend tests to verify frozen-only validation fails**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_proposal_text_contract.py backend/tests/test_category_evaluation_v3_config_api.py -q
```

Expected: the operator-edited candidate is rejected by `_CONTRACT_VERSIONS` or fixed maximum checks.

- [ ] **Step 3: Generalize Proposal validation without changing current frozen data**

Keep `contract_version`、`profile_type` and `category_key` fixed. Require non-empty `spec_version`、`call_a_version` and `call_b_version`; validate their maximum length and printable string shape instead of a two-entry allowlist.

Validate these exact ranges and invariants:

```text
call_a.batch_size: integer 1..32
call_a.max_side_px: integer 512..2048
call_b.sample_size: integer 1..32
track maxima: integers 0..100 and visual+narrative+innovation <= 100
members: unique non-empty strings
redline hit_score_cap: number 0..100
grade bands: integer closed intervals, L5..L1 contiguous and covering 0..100 once
```

Keep `model_must_not_output == ["score", "rate", "grade"]`, engine-computed totals, source-PDF identity, and current redline enum set. Existing v1/v2 fixtures and seeds must still pass unchanged.

- [ ] **Step 4: Write the failing frontend Proposal editor contract**

Extend the contract script:

```ts
const original = { known: { value: 1 }, extension: { keep: ["x"] } }
const next = patchProposalContract(original, ["known", "value"], 2)
assert.deepEqual(next.extension, { keep: ["x"] })
assert.deepEqual(original.known, { value: 1 })
assert.match(editorSource, /PDF 输入与确定性预检/)
assert.match(editorSource, /红线与人工复核/)
assert.match(editorSource, /赛道与三分项评分/)
assert.match(editorSource, /回归与验收/)
```

- [ ] **Step 5: Run the frontend contract to verify it fails**

Run: `npm --prefix frontend run contract:mechanism-editor`

Expected: FAIL because the Proposal editor and patch helper do not exist.

- [ ] **Step 6: Implement the six-step Proposal workbench**

Use a three-column desktop grid: fixed step navigation, central form, right validation/change summary. Implement these steps from the approved spec: identity, PDF input, redline/manual review, tracks/components, level/output fields, regression/acceptance.

Every input calls `patchProposalContract()` on the full object. Creating a candidate sends the complete `contract` plus untouched `classification_map` and `subcategory_dimensions` to the revision endpoint. Full JSON and diff open in `SecondaryDrawer`; do not reconstruct a whitelist object.

- [ ] **Step 7: Verify Proposal load-edit-create-candidate-reopen**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_proposal_text_contract.py backend/tests/test_proposal_text_aggregator.py backend/tests/test_proposal_text_integration.py backend/tests/test_category_evaluation_v3_config_api.py -q
npm --prefix frontend run contract:mechanism-editor
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

Expected: all pass; current frozen PDF score tests remain unchanged.

- [ ] **Step 8: Commit the Proposal editor**

```bash
git add backend/app/proposal_text_contract.py backend/tests/test_proposal_text_contract.py backend/tests/test_category_evaluation_v3_config_api.py frontend/src/features/mechanism-config/proposal-text-contract.ts frontend/src/features/mechanism-config/proposal-text-editor.tsx frontend/src/features/mechanism-config/registry.tsx frontend/scripts/check-mechanism-editor-contract.ts
git commit -m "feat: add lossless Proposal PDF contract editor"
```

### Task 6: Add bonus and dimension-cap contract validation without changing old mode selection

**Files:**
- Modify: `backend/app/category_evaluation_contract.py`
- Modify: `backend/app/dimension_composition.py`
- Modify: `backend/tests/test_deduction_rule_validator.py`
- Modify: `backend/tests/test_dimension_composition.py`

**Interfaces:**
- Produces: `BonusRule(rule_id, description, bonus, tags)`.
- Produces: `dimension_rule_mode(dimension: Any) -> Literal["grade_fallback", "deduction_v1", "bonus_cap_v2"]`.
- Produces: `validate_dimension_rules(dimension, dimension_key) -> None` enforcing shared ID uniqueness.
- Old dimensions with neither `deduction_rules` nor `bonus_rules` remain `grade_fallback`; old dimensions with only non-empty `deduction_rules` remain `deduction_v1`.

- [ ] **Step 1: Write failing validation tests**

Add exact cases:

```python
def test_bonus_rule_requires_positive_value_and_chinese_description() -> None:
    rule = BonusRule.model_validate({
        "rule_id": "composition_clear",
        "description": "构图层级清晰完整",
        "bonus": 8,
        "tags": ["构图"],
    })
    assert rule.bonus == 8

def test_rule_ids_are_unique_across_deduction_and_bonus() -> None:
    dimension = image_dimension()
    dimension["bonus_rules"] = [{
        "rule_id": dimension["deduction_rules"][0]["rule_id"],
        "description": "重复标识的正向规则",
        "bonus": 5,
    }]
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config_with(dimension))
    assert excinfo.value.code.endswith("rule_id_duplicate")

def test_old_contract_mode_is_not_changed_by_read_defaults() -> None:
    old = legacy_grade_dimension()
    assert dimension_rule_mode(old) == "grade_fallback"
    assert "bonus_rules" not in old
```

Cover cap values `0`, `100`, `-1`, `101`, `nan`; allow only-bonus, only-deduction and mixed v2 dimensions; reject v2/grade mixing within one track.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_deduction_rule_validator.py backend/tests/test_dimension_composition.py -q
```

Expected: FAIL because `BonusRule`, cap validation and the three-mode selector do not exist.

- [ ] **Step 3: Implement explicit mode detection and validation**

Use raw field presence, not normalized defaults:

```python
def dimension_rule_mode(dimension: Any) -> str:
    if not isinstance(dimension, dict):
        return "grade_fallback"
    if "bonus_rules" in dimension or "dimension_score_cap" in dimension:
        return "bonus_cap_v2"
    if "deduction_rules" in dimension:
        return "deduction_v1"
    return "grade_fallback"
```

For `bonus_cap_v2`, require both arrays to exist, permit either one empty, require at least one combined rule, require finite cap `0..100`, and require combined `rule_id` uniqueness. Preserve current `validate_deduction_rules()` behavior for `deduction_v1`, including its non-empty array requirement.

- [ ] **Step 4: Run focused validation and legacy regression tests**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_deduction_rule_validator.py backend/tests/test_dimension_composition.py backend/tests/test_dimension_grade_bridge.py backend/tests/test_space_zero_drift_replay.py -q
git diff --check
```

Expected: all pass; old grade fallback and old rule-deduction fixtures retain their original mode.

- [ ] **Step 5: Commit contract validation**

```bash
git add backend/app/category_evaluation_contract.py backend/app/dimension_composition.py backend/tests/test_deduction_rule_validator.py backend/tests/test_dimension_composition.py
git commit -m "feat: validate image bonus rules and dimension caps"
```

### Task 7: Implement deterministic bonus/cap scoring, evidence, and provider fallback

**Files:**
- Modify: `backend/app/dimension_deduction_bridge.py`
- Modify: `backend/app/worker_v3_authoritative.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/tests/test_multimodal_deduction_bridge.py`
- Modify: `backend/tests/test_dimension_deduction_aggregator.py`
- Modify: `backend/tests/test_worker_v3_authoritative.py`
- Modify: `backend/tests/test_space_zero_drift_replay.py`

**Interfaces:**
- Produces: `RULE_COMPOSITION_V2 = "dimension-rule-composition-v2-bonus-cap"` and a new bridge version.
- New normalized dimension output shape is `{ hit_rules: RuleHit[], hit_bonus_rules: RuleHit[] }`.
- `compose_rule_scores(config, dimension_output)` returns aggregator-compatible `deductions` plus detailed bonus/cap evidence.
- Existing `compose_rule_deductions()` remains the entry point for frozen deduction-v1 contracts or delegates only after explicit mode selection.

- [ ] **Step 1: Write failing math and evidence tests**

Add deterministic examples using one dimension with share `30`:

```python
def test_bonus_offsets_deduction_before_cap() -> None:
    result = compose_rule_scores(
        config=config(cap=90, deduction=20, bonus=8),
        dimension_output=hits(deduction=True, bonus=True),
    )
    evidence = result["evidence"]["visual_structure"]
    assert evidence["raw_dimension_score"] == 88
    assert evidence["dimension_score"] == 88
    assert evidence["point_contribution"] == 26.4
    assert result["deductions"]["visual_structure"] == 3.6

def test_dimension_cap_limits_unpenalized_dimension() -> None:
    result = compose_rule_scores(config=config(cap=80), dimension_output=hits())
    assert result["evidence"]["visual_structure"]["dimension_score"] == 80
```

Cover only-bonus, both hits, deduction over 100, cap 0/100, duplicate provider hits, unknown IDs, complete dimension-key enforcement, track cap and hard-defect ordering.

- [ ] **Step 2: Write failing provider and replay tests**

Assert the prompt contains both “扣分规则” and “加分规则”, normalized output includes both arrays, and provider failure returns both empty plus warning. Assert warning propagates to scoring evidence and `needs_review=True` without nulling the deterministic score.

Add byte-for-byte comparisons for existing old fixtures:

```python
assert replay_old_frozen_fixture() == OLD_EXPECTED_JSON
assert old_result["composition_version"] == "dimension-rule-composition-v1"
```

- [ ] **Step 3: Run tests to verify the new behavior fails**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_multimodal_deduction_bridge.py backend/tests/test_dimension_deduction_aggregator.py backend/tests/test_worker_v3_authoritative.py backend/tests/test_space_zero_drift_replay.py -q
```

Expected: FAIL because bonus hits, cap math and warning review propagation are absent.

- [ ] **Step 4: Implement normalized v2 provider output and prompt**

Validate deduction and bonus IDs against separate configured maps but reject duplication across both hit arrays. The prompt must require independent Chinese evidence and `high|medium|low` confidence for every hit; the model still must not output score or level.

On provider exception, return every configured dimension with both arrays empty, the existing warning, prompt identity and raw payload `None`. Local contract corruption remains outside the provider `try` block and fails closed.

- [ ] **Step 5: Implement the exact composition formula**

For each dimension:

```python
raw_score = 100.0 - raw_deduction + raw_bonus
dimension_score = min(max(raw_score, 0.0), float(dimension["dimension_score_cap"]))
share = float(dimension["weight"]) * weight_scale
contribution = round(share * dimension_score / 100.0, 4)
point_deduction = round(share - contribution, 4)
```

Record raw/applied deduction and bonus totals, pre-cap score, post-cap score, cap reason, share, weight mode, contribution, point deduction and both hit arrays. Return aggregator-compatible point deductions so existing media modifiers, hard defects, track cap and final `0..100` clamp remain in their current order.

- [ ] **Step 6: Route modes and mark provider warnings for review**

At the frozen contract boundary choose `grade_fallback`, `deduction_v1` or `bonus_cap_v2` before calling the provider/composer. Do not add default fields to the frozen JSON. When the subjective provider warning exists, keep the computed score but set the evaluation review flag and include the warning in scoring evidence.

- [ ] **Step 7: Run focused and legacy regression verification**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_multimodal_deduction_bridge.py backend/tests/test_dimension_deduction_aggregator.py backend/tests/test_worker_v3_authoritative.py backend/tests/test_space_zero_drift_replay.py backend/tests/test_category_evaluation_aggregator.py -q
git diff --check
```

Expected: new v2 math passes and all frozen old outputs are unchanged.

- [ ] **Step 8: Commit scoring changes**

```bash
git add backend/app/dimension_deduction_bridge.py backend/app/worker_v3_authoritative.py backend/app/worker.py backend/tests/test_multimodal_deduction_bridge.py backend/tests/test_dimension_deduction_aggregator.py backend/tests/test_worker_v3_authoritative.py backend/tests/test_space_zero_drift_replay.py
git commit -m "feat: score image bonus rules with dimension caps"
```

### Task 8: Extend append-only node correction to bonus-rule hits

**Files:**
- Modify: `backend/app/node_correction_api.py`
- Modify: `backend/app/category_evaluation_contract.py`
- Modify: `backend/tests/test_node_correction.py`
- Modify: `frontend/src/lib/node-correction.ts`
- Modify: `frontend/src/pages/node-correction-editor.tsx`
- Modify: `frontend/scripts/check-node-correction-editor.ts`

**Interfaces:**
- Keeps `node_type="dimension_rule"` for both polarities.
- Accepts `node_path="dimension.<dimension_key>.hit_rules[.<rule_id>]"` and `node_path="dimension.<dimension_key>.hit_bonus_rules[.<rule_id>]"`.
- `RuleDefinition` gains `kind: "deduction" | "bonus"` and `value: number`; old deduction-only result adapters still populate the old UI correctly.

- [ ] **Step 1: Write failing backend correction tests**

Add a v2 frozen evaluation, submit a bonus correction, and assert:

```python
response = client.post(
    f"/api/evaluation-results/{result.id}/correct-node",
    json={
        "correction_key": "bonus-add-1",
        "node_type": "dimension_rule",
        "node_path": "dimension.visual_structure.hit_bonus_rules.composition_clear",
        "old_value": None,
        "new_value": {
            "rule_id": "composition_clear",
            "confidence": "high",
            "evidence": "主体、留白与层级关系清晰",
        },
        "evidence": [],
        "reason": "人工确认正向规则命中",
    },
)
assert response.status_code == 200
assert response.json()["score"] > original_score
assert history[-1]["node_path"].endswith("hit_bonus_rules.composition_clear")
```

Assert idempotent correction keys, stale `old_value` conflict, unknown bonus ID rejection, and old deduction-only result behavior.

- [ ] **Step 2: Write failing frontend correction contract assertions**

Extend the fixture with `bonus_rules` and `hit_bonus_rules`; assert the built correction node reports separate hit counts, polarity labels and evidence lines, and emits the canonical bonus path.

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_node_correction.py -q
npm --prefix frontend run contract:node-correction
```

Expected: bonus paths and definitions are unsupported.

- [ ] **Step 4: Generalize backend dimension-node access**

Parse the second path segment as one of `hit_rules` or `hit_bonus_rules`. Validate both with the same hit model, but cross-check the target rule ID against the matching frozen configured array before mutation. Recompute through the v2 composition using the same frozen `v3_context`; append correction history and never overwrite the original provider payload.

- [ ] **Step 5: Generalize frontend node construction and editor labels**

Normalize configured rules into:

```ts
type RuleDefinition = {
  rule_id: string
  description: string
  kind: "deduction" | "bonus"
  value: number
  tags?: string[]
}
```

Render “扣分 20” and “加分 8” explicitly. Preserve existing evidence delta, confidence normalization, read-only mismatch detection and old result compatibility.

- [ ] **Step 6: Run focused correction verification**

Run:

```bash
.venv312/bin/python -m pytest backend/tests/test_node_correction.py backend/tests/test_worker_v3_authoritative.py -q
npm --prefix frontend run contract:node-correction
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

Expected: all pass; correction history remains append-only.

- [ ] **Step 7: Commit correction support**

```bash
git add backend/app/node_correction_api.py backend/app/category_evaluation_contract.py backend/tests/test_node_correction.py frontend/src/lib/node-correction.ts frontend/src/pages/node-correction-editor.tsx frontend/scripts/check-node-correction-editor.ts
git commit -m "feat: correct bonus rule hits with frozen replay"
```

### Task 9: Add bonus/cap controls to the image mechanism editor

**Files:**
- Modify: `frontend/src/features/mechanism-config/image-rule-editor.tsx`
- Create: `frontend/src/features/mechanism-config/image-rule-contract.ts`
- Modify: `frontend/scripts/check-mechanism-editor-contract.ts`

**Interfaces:**
- Produces: `imageRuleViewDefaults(dimension): { dimensionScoreCap; deductionRules; bonusRules }` without mutating the draft.
- Produces: `prepareImageRulePayload(draft): EditableConfig`, which writes explicit v2 defaults only into the outgoing cloned payload.
- The route page uses the plugin's `prepareForSave` hook before validate/save; old loaded drafts remain byte-identical until a save is requested.

- [ ] **Step 1: Write failing pure-helper and source contracts**

Add assertions:

```ts
const legacy = { key: "visual", weight: 1, deduction_rules: [{ rule_id: "r1" }] }
const view = imageRuleViewDefaults(legacy)
assert.equal(view.dimensionScoreCap, 100)
assert.deepEqual(view.bonusRules, [])
assert.equal("bonus_rules" in legacy, false)

const outgoing = prepareImageRulePayload(draftWithLegacyDimension)
assert.equal(outgoing.subcategory_dimensions.class_one.common_group.schema_definition.dimensions[0].dimension_score_cap, 100)
assert.deepEqual(outgoing.subcategory_dimensions.class_one.common_group.schema_definition.dimensions[0].bonus_rules, [])
```

Inspect the editor for visible “维度分数上限” and “加分规则” controls.

- [ ] **Step 2: Run the contract to verify it fails**

Run: `npm --prefix frontend run contract:mechanism-editor`

Expected: FAIL because helpers and controls do not exist.

- [ ] **Step 3: Implement non-mutating defaults and outgoing upgrade**

The view helper reads absent cap as 100 and absent bonuses as `[]`. `prepareImageRulePayload()` deep clones the whole editable config, then for every deduction-v1 dimension writes `dimension_score_cap=100` and `bonus_rules=[]`; it must not touch grade-fallback dimensions.

Wire the plugin's validate/save callbacks through this outgoing payload so the first save creates a new revision while the loaded historical JSON remains unchanged until submission.

- [ ] **Step 4: Implement image rule UI controls**

For each rule-mode dimension show:

- numeric cap input `min=0 max=100 step=1`;
- computed maximum track contribution from frozen weight share;
- deduction list and bonus list with independent add/remove actions;
- shared rule-ID validation message when a duplicate appears across lists;
- Chinese description, positive value and comma-separated tags for each bonus.

Keep JSON, revision history and validation evidence in a `SecondaryDrawer`, not permanently expanded.

- [ ] **Step 5: Run frontend and API round-trip verification**

Run:

```bash
npm --prefix frontend run contract:mechanism-editor
npm --prefix frontend run contract:v3-only
npm --prefix frontend run lint
npm --prefix frontend run build
.venv312/bin/python -m pytest backend/tests/test_category_evaluation_v3_config_api.py backend/tests/test_deduction_rule_validator.py -q
git diff --check
```

Expected: all pass; candidate create/reload retains cap and bonus fields, creates a new candidate revision and leaves active runtime JSON/hash unchanged.

- [ ] **Step 6: Commit image editor controls**

```bash
git add frontend/src/features/mechanism-config/image-rule-editor.tsx frontend/src/features/mechanism-config/image-rule-contract.ts frontend/scripts/check-mechanism-editor-contract.ts
git commit -m "feat: edit image bonus rules and dimension caps"
```

### Task 10: Rebuild baseline regression around the primary correction workflow

**Files:**
- Create: `frontend/src/features/baseline-regression/baseline-set-dialog.tsx`
- Create: `frontend/src/features/baseline-regression/run-config-drawer.tsx`
- Create: `frontend/src/features/baseline-regression/metrics-drawer.tsx`
- Create: `frontend/src/features/baseline-regression/run-history-drawer.tsx`
- Create: `frontend/src/features/baseline-regression/correction-workbench.tsx`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/scripts/check-information-architecture-contract.ts`

**Interfaces:**
- `BaselineSetDialog` receives existing asset/package queries and calls the existing create-set mutation.
- `RunConfigDrawer` receives prompt choices and calls the existing create-run mutation.
- `MetricsDrawer` and `RunHistoryDrawer` are read-only consumers of `BaselineRegressionDetail` and set summaries.
- `CorrectionWorkbench` consumes one `BaselineRegressionItem`, embeds the existing `NodeCorrectionEditor`, and updates URL search params `run` and `item`.
- Produces pure `baselineAcceptanceProgress(items)` over evaluable rows: `reviewed` means `evaluation.human_review.decision in {approved, corrected, rejected}`; `complete` means every evaluable row is reviewed and the run is terminal.
- “完成人工验收” is computed UI guidance only. No backend endpoint, round-level database status, mechanism publication or label publication is added in this task.

- [ ] **Step 1: Write the failing information-architecture contract**

Assert source boundaries and main-line labels:

```ts
assert.match(pageSource, /选择基准集/)
assert.match(pageSource, /逐条确认与纠偏/)
assert.match(pageSource, /完成人工验收/)
assert.match(pageSource, /BaselineSetDialog/)
assert.match(pageSource, /RunConfigDrawer/)
assert.match(pageSource, /MetricsDrawer/)
assert.match(pageSource, /CorrectionWorkbench/)
assert.doesNotMatch(pageSource, /function BaselineCorrectionPanel/)
assert.match(workbenchSource, /<NodeCorrectionEditor/)
assert.match(workbenchSource, /返回轮次列表/)
assert.deepEqual(baselineAcceptanceProgress(reviewedItems), { reviewed: 3, total: 3, complete: true })
assert.deepEqual(baselineAcceptanceProgress(partiallyReviewedItems), { reviewed: 1, total: 3, complete: false })
```

- [ ] **Step 2: Run the contract to verify it fails**

Run: `npm --prefix frontend run contract:information-architecture`

Expected: FAIL because all secondary content is still inside the 2,000-line page.

- [ ] **Step 3: Extract dialogs and read-only drawers without behavior changes**

Move the existing create-set form and asset selection into `BaselineSetDialog`. Move run parameters into `RunConfigDrawer`; metrics/confusion matrix into `MetricsDrawer`; historical runs/logs into `RunHistoryDrawer`. Keep existing TanStack query keys, API payloads, toast copy and mutation functions unchanged.

Each drawer uses the shared `SecondaryDrawer`; the create dialog uses `ConfirmDialog` or a dedicated Radix Dialog with the same focus contract.

- [ ] **Step 4: Make the route page show only the primary sequence**

The default page layout must contain:

```text
compact category/set/run selector
status strip: total, pending confirmation, deviations, acceptance progress
high-frequency filters and previous/next controls
large selected-item comparison and confirm/correct actions
secondary buttons: create set, run config, metrics, history
```

Acceptance progress counts terminal rows with an attached evaluation as evaluable; failed/unscored rows remain explicit blockers and are shown separately rather than silently counted as reviewed. The “完成人工验收” control is enabled only when the run is terminal, every evaluable row has one of the existing per-result review decisions, and there are no unresolved failed/unscored blockers. Clicking it shows a local completion summary and the existing next-step links; it does not persist a new state or publish anything.

At `1280×720`, use a two-column content grid with the selected-item operation pane at least `minmax(0, 1fr)` and no always-open metrics/form column. Remove mobile-specific acceptance criteria and do not introduce horizontal document overflow.

- [ ] **Step 5: Implement URL-restorable correction workbench mode**

Use `useSearchParams()` so `?run=<id>&item=<item-id>&mode=correction` restores the current item after refresh. The workbench shows asset, frozen truth, candidate result, node evidence and `NodeCorrectionEditor`, with an explicit action that removes `mode`/`item` and returns to the run list.

Do not duplicate correction submit logic. After successful correction, invalidate the existing `baseline-regression` and evaluation query keys.

- [ ] **Step 6: Run frontend verification**

Run:

```bash
npm --prefix frontend run contract:information-architecture
npm --prefix frontend run contract:node-correction
npm --prefix frontend run contract:balanced-100
npm --prefix frontend run test:lightbox
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

Expected: all pass; the main page no longer defines the extracted secondary panels.

- [ ] **Step 7: Commit baseline information architecture**

```bash
git add frontend/src/features/baseline-regression frontend/src/pages/baseline-regression-page.tsx frontend/scripts/check-information-architecture-contract.ts
git commit -m "refactor: focus baseline regression on correction work"
```

### Task 11: Record the full-route audit and long-term architecture decisions

**Files:**
- Create: `docs/discussion/frontend-information-architecture-audit-20260812.md`
- Create: `docs/decisions/0044-frontend-workspaces-and-mechanism-profile-plugins.md`
- Modify: `docs/decisions/README.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- The audit is the only output for non-batch routes and must not imply implementation authorization.
- ADR-0044 records shared workspaces, controlled profile plugins, candidate revision storage, Proposal separation and bonus/cap replay boundaries.
- The Gap section must record that candidate V3 revisions are not yet bindable to `PromptRegressionRun` / `EvaluationPackage` / `MechanismRelease`; no activation API is implemented in this batch. This is returned to the 标签体系 Owner for next-phase freeze.

- [ ] **Step 1: Generate the reachable-route inventory from source**

Run:

```bash
rg -n '<Route path=' frontend/src/App.tsx
rg -n 'to: "/workflow|to="/workflow|to: "/legacy|to="/legacy' frontend/src
```

Use the union as the audit inventory. Every reachable route gets one row with source file, user, primary task, current flattening problem, level-one content, secondary carrier, reusable component, dependency/risk and recommended batch.

- [ ] **Step 2: Write ADR-0044 with exact non-expansion language**

Record these accepted decisions:

```text
一级页面只保留主线决策信息和操作
复杂配置/历史/证据/指标进入宽抽屉
合同编辑与逐条纠偏使用独立工作台
机制编辑器按受控 profile_type 插件扩展
未知机制读取可用、写入 fail-closed
现有 active V3 行是运行时投影，编辑只创建不可变候选 revision
普通 PUT、level-scale PUT 和 status PUT 不得绕过既有人工发布门禁
Proposal PDF 不套图像规则数学
旧冻结合同不因读取默认值切换 composition
```

State explicitly that other routes, 3D/SU plugins, production release and real stock reruns remain outside this batch. Add a concrete Gap table with owner=`标签体系` and next freeze input:

```text
candidate revision -> candidate-aware regression snapshot
regression evidence -> EvaluationPackage manifest binding
approved package -> atomic active projection switch + MechanismRelease
append-only rollback release -> prior revision projection restore
```

These are architecture gaps, not hidden implementation steps in this batch.

- [ ] **Step 3: Update the decision index and current status**

Add ADR-0044 to `docs/decisions/README.md`. Add a new top `PROJECT_STATUS.md` section containing branch/base, implemented pages, profile registry, candidate storage/no-activation boundary, scoring compatibility, verification evidence and still-excluded items. Do not overwrite historical sections.

- [ ] **Step 4: Verify documentation consistency**

Run:

```bash
rg -n '两套独立项目|实验台仅为评测工具|仅用于评测工具' PRODUCT.md PROJECT_STATUS.md docs/decisions docs/discussion
rg -n 'TBD|TODO|FIXME|待定|待补' docs/discussion/frontend-information-architecture-audit-20260812.md docs/decisions/0044-frontend-workspaces-and-mechanism-profile-plugins.md PROJECT_STATUS.md
git diff --check
```

Expected: first command has no contradictory product-positioning hit; second command has no placeholder hit.

- [ ] **Step 5: Commit docs and status**

```bash
git add docs/discussion/frontend-information-architecture-audit-20260812.md docs/decisions/0044-frontend-workspaces-and-mechanism-profile-plugins.md docs/decisions/README.md PROJECT_STATUS.md
git commit -m "docs: record LabelLab workspace and mechanism architecture"
```

### Task 12: Run full verification, Codeup review, merge, and guarded test deployment

**Files:**
- Modify only if verification exposes an in-scope defect; any fix must return to the responsible earlier task's test-first cycle.
- Evidence output must remain outside Git unless the repository already tracks the corresponding verification document.

**Interfaces:**
- Codeup origin must equal `git@codeup.aliyun.com:3d66/tepeng/3d66.label-system.git` or the deployment script's accepted HTTPS equivalent.
- Deployment consumes Codeup `origin/main` only and uses the guarded server rollback script.
- Browser acceptance uses Edge and the authenticated account already stored there; do not export cookies/passwords.

- [ ] **Step 1: Run the complete deterministic backend suite**

Run:

```bash
LABELLAB_TEST_DATA="$(mktemp -d)"
DATA_DIR="$LABELLAB_TEST_DATA" .venv312/bin/python -m pytest backend/tests -q -k 'not test_macos_keychain_real_isolated_round_trip_update_and_cleanup'
```

Expected: all code tests pass. Report the real-Keychain environment test separately; do not claim it passed if it remains excluded or returns `OSStatus -25293`.

- [ ] **Step 2: Run every frontend contract and production build with a real SHA**

Run:

```bash
npm --prefix frontend run contract:dimensions
npm --prefix frontend run contract:v3-only
npm --prefix frontend run contract:node-correction
npm --prefix frontend run contract:proposal-pdf
npm --prefix frontend run contract:balanced-100
npm --prefix frontend run contract:level-scale-thinking
npm --prefix frontend run contract:model-registry
npm --prefix frontend run contract:information-architecture
npm --prefix frontend run contract:mechanism-editor
npm --prefix frontend run test:lightbox
npm --prefix frontend run lint
LABEL_LAB_BUILD_SHA="$(git rev-parse --short=7 HEAD)" npm --prefix frontend run build
rg -n 'build dev' frontend/dist && exit 1 || true
git diff --check
git status --short
```

Expected: all pass; the built bundle does not contain the test-deployment fallback `build dev`; worktree contains no untracked build/dependency artifacts.

- [ ] **Step 3: Perform local desktop browser acceptance before pushing**

Start the app with an isolated `DATA_DIR`, then use Edge at `1440×900` and `1280×720` to verify:

```text
高级设置无顶部三个重复入口，预览/V3入口在常规列表
LabelLab v0.2.0 · build <current SHA>
proposal_text_pdf loads, validates, creates a candidate and reloads that revision with extension fields preserved; active stays unchanged
unknown profile shows read-only summary and JSON, with no uncaught console error
image profile edits deduction, bonus and cap, then creates/reopens a candidate; active stays unchanged
revision history labels active/candidate clearly and exposes no direct status activation control
baseline main pane dominates; dialogs/drawers/workbench open, close and restore focus
correction workbench refresh preserves run/item identity
baseline acceptance progress equals existing per-item review decisions and does not persist a round status
no document-level horizontal overflow at either desktop size
```

Stop if any route white-screens, any save loses fields, or any old replay changes.

- [ ] **Step 4: Review branch history and request code review**

Run:

```bash
git fetch origin main
git log --oneline --decorate origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD
git status --short --branch
```

Invoke `superpowers:requesting-code-review`. Resolve only in-scope findings, rerun the affected task tests and then repeat Steps 1–3.

- [ ] **Step 5: Push the feature branch and create the Codeup MR**

Run:

```bash
git remote set-url origin git@codeup.aliyun.com:3d66/tepeng/3d66.label-system.git
git push -u origin codex/frontend-information-architecture-v1
```

Create an MR targeting `main` with the approved scope, test evidence, browser evidence, rollback point and explicit non-goals. Do not include secrets, cookies, local paths to credential stores or raw user passwords.

- [ ] **Step 6: Merge only after the MR head and checks match the verified commit**

Before merge, record the feature head:

```bash
FEATURE_HEAD="$(git rev-parse HEAD)"
git ls-remote origin refs/heads/codex/frontend-information-architecture-v1
```

Confirm the remote branch SHA equals `$FEATURE_HEAD`, MR targets `main`, required review/checks pass, and no new Codeup `main` commit creates a conflict. Merge through Codeup, then run:

```bash
git fetch origin main
git merge-base --is-ancestor "$FEATURE_HEAD" origin/main
git log -3 --oneline origin/main
```

Expected: the feature head is an ancestor of Codeup `origin/main`.

- [ ] **Step 7: Dry-run and deploy the exact merged Codeup main**

Run:

```bash
python3 scripts/deploy-test.py --dry-run
python3 scripts/deploy-test.py --yes
```

The server script must capture its previous commit, build with the merged short SHA, wait for container health and automatically restore the previous commit if build/health fails.

- [ ] **Step 8: Verify the shared test environment in Edge**

Open `http://192.168.1.35:8081` in Edge. Verify `/api/health`, displayed build SHA, the three reconstructed pages and console errors at both desktop sizes. Compare:

```text
page build SHA == deployed origin/main short SHA
server Git HEAD == deployed origin/main full SHA
health status == ok
```

If post-deploy browser acceptance fails, use the existing guarded rollback path to restore the prior server commit and report the failing criterion; do not patch directly on the server.

- [ ] **Step 9: Final repository and delivery check**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
git diff --check origin/main...HEAD
```

Expected: local branch has no uncommitted changes; Codeup `main` contains the verified feature head; test server is healthy on the matching SHA. Report the dependency audit warnings separately and do not run `npm audit fix` unless separately approved.

---

## Stop Conditions

Stop the current execution batch and report evidence instead of continuing when any of these occurs:

- the current Codeup `main` is no longer a descendant-compatible base and reconciliation would change the frozen scope;
- full tests fail outside the known real-Keychain environment case;
- old frozen replay output changes by any field;
- Proposal candidate create/reload drops or rewrites an unknown field;
- unknown profile can enter a write path or still white-screens;
- any editor path mutates `CategoryEvaluationV3Config`, calls the retired status/level-scale PUT, or exposes candidate activation without an approved `EvaluationPackage`;
- implementation requires a production write, real stock rerun, credential cleanup, new 3D/SU editor, or non-audited refactor of other pages;
- test deployment cannot prove the exact Codeup SHA or guarded rollback point.
