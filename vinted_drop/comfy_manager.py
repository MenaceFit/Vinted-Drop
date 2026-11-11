"""Gestion de ComfyUI depuis le dashboard : détection, vérification, téléchargement des modèles, démarrage.

Tout est pensé pour un ComfyUI installé sur le même PC que le bot :
- détection automatique de l'adresse (ports 8188, 8189, 8000, 8001, 8288) ;
- contrôle de la carte graphique, des fichiers du modèle et des nœuds nécessaires (API de ComfyUI) ;
- téléchargement des fichiers manquants depuis Hugging Face, avec reprise après coupure ;
- installation de l'extension ComfyUI-GGUF (profil léger) ;
- démarrage de ComfyUI (version portable ou installation manuelle) ;
- file d'attente : interrompre, vider, libérer la mémoire vidéo.

Aucun fichier n'est téléchargé ailleurs que depuis les adresses fixes des profils (comfyui.PRESETS).
"""
import io
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import requests

from . import comfyui, config, db

PORTS = (8188, 8189, 8000, 8001, 8288)
GGUF_ZIP = "https://github.com/city96/ComfyUI-GGUF/archive/refs/heads/main.zip"
CHUNK = 1024 * 1024
PROGRESS_KEY = "_comfy_download"
CANCEL_KEY = "_comfy_download_cancel"


class ManagerError(RuntimeError):
    pass


# ---------------- réglages ----------------
def comfy_dir() -> Path | None:
    d = db.get_setting("comfy_dir") or config.COMFY_DIR
    return Path(d).expanduser() if d else None


def models_dir(base: Path | None = None) -> Path | None:
    """Dossier « models » de ComfyUI : <dossier>/models (Desktop, installation git) ou
    <dossier>/ComfyUI/models (version portable)."""
    base = base or comfy_dir()
    if not base:
        return None
    for cand in (base / "models", base / "ComfyUI" / "models"):
        if cand.is_dir():
            return cand
    return base / "ComfyUI" / "models" if (base / "ComfyUI").is_dir() else base / "models"


def save_settings(url=None, folder=None, preset=None):
    if url is not None:
        url = url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ManagerError("L'adresse doit commencer par http:// (ex. http://127.0.0.1:8188)")
        db.set_setting("comfy_url", url)
    if folder is not None:
        folder = folder.strip().strip('"')
        if folder and not Path(folder).expanduser().is_dir():
            raise ManagerError(f"Dossier introuvable sur ce PC : {folder}")
        db.set_setting("comfy_dir", folder)
    if preset is not None:
        if preset not in comfyui.PRESETS:
            raise ManagerError("Profil inconnu")
        db.set_setting("comfy_preset", preset)


# ---------------- détection ----------------
def _is_comfy(base, timeout=1.5):
    try:
        r = requests.get(f"{base}/system_stats", timeout=timeout)
        return r.status_code == 200 and "devices" in r.json()
    except (requests.RequestException, ValueError):
        return False


def detect(host="127.0.0.1") -> str | None:
    """Cherche ComfyUI sur ce PC et enregistre son adresse. Ignore le port du dashboard."""
    for port in PORTS:
        if port == config.DASHBOARD_PORT:
            continue
        base = f"http://{host}:{port}"
        if _is_comfy(base):
            db.set_setting("comfy_url", base)
            db.log_event("comfyui", f"ComfyUI détecté sur {base}")
            return base
    return None


