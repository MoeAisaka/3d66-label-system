from __future__ import annotations

import json
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    AgentPlanVersion,
    AutomationPolicy,
    CategoryEvaluationV3Config,
    EvaluationCategoryProfile,
    EvaluationControl,
    ModelConfig,
    OptimizerConfig,
    PromptVersion,
    ReviewWorkflowPolicy,
    SamplingPolicy,
    User,
)
from .prompt_loader import load_prompt_pairs, load_standalone_prompt
from .security import DEFAULT_ADMIN_PASSWORD_HASH


def seed_defaults(db: Session) -> None:
    settings = get_settings()
    if db.scalar(select(User).where(User.username == "sol")) is None:
        db.add(
            User(
                username="sol",
                password_hash=DEFAULT_ADMIN_PASSWORD_HASH,
                display_name="系统管理员",
                role="admin",
            )
        )

    if db.scalar(select(ModelConfig).limit(1)) is None:
        db.add(ModelConfig())

    db.flush()
    from .models import ModelNodeBinding
    primary = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id.asc()))
    if primary is not None:
        existing_nodes = {row[0] for row in db.query(ModelNodeBinding.node_key).all()}
        for node_key in ("evaluation_main", "pdf_summary", "optimization", "benchmark", "diagnostic"):
            if node_key not in existing_nodes:
                db.add(ModelNodeBinding(node_key=node_key, model_config_id=primary.id))

    if db.scalar(select(OptimizerConfig).limit(1)) is None:
        db.add(OptimizerConfig())

    if db.get(EvaluationControl, 1) is None:
        db.add(EvaluationControl(id=1))

    if db.get(SamplingPolicy, 1) is None:
        db.add(SamplingPolicy(id=1))

    if db.get(ReviewWorkflowPolicy, 1) is None:
        db.add(ReviewWorkflowPolicy(id=1, initial_reviewers=1))

    if db.get(AutomationPolicy, 1) is None:
        db.add(
            AutomationPolicy(
                id=1,
                enabled=False,
                dry_run=True,
                daily_budget_micros=0,
            )
        )

    if db.scalar(select(AgentPlanVersion).limit(1)) is None:
        db.add(
            AgentPlanVersion(
                name="A预检—B美感—高风险保守复核",
                version="controlled-agent-plan-v1",
                plan_json=(
                    '{"roles":["precheck","aesthetic","risk_review"],'
                    '"routing":"controlled","max_rounds":3}'
                ),
                status="published",
                created_by="system",
            )
        )

    if db.scalar(select(PromptVersion).limit(1)) is None:
        pairs = load_prompt_pairs(settings.prompt_source)
        db.add_all(
            [
                PromptVersion(
                    stage="A",
                    name="图片分类、形态与画质预检",
                    version="prompt-a-v2.1",
                    system_prompt=pairs["A"].system,
                    user_prompt=pairs["A"].user,
                    rubric_version="rubric-v2.1",
                    status="published",
                    source="imported",
                    change_note="来自用户提供的 Doubao-Seed-2.0-Lite V2.1 提示词",
                ),
                PromptVersion(
                    stage="B",
                    name="空间与建筑美感维度评价",
                    version="prompt-b-v2.1",
                    system_prompt=pairs["B"].system,
                    user_prompt=pairs["B"].user,
                    rubric_version="rubric-v2.1",
                    status="published",
                    source="imported",
                    change_note="来自用户提供的 Doubao-Seed-2.0-Lite V2.1 提示词",
                ),
            ]
        )

    split_prompts = (
        {
            "stage": "A",
            "name": "空间图片范围、分类与画质预检",
            "version": "space_precheck_v1.3-split.1",
            "filename": "space-precheck-v1.3-split.1.md",
            "note": "从 space_aesthetic_v1.3-draft.2 拆分的 A 阶段预检提示词",
        },
        {
            "stage": "B",
            "name": "空间与建筑八维美感评价",
            "version": "space_aesthetic_dimensions_v1.3-split.1",
            "filename": "space-aesthetic-dimensions-v1.3-split.1.md",
            "note": "从 space_aesthetic_v1.3-draft.2 拆分的 B 阶段美感提示词",
        },
        {
            "stage": "A",
            "name": "空间图片严格预检与拍摄方式校准",
            "version": "space_precheck_v1.3-split.2",
            "filename": "space-precheck-v1.3-split.1.md",
            "calibration_filename": "space-precheck-v1.3-split.2-calibration.md",
            "note": "Split.2 严格区分专业摄影、现场记录和随拍，修正拍摄方式误判",
        },
        {
            "stage": "B",
            "name": "空间八维美感严格校准评价",
            "version": "space_aesthetic_dimensions_v1.3-split.2",
            "filename": "space-aesthetic-dimensions-v1.3-split.1.md",
            "calibration_filename": "space-aesthetic-v1.3-split.2-calibration.md",
            "note": "Split.2 以3级为基准，增加4/5级证据门槛和防机械同分校验",
        },
        {
            "stage": "A",
            "name": "空间图片画质与摄影类型严格校准",
            "version": "space_precheck_v1.3-split.3",
            "filename": "space-precheck-v1.3-split.1.md",
            "calibration_filenames": [
                "space-precheck-v1.3-split.2-calibration.md",
                "space-precheck-v1.3-split.3-calibration.md",
            ],
            "note": "Split.3 收紧画质正常和专业摄影定义，修正效果图、噪点、曝光与呈现问题误判",
        },
        {
            "stage": "B",
            "name": "空间八维美感差异化校准评价",
            "version": "space_aesthetic_dimensions_v1.3-split.3",
            "filename": "space-aesthetic-dimensions-v1.3-split.1.md",
            "calibration_filenames": [
                "space-aesthetic-v1.3-split.2-calibration.md",
                "space-aesthetic-v1.3-split.3-calibration.md",
            ],
            "note": "Split.3 独立评价八个维度，禁止3级或4级机械同分并强化缺陷降级映射",
        },
        {
            "stage": "A",
            "name": "空间图片画质与摄影类型业务硬约束",
            "version": "space_precheck_v1.3-split.3.1",
            "filename": "space-precheck-v1.3-split.1.md",
            "calibration_filenames": [
                "space-precheck-v1.3-split.2-calibration.md",
                "space-precheck-v1.3-split.3-calibration.md",
            ],
            "note": "Split.3.1 增加局部空间、水印、边框和未完工不得判为画质正常或专业摄影的硬约束",
        },
        {
            "stage": "A",
            "name": "空间图片Lite精简预检",
            "version": "space_precheck_v1.4-lite.1",
            "filename": "space-precheck-v1.4-lite.1.md",
            "note": "Lite精简候选版：删除重复校准段，按范围、形态、拍摄方式、画质决策树执行",
        },
        {
            "stage": "B",
            "name": "空间八维美感Lite精简评价",
            "version": "space_aesthetic_dimensions_v1.4-lite.1",
            "filename": "space-aesthetic-v1.4-lite.1.md",
            "note": "Lite精简候选版：统一等级锚点、反高分规则和八维输出，等待黄金回归后发布",
        },
        {
            "stage": "B",
            "name": "空间八维美感Lite等级上限校准",
            "version": "space_aesthetic_dimensions_v1.4-lite.2",
            "filename": "space-aesthetic-v1.4-lite.1.md",
            "calibration_filename": "space-aesthetic-v1.4-lite.2-calibration.md",
            "rubric_version": "space-rubric-v1.4",
            "note": "Lite精简候选版：随拍图或画质受损时最终等级最高为L2",
        },
    )
    for item in split_prompts:
        exists = db.scalar(
            select(PromptVersion.id).where(PromptVersion.version == item["version"])
        )
        if exists is not None:
            continue
        prompt = load_standalone_prompt(settings.project_root / "prompts" / item["filename"])
        calibration_filenames = item.get("calibration_filenames") or [
            item.get("calibration_filename")
        ]
        calibration = "\n\n".join(
            (settings.project_root / "prompts" / filename).read_text(encoding="utf-8")
            for filename in calibration_filenames
            if filename
        )
        db.add(
            PromptVersion(
                stage=item["stage"],
                name=item["name"],
                version=item["version"],
                system_prompt=(prompt.system + "\n\n" + calibration).strip(),
                user_prompt=prompt.user,
                rubric_version=item.get("rubric_version", "space-rubric-v1.3"),
                status="draft",
                source="split",
                change_note=item["note"],
            )
        )
    _seed_inspiration_image_prompts(db, settings)
    _seed_inspiration_image_v3_config(db)
    _seed_v3_only_category_clones(db)
    from .proposal_text_seed import seed_proposal_text_pdf
    seed_proposal_text_pdf(db)
    # Existing grade-era rows are upgraded in place; fresh rows are already in
    # rule form so this converges without an extra revision bump.
    from .migrations.upgrade_v3_to_rule_deduction import (
        upgrade_v3_to_rule_deduction,
    )
    upgrade_v3_to_rule_deduction(db)
    db.commit()


