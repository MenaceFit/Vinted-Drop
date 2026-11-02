"""Générateurs d'images : local gratuit (ComfyUI + Qwen-Image-Edit), Google Gemini (Nano Banana), OpenAI (GPT Image).

Interface unique : generate(prompt, refs, out_path, provider="auto", aspect="3:4")
- refs : images de référence (modèle de la boutique, produit...), dans l'ordre du prompt
- provider : "local", "gemini", "openai" ou "auto" (le fournisseur préféré, puis les autres en secours)
- les fournisseurs payants respectent le budget du jour (réglage daily_budget)
Chaque image générée est enregistrée dans le suivi des coûts.
"""
import base64
import mimetypes
from pathlib import Path

import requests

from . import config, db

OPENAI_API = "https://api.openai.com/v1"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"

# Tailles proches du 3:4 des annonces Vinted
OPENAI_SIZES = {"3:4": "1024x1536", "1:1": "1024x1024", "4:3": "1536x1024"}


class ImageError(RuntimeError):
    pass


PAID = ("gemini", "openai")


def available() -> list[str]:
    out = []
    if config.COMFYUI_URL:
        out.append("local")
    if config.GEMINI_API_KEY:
        out.append("gemini")
    if config.OPENAI_API_KEY:
        out.append("openai")
    return out


def order(provider: str) -> list[str]:
    keys = available()
    if not keys:
        raise ImageError("Aucun générateur d'images : configure COMFYUI_URL (gratuit), GEMINI_API_KEY ou OPENAI_API_KEY dans .env")
    if provider in ("openai", "gemini", "local"):
        if provider not in keys:
            raise ImageError(f"Clé manquante pour {provider} dans .env")
        return [provider] + [k for k in keys if k != provider]  # l'autre en secours
    pref = config.PHOTO_PROVIDER if config.PHOTO_PROVIDER in keys else keys[0]
    return [pref] + [k for k in keys if k != pref]


def _mime(p: Path) -> str:
    return mimetypes.guess_type(p.name)[0] or "image/png"


def _err(resp, who):
    try:
        j = resp.json()
        msg = (j.get("error") or {}).get("message") or str(j)[:300]
    except ValueError:
        msg = resp.text[:300]
    return ImageError(f"{who} {resp.status_code} : {msg}")


