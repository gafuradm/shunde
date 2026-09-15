"""Конфигурация приложения (читается из backend/.env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]  # .../backend


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- общие ---
    app_name: str = "Shunde Tutor"
    data_dir: str = "data"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_mb: int = 40
    teacher_token: str = "teacher"

    # --- LLM (OpenAI-совместимый) ---
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_model_fast: str = ""
    llm_temperature: float = 0.2
    llm_timeout: float = 120.0
    llm_json_mode: bool = False
    llm_vision: bool = False
    # Отключать «размышления» reasoning-моделей: добавляет в запрос
    # {"thinking": {"type": "disabled"}}. Резко ускоряет перевод речи и разметку,
    # а также не даёт модели тратить max_tokens на reasoning.
    # Для обычных (не reasoning) моделей оставляйте false.
    llm_disable_thinking: bool = False

    # --- веб-поиск ---
    tavily_api_key: str = ""
    serper_api_key: str = ""
    bing_search_key: str = ""
    search_pages: int = 2
    # Проверка резолвинга хоста при загрузке страниц (защита от SSRF).
    # Включать не нужно, если DNS подменяется fake-IP (VPN/Clash) — иначе ничего не загрузится.
    search_dns_guard: bool = False
    allow_google_free_translate: bool = True

    # --- распознавание речи на сервере (опционально) ---
    asr_api_key: str = ""
    asr_base_url: str = "https://api.openai.com/v1"
    asr_model: str = "whisper-1"

    # ---------- производные ----------
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def upload_path(self) -> Path:
        return self.data_path / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_path / "shunde.sqlite3"

    @property
    def llm_available(self) -> bool:
        return bool(self.llm_api_key.strip())

    @property
    def vision_available(self) -> bool:
        return self.llm_available and self.llm_vision

    @property
    def asr_available(self) -> bool:
        return bool(self.asr_api_key.strip())

    @property
    def fast_model(self) -> str:
        return self.llm_model_fast.strip() or self.llm_model

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.data_path.mkdir(parents=True, exist_ok=True)
    s.upload_path.mkdir(parents=True, exist_ok=True)
    return s