def _seed_inspiration_image_prompts(db: Session, settings) -> None:
    """Seed versioned inspiration_image A/B ``PromptVersion`` rows idempotently.

    调用 A 预检（红线/赛道/媒介/hard_defects）与人工校准版调用 B live as plain
    single-block prompt files under
    ``prompts/``.  Each file is loaded wholesale as ``system_prompt`` (they carry
    no ``### System Prompt`` split markers); ``user_prompt`` is empty since the
    per-image context is injected by the worker at call time.  Only inserted when
    the ``version`` is absent, so re-running seed_defaults never duplicates.
    """
    prompt_specs = (
        {
            "stage": "A",
            "name": "灵感图人工校准版预检（红线/赛道/媒介/硬伤）",
            "version": "inspiration-a-v2-human-calibrated-20260805",
            "filename": "inspiration_image_call_a_rev3.txt",
            "pipeline_scope": "full_pipeline",
            "note": "2026-08-05 rev3 冻结版：同步十条硬伤与 trait 直出字段",
        },
        {
            "stage": "A",
            "name": "灵感图 rev4 决定性信号高召回前检",
            "version": "inspiration-a-v3-hard-defect-recall-rev4-20260805",
            "filename": "inspiration_image_call_a.txt",
            "pipeline_scope": "shared",
            "note": "2026-08-05 rev4：新增 image_defects、证据与缺失/不确定 fail-closed",
        },
        {
            "stage": "B",
            "name": "灵感图人工校准版全赛道评审",
            "version": "inspiration-b-v2-human-calibrated-20260805",
            "filename": "inspiration_image_call_b.txt",
            "pipeline_scope": "full_pipeline",
            "note": "2026-08-05 人工校准版：完整红线/赛道/维度/压分/等级/标签合同",
        },
        {
            "stage": "B",
            "name": "灵感图D锚图美感基础评分器",
            "version": "inspiration-b-v3-anchor-aesthetic-20260806",
            "filename": "inspiration_image_call_b_aesthetic_v3.txt",
            "pipeline_scope": "full_pipeline",
            "note": "2026-08-06：4张Owner锚图，输出前置美感分+冻结八维；不输出最终等级",
        },
        {
            "stage": "B",
            "name": "灵感图逐维证据合同美感基础评分器",
            "version": "inspiration-b-v4-evidence-contract-20260806",
            "filename": "inspiration_image_call_b_aesthetic_v4.txt",
            "relative_path": "backend/prompts/inspiration_image_call_b_aesthetic_v4.txt",
            "pipeline_scope": "shared",
            "note": "2026-08-06：完整JSON结构实例，八维证据非空，保留严格校验",
        },
    )
    for item in prompt_specs:
        exists = db.scalar(
            select(PromptVersion.id).where(PromptVersion.version == item["version"])
        )
        if exists is not None:
            continue
        system_prompt = (
            settings.project_root
            / item.get("relative_path", f"prompts/{item['filename']}")
        ).read_text(encoding="utf-8").strip()
        db.add(
            PromptVersion(
                stage=item["stage"],
                category_key="inspiration_image",
                pipeline_scope=item["pipeline_scope"],
                name=item["name"],
                version=item["version"],
                system_prompt=system_prompt,
                user_prompt="",
                rubric_version="inspiration-rubric-v1",
                status="published",
                source="imported",
                change_note=item["note"],
            )
        )
    db.flush()
    # profile 的 A/B 绑定必须与激活合同原子对齐；PromptVersion 内容保持不可变，
    # 旧版本仍保留，回滚可重新绑定旧 prompt_a_id / prompt_b_id。
    target_a = db.scalar(
        select(PromptVersion).where(
            PromptVersion.version == "inspiration-a-v3-hard-defect-recall-rev4-20260805"
        )
    )
    new_b = db.scalar(
        select(PromptVersion).where(
            PromptVersion.version == "inspiration-b-v4-evidence-contract-20260806"
        )
    )
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "inspiration_image"
        )
    )
    if profile is not None and target_a is not None and new_b is not None:
        changed = False
        if profile.prompt_a_id != target_a.id:
            profile.prompt_a_id = target_a.id
            changed = True
        if profile.prompt_b_id != new_b.id:
            profile.prompt_b_id = new_b.id
            changed = True
        if changed:
            profile.pipeline_revision += 1


