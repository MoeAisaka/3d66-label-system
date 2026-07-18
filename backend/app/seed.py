from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EvaluationControl, ModelConfig, OptimizerConfig, PromptVersion, User
from .prompt_loader import load_prompt_pairs
from .security import DEFAULT_ADMIN_PASSWORD_HASH


def seed_defaults(db: Session) -> None:
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
        pairs = load_prompt_pairs(get_settings().prompt_source)
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
    db.commit()