# ---------------- vérification ----------------
def _api(path, timeout=5):
    r = requests.get(f"{comfyui.url()}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def required_nodes(preset_key=None) -> list[str]:
    key = preset_key or comfyui.preset_key()
    pr = comfyui.PRESETS[key]
    wf = comfyui.load_workflow(config.ROOT / "data/comfyui" / pr["workflow"])
    nodes = sorted({n["class_type"] for n in wf.values()} - {"UNETLoader"} | {pr["loader"]})
    return nodes


def _disk_file(folder: str, name: str):
    md = models_dir()
    if not md:
        return None, 0
    path = md / folder / name
    if path.exists():
        return path, path.stat().st_size
    part = path.with_name(path.name + ".part")
    return part, part.stat().st_size if part.exists() else 0


def check(preset_key=None, timeout=4) -> dict:
    """État complet pour la page du dashboard. Ne lève jamais d'exception."""
    key = preset_key or comfyui.preset_key()
    pr = comfyui.PRESETS[key]
    st = comfyui.status(timeout=timeout)
    out = {"status": st, "url": comfyui.url(), "preset": key, "presets": comfyui.PRESETS,
           "dir": str(comfy_dir()) if comfy_dir() else "", "models_dir": str(models_dir()) if models_dir() else "",
           "files": [], "nodes": [], "queue": {"running": 0, "pending": 0}, "problems": []}
    api_lists = {}
    if st["ok"]:
        try:
            q = _api("/queue", timeout)
            out["queue"] = {"running": len(q.get("queue_running") or []), "pending": len(q.get("queue_pending") or [])}
        except (requests.RequestException, ValueError):
            pass
        for node in required_nodes(key):
            try:
                ok = node in _api(f"/object_info/{node}", timeout)
            except (requests.RequestException, ValueError):
                ok = False
            out["nodes"].append({"name": node, "ok": ok})
    for role, f in pr["files"].items():
        api_folder = f.get("api_folder", f["folder"])
        present_api = None
        if st["ok"]:
            if api_folder not in api_lists:
                try:
                    api_lists[api_folder] = _api(f"/models/{api_folder}", timeout)
                except (requests.RequestException, ValueError):
                    api_lists[api_folder] = None
            if api_lists[api_folder] is not None:
                present_api = any(Path(x).name == f["name"] for x in api_lists[api_folder])
        path, size = _disk_file(f["folder"], f["name"])
        on_disk = bool(path and path.exists() and not path.name.endswith(".part") and size > 0)
        partial = bool(path and path.name.endswith(".part") and size > 0)
        out["files"].append({**f, "role": role, "present": bool(present_api or on_disk), "seen_by_comfy": present_api,
                             "on_disk": on_disk, "partial_gb": round(size / 1e9, 2) if partial else 0,
                             "path": str(path) if path else None})
    missing = [f for f in out["files"] if not f["present"]]
    out["missing_gb"] = round(sum(f["gb"] - f["partial_gb"] for f in missing), 1)
    md = models_dir()
    if md:
        probe = md if md.exists() else md.parent if md.parent.exists() else None
        if probe:
            out["disk_free_gb"] = round(shutil.disk_usage(probe).free / 1e9, 1)
    # conseils
    if not st["ok"]:
        out["problems"].append(st["error"])
    else:
        if st.get("type") and st["type"] != "cuda":
            out["problems"].append(f"Pas de carte NVIDIA détectée ({st['type']}) : la génération sera très lente.")
        if st.get("vram_gb") and st["vram_gb"] < pr["vram"] - 0.5:
            out["problems"].append(f"Ta carte a {st['vram_gb']} Go de mémoire vidéo : ce profil en demande {pr['vram']}."
                                   + (" Choisis le profil « Léger »." if key != "leger" else ""))
        if st.get("ram_gb") and st["ram_gb"] < pr["ram"] - 2:
            out["problems"].append(f"{st['ram_gb']} Go de mémoire vive : {pr['ram']} Go conseillés (sinon lenteurs).")
        bad_nodes = [n["name"] for n in out["nodes"] if not n["ok"]]
        if bad_nodes:
            if pr.get("custom_node") and pr["loader"] in bad_nodes:
                out["problems"].append("Extension ComfyUI-GGUF absente : installe-la (bouton ci-dessous) puis redémarre ComfyUI.")
            others = [n for n in bad_nodes if n != pr["loader"]] if pr.get("custom_node") else bad_nodes
            if others:
                out["problems"].append("ComfyUI trop ancien (nœuds absents : " + ", ".join(others) + ") : mets-le à jour.")
    if missing:
        out["problems"].append(f"{len(missing)} fichier(s) du modèle manquant(s) ({out['missing_gb']} Go).")
        if out.get("disk_free_gb") is not None and out["disk_free_gb"] < out["missing_gb"] + 2:
            out["problems"].append(f"Espace disque insuffisant : {out['disk_free_gb']} Go libres pour "
                                   f"{out['missing_gb']} Go à télécharger.")
    out["ready"] = st["ok"] and not missing and all(n["ok"] for n in out["nodes"])
    out["download"] = progress()
    out["recommended"] = recommend_preset(st)
    return out


def recommend_preset(st: dict) -> str | None:
    if not st.get("ok") or not st.get("vram_gb"):
        return None
    return "standard" if st["vram_gb"] >= 11.5 else "leger"


# ---------------- téléchargement ----------------
def progress() -> dict:
    return db.get_json_setting(PROGRESS_KEY, {}) or {}


def _set_progress(**p):
    cur = progress()
    cur.update(p)
    db.set_setting(PROGRESS_KEY, json.dumps(cur, ensure_ascii=False))


def cancel_download():
    db.set_setting(CANCEL_KEY, "1")


def _fetch(url, dest: Path, label, index, count):
    """Télécharge url vers dest, en reprenant un .part existant. Vérifie la taille annoncée."""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "vinted-drop/2.3"}
    if have:
        headers["Range"] = f"bytes={have}-"
    with requests.get(url, headers=headers, stream=True, timeout=(15, 120), allow_redirects=True) as r:
        if r.status_code == 416:            # déjà complet
            part.replace(dest)
            return
        if r.status_code not in (200, 206):
            raise ManagerError(f"{label} : téléchargement refusé (HTTP {r.status_code})")
        if r.status_code == 200:            # le serveur ne reprend pas : on recommence
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        mode = "ab" if have else "wb"
        last, t0, start_have = 0.0, time.time(), have
        with open(part, mode) as fh:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                fh.write(chunk)
                have += len(chunk)
                now = time.time()
                if now - last >= 1:
                    last = now
                    if db.get_setting(CANCEL_KEY) == "1":
                        raise ManagerError("Téléchargement arrêté (il reprendra là où il s'est arrêté)")
                    speed = (have - start_have) / max(now - t0, 0.001)
                    _set_progress(file=label, index=index, count=count, done=have, total=total,
                                  speed_mb=round(speed / 1e6, 1),
                                  eta_min=round((total - have) / speed / 60, 1) if speed and total else None)
    if total and have < total:
        raise ManagerError(f"{label} : téléchargement incomplet ({have} / {total} octets), relance pour reprendre")
    part.replace(dest)


