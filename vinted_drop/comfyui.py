"""Génération d'images gratuite sur ton PC avec ComfyUI et Qwen-Image-Edit (licence Apache 2.0).

Trois profils (choisis dans le dashboard, page « Photos gratuites ») :
- standard : Qwen-Image-Edit-2511 fp8 + Lightning 4 étapes (12 Go de mémoire vidéo ou plus) ;
- leger : la même chose en GGUF Q4 (8 à 11 Go, extension ComfyUI-GGUF nécessaire) ;
- 2509 : l'ancienne version, pour garder des fichiers déjà téléchargés.

Le workflow (data/comfyui/*.json) contient des repères remplacés à chaque image :
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

HF = "https://huggingface.co"
_TE = {"folder": "text_encoders", "name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "gb": 9.4,
       "url": f"{HF}/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"}
_VAE = {"folder": "vae", "name": "qwen_image_vae.safetensors", "gb": 0.25,
        "url": f"{HF}/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors"}
_LORA_2511 = {"folder": "loras", "name": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", "gb": 0.85,
              "url": f"{HF}/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"}

PRESETS = {
    "standard": {
        "label": "Qualité — Qwen-Image-Edit-2511 (fp8)", "vram": 12, "ram": 32,
        "help": "Le meilleur rendu. Carte NVIDIA 12 Go ou plus (RTX 3060 12 Go, 4070, 4080, 3090, 4090, 5090).",
        "workflow": "qwen_image_edit_2511.json", "loader": "UNETLoader",
        "files": {"unet": {"folder": "diffusion_models", "name": "qwen_image_edit_2511_fp8mixed.safetensors", "gb": 20.5,
                           "url": f"{HF}/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors"},
                  "clip": _TE, "vae": _VAE, "lora": _LORA_2511},
    },
    "leger": {
        "label": "Léger — Qwen-Image-Edit-2511 (GGUF Q4)", "vram": 8, "ram": 32,
        "help": "Pour les cartes de 8 à 11 Go (RTX 3060 8 Go, 3070, 4060). Un peu moins fin et plus lent. "
                "Demande l'extension ComfyUI-GGUF (installable depuis cette page).",
        "workflow": "qwen_image_edit_2511.json", "loader": "UnetLoaderGGUF", "custom_node": "ComfyUI-GGUF",
        "files": {"unet": {"folder": "diffusion_models", "api_folder": "unet_gguf", "name": "qwen-image-edit-2511-Q4_K_M.gguf",
                           "gb": 12.8, "url": f"{HF}/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/qwen-image-edit-2511-Q4_K_M.gguf"},
                  "clip": _TE, "vae": _VAE, "lora": _LORA_2511},
    },
    "2509": {
        "label": "Ancien — Qwen-Image-Edit-2509 (fp8)", "vram": 12, "ram": 32,
        "help": "À garder seulement si tu as déjà téléchargé les fichiers 2509.",
        "workflow": "qwen_image_edit_2509.json", "loader": "UNETLoader",
        "files": {"unet": {"folder": "diffusion_models", "name": "qwen_image_edit_2509_fp8_e4m3fn.safetensors", "gb": 20.4,
                           "url": f"{HF}/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors"},
                  "clip": _TE, "vae": _VAE,
                  "lora": {"folder": "loras", "name": "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors", "gb": 0.85,
                           "url": f"{HF}/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors"}},
    },
}


def _setting(key, default=""):
    from . import db
    try:
        return db.get_setting(key) or default
    except Exception:          # base pas encore créée
        return default


def preset_key() -> str:
    """Profil choisi dans le dashboard, sinon COMFY_PRESET, sinon déduit d'une ancienne config 2509, sinon standard."""
    key = _setting("comfy_preset") or config.COMFY_PRESET
    if key in PRESETS:
        return key
    if "2509" in (config.COMFY_UNET or ""):
        return "2509"
    return "standard"


