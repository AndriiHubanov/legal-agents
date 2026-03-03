from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str

    CHROMA_DB_PATH: str = "./data/chroma_db"
    RAW_DATA_PATH: str = "./data/raw_decisions"
    OUTPUT_PATH: str = "./data/output_documents"
    REPORTS_PATH: str = "./data/analysis_reports"

    SCRAPE_DELAY_SECONDS: float = 3.0
    MAX_DECISIONS_PER_RUN: int = 100
    HEADLESS_BROWSER: bool = True

    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    MAX_TOKENS: int = 4096

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def ensure_dirs(self) -> None:
        for path_str in [
            self.CHROMA_DB_PATH,
            self.RAW_DATA_PATH,
            self.OUTPUT_PATH,
            self.REPORTS_PATH,
        ]:
            Path(path_str).mkdir(parents=True, exist_ok=True)


settings = Settings()
