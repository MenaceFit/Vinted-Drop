"""Appels à l'API OpenAI en REST (pas de dépendance au SDK, qui change souvent)."""
import base64
import json
import mimetypes
from pathlib import Path

import requests

from . import config

API = "https://api.openai.com/v1"


class AIError(RuntimeError):
    pass


def _headers():
    if not config.OPENAI_API_KEY:
        raise AIError("OPENAI_API_KEY manquant dans le fichier .env")
    return {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}


def _check(resp):
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        raise AIError(f"OpenAI {resp.status_code}: {msg}")
    return resp.json()


def generate_image(prompt: str, out_path: Path, size="1024x1536") -> Path:
    resp = requests.post(f"{API}/images/generations", headers=_headers(), timeout=300, json={
        "model": config.OPENAI_IMAGE_MODEL, "prompt": prompt, "size": size,
        "quality": config.OPENAI_IMAGE_QUALITY, "n": 1,
    })
    return _save(_check(resp), out_path)


def edit_image(prompt: str, images: list[Path], out_path: Path, size="1024x1536") -> Path:
    """Génère une image à partir d'images de référence (produit, modèle...)."""
    files = []
    for p in images:
        p = Path(p)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        files.append(("image[]", (p.name, p.read_bytes(), mime)))
    data = {"model": config.OPENAI_IMAGE_MODEL, "prompt": prompt, "size": size,
            "quality": config.OPENAI_IMAGE_QUALITY, "n": "1"}
    resp = requests.post(f"{API}/images/edits", headers=_headers(), data=data, files=files, timeout=300)
    return _save(_check(resp), out_path)


def _save(payload, out_path: Path) -> Path:
    item = payload["data"][0]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if item.get("b64_json"):
        out_path.write_bytes(base64.b64decode(item["b64_json"]))
    else:
        out_path.write_bytes(requests.get(item["url"], timeout=120).content)
    return out_path


def image_to_data_url(path: Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def chat_json(system: str, user_text: str, image: Path | None = None) -> dict:
    content = [{"type": "text", "text": user_text}]
    if image:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(image)}})
    resp = requests.post(f"{API}/chat/completions", headers=_headers(), timeout=120, json={
        "model": config.OPENAI_TEXT_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
    })
    text = _check(resp)["choices"][0]["message"]["content"]
    return json.loads(text)


def chat_tools(messages: list, tools: list) -> dict:
    """Un tour de conversation avec appel d'outils (function calling)."""
    resp = requests.post(f"{API}/chat/completions", headers=_headers(), timeout=120, json={
        "model": config.OPENAI_TEXT_MODEL, "messages": messages, "tools": tools,
    })
    return _check(resp)["choices"][0]["message"]
