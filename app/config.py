# app/config.py
from pydantic import BaseModel, Field, validator, SecretStr
from dotenv import load_dotenv
import os
from pathlib import Path
from typing import Optional, Dict

load_dotenv()

class Settings(BaseModel):
    bot_token: SecretStr = Field(..., min_length=1, description="Telegram bot token")
    owner_id: int = Field(..., gt=0, description="Owner Telegram ID")
    spreadsheet_id: Optional[str] = Field(default="", description="Google Sheets ID")
    gcp_sa_json_path: Optional[Path] = Field(default="", description="Path to GCP service account JSON")
    financial_settings_sheet_name: str = Field(default="Финансовые настройки", description="Name of the sheet for financial settings")
    fixed_costs_cell: str = Field(default="L6", description="Cell for total fixed costs in financial settings sheet")
    margin_percent_cell: str = Field(default="L5", description="Cell for margin percentage in financial settings sheet")
    financial_settings_columns: Dict[str, str] = Field(
        default={
            "fixed_costs": "Постоянные траты",
            "margin_percent": "Маржа"
        },
        description="Mapping of financial settings column names"
    )
    tz: str = Field(default="Europe/Moscow", description="Timezone")
    polling: bool = Field(default=True, description="Use polling instead of webhooks")
    log_level: str = Field(default="INFO", description="Logging level")
    db_path: Path = Field(default=Path("data/app.db"), description="SQLite database path")
    
    # YooKassa settings
    yookassa_shop_id: Optional[str] = Field(default="", description="YooKassa Shop ID")
    yookassa_secret_key: Optional[SecretStr] = Field(default="", description="YooKassa Secret Key")
    yookassa_return_url: Optional[str] = Field(default="", description="Return URL after payment")
    use_yookassa: bool = Field(default=False, description="Whether to use YooKassa for payments")
    
    # Database settings
    db_pool_size: int = Field(default=5, ge=1, le=20, description="Database connection pool size")
    db_timeout: float = Field(default=30.0, ge=1.0, description="Database connection timeout in seconds")
    
    @validator('bot_token')
    def validate_bot_token(cls, v):
        if not v or v.get_secret_value() == "":
            raise ValueError("BOT_TOKEN is required")
        return v
    
    @validator('use_yookassa')
    def validate_yookassa_config(cls, v, values):
        """Проверка, что YooKassa настроен, если используется"""
        if v:  # Если use_yookassa=True
            shop_id = values.get('yookassa_shop_id')
            secret_key = values.get('yookassa_secret_key')
            
            if not shop_id or shop_id == "":
                raise ValueError(
                    "YOOKASSA_SHOP_ID is required when use_yookassa=True. "
                    "Please set it in .env file or disable YooKassa."
                )
            
            if not secret_key or (hasattr(secret_key, 'get_secret_value') and secret_key.get_secret_value() == ""):
                raise ValueError(
                    "YOOKASSA_SECRET_KEY is required when use_yookassa=True. "
                    "Please set it in .env file or disable YooKassa."
                )
        
        return v
    
    @validator('owner_id')
    def validate_owner_id(cls, v):
        if v <= 0:
            raise ValueError("OWNER_ID must be a positive integer")
        return v
    
    @validator('gcp_sa_json_path')
    def validate_gcp_path(cls, v):
        if isinstance(v, str):
            v = Path(v) if v else Path("")
        if v and str(v) and not v.exists():
            raise ValueError(f"GCP service account JSON file not found: {v}")
        return v.absolute() if v and str(v) else Path("")

    @validator('db_path')
    def validate_db_path(cls, v):
        v.parent.mkdir(parents=True, exist_ok=True)
        return v.absolute()
    
    class Config:
        env_file = ".env"
        case_sensitive = False

def load_settings() -> Settings:
    """Load and validate settings"""
    try:
        # Определяем, используется ли YooKassa
        use_yookassa = os.getenv("USE_YOOKASSA", "false").lower() == "true"
        
        return Settings(
            bot_token=os.getenv("BOT_TOKEN", ""),
            owner_id=int(os.getenv("OWNER_ID", "0")),
            spreadsheet_id=os.getenv("SHEET_SPREADSHEET_ID", ""),
            gcp_sa_json_path=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
            financial_settings_sheet_name=os.getenv("FINANCIAL_SETTINGS_SHEET_NAME", "Финансовые настройки"),
            fixed_costs_cell=os.getenv("FIXED_COSTS_CELL", "L6"),
            margin_percent_cell=os.getenv("MARGIN_PERCENT_CELL", "L5"),
            financial_settings_columns={
                "fixed_costs": os.getenv("FIXED_COSTS_COLUMN_NAME", "Постоянные траты"),
                "margin_percent": os.getenv("MARGIN_PERCENT_COLUMN_NAME", "Маржа")
            },
            tz=os.getenv("TZ", "Europe/Moscow"),
            polling=os.getenv("POLLING", "true").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            db_path=Path(os.getenv("DB_PATH", "data/app.db")),
            yookassa_shop_id=os.getenv("YOOKASSA_SHOP_ID", ""),
            yookassa_secret_key=os.getenv("YOOKASSA_SECRET_KEY", ""),
            yookassa_return_url=os.getenv("YOOKASSA_RETURN_URL", ""),
            use_yookassa=use_yookassa,
            db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            db_timeout=float(os.getenv("DB_TIMEOUT", "30.0"))
        )
    except ValueError as e:
        # Pydantic validation error - более понятное сообщение
        raise RuntimeError(
            f"Configuration error: {e}\n\n"
            "Please check your .env file and ensure all required settings are configured correctly."
        )
    except Exception as e:
        raise RuntimeError(f"Failed to load settings: {e}")

settings = load_settings()
