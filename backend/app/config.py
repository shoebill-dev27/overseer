"""Application settings loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.github_client_id = os.getenv("GITHUB_CLIENT_ID", "")
        self.github_client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
        self.github_callback_url = os.getenv("GITHUB_CALLBACK_URL", "")
        self.app_base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")

        raw_ids = os.getenv("ALLOWED_GITHUB_USER_IDS", "")
        self.allowed_github_user_ids: list[str] = [
            uid.strip() for uid in raw_ids.split(",") if uid.strip()
        ]

        # Required secrets — absence will cause startup FAILED
        self.secret_key = os.getenv("SECRET_KEY", "")
        self.agent_hmac_secret = os.getenv("AGENT_HMAC_SECRET", "")

        self.database_path = os.getenv("DATABASE_PATH", "overseer.db")

    def check(self) -> dict[str, bool]:
        """Return per-item health for startup self-check."""
        return {
            "SECRET_KEY": bool(self.secret_key),
            "AGENT_HMAC_SECRET": bool(self.agent_hmac_secret),
            "GITHUB_CLIENT_ID": bool(self.github_client_id),
            "GITHUB_CLIENT_SECRET": bool(self.github_client_secret),
            "GITHUB_CALLBACK_URL": bool(self.github_callback_url),
            "ALLOWED_GITHUB_USER_IDS": bool(self.allowed_github_user_ids),
        }


settings = Settings()