def download_missing(preset_key=None) -> dict:
    """Télécharge les fichiers manquants du profil dans le dossier de ComfyUI (tâche de fond)."""
    key = preset_key or comfyui.preset_key()
    md = models_dir()
    if not md:
        raise ManagerError("Indique d'abord le dossier de ComfyUI sur ce PC")
    state = check(key, timeout=3)
    todo = [f for f in state["files"] if not f["present"]]
    if not todo:
        _set_progress(status="done", file=None, message="Tous les fichiers sont déjà là")
        return {"downloaded": 0}
    need = sum(f["gb"] - f["partial_gb"] for f in todo)
    free = state.get("disk_free_gb")
    if free is not None and free < need + 1:
        raise ManagerError(f"Pas assez de place : {free} Go libres, {need:.1f} Go nécessaires")
    db.set_setting(CANCEL_KEY, "0")
    db.set_setting(PROGRESS_KEY, json.dumps({"status": "running", "preset": key, "started_at": db.now()}))
    try:
        for i, f in enumerate(todo, 1):
            _set_progress(file=f["name"], index=i, count=len(todo), done=0, total=int(f["gb"] * 1e9))
            _fetch(f["url"], md / f["folder"] / f["name"], f["name"], i, len(todo))
            db.log_event("comfyui", f"Modèle téléchargé : {f['name']}")
    except Exception as e:
        _set_progress(status="error", error=str(e)[:300])
        raise
    _set_progress(status="done", file=None, message=f"{len(todo)} fichier(s) téléchargé(s). "
                  "Dans ComfyUI, appuie sur R (ou redémarre-le) pour qu'il les voie.")
    return {"downloaded": len(todo)}


