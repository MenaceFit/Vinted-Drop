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

# --- Photos gratuites sur ton PC : ComfyUI + Qwen-Image-Edit (voir docs/PHOTOS_GRATUITES.md) ---
# Tout se règle aussi depuis le dashboard (page « Photos gratuites ») ; ces variables servent de valeurs par défaut.
COMFYUI_URL = os.getenv("COMFYUI_URL", "")          # ex. http://127.0.0.1:8188 (vide = détection depuis le dashboard)
COMFY_DIR = os.getenv("COMFY_DIR", "")              # dossier de ComfyUI sur ce PC (téléchargement des modèles, démarrage)
COMFY_PRESET = os.getenv("COMFY_PRESET", "")        # standard | leger | 2509 (vide = standard)
COMFY_WORKFLOW = os.getenv("COMFY_WORKFLOW", "")    # vide = workflow du profil
# Noms de fichiers personnalisés (vides = ceux du profil). Une ancienne config 2509 est reconnue automatiquement.
COMFY_UNET = os.getenv("COMFY_UNET", "")
COMFY_CLIP = os.getenv("COMFY_CLIP", "")
COMFY_VAE = os.getenv("COMFY_VAE", "")
COMFY_LORA = os.getenv("COMFY_LORA", "")
COMFY_STEPS = int(os.getenv("COMFY_STEPS", "4"))
COMFY_CFG = float(os.getenv("COMFY_CFG", "1.0"))
COMFY_TIMEOUT = int(os.getenv("COMFY_TIMEOUT", "600"))

# Fournisseur préféré en mode "auto" : local (gratuit), gemini ou openai.
# Vide (conseillé) : ton PC d'abord dès que ComfyUI est branché, sinon gemini puis openai.
PHOTO_PROVIDER = os.getenv("PHOTO_PROVIDER", "")
# Note minimale de fidélité (0-10) d'une photo ; en dessous elle est refaite une fois
PHOTO_QA_MIN = int(os.getenv("PHOTO_QA_MIN", "7"))
# Estimation du coût par image (€) pour le suivi des dépenses
COST_PER_IMAGE = {"openai": float(os.getenv("COST_IMAGE_OPENAI", "0.05")),
                  "gemini": float(os.getenv("COST_IMAGE_GEMINI", "0.03")),
                  "local": 0.0, "hf": 0.0, "studio": 0.0}

# --- Photos gratuites sans carte graphique : Space Hugging Face (quota GPU gratuit, ~5 min/jour par compte) ---
# Jeton « read » gratuit : https://huggingface.co/settings/tokens (réglable aussi dans le dashboard)
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_SPACE = os.getenv("HF_SPACE", "Qwen/Qwen-Image-Edit-2511")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = set()  # rempli plus bas avec _chat_id
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "21:00")


def _chat_id(raw: str):
    """Accepte -1001234567890, 1234567890 ou un lien web.telegram.org/a/#-1001234567890."""
    raw = (raw or "").strip()
    if "#" in raw:
        raw = raw.rsplit("#", 1)[1]
    raw = raw.strip("/ ")
    try:
        cid = int(raw)
    except ValueError:
        return None
    # un id de canal commence toujours par -100 : on remet le "-" s'il a été oublié
    if cid > 0 and str(cid).startswith("100") and len(str(cid)) >= 13:
        cid = -cid
    return cid


TELEGRAM_ALLOWED_CHAT_IDS = {c for c in (_chat_id(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(","))
                             if c is not None}

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
# 127.0.0.1 = accessible seulement depuis ce PC. Sur un VPS : DASHBOARD_HOST=0.0.0.0 + DASHBOARD_PASSWORD
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_OPEN_BROWSER = os.getenv("DASHBOARD_OPEN_BROWSER", "1") == "1"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", f"http://localhost:{DASHBOARD_PORT}")

TARGET_MULTIPLIER = _float("TARGET_MULTIPLIER", 2.5)
MIN_PROFIT = _float("MIN_PROFIT", 20)
NEGOTIATION_BUFFER = _float("NEGOTIATION_BUFFER", 0.15)
# Ancien réglage (emballage seul). Remplacé en 2.2 par COST_PER_SALE, gardé pour compatibilité.
PACKAGING_COST = _float("PACKAGING_COST", 0.5)
# Coût fixe payé à chaque vente (emballage, étiquette, dépôt…). Modifiable dans Paramètres.
COST_PER_SALE = _float("COST_PER_SALE", 3)
# Frais de plateforme payés par le vendeur (% du prix). Particulier Vinted : 0 (l'acheteur paie la protection).
# Vérifie les conditions de ton compte (Vinted Pro) et ajuste.
PLATFORM_FEE_PCT = _float("PLATFORM_FEE_PCT", 0)
# Depop, vendeur basé en France (hors Royaume-Uni / États-Unis / Australie) : 10 % de commission Depop
# + frais PayPal (≈ 2,9 % + 0,35 € selon ton compte PayPal). Source : aide Depop « Seller fees and charges ».
DEPOP_FEE_PCT = _float("DEPOP_FEE_PCT", 12.9)
DEPOP_FIXED_FEE = _float("DEPOP_FIXED_FEE", 0.35)

PLATFORMS = {
    "vinted": {"label": "Vinted", "pct": PLATFORM_FEE_PCT, "fixed": 0.0},
    "depop": {"label": "Depop", "pct": DEPOP_FEE_PCT, "fixed": DEPOP_FIXED_FEE},
}

# --- Sécurité / sauvegardes ---
# Clé de chiffrement des tokens Vinted. Vide = générée dans data/secret.key (à sauvegarder avec la base).
SECRET_KEY = os.getenv("SECRET_KEY", "")
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))
# Nombre de tâches de fond simultanées (photos, fiches, synchro)
TASK_WORKERS = int(os.getenv("TASK_WORKERS", "3"))

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
