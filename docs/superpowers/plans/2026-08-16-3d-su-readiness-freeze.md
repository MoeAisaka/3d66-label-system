# 3D/SU 真实闭环接入前置冻结 Implementation Plan

> For agentic workers: use executing-plans or subagent-driven-development. Steps use checkbox syntax.

Goal: 形成可交付给研发的 3D/SU 真实闭环接入就绪包，冻结身份、字段、黄金集、权限、RACI、验收与停止条件，并保持所有外部系统只读/未连接。

Architecture: 复用现有 source_identity_probe、asset_identity、semantic_tag_contracts、SampleSet/黄金集和五队列运行时；新增一个本地只读 readiness manifest，把这些合同绑定为有版本、可哈希、可检查的接入前置清单。readiness 状态必须为 pending_external_signoff，在身份探查、字段 Owner、黄金集和权限均未签认前，不允许生成真实接入或写入配置。

Tech Stack: Python 3.12、Pydantic 2、pytest、现有 SQLAlchemy/SQLite 只读合同、Markdown/JSON 文档。

## Global Constraints

- 产品主体仍为 TPENG 标签实验台（LabelLab），3D/SU 只是第一个真实纵切，不复制平台通用能力。
- 本批只做本地合同、脱敏清单、确定性哈希和测试；不执行 DataWorks/ODPS SQL，不申请权限，不连接真实上游、模型或数据库。
- 不执行真实数据库写入、批量模型调用、正式标签发布、存量覆盖、Codeup 推送、MR 合并或测试服部署。
- 身份候选键固定为 source_system + res_type + ll_id；res_type=1 为 3D，res_type=6 为 SU；重复或多 res_id 冲突时 fail-closed。
- 平台语义字段与 3D/SU 专有字段必须分层；not_applicable、not_detected、needs_review 不得折叠为空字符串。
- 质量默认门槛为 Precision ≥ 0.80、Recall ≥ 0.70；最终降低门槛必须另行 Owner 批准。
- 权限清单只允许 Select/Describe，明确排除 Download、Update、Alter、Drop 和 DML。
- 黄金集修订使用新 revision 或新 SampleSet，不原地覆盖锁定历史。

---

### Task 1: Add a machine-checkable readiness manifest

Files:
- Create backend/app/three_d_readiness.py
- Create backend/tests/test_three_d_readiness.py
- Modify backend/app/source_identity_probe.py only if stable probe metadata is needed

Interfaces:
- build_three_d_readiness_manifest() -> ThreeDReadinessManifest
- validate_three_d_readiness_manifest(manifest) -> ThreeDReadinessManifest
- readiness_manifest_hash(manifest) -> str

- [ ] Write failing tests for pending_external_signoff, all external_effects false, exact identity table/key, Select/Describe permissions, stable probe hash, and rejection of ready status without signed evidence.
- [ ] Run focused tests and confirm collection/import failure because app.three_d_readiness does not exist.
- [ ] Implement immutable Pydantic types for identity, fields, golden_set, permissions, RACI, external_effects and stop_conditions. Reject unknown keys, unsafe table names, missing Owners, weak quality gates, or permissions outside the allow/deny contract.
- [ ] Bind identity.probe_hash to build_three_d_su_identity_probe('aliyun_3d66_dw.dim_res_info_union'); store no query results, credentials or source rows.
- [ ] Run focused tests and commit feat: add 3d su readiness manifest.

### Task 2: Add the研发接入清单 and source/field/golden-set contracts

Files:
- Create docs/contracts/3d-su-readiness-freeze-v1.md
- Create docs/contracts/3d-su-field-signoff-template-v1.csv
- Create docs/contracts/3d-su-golden-set-plan-v1.md
- Create docs/contracts/3d-su-permission-raci-v1.md
- Modify PROJECT_STATUS.md

- [ ] Document the candidate source table, four SELECT probe purposes, signed data window, zero duplicate/conflict decision, probe hash binding and separate Select/Describe approval.
- [ ] Document platform fields space, object, style, material, structural_features, architectural_element, soft_decoration, hard_decoration, color and localized title; extensions only under category.model_3d_su.*. Every row includes owner, whole/single applicability, null semantics, cardinality, vocabulary version, quality gate and rollback release.
- [ ] Document a minimum 100 locked/challenge samples across 3D/SU, whole/single, three evaluation tracks, semantic hard cases and L1-L5 levels. Truth changes create a new revision; no UPDATE to a locked set.
- [ ] Document source discovery permissions, target-table DML as a future separately approved dependency, and product/data/algorithm/platform/reviewer/consumer Owners with expiry and evidence fields.
- [ ] Add project status links and unresolved signoff fields; state explicitly this is pre-freeze, not real ingress readiness.

### Task 3: Validate docs and handoff evidence

Files:
- Create frontend/scripts/check-three-d-readiness-contract.ts
- Modify frontend/package.json
- Create docs/superpowers/receipts/2026-08-16-3d-su-readiness-freeze.md

- [ ] Add a static contract script that checks table names, Select/Describe permissions, model_3d_su, res_type 1/6, 100-sample floor, P/R gates, double human gates and no-external-effects statements.
- [ ] Run npm run contract:three-d-readiness and git diff --check.
- [ ] Write a receipt with branch, commit, manifest hash, exact counts, unresolved dependencies and explicit no real source/model/database contact.

### Task 4: Final local verification and handoff

- [ ] Run new backend focused tests with fresh temporary DATA_DIR.
- [ ] Run the readiness contract, source identity and semantic contract tests, frontend lint and build.
- [ ] Run git diff --check, inspect git status --short and verify no remote write occurred.
- [ ] Commit only this readiness package; do not push, merge or deploy.
- [ ] Return the signed-evidence checklist to the 标签体系 reconstruction thread for Owner/Data/Algorithm signoff.

## Stop Conditions

Stop immediately if implementation connects to DataWorks, calls a real model, writes an external database, requests credentials, activates a contract, publishes labels, or turns pending_external_signoff into production-ready without signed evidence.