def _seed_inspiration_image_v3_config(db: Session) -> None:
    """Seed the inspiration template used by all v3-only categories.

    Idempotent within one ``spec_version``.  A deliberately bumped frozen seed spec
    replaces the prior inspiration config exactly once and increments revision;
    otherwise startup does not overwrite later operator edits to the same spec.
    The artifacts are validated by the same deterministic validators the CRUD
    API uses, then cloned by the v3-only seed migration.
    """
    from .category_evaluation_contract import (
        canonical_contract_hash,
        validate_category_evaluation_contract,
    )
    from .dimension_composition import validate_subcategory_dimensions
    from .dimension_schema_registry import canonical_json
    from .dimension_deduction_bridge import extract_dimension_deduction_rules
    from .inspiration_category_seed import (
        build_inspiration_classification_map,
        build_inspiration_subcategory_dimensions,
        build_inspiration_v3_contract,
    )
    from .subcategory_resolver import validate_classification_map

    category_key = "inspiration_image"
    existing = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == category_key
        )
    )

    contract = build_inspiration_v3_contract()
    classification_map = build_inspiration_classification_map()
    subcategory_dimensions = build_inspiration_subcategory_dimensions()

    # Validate with the exact validators the CRUD API uses, so a seeded config
    # can never be less strict than an operator-created one.
    validate_category_evaluation_contract(contract)
    validate_classification_map(
        classification_map,
        valid_track_keys={
            track["key"]
            for track in contract["track_classification"]["tracks"]
        },
    )
    for config in subcategory_dimensions.values():
        validate_subcategory_dimensions(config)

    persisted = {
        "display_name": "灵感图",
        "status": "active",
        "contract_json": canonical_json(contract),
        "classification_map_json": canonical_json(classification_map),
        "subcategory_dimensions_json": canonical_json(subcategory_dimensions),
        "dimension_deduction_rules_json": canonical_json(
            extract_dimension_deduction_rules(subcategory_dimensions)
        ),
        "media_penalty_enabled": False,
        "contract_hash": canonical_contract_hash(contract),
    }
    if existing is not None:
        try:
            existing_spec = json.loads(existing.contract_json or "{}").get(
                "spec_version"
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            existing_spec = None
        if existing_spec == contract["spec_version"]:
            return
        for field, value in persisted.items():
            setattr(existing, field, value)
        existing.revision += 1
        return

    db.add(
        CategoryEvaluationV3Config(
            category_key=category_key,
            **persisted,
            revision=1,
            created_by="system:inspiration-v2-human-calibrated",
        )
    )



def _seed_v3_only_category_clones(db: Session) -> None:
    """Seed category-safe active v3 clones from the calibrated inspiration contract.

    The three legacy categories start from the currently active inspiration
    contract and its A/B prompts, but receive independent category keys,
    versions and provenance.  Existing system-generated 8-dimension draft
    placeholders are upgraded exactly once.  Any other existing config is
    treated as an operator-owned edit and is never overwritten on startup.
    """

    from .category_evaluation_contract import (
        canonical_contract_hash,
        validate_category_evaluation_contract,
    )
    from .dimension_composition import validate_subcategory_dimensions
    from .dimension_deduction_bridge import extract_dimension_deduction_rules
    from .dimension_schema_registry import canonical_json
    from .inspiration_category_seed import (
        build_inspiration_classification_map,
        build_inspiration_subcategory_dimensions,
        build_inspiration_v3_rev3_contract,
    )
    from .subcategory_resolver import validate_classification_map

    db.flush()
    source = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "inspiration_image",
            CategoryEvaluationV3Config.status == "active",
        )
    )
    if source is None:
        raise RuntimeError("缺少 active 灵感图 v3 合同，无法生成其他类目初版")

    source_contract = build_inspiration_v3_rev3_contract()
    source_classification = build_inspiration_classification_map()
    source_dimensions = build_inspiration_subcategory_dimensions()
    bindings = source_contract.get("prompt_bindings")
    if not isinstance(bindings, dict):
        raise RuntimeError("灵感图 v3 合同缺少 A/B prompt_bindings")
    source_prompts: dict[str, PromptVersion] = {}
    for stage, key in (("A", "call_a_version"), ("B", "call_b_version")):
        version = bindings.get(key)
        prompt = db.scalar(
            select(PromptVersion).where(
                PromptVersion.category_key == "inspiration_image",
                PromptVersion.stage == stage,
                PromptVersion.version == version,
            )
        )
        if prompt is None:
            raise RuntimeError(f"灵感图 v3 合同绑定的调用 {stage} prompt 不存在")
        source_prompts[stage] = prompt

    category_specs = {
        "space_image": ("空间图片", "space-image"),
        "material_image": ("材质图", "material-image"),
        "pdf_text": ("PDF 方案文本", "pdf-text"),
    }
    for category_key, (display_name, slug) in category_specs.items():
        prompt_versions = {
            "A": f"{slug}-a-v3-initial-20260805",
            "B": f"{slug}-b-v3-initial-20260805",
        }
        for stage, version in prompt_versions.items():
            existing_prompt = db.scalar(
                select(PromptVersion).where(PromptVersion.version == version)
            )
            if existing_prompt is not None:
                if (
                    existing_prompt.category_key != category_key
                    or existing_prompt.stage != stage
                ):
                    raise RuntimeError(
                        f"v3 seed prompt {version} 与类目或阶段不匹配"
                    )
                continue
            template = source_prompts[stage]
            db.add(
                PromptVersion(
                    stage=stage,
                    category_key=category_key,
                    pipeline_scope="shared",
                    name=f"{display_name} v3 初版调用 {stage}",
                    version=version,
                    system_prompt=template.system_prompt,
                    user_prompt=template.user_prompt,
                    rubric_version=template.rubric_version,
                    status="published",
                    source="v3_seed_clone",
                    change_note=(
                        "2026-08-05 初版：复制灵感图人工校准 prompt，"
                        "已按类目隔离版本；等待人工后续修改"
                    ),
                    created_by="system:v3-only-seed",
                )
            )

        contract = deepcopy(source_contract)
        contract["category_key"] = category_key
        contract["spec_version"] = (
            f"{slug}-v3-initial-from-inspiration-20260805"
        )
        contract["prompt_bindings"] = {
            "call_a_version": prompt_versions["A"],
            "call_b_version": prompt_versions["B"],
        }
        classification_map = deepcopy(source_classification)
        subcategory_dimensions = deepcopy(source_dimensions)

        validate_category_evaluation_contract(contract)
        track_keys = {
            track["key"]
            for track in contract["track_classification"]["tracks"]
        }
        validate_classification_map(
            classification_map, valid_track_keys=track_keys
        )
        for config in subcategory_dimensions.values():
            validate_subcategory_dimensions(config)

        persisted = {
            "display_name": display_name,
            "status": "active",
            "contract_json": canonical_json(contract),
            "classification_map_json": canonical_json(classification_map),
            "subcategory_dimensions_json": canonical_json(
                subcategory_dimensions
            ),
            "dimension_deduction_rules_json": canonical_json(
                extract_dimension_deduction_rules(subcategory_dimensions)
            ),
            "media_penalty_enabled": source.media_penalty_enabled,
            "contract_hash": canonical_contract_hash(contract),
            "created_by": "system:v3-only-seed",
        }
        existing = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == category_key
            )
        )
        if existing is None:
            db.add(
                CategoryEvaluationV3Config(
                    category_key=category_key,
                    revision=1,
                    **persisted,
                )
            )
            continue

        try:
            existing_contract = json.loads(existing.contract_json or "{}")
        except (json.JSONDecodeError, TypeError):
            existing_contract = {}
        if existing_contract.get("spec_version") == contract["spec_version"]:
            continue
        is_system_placeholder = (
            existing.created_by == "system"
            and existing.status == "draft"
            and existing_contract.get("category_key") == category_key
        )
        if not is_system_placeholder:
            continue
        for field, value in persisted.items():
            setattr(existing, field, value)
        existing.revision += 1