# ---------------- extension GGUF ----------------
def comfy_python(base: Path | None = None) -> Path | None:
    """Python utilisé par ComfyUI (portable : python_embeded ; installation manuelle : .venv / venv)."""
    base = base or comfy_dir()
    if not base:
        return None
    win = os.name == "nt"
    cands = [base / "python_embeded" / "python.exe", base.parent / "python_embeded" / "python.exe"]
    for v in (".venv", "venv"):
        cands.append(base / v / ("Scripts/python.exe" if win else "bin/python"))
        cands.append(base / "ComfyUI" / v / ("Scripts/python.exe" if win else "bin/python"))
    return next((c for c in cands if c.exists()), None)


def comfy_root(base: Path | None = None) -> Path | None:
    """Dossier qui contient main.py (le code de ComfyUI)."""
    base = base or comfy_dir()
    if not base:
        return None
    for cand in (base, base / "ComfyUI"):
        if (cand / "main.py").exists():
            return cand
    return None


def install_gguf_node() -> str:
    root = comfy_root()
    md = models_dir()
    target_parent = (root / "custom_nodes") if root else (md.parent / "custom_nodes" if md else None)
    if not target_parent:
        raise ManagerError("Indique d'abord le dossier de ComfyUI")
    target = target_parent / "ComfyUI-GGUF"
    if not (target / "nodes.py").exists():
        r = requests.get(GGUF_ZIP, timeout=120)
        r.raise_for_status()
        target_parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            prefix = z.namelist()[0].split("/")[0] + "/"
            for m in z.infolist():
                rel = m.filename[len(prefix):]
                if not rel or rel.startswith(("/", "..")) or ".." in Path(rel).parts:
                    continue
                dest = target / rel
                if m.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(m))
    py = comfy_python()
    if not py:
        db.log_event("comfyui", "Extension ComfyUI-GGUF copiée ; dépendance « gguf » à installer depuis ComfyUI Manager")
        return ("Extension copiée dans custom_nodes. Il manque la librairie « gguf » : ouvre ComfyUI Manager → "
                "« Install Missing Custom Nodes », ou redémarre ComfyUI Desktop qui l'installe. Puis redémarre ComfyUI.")
    res = subprocess.run([str(py), "-m", "pip", "install", "-r", str(target / "requirements.txt")],
                         capture_output=True, text=True, timeout=900)
    if res.returncode != 0:
        raise ManagerError("Installation de la librairie gguf impossible : " + (res.stderr or res.stdout)[-300:])
    db.log_event("comfyui", "Extension ComfyUI-GGUF installée")
    return "Extension ComfyUI-GGUF installée. Redémarre ComfyUI pour l'activer."


