# app/config.py
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()

class Settings(BaseModel):
    bot_token: str = os.getenv("BOT_TOKEN", "")
    owner_id: int = int(os.getenv("OWNER_ID", "0"))
    spreadsheet_id: str = os.getenv("SHEET_SPREADSHEET_ID", "")
    gcp_sa_json_path: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    tz: str = os.getenv("TZ", "Europe/Moscow")
    polling: bool = os.getenv("POLLING", "true").lower() == "true"

settings = Settings()
