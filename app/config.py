# app/config.py
from pydantic import BaseModel, Field, SecretStr, field_validator
from dotenv import load_dotenv
import os
from pathlib import Path
from typing import Optional, Dict, Any

load_dotenv()


class Settings(BaseModel):
    # Бот
    bot_token: SecretStr = Field(..., description="Telegram bot token")
    owner_id: int = Field(..., gt=0, description="Owner Telegram ID")

    # CRM / Sheets
    spreadsheet_id: Optional[str] = Field(default="", description="Google Sheets ID")
    gcp_sa_json_path: Optional[Path] = Field(
        default=None, description="Path to GCP service account JSON"
    )
    financial_settings_sheet_name: str = Field(
        default="Финансовые настройки", description="Sheet name for financial settings"
    )
    fixed_costs_cell: str = Field(
        default="L6", description="Cell for total fixed costs in financial settings sheet"
    )
    financial_settings_columns: Dict[str, str] = Field(
        default_factory=dict, description="Mapping of financial settings column names"
    )

    # Новый способ оплаты — второй бот
    payment_bot_url: Optional[str] = Field(
        default=os.getenv("PAYMENT_BOT_URL", "").strip(),
        description="URL второго Telegram-бота для оплаты (например, https://t.me/YourPaymentsBot)",
    )

    # Прочее
    tz: str = Field(default="Europe/Moscow", description="Timezone")
    polling: bool = Field(default=True, description="Use polling instead of webhooks")
    log_level: str = Field(default="INFO", description="Logging level")

    # БД
    db_path: Path = Field(default=Path("data/app.db"), description="SQLite DB path")
    db_pool_size: int = Field(default=5, ge=1, le=20, description="DB pool size")
    db_timeout: float = Field(default=30.0, ge=1.0, description="DB timeout (seconds)")

    # ---------- Validators (Pydantic v2) ----------
    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, v: SecretStr) -> SecretStr:
        if not v or not (v.get_secret_value() or "").strip():
            raise ValueError("BOT_TOKEN is required")
        return v

    @field_validator("owner_id")
    @classmethod
    def validate_owner_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("OWNER_ID must be positive integer")
        return v

    @field_validator("gcp_sa_json_path")
    @classmethod
    def validate_gcp_path(cls, v: Optional[Path]) -> Optional[Path]:
        if not v:
            return None
        v = Path(v)
        if str(v).strip() and not v.exists():
            raise ValueError(f"GCP service account JSON file not found: {v}")
        return v.resolve()

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, v: Path) -> Path:
        v = Path(v)
        v.parent.mkdir(parents=True, exist_ok=True)
        return v.resolve()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    try:
        return Settings(
            bot_token=SecretStr(os.getenv("BOT_TOKEN", "")),
            owner_id=int(os.getenv("OWNER_ID", "0")),

            spreadsheet_id=os.getenv("SPREADSHEET_ID", "").strip(),
            gcp_sa_json_path=os.getenv("GCP_SA_JSON_PATH", "").strip() or None,
            financial_settings_sheet_name=os.getenv(
                "FIN_SETTINGS_SHEET_NAME", "Финансовые настройки"
            ),
            fixed_costs_cell=os.getenv("FIXED_COSTS_CELL", "L6"),
            # Можно прокинуть JSON-строку маппинга, если используете; иначе оставим пустым
            financial_settings_columns={},  # или распарсить здесь os.getenv("FIN_SETTINGS_COLUMNS_JSON", "{}")

            payment_bot_url=os.getenv("PAYMENT_BOT_URL", "").strip(),

            tz=os.getenv("TZ", "Europe/Moscow"),
            polling=_bool_env("USE_POLLING", True),
            log_level=os.getenv("LOG_LEVEL", "INFO"),

            db_path=Path(os.getenv("DB_PATH", "data/app.db")),
            db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            db_timeout=float(os.getenv("DB_TIMEOUT", "30.0")),
        )
    except Exception as e:
        raise RuntimeError(f"Failed to load settings: {e}")


settings = load_settings()