# ---------------- démarrage et file d'attente ----------------
def start(port=8188) -> str:
    """Lance ComfyUI en arrière-plan (version portable ou installation manuelle)."""
    if comfyui.url() and _is_comfy(comfyui.url()):
        return f"ComfyUI tourne déjà sur {comfyui.url()}"
    if port == config.DASHBOARD_PORT:
        port = 8188
    root, py = comfy_root(), comfy_python()
    if not root or not py:
        raise ManagerError("Démarrage automatique possible avec la version portable ou une installation manuelle de "
                           "ComfyUI (dossier contenant main.py). Avec ComfyUI Desktop, ouvre simplement l'application.")
    args = [str(py), "-s", str(root / "main.py"), "--port", str(port), "--disable-auto-launch"]
    if os.name == "nt" and "python_embeded" in str(py):
        args.insert(3, "--windows-standalone-build")
    log = open(config.DATA_DIR / "comfyui.log", "ab")
    kw = {"cwd": str(root), "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)
    url = f"http://127.0.0.1:{port}"
    db.set_setting("comfy_url", url)
    db.log_event("comfyui", f"ComfyUI lancé ({url})")
    return f"ComfyUI démarre sur {url} (30 s à 1 min). Journal : data/comfyui.log"


def wait_ready(timeout=120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if comfyui.url() and _is_comfy(comfyui.url()):
            return True
        time.sleep(3)
    return False


def _post(path, payload=None):
    r = requests.post(f"{comfyui.url()}{path}", json=payload or {}, timeout=10)
    if r.status_code >= 400:
        raise ManagerError(f"ComfyUI a refusé ({r.status_code})")


def interrupt():
    _post("/interrupt")


def clear_queue():
    _post("/queue", {"clear": True})


def free_memory():
    _post("/free", {"unload_models": True, "free_memory": True})


# ---------------- test et mode gratuit ----------------
TEST_PROMPT = ("Professional e-commerce flat lay photo of this exact garment on a light grey studio background, "
               "soft even lighting, sharp details, no text")


def _test_reference() -> Path:
    """Image de référence pour le test : la photo fournisseur d'un produit, sinon un vêtement dessiné."""
    with db.connect() as c:
        r = c.execute("SELECT supplier_image FROM products WHERE supplier_image IS NOT NULL AND supplier_image != '' "
                      "ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        from .photos import abs_media
        p = Path(abs_media(r[0]))
        if p.exists():
            return p
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (768, 1024), (236, 236, 236))
    d = ImageDraw.Draw(img)
    d.polygon([(230, 180), (330, 140), (384, 190), (438, 140), (538, 180), (640, 330), (560, 380), (520, 320),
               (520, 860), (248, 860), (248, 320), (208, 380), (128, 330)], fill=(24, 24, 28))
    d.polygon([(330, 140), (384, 190), (438, 140), (420, 132), (384, 165), (348, 132)], fill=(200, 170, 80))
    path = config.MEDIA_DIR / "comfy_test_ref.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def run_test() -> dict:
    """Génère une image de test (gratuite) et l'enregistre dans media/comfy_test.png."""
    ref = _test_reference()
    t0 = time.time()
    raw = comfyui.generate(TEST_PROMPT, [ref], "3:4")
    out = config.MEDIA_DIR / "comfy_test.png"
    out.write_bytes(raw)
    res = {"seconds": round(time.time() - t0, 1), "at": db.now(), "preset": comfyui.preset_key(),
           "ref": ref.name}
    db.set_setting("_comfy_test", json.dumps(res))
    db.log_event("comfyui", f"Image test générée en {res['seconds']} s (0 €)")
    return res


def last_test() -> dict | None:
    t = db.get_json_setting("_comfy_test", None)
    if t and (config.MEDIA_DIR / "comfy_test.png").exists():
        return t
    return None


def free_mode(qa_off=False) -> list[str]:
    """Mode 100 % gratuit : IA gratuites uniquement (ton PC, Hugging Face), puis studio instantané.
    Aucun fournisseur payant n'est jamais appelé, quel que soit le budget."""
    db.set_setting("photo_provider", "gratuit")
    done = ["Photos : ton PC → Hugging Face → studio instantané", "Aucun fournisseur payant (Gemini, OpenAI)"]
    if qa_off:
        db.set_setting("photo_qa", "off")
        done.append("Contrôle qualité IA coupé (vérifie les photos toi-même)")
    db.log_event("settings", "Mode 100 % gratuit activé : " + ", ".join(done))
    return done
