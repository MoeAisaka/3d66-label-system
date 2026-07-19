from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EvaluationControl, ModelConfig, OptimizerConfig, PromptVersion, User
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
            )
        )

    if db.scalar(select(ModelConfig).limit(1)) is None:
        db.add(ModelConfig())

    if db.scalar(select(OptimizerConfig).limit(1)) is None:
        db.add(OptimizerConfig())

    if db.get(EvaluationControl, 1) is None:
        db.add(EvaluationControl(id=1))

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
                rubric_version="space-rubric-v1.3",
                status="draft",
                source="split",
                change_note=item["note"],
            )
        )
    db.commit()
