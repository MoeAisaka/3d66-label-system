from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    AgentPlanVersion,
    AutomationPolicy,
    CategoryEvaluationV3Config,
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
    db.commit()


def _seed_inspiration_image_prompts(db: Session, settings) -> None:
    """Seed the ADR-0033 inspiration_image A/B ``PromptVersion`` rows (idempotent).

    Task 3b (方案 A): the two real-contract prompts —调用A 预检（红线/赛道/媒介/
    hard_defects）与调用B 6/5 维度评级—live as plain single-block prompt files under
    ``prompts/``.  Each file is loaded wholesale as ``system_prompt`` (they carry
    no ``### System Prompt`` split markers); ``user_prompt`` is empty since the
    per-image context is injected by the worker at call time.  Only inserted when
    the ``version`` is absent, so re-running seed_defaults never duplicates.
    """
    prompt_specs = (
        {
            "stage": "A",
            "name": "灵感图预检（红线/赛道/媒介/硬伤）",
            "version": "inspiration-a-v1",
            "filename": "inspiration_image_call_a.txt",
            "note": "ADR-0033 Task3b 方案A：灵感图调用A 预检，含 hard_defects 硬伤信号",
        },
        {
            "stage": "B",
            "name": "灵感图 6/5 维度评级",
            "version": "inspiration-b-v1",
            "filename": "inspiration_image_call_b.txt",
            "note": "ADR-0033 Task3b 方案A：灵感图调用B 真实 6/5 维度 1-5 档评级",
        },
    )
    for item in prompt_specs:
        exists = db.scalar(
            select(PromptVersion.id).where(PromptVersion.version == item["version"])
        )
        if exists is not None:
            continue
        system_prompt = (
            settings.project_root / "prompts" / item["filename"]
        ).read_text(encoding="utf-8").strip()
        db.add(
            PromptVersion(
                stage=item["stage"],
                category_key="inspiration_image",
                pipeline_scope="full_pipeline",
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


def _seed_inspiration_image_v3_config(db: Session) -> None:
    """Seed + activate the ADR-0033 v3 evaluation config for ``inspiration_image``.

    Idempotent: only inserts when the row is absent, so re-running seed_defaults
    (on every init) never duplicates or overwrites an operator-edited config.
    The v3 authoritative worker branch only fires for categories that have an
    ``active`` config here; this makes inspiration_image use v3 out of the box
    while every legacy category (space_image / material_image / pdf_text) keeps
    running the untouched v1 engine.  The three artifacts come from the frozen
    seed builders and are validated by the same deterministic validators the
    CRUD API uses before persisting.
    """
    from .category_evaluation_contract import (
        canonical_contract_hash,
        validate_category_evaluation_contract,
    )
    from .dimension_composition import validate_subcategory_dimensions
    from .dimension_schema_registry import canonical_json
    from .inspiration_category_seed import (
        build_inspiration_classification_map,
        build_inspiration_subcategory_dimensions,
        build_inspiration_v3_contract,
    )
    from .subcategory_resolver import validate_classification_map

    category_key = "inspiration_image"
    if db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == category_key
        )
    ) is not None:
        return

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

    db.add(
        CategoryEvaluationV3Config(
            category_key=category_key,
            display_name="灵感图",
            status="active",
            contract_json=canonical_json(contract),
            classification_map_json=canonical_json(classification_map),
            subcategory_dimensions_json=canonical_json(subcategory_dimensions),
            revision=1,
            contract_hash=canonical_contract_hash(contract),
            created_by="system",
        )
    )
