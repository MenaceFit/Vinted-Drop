"""Page « Photos gratuites » : piloter ComfyUI (ton PC) depuis le dashboard."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import comfy_manager as cm
from .. import comfyui, config, db, hf_space, studio, tasks
from .common import background, redirect, render

router = APIRouter()
PAGE = "/photos-gratuites"


@router.get(PAGE)
def page(request: Request):
    state = cm.check()
    return render(request, "comfyui.html", s=state, test=cm.last_test(), settings=db.all_settings(),
                  hf=hf_space.check(), rembg=studio.rembg_available(), studio_bgs=studio.BACKGROUNDS,
                  studio_test=db.get_json_setting("_studio_test", None),
                  config_port=config.DASHBOARD_PORT,
                  running=bool(tasks.active(dedupe_key="comfy:download") or tasks.active(dedupe_key="comfy:test")),
                  gen=db.generation_stats())


@router.get("/api/comfyui")
def api_state():
    st = cm.check(timeout=2)
    st.pop("presets", None)
    return JSONResponse(st)


@router.post(f"{PAGE}/reglages")
def save(url: str = Form(""), folder: str = Form(""), preset: str = Form("")):
    try:
        cm.save_settings(url=url, folder=folder, preset=preset or None)
    except cm.ManagerError as e:
        return redirect(PAGE, err=str(e))
    db.log_event("settings", "Réglages ComfyUI modifiés")
    return redirect(PAGE, ok="Réglages enregistrés")


@router.post(f"{PAGE}/profil")
def set_preset(preset: str = Form(...)):
    try:
        cm.save_settings(preset=preset)
    except cm.ManagerError as e:
        return redirect(PAGE, err=str(e))
    return redirect(PAGE, ok=f"Profil choisi : {comfyui.PRESETS[preset]['label']}")


@router.post(f"{PAGE}/detecter")
def detect():
    found = cm.detect()
    return redirect(PAGE, ok=f"ComfyUI trouvé sur {found}") if found else redirect(
        PAGE, err="ComfyUI introuvable sur ce PC : lance-le (ou clique « Démarrer ComfyUI ») puis réessaie.")


@router.post(f"{PAGE}/demarrer")
def start():
    try:
        msg = cm.start()
    except (cm.ManagerError, OSError) as e:
        return redirect(PAGE, err=str(e))
    return redirect(PAGE, ok=msg)


@router.post(f"{PAGE}/telecharger")
def download():
    if not cm.models_dir():
        return redirect(PAGE, err="Indique d'abord le dossier de ComfyUI sur ce PC (le téléchargement va dedans).")
    _, created = background("comfyui", cm.download_missing, label="Téléchargement des modèles ComfyUI",
                            dedupe_key="comfy:download")
    return redirect(PAGE, ok="Téléchargement lancé : tu peux suivre la progression ici." if created
                    else "Un téléchargement est déjà en cours.")


@router.post(f"{PAGE}/arreter")
def stop_download():
    cm.cancel_download()
    return redirect(PAGE, ok="Arrêt demandé : le téléchargement reprendra là où il s'est arrêté.")


@router.post(f"{PAGE}/gguf")
def gguf():
    background("comfyui", cm.install_gguf_node, label="Installation de ComfyUI-GGUF", dedupe_key="comfy:gguf")
    return redirect(PAGE, ok="Installation de l'extension lancée (voir Activité). Redémarre ComfyUI ensuite.")


@router.post(PAGE + "/file/{action}")
def queue(action: str):
    fn = {"interrompre": cm.interrupt, "vider": cm.clear_queue, "liberer": cm.free_memory}.get(action)
    if not fn:
        return redirect(PAGE, err="Action inconnue")
    try:
        fn()
    except Exception as e:
        return redirect(PAGE, err=f"ComfyUI n'a pas répondu : {e}")
    return redirect(PAGE, ok={"interrompre": "Génération en cours interrompue", "vider": "File d'attente vidée",
                              "liberer": "Mémoire vidéo libérée"}[action])


@router.post(f"{PAGE}/test")
def test():
    if not comfyui.url():
        return redirect(PAGE, err="Branche d'abord ComfyUI (Détecter).")
    _, created = background("comfyui", cm.run_test, label="Image test ComfyUI", dedupe_key="comfy:test")
    return redirect(PAGE, ok="Image test lancée (30 s à 2 min au premier essai, le temps de charger le modèle)."
                    if created else "Un test est déjà en cours.")


@router.post(f"{PAGE}/gratuit")
def free(qa_off: str = Form("")):
    done = cm.free_mode(qa_off == "on")
    return redirect(PAGE, ok="Mode 100 % gratuit : " + " · ".join(done))


# ---------------- studio instantané ----------------
def _studio_test() -> dict:
    """Aperçu avant / après sur la photo fournisseur du dernier produit (ou un vêtement dessiné)."""
    import time
    ref = cm._test_reference()
    bg = db.get_setting("studio_bg")
    bg = bg if bg in studio.BACKGROUNDS else "gris"
    t0 = time.time()
    out = studio.render("plat", ref, config.MEDIA_DIR / "studio_test.jpg", bg)
    studio.render("detail", ref, config.MEDIA_DIR / "studio_test_detail.jpg", bg)
    res = {"seconds": round(time.time() - t0, 1), "cutout": out["cutout"], "at": db.now(), "ref": ref.name,
           "bg": bg}
    db.set_setting("_studio_test", __import__("json").dumps(res))
    return res


@router.post(f"{PAGE}/studio")
def studio_save(studio_bg: str = Form("auto")):
    db.set_setting("studio_bg", studio_bg if studio_bg in studio.BACKGROUNDS else "auto")
    return redirect(PAGE + "#studio", ok="Fond enregistré")


@router.post(f"{PAGE}/studio-test")
def studio_test():
    try:
        res = _studio_test()
    except Exception as e:
        return redirect(PAGE + "#studio", err=f"Aperçu impossible : {e}")
    return redirect(PAGE + "#studio", ok=f"Aperçu prêt en {res['seconds']} s (détourage {res['cutout']})")


@router.post(f"{PAGE}/rembg")
def rembg_install():
    background("studio", studio.install_rembg, label="Installation du détourage IA (rembg)", dedupe_key="studio:rembg")
    return redirect(PAGE + "#studio", ok="Installation lancée (quelques minutes, voir Activité).")


# ---------------- Hugging Face ----------------
@router.post(f"{PAGE}/hf")
def hf_save(token: str = Form(""), enabled: str = Form(""), steps: str = Form("24"), space: str = Form(""),
            clear_token: str = Form("")):
    try:
        hf_space.save(token_value=("" if clear_token == "on" else token) if (token or clear_token == "on") else None,
                      enabled_value=enabled == "on", steps_value=int(steps or 24), space_value=space or None)
    except (hf_space.HFError, ValueError) as e:
        return redirect(PAGE + "#hf", err=str(e))
    db.log_event("settings", "Réglages Hugging Face modifiés")
    return redirect(PAGE + "#hf", ok="Hugging Face " + ("activé" if enabled == "on" else "désactivé"))


@router.post(f"{PAGE}/hf-test")
def hf_test():
    r = hf_space.ping()
    return redirect(PAGE + "#hf", ok=f"Space {hf_space.space()} joignable") if r["ok"] else redirect(
        PAGE + "#hf", err=f"Space injoignable : {r['error']}")
