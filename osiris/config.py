"""User settings. Secrets stay in environment variables, never in this file."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from osiris.util import detect_timezone

# LM Studio's local server expects a non-secret placeholder when no key is set.
# A real key, if you use one, must come from OSIRIS_LM_API_KEY.
LM_STUDIO_LOCAL_PLACEHOLDER = "lm-studio"
API_KEY_ENV = "OSIRIS_LM_API_KEY"


@dataclass
class Settings:
    lm_base_url: str = "http://localhost:1234/v1"
    lm_model: str = ""
    timeout_seconds: float = 60.0
    max_retries: int = 2
    temperature: float = 0.7
    planner_temperature: float = 0.2
    timezone: str = ""
    scheduler_poll_seconds: int = 20
    execution_timeout_seconds: int = 30
    max_output_chars: int = 20000
    max_script_chars: int = 30000
    memory_limit_mb: int = 256
    allowed_directories: list[str] = field(default_factory=list)
    brand_guidelines: str = ""
    content_preferences: str = ""

    def __post_init__(self) -> None:
        if not self.timezone:
            self.timezone = detect_timezone()

    @staticmethod
    def api_key() -> str:
        """Return the local placeholder or the key from the environment."""
        return os.environ.get(API_KEY_ENV, LM_STUDIO_LOCAL_PLACEHOLDER)

    def secret_values(self) -> list[str]:
        """Values that must be removed from logs. The local placeholder is not one."""
        values = []
        for name in (API_KEY_ENV, "GITHUB_TOKEN", "EMAIL_OAUTH_TOKEN", "SOCIAL_OAUTH_TOKEN"):
            raw = os.environ.get(name, "")
            if raw and raw != LM_STUDIO_LOCAL_PLACEHOLDER and len(raw) >= 6:
                values.append(raw)
        return values

    def validate(self) -> None:
        if not self.lm_base_url.startswith(("http://", "https://")):
            raise ValueError("LM Studio URL must start with http:// or https://.")
        if self.timeout_seconds < 1 or self.timeout_seconds > 600:
            raise ValueError("Timeout must be between 1 and 600 seconds.")
        if self.max_retries < 0 or self.max_retries > 5:
            raise ValueError("Retry limit must be between 0 and 5.")
        if self.execution_timeout_seconds < 1 or self.execution_timeout_seconds > 300:
            raise ValueError("Execution timeout must be between 1 and 300 seconds.")
        if self.scheduler_poll_seconds < 5 or self.scheduler_poll_seconds > 3600:
            raise ValueError("Scheduler poll must be between 5 and 3600 seconds.")
        if self.memory_limit_mb < 64 or self.memory_limit_mb > 4096:
            raise ValueError("Memory limit must be between 64 and 4096 MB.")
        # Import here so a bad name raises ValueError from one place.
        from osiris.util import zone

        zone(self.timezone)

    def to_json(self) -> dict:
        payload = asdict(self)
        return payload

    def save(self, path: Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Settings":
        data = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        known = {item.name for item in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {key: value for key, value in data.items() if key in known}
        # Environment overrides win so the endpoint and model stay configurable
        # without editing the saved file.
        if os.environ.get("OSIRIS_LM_BASE_URL"):
            filtered["lm_base_url"] = os.environ["OSIRIS_LM_BASE_URL"]
        if os.environ.get("OSIRIS_LM_MODEL"):
            filtered["lm_model"] = os.environ["OSIRIS_LM_MODEL"]
        if os.environ.get("OSIRIS_TIMEZONE"):
            filtered["timezone"] = os.environ["OSIRIS_TIMEZONE"]
        settings = cls(**filtered)
        settings.validate()
        return settings
