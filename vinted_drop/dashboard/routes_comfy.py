"""Page « Photos gratuites » : piloter ComfyUI (ton PC) depuis le dashboard."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import comfy_manager as cm
from .. import comfyui, config, db, tasks
from .common import background, redirect, render

router = APIRouter()
PAGE = "/photos-gratuites"


@router.get(PAGE)
def page(request: Request):
    state = cm.check()
    return render(request, "comfyui.html", s=state, test=cm.last_test(), settings=db.all_settings(),
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