# ---------------- OpenAI ----------------
def openai_generate(prompt, refs, aspect="3:4", quality=None) -> bytes:
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    size = OPENAI_SIZES.get(aspect, "1024x1536")
    quality = quality or config.OPENAI_IMAGE_QUALITY
    if refs:
        files = [("image[]", (Path(r).name, Path(r).read_bytes(), _mime(Path(r)))) for r in refs]
        data = {"model": config.OPENAI_IMAGE_MODEL, "prompt": prompt, "size": size, "quality": quality, "n": "1"}
        r = requests.post(f"{OPENAI_API}/images/edits", headers=headers, data=data, files=files, timeout=300)
    else:
        r = requests.post(f"{OPENAI_API}/images/generations", headers=headers, timeout=300, json={
            "model": config.OPENAI_IMAGE_MODEL, "prompt": prompt, "size": size, "quality": quality, "n": 1})
    if r.status_code >= 400:
        raise _err(r, "OpenAI")
    item = r.json()["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    return requests.get(item["url"], timeout=120).content


# ---------------- Gemini ----------------
def find_image_b64(obj):
    """Trouve la première image base64 dans une réponse Gemini (API Interactions ou generateContent)."""
    if isinstance(obj, dict):
        if obj.get("type") == "image" and obj.get("data"):
            return obj["data"]
        for key in ("inlineData", "inline_data"):
            inner = obj.get(key)
            if isinstance(inner, dict) and inner.get("data") and str(inner.get("mimeType", inner.get("mime_type", "image"))).startswith("image"):
                return inner["data"]
        if isinstance(obj.get("output_image"), dict) and obj["output_image"].get("data"):
            return obj["output_image"]["data"]
        for v in obj.values():
            found = find_image_b64(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_image_b64(v)
            if found:
                return found
    return None


def gemini_generate(prompt, refs, aspect="3:4") -> bytes:
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    images = [{"type": "image", "mime_type": _mime(Path(r)), "data": base64.b64encode(Path(r).read_bytes()).decode()}
              for r in refs]
    body = {"model": config.GEMINI_IMAGE_MODEL, "input": [{"type": "text", "text": prompt}, *images],
            "response_format": {"type": "image", "aspect_ratio": aspect, "image_size": config.GEMINI_IMAGE_SIZE}}
    r = requests.post(f"{GEMINI_API}/interactions", headers=headers, json=body, timeout=300)
    if r.status_code == 404:  # ancienne API generateContent en secours
        parts = [{"text": prompt}] + [{"inline_data": {"mime_type": i["mime_type"], "data": i["data"]}} for i in images]
        r = requests.post(f"{GEMINI_API}/models/{config.GEMINI_IMAGE_MODEL}:generateContent", headers=headers,
                          timeout=300, json={"contents": [{"parts": parts}], "generationConfig": {
                              "responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}})
    if r.status_code >= 400:
        raise _err(r, "Gemini")
    data = find_image_b64(r.json())
    if not data:
        raise ImageError("Gemini n'a pas renvoyé d'image (contenu refusé ou prompt à reformuler)")
    return base64.b64decode(data)


# ---------------- budget ----------------
def budget_left():
    """Budget restant aujourd'hui (€) pour les fournisseurs payants, None = illimité."""
    raw = db.get_setting("daily_budget")
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return None
    return round(budget - db.cost_today(), 4)


def _budget_alert(blocked=False):
    """Prévient une fois par jour quand la dépense approche ou atteint le budget (ou bloque une génération)."""
    try:
        budget = float(db.get_setting("daily_budget") or 0)
        pct = float(db.get_setting("budget_alert_pct") or 80)
    except ValueError:
        return
    if budget <= 0:
        return
    spent = db.cost_today()
    from . import notify
    today = db.now()[:10]
    if (blocked or spent >= budget) and db.notify_once(f"budget-over-{today}"):
        notify.send(f"🛑 <b>Budget IA du jour atteint</b> : {spent:.2f} € / {budget:.2f} €. Seul ton PC (gratuit) "
                    "génère encore des photos aujourd'hui.", buttons=[[("📸 Studio", "m:studio")]])
        db.log_event("photo", f"Budget IA du jour atteint ({spent:.2f} €)", level="warning")
    elif spent >= budget * pct / 100 and db.notify_once(f"budget-warn-{today}"):
        notify.send(f"⚠️ Budget IA à {100 * spent / budget:.0f} % : {spent:.2f} € / {budget:.2f} € aujourd'hui.")


# ---------------- interface ----------------
def generate(prompt: str, refs, out_path: Path, provider="auto", aspect="3:4", kind="photo", product_id=None) -> dict:
    """Génère une image et l'enregistre. Renvoie {"path", "provider", "model", "cost", "fallback_from"}."""
    refs = [Path(r) for r in (refs or [])]
    errors = []
    from . import comfyui
    for name in order(provider):
        cost = config.COST_PER_IMAGE.get(name, 0.05)
        left = budget_left() if name in PAID else None
        if left is not None and left < cost:
            errors.append(f"{name} : budget du jour atteint ({db.get_setting('daily_budget')} €)")
            _budget_alert(blocked=True)
            continue
        try:
            if name == "local":
                raw = comfyui.generate(prompt, refs, aspect)
            elif name == "gemini":
                raw = gemini_generate(prompt, refs, aspect)
            else:
                raw = openai_generate(prompt, refs, aspect)
        except (ImageError, comfyui.ComfyError, requests.RequestException) as e:
            errors.append(f"{name} : {e}")
            continue
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
        model = {"gemini": config.GEMINI_IMAGE_MODEL, "openai": config.OPENAI_IMAGE_MODEL,
                 "local": config.COMFY_UNET.split(".")[0]}[name]
        db.log_cost(name, model, kind, cost, product_id)
        if name in PAID:
            _budget_alert()
        return {"path": out_path, "provider": name, "model": model, "cost": cost,
                "fallback_from": errors[0].split(" : ")[0] if errors else None}
    raise ImageError(" | ".join(errors))
