import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class Settings:
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    PLAYWRIGHT_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
    MAX_RESULTS_PER_PLATFORM: int = int(os.getenv("MAX_RESULTS", "5"))
    SEARCH_TIMEOUT: int = int(os.getenv("SEARCH_TIMEOUT", "60"))
    DOWNLOAD_TIMEOUT: int = 60

    @property
    def DOUYIN_COOKIES_FILE(self) -> Path | None:
        """Path to Douyin cookies JSON file (optional)."""
        env_path = os.getenv("DOUYIN_COOKIES_FILE", "")
        return Path(env_path) if env_path else None

    def storage_dir(self, topic: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = topic.replace("/", "_").replace("\\", "_").replace(" ", "_")[:50]
        base = Path(__file__).parent.parent.parent / "downloads"
        return base / f"{safe_topic}_{timestamp}"


settings = Settings()
