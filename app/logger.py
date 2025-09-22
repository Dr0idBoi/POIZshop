# app/logger.py
import logging
import os
from .config import settings

def setup_logging():
    level = os.getenv("LOG_LEVEL","INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    # silence noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.INFO)
