import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "vinted_drop.db"))


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = {
    int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x
}
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "21:00")

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", f"http://localhost:{DASHBOARD_PORT}")

TARGET_MULTIPLIER = _float("TARGET_MULTIPLIER", 2.5)
MIN_PROFIT = _float("MIN_PROFIT", 8)
NEGOTIATION_BUFFER = _float("NEGOTIATION_BUFFER", 0.15)
PACKAGING_COST = _float("PACKAGING_COST", 0.5)

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
