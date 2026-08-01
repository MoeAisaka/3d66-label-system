from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _default_data_dir(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    effective_platform = platform_name or sys.platform
    if effective_platform == "darwin":
        return (home or Path.home()) / "Library" / "Application Support" / "3d66-label-system"
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "3d66-label-system"
    return PROJECT_ROOT / "data"


def _production_feedback_token(data_dir: Path) -> str | None:
    environment_token = os.getenv("PRODUCTION_FEEDBACK_TOKEN", "").strip()
    if environment_token:
        return environment_token
    token_file = Path(
        os.getenv(
            "PRODUCTION_FEEDBACK_TOKEN_FILE",
            str(data_dir / "secrets" / "production-feedback.token"),
        )
    ).expanduser()
    if not token_file.is_file():
        return None
    token = token_file.read_text(encoding="utf-8").strip()
    return token or None


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    upload_dir: Path
    log_dir: Path
    prompt_source: Path
    frontend_dist: Path
    host: str
    port: int
    session_days: int
    production_feedback_token: str | None
    database_url: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", str(_default_data_dir()))).expanduser().resolve()
    frontend_dist = Path(
        os.getenv("FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))
    ).expanduser().resolve()
    settings = Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        database_path=data_dir / "database" / "app.db",
        upload_dir=data_dir / "images",
        log_dir=data_dir / "logs",
        prompt_source=PROJECT_ROOT / "prompts" / "3d66-aesthetic-v2.1.md",
        frontend_dist=frontend_dist,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8080")),
        session_days=max(1, int(os.getenv("SESSION_DAYS", "7"))),
        production_feedback_token=_production_feedback_token(data_dir),
        database_url=os.getenv(
            "DATABASE_URL", f"sqlite:///{(data_dir / 'database' / 'app.db').as_posix()}"
        ),
    )
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings
