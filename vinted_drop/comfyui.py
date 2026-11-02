"""Génération d'images gratuite sur ton PC avec ComfyUI (modèle Qwen-Image-Edit-2509, licence Apache 2.0).

Le workflow (data/comfyui/qwen_image_edit_2509.json) contient des repères remplacés à chaque image :
  __PROMPT__ __NEGATIVE__ __IMAGE1__ __IMAGE2__ __IMAGE3__ __SEED__ __WIDTH__ __HEIGHT__
  __STEPS__ __CFG__ __UNET__ __CLIP__ __VAE__ __LORA__
Tu peux utiliser ton propre workflow : exporte-le depuis ComfyUI (menu Workflow → Export (API)),
mets ces repères dans les bons champs et indique son chemin dans COMFY_WORKFLOW.
Les emplacements d'image non utilisés (moins de 3 références) sont retirés automatiquement.
"""
import copy
import json
import random
import re
import time
import uuid
from pathlib import Path

import requests

from . import config

NEGATIVE = ("blurry, deformed hands, extra fingers, extra limbs, distorted face, text, watermark, logo, "
            "cartoon, illustration, 3d render, oversaturated")
SIZES = {"3:4": (896, 1184), "1:1": (1024, 1024), "4:3": (1184, 896)}


class ComfyError(RuntimeError):
    pass


def url():
    return config.COMFYUI_URL.rstrip("/")


def status(timeout=5) -> dict:
    """État du serveur ComfyUI : {"ok": bool, "gpu": str, "vram_gb": float, "error": str}."""
    if not config.COMFYUI_URL:
        return {"ok": False, "error": "COMFYUI_URL non configuré"}
    try:
        r = requests.get(f"{url()}/system_stats", timeout=timeout)
        r.raise_for_status()
        dev = (r.json().get("devices") or [{}])[0]
        return {"ok": True, "gpu": dev.get("name", "?"), "vram_gb": round((dev.get("vram_total") or 0) / 1e9, 1)}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": f"ComfyUI injoignable ({e.__class__.__name__}) : lance ComfyUI sur {config.COMFYUI_URL}"}


def load_workflow() -> dict:
    path = Path(config.COMFY_WORKFLOW) if config.COMFY_WORKFLOW else config.ROOT / "data/comfyui/qwen_image_edit_2509.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def prune(wf: dict, n_images: int, use_lora: bool) -> dict:
    """Retire les emplacements d'image inutilisés et, si besoin, le LoRA."""
    wf = copy.deepcopy(wf)
    removed = {nid for nid, node in wf.items()
               for k, v in node.get("inputs", {}).items()
               if isinstance(v, str) and (m := re.fullmatch(r"__IMAGE(\d)__", v)) and int(m.group(1)) > n_images}
    if not use_lora:
        for nid, node in list(wf.items()):
            if node.get("class_type") == "LoraLoaderModelOnly":
                src = node["inputs"].get("model")
                for other in wf.values():  # on branche directement le modèle de base
                    for k, v in other.get("inputs", {}).items():
                        if _is_link(v) and v[0] == nid:
                            other["inputs"][k] = src
                del wf[nid]
    changed = True
    while changed:
        changed = False
        for nid in removed:
            wf.pop(nid, None)
        for nid, node in list(wf.items()):
            for k, v in list(node.get("inputs", {}).items()):
                if _is_link(v) and v[0] in removed:
                    if re.fullmatch(r"image\d+", k):     # entrée d'image facultative : on la retire
                        del node["inputs"][k]
                    elif nid not in removed:             # nœud qui dépend d'une image retirée
                        removed.add(nid)
                        changed = True
    for nid in removed:
        wf.pop(nid, None)
    return wf


def fill(wf: dict, values: dict) -> dict:
    def sub(v):
        if isinstance(v, str) and v in values:
            return values[v]
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        return v
    return sub(wf)


def upload(path: Path) -> str:
    path = Path(path)
    with open(path, "rb") as f:
        r = requests.post(f"{url()}/upload/image", timeout=60,
                          files={"image": (f"vd_{uuid.uuid4().hex[:8]}{path.suffix}", f)},
                          data={"overwrite": "true", "type": "input"})
    if r.status_code >= 400:
        raise ComfyError(f"Envoi de l'image à ComfyUI impossible : {r.text[:200]}")
    j = r.json()
    return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]


def generate(prompt: str, refs, aspect="3:4") -> bytes:
    if not config.COMFYUI_URL:
        raise ComfyError("COMFYUI_URL non configuré")
    refs = [Path(r) for r in refs][:3]
    names = [upload(r) for r in refs]
    w, h = SIZES.get(aspect, SIZES["3:4"])
    use_lora = bool(config.COMFY_LORA)
    wf = prune(load_workflow(), len(names), use_lora)
    values = {"__PROMPT__": prompt, "__NEGATIVE__": NEGATIVE, "__SEED__": random.randint(1, 2**31 - 1),
              "__WIDTH__": w, "__HEIGHT__": h, "__STEPS__": config.COMFY_STEPS if use_lora else max(config.COMFY_STEPS, 20),
              "__CFG__": config.COMFY_CFG if use_lora else max(config.COMFY_CFG, 2.5),
              "__UNET__": config.COMFY_UNET, "__CLIP__": config.COMFY_CLIP, "__VAE__": config.COMFY_VAE,
              "__LORA__": config.COMFY_LORA}
    values.update({f"__IMAGE{i + 1}__": n for i, n in enumerate(names)})
    wf = fill(wf, values)
    r = requests.post(f"{url()}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex}, timeout=60)
    if r.status_code >= 400:
        raise ComfyError(f"ComfyUI a refusé le workflow (modèle manquant ?) : {r.text[:300]}")
    pid = r.json()["prompt_id"]
    deadline = time.time() + config.COMFY_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        h_ = requests.get(f"{url()}/history/{pid}", timeout=30).json().get(pid)
        if not h_:
            continue
        st = h_.get("status") or {}
        if st.get("status_str") == "error":
            raise ComfyError(f"Erreur ComfyUI : {str(st.get('messages'))[:300]}")
        for out in (h_.get("outputs") or {}).values():
            for img in out.get("images") or []:
                v = requests.get(f"{url()}/view", timeout=60, params={
                    "filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
                v.raise_for_status()
                return v.content
    raise ComfyError(f"ComfyUI n'a pas fini en {config.COMFY_TIMEOUT} s")
