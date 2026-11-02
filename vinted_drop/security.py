"""Sécurité : chiffrement des secrets stockés, masquage dans les logs, protection CSRF du dashboard."""
import os
import re
from pathlib import Path

from . import config

_PREFIX = "enc:v1:"
_fernet = None


def _key() -> bytes:
    """Clé de chiffrement : SECRET_KEY dans .env, sinon data/secret.key (créée au premier lancement)."""
    from cryptography.fernet import Fernet
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    path = Path(config.DATA_DIR) / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_bytes().strip()


def fernet():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_key())
    return _fernet


def encrypt(value: str | None) -> str | None:
    if value in (None, "") or str(value).startswith(_PREFIX):
        return value
    return _PREFIX + fernet().encrypt(str(value).encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value or not str(value).startswith(_PREFIX):
        return value  # ancienne valeur en clair (migrée au prochain enregistrement)
    from cryptography.fernet import InvalidToken
    try:
        return fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError("Impossible de déchiffrer le token Vinted : la clé (data/secret.key ou SECRET_KEY) a changé. "
                         "Reconnecte le compte Vinted.")


_SECRET_PATTERNS = [
    re.compile(r"bot\d+:[A-Za-z0-9_-]{30,}"),                 # token Telegram dans une URL
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),           # token Telegram
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),                     # clé OpenAI
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),                    # clé Google
    re.compile(r"(access_token_web|refresh_token_web|_vinted_fr_session)=[^;\s]+"),
]


def mask(text: str) -> str:
    text = str(text)
    for p in _SECRET_PATTERNS:
        text = p.sub(lambda m: m.group(0)[:6] + "…[masqué]", text)
    return text


class MaskingFilter:
    """Filtre logging qui masque les secrets dans tous les messages."""
    def filter(self, record):
        try:
            record.msg = mask(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def install_log_masking():
    import logging
    f = MaskingFilter()
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)


def same_origin(request) -> bool:
    """Protection CSRF : une requête POST doit venir du dashboard lui-même (en-têtes Origin / Referer)."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # outils en ligne de commande / clients sans navigateur (les navigateurs envoient toujours Origin)
    host = request.headers.get("host", "")
    m = re.match(r"https?://([^/]+)", origin)
    return bool(m) and m.group(1) == host
