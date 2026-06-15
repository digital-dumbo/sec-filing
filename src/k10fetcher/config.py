from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP_DIR = PROJECT_ROOT / ".k10fetcher"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="K10FETCHER_", extra="ignore")

    app_dir: Path = DEFAULT_APP_DIR
    org_name: str = "YourOrgName"
    contact_email: str = "you@example.com"
    sec_rate_limit_per_second: int = 10

    @property
    def db_path(self) -> Path:
        return self.app_dir / "k10fetcher.db"

    @property
    def data_dir(self) -> Path:
        return self.app_dir / "data"

    @property
    def log_path(self) -> Path:
        return self.app_dir / "logs" / "k10fetcher.log"

    @property
    def sec_user_agent(self) -> str:
        return f"{self.org_name} {self.contact_email}"


settings = Settings()