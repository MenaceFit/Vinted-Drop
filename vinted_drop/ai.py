"""Appels IA texte / vision (OpenAI en priorité, Gemini en secours), en REST."""
import base64
import json
import mimetypes
import re
from pathlib import Path

import requests

from . import config

API = "https://api.openai.com/v1"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_working_model = None  # premier modèle OpenAI qui a répondu


class AIError(RuntimeError):
    pass


def _check(resp, who="OpenAI"):
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        raise AIError(f"{who} {resp.status_code}: {msg}")
    return resp.json()


def image_to_data_url(path: Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _openai_chat(payload: dict) -> dict:
    """Essaie OPENAI_TEXT_MODEL puis les modèles de secours si un modèle a été retiré."""
    global _working_model
    if not config.OPENAI_API_KEY:
        raise AIError("OPENAI_API_KEY manquant")
    models = [_working_model] if _working_model else []
    models += [m for m in [config.OPENAI_TEXT_MODEL, *config.OPENAI_TEXT_FALLBACKS] if m and m not in models]
    last = None
    for m in models:
        r = requests.post(f"{API}/chat/completions", headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                          timeout=120, json={**payload, "model": m})
        if r.status_code in (400, 404) and "model" in r.text.lower() and ("not" in r.text.lower() or "exist" in r.text.lower()):
            last = r
            continue
        data = _check(r)
        _working_model = m
        return data
    raise AIError(f"Aucun modèle OpenAI disponible : {last.text[:200] if last is not None else ''}")


def _gemini_text(system: str, text: str, images=()) -> str:
    if not config.GEMINI_API_KEY:
        raise AIError("GEMINI_API_KEY manquant")
    parts = [{"type": "text", "text": f"{system}\n\n{text}"}]
    for img in images:
        p = Path(img)
        parts.append({"type": "image", "mime_type": mimetypes.guess_type(p.name)[0] or "image/jpeg",
                      "data": base64.b64encode(p.read_bytes()).decode()})
    r = requests.post(f"{GEMINI_API}/interactions", timeout=120,
                      headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
                      json={"model": config.GEMINI_TEXT_MODEL, "input": parts})
    data = _check(r, "Gemini")
    return find_text(data)


def find_text(obj) -> str:
    if isinstance(obj, dict):
        if isinstance(obj.get("output_text"), str):
            return obj["output_text"]
        if obj.get("type") == "text" and isinstance(obj.get("text"), str):
            return obj["text"]
        if isinstance(obj.get("text"), str) and "candidates" not in obj:
            return obj["text"]
        for v in obj.values():
            t = find_text(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = find_text(v)
            if t:
                return t
    return ""


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise AIError("Réponse IA sans JSON")
    return json.loads(m.group(0))


def chat_json(system: str, user_text: str, image: Path | None = None, images=None) -> dict:
    """Réponse JSON, avec une ou plusieurs images en entrée. OpenAI, puis Gemini en secours."""
    imgs = [Path(i) for i in (images or ([image] if image else []))]
    errors = []
    if config.OPENAI_API_KEY:
        content = [{"type": "text", "text": user_text}] + [
            {"type": "image_url", "image_url": {"url": image_to_data_url(i)}} for i in imgs]
        try:
            data = _openai_chat({"response_format": {"type": "json_object"},
                                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]})
            return json.loads(data["choices"][0]["message"]["content"])
        except (AIError, requests.RequestException, ValueError) as e:
            errors.append(str(e))
    if config.GEMINI_API_KEY:
        try:
            return _parse_json(_gemini_text(system + "\nRéponds uniquement en JSON.", user_text, imgs))
        except (AIError, requests.RequestException, ValueError) as e:
            errors.append(str(e))
    raise AIError(" | ".join(errors) or "Aucune clé IA (OPENAI_API_KEY ou GEMINI_API_KEY)")


def chat_tools(messages: list, tools: list) -> dict:
    """Un tour de conversation avec appel d'outils (assistant Telegram, OpenAI)."""
    return _openai_chat({"messages": messages, "tools": tools})["choices"][0]["message"]
