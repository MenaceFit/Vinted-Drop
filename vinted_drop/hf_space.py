"""Photos IA gratuites sans carte graphique : Space Hugging Face officiel Qwen-Image-Edit-2511 (ZeroGPU).

Hugging Face prête un GPU gratuitement, dans la limite d'un quota quotidien : environ 2 min sans compte,
5 min avec un compte gratuit, 40 min avec un compte PRO (doc « Spaces ZeroGPU », novembre 2026). Une photo
prend de 20 à 60 s de GPU selon le nombre d'étapes : compte quelques photos par jour.

- Jeton « read » gratuit sur https://huggingface.co/settings/tokens, collé dans le dashboard (chiffré en base).
- Quand le quota est épuisé, le bot n'insiste pas : il passe au fournisseur suivant (ton PC, studio
  instantané…) et réessaie Hugging Face quelques heures plus tard.
- Les photos du fournisseur sont envoyées au Space (serveurs de Hugging Face). Désactivable à tout moment.
"""
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, security

SIZES = {"3:4": (896, 1184), "1:1": (1024, 1024), "4:3": (1184, 896)}
QUOTA_KEY = "_hf_quota_until"
_clients: dict = {}
_lock = threading.Lock()


class HFError(RuntimeError):
    pass


def token() -> str:
    stored = db.get_setting("hf_token")
    if stored:
        try:
            return security.decrypt(stored) or ""
        except Exception:
            return ""
    return config.HF_TOKEN


def space() -> str:
    return db.get_setting("hf_space") or config.HF_SPACE


def steps() -> int:
    try:
        return max(4, min(50, int(db.get_setting("hf_steps") or 24)))
    except ValueError:
        return 24


def enabled() -> bool:
    return db.get_setting("hf_enabled") == "on"


def save(token_value=None, enabled_value=None, steps_value=None, space_value=None):
    if token_value is not None:
        token_value = token_value.strip()
        if token_value and not token_value.startswith("hf_"):
            raise HFError("Un jeton Hugging Face commence par « hf_ »")
        db.set_setting("hf_token", security.encrypt(token_value) if token_value else "")
        _clients.clear()
    if enabled_value is not None:
        db.set_setting("hf_enabled", "on" if enabled_value else "off")
    if steps_value is not None:
        db.set_setting("hf_steps", str(max(4, min(50, int(steps_value)))))
    if space_value is not None and space_value.strip():
        if space_value.count("/") != 1:
            raise HFError("Nom de Space attendu : propriétaire/nom (ex. Qwen/Qwen-Image-Edit-2511)")
        db.set_setting("hf_space", space_value.strip())
        _clients.clear()


def quota_until() -> str | None:
    until = db.get_setting(QUOTA_KEY)
    return until if until and until > db.now() else None


def _client():
    from gradio_client import Client
    key = (space(), token())
    with _lock:
        if key not in _clients:
            _clients[key] = Client(key[0], token=key[1] or None, verbose=False,
                                   download_files=tempfile.mkdtemp(prefix="vd_hf_"))
        return _clients[key]


def _first_image(result) -> Path:
    """Le Space renvoie (galerie, graine) ; la galerie est une liste de chemins ou de {"image": chemin}."""
    gallery = result[0] if isinstance(result, (list, tuple)) and result and not isinstance(result[0], str) else result
    if isinstance(gallery, (list, tuple)) and gallery:
        item = gallery[0]
    else:
        item = gallery
    if isinstance(item, dict):
        item = item.get("image") or item.get("path") or item.get("name")
        if isinstance(item, dict):
            item = item.get("path") or item.get("url")
    if isinstance(item, (list, tuple)):
        item = item[0] if item else None
    if not item or not Path(str(item)).exists():
        raise HFError("Réponse inattendue du Space Hugging Face (aucune image)")
    return Path(str(item))


def _is_quota(msg: str) -> bool:
    m = msg.lower()
    return "quota" in m or "zerogpu" in m and "exceed" in m


def generate(prompt: str, refs, aspect="3:4", timeout=420) -> bytes:
    if quota_until():
        raise HFError(f"Quota gratuit Hugging Face épuisé (nouvel essai après {quota_until()[11:16]})")
    from gradio_client import handle_file
    w, h = SIZES.get(aspect, SIZES["3:4"])
    images = [{"image": handle_file(str(p)), "caption": None} for p in list(refs)[:3]]
    try:
        job = _client().submit(images, prompt, 0, True, 4.0, steps(), h, w, False, api_name="/infer")
        path = _first_image(job.result(timeout=timeout))
    except HFError:
        raise
    except Exception as e:
        msg = str(e)
        if _is_quota(msg):
            until = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
            db.set_setting(QUOTA_KEY, until)
            db.log_event("photo", "Quota gratuit Hugging Face épuisé : pause de 3 h", level="warning")
            raise HFError("Quota gratuit Hugging Face épuisé pour le moment") from e
        raise HFError(f"Space Hugging Face indisponible : {security.mask(msg)[:200]}") from e
    return path.read_bytes()


def check() -> dict:
    """État pour le dashboard : jeton, Space joignable, images faites aujourd'hui, pause quota."""
    out = {"enabled": enabled(), "has_token": bool(token()), "space": space(), "steps": steps(),
           "quota_until": quota_until(), "ok": None, "error": None}
    with db.connect() as c:
        r = c.execute("SELECT COUNT(*) n, ROUND(AVG(duration_s),1) s FROM photo_generations "
                      "WHERE provider='hf' AND date(created_at)=date(?)", (db.now(),)).fetchone()
    out["today"], out["avg_s"] = r["n"], r["s"]
    return out


def ping() -> dict:
    try:
        _client()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": security.mask(str(e))[:200]}