def settings() -> dict:
    """Réglages effectifs : profil + noms de fichiers (personnalisés dans .env seulement sans profil choisi)."""
    key = preset_key()
    pr = PRESETS[key]
    custom = not _setting("comfy_preset")       # pas de choix dans le dashboard : on respecte le .env
    f = pr["files"]
    pick = (lambda env, role: (env if custom and env else f[role]["name"]))
    return {"url": url(), "preset": key, "label": pr["label"], "loader": pr["loader"],
            "workflow": config.COMFY_WORKFLOW or str(config.ROOT / "data/comfyui" / pr["workflow"]),
            "unet": pick(config.COMFY_UNET, "unet"), "clip": pick(config.COMFY_CLIP, "clip"),
            "vae": pick(config.COMFY_VAE, "vae"), "lora": pick(config.COMFY_LORA, "lora"),
            "steps": config.COMFY_STEPS, "cfg": config.COMFY_CFG}


class ComfyError(RuntimeError):
    pass


def url() -> str:
    """Adresse de ComfyUI : réglage du dashboard, sinon COMFYUI_URL du .env."""
    return (_setting("comfy_url") or config.COMFYUI_URL or "").rstrip("/")


def _gpu_name(dev):
    name = dev.get("name", "?")
    return re.sub(r"^cuda:\d+\s*", "", name).split(" : ")[0].strip()


def status(timeout=5) -> dict:
    """État du serveur ComfyUI : {"ok", "gpu", "vram_gb", "vram_free_gb", "ram_gb", "version", "error"}."""
    if not url():
        return {"ok": False, "error": "ComfyUI n'est pas encore branché : lance-le puis clique « Détecter »"}
    try:
        r = requests.get(f"{url()}/system_stats", timeout=timeout)
        r.raise_for_status()
        j = r.json()
        dev = (j.get("devices") or [{}])[0]
        sysi = j.get("system") or {}
        return {"ok": True, "gpu": _gpu_name(dev), "type": dev.get("type"),
                "vram_gb": round((dev.get("vram_total") or 0) / 1e9, 1),
                "vram_free_gb": round((dev.get("vram_free") or 0) / 1e9, 1),
                "ram_gb": round((sysi.get("ram_total") or 0) / 1e9, 1) or None,
                "version": sysi.get("comfyui_version")}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": f"ComfyUI injoignable ({e.__class__.__name__}) : lance ComfyUI sur {url()}"}


def load_workflow(path=None) -> dict:
    path = Path(path or settings()["workflow"])
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


def build(prompt: str, names: list, aspect="3:4", seed=None) -> dict:
    """Workflow prêt à envoyer (repères remplis, chargeur GGUF si profil léger)."""
    st = settings()
    w, h = SIZES.get(aspect, SIZES["3:4"])
    use_lora = bool(st["lora"])
    wf = prune(load_workflow(st["workflow"]), len(names), use_lora)
    if st["loader"] != "UNETLoader":
        for node in wf.values():
            if node.get("class_type") == "UNETLoader":
                node["class_type"] = st["loader"]
                node["inputs"] = {"unet_name": node["inputs"]["unet_name"]}
    values = {"__PROMPT__": prompt, "__NEGATIVE__": NEGATIVE, "__SEED__": seed or random.randint(1, 2**31 - 1),
              "__WIDTH__": w, "__HEIGHT__": h, "__STEPS__": st["steps"] if use_lora else max(st["steps"], 20),
              "__CFG__": st["cfg"] if use_lora else max(st["cfg"], 2.5),
              "__UNET__": st["unet"], "__CLIP__": st["clip"], "__VAE__": st["vae"], "__LORA__": st["lora"]}
    values.update({f"__IMAGE{i + 1}__": n for i, n in enumerate(names)})
    return fill(wf, values)


def generate(prompt: str, refs, aspect="3:4") -> bytes:
    if not url():
        raise ComfyError("ComfyUI n'est pas branché (page Photos gratuites du dashboard)")
    refs = [Path(r) for r in refs][:3]
    names = [upload(r) for r in refs]
    wf = build(prompt, names, aspect)
    r = requests.post(f"{url()}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex}, timeout=60)
    if r.status_code >= 400:
        raise ComfyError(f"ComfyUI a refusé le workflow (modèle ou extension manquant : voir la page Photos "
                         f"gratuites) : {r.text[:300]}")
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
