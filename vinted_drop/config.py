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
# gpt-image-1 a été arrêté par OpenAI le 23/10/2026
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-terra")
# modèles essayés si le premier n'existe plus
OPENAI_TEXT_FALLBACKS = [m for m in os.getenv("OPENAI_TEXT_FALLBACKS", "gpt-5.6-terra,gpt-4o-mini").split(",") if m]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1")
GEMINI_IMAGE_SIZE = os.getenv("GEMINI_IMAGE_SIZE", "1K")  # 1K = 0,034 $/image, 2K = 0,050 $
GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3-flash-preview")

# --- Photos gratuites sur ton PC : ComfyUI + Qwen-Image-Edit-2509 (voir docs/PHOTOS_GRATUITES.md) ---
COMFYUI_URL = os.getenv("COMFYUI_URL", "")          # ex. http://127.0.0.1:8188
COMFY_WORKFLOW = os.getenv("COMFY_WORKFLOW", "")    # vide = workflow fourni
COMFY_UNET = os.getenv("COMFY_UNET", "qwen_image_edit_2509_fp8_e4m3fn.safetensors")
COMFY_CLIP = os.getenv("COMFY_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors")
COMFY_VAE = os.getenv("COMFY_VAE", "qwen_image_vae.safetensors")
COMFY_LORA = os.getenv("COMFY_LORA", "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors")
COMFY_STEPS = int(os.getenv("COMFY_STEPS", "4"))
COMFY_CFG = float(os.getenv("COMFY_CFG", "1.0"))
COMFY_TIMEOUT = int(os.getenv("COMFY_TIMEOUT", "600"))

# Fournisseur préféré en mode "auto" : local (gratuit), gemini ou openai.
# Par défaut : local s'il est configuré, sinon gemini.
PHOTO_PROVIDER = os.getenv("PHOTO_PROVIDER", "local" if COMFYUI_URL else "gemini")
# Note minimale de fidélité (0-10) d'une photo ; en dessous elle est refaite une fois
PHOTO_QA_MIN = int(os.getenv("PHOTO_QA_MIN", "7"))
# Estimation du coût par image (€) pour le suivi des dépenses
COST_PER_IMAGE = {"openai": float(os.getenv("COST_IMAGE_OPENAI", "0.05")),
                  "gemini": float(os.getenv("COST_IMAGE_GEMINI", "0.03")),
                  "local": 0.0}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = {
    int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "21:00")


def _chat_id(raw: str):
    """Accepte -1001234567890, 1234567890 ou un lien web.telegram.org/a/#-1001234567890."""
    raw = (raw or "").strip()
    if "#" in raw:
        raw = raw.rsplit("#", 1)[1]
    raw = raw.strip("/ ")
    try:
        return int(raw)
    except ValueError:
        return None


# Canal où le bot publie ventes, validations et récaps (le bot doit y être administrateur)
TELEGRAM_CHANNEL_ID = _chat_id(os.getenv("TELEGRAM_CHANNEL_ID", ""))


def telegram_chats() -> set:
    """Discussions autorisées à piloter le bot."""
    return TELEGRAM_ALLOWED_CHAT_IDS | ({TELEGRAM_CHANNEL_ID} if TELEGRAM_CHANNEL_ID else set())


def notify_targets() -> set:
    """Où envoyer les notifications : le canal s'il est configuré, sinon les discussions autorisées."""
    return {TELEGRAM_CHANNEL_ID} if TELEGRAM_CHANNEL_ID else set(TELEGRAM_ALLOWED_CHAT_IDS)

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", f"http://localhost:{DASHBOARD_PORT}")

TARGET_MULTIPLIER = _float("TARGET_MULTIPLIER", 2.5)
MIN_PROFIT = _float("MIN_PROFIT", 8)
NEGOTIATION_BUFFER = _float("NEGOTIATION_BUFFER", 0.15)
PACKAGING_COST = _float("PACKAGING_COST", 0.5)

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
