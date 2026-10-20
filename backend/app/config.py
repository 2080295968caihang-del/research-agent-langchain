from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_APP_ROOT = Path(r"E:\ResearchAgent")


class Settings(BaseSettings):
    """Runtime paths and database configuration for the local app."""

    app_root: Path = DEFAULT_APP_ROOT
    projects_root: Path = Path(r"E:\ResearchProjects")
    database_url: str = "sqlite:///E:/ResearchAgent/data/app.db"

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()
