"""Studio photo : un modèle récurrent par boutique, des photos fidèles au produit, contrôlées par l'IA.

Étapes pour un produit :
  1. Nettoyage (option) : on isole le vêtement de l'image fournisseur (texte, collage, mannequin retirés).
  2. Prises de vue en parallèle : portée (miroir), pose, à plat, sur cintre, détail.
  3. Contrôle qualité : une IA compare chaque photo au produit d'origine (couleur, coupe, détails,
     défauts comme mains déformées ou texte parasite). Note < PHOTO_QA_MIN → nouvel essai, avec
     l'autre fournisseur si disponible. On garde la meilleure.
  4. Finition : format 3:4 (1200x1600) en JPEG, comme une photo de téléphone.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai, config, db, studio, tryon
from .image_providers import ImageError, generate

FIDELITY = (
    "The garment must be reproduced EXACTLY as in the product reference image: same color and shade, "
    "fabric and texture, pattern and print placement, cut, length, neckline, sleeves, buttons, lace and "
    "every detail. Do not add, remove or embellish anything. No text, no watermark, no logo or brand "
    "label that is not on the product."
)
AMATEUR = (
    "It must look like a genuine smartphone photo taken by a regular person selling clothes on Vinted: "
    "natural window daylight, slightly imperfect framing, realistic skin and fabric texture with natural "
    "wrinkles, ordinary tidy home, no studio lighting, no professional retouching, no bokeh-heavy look."
)
DEFAULT_DECOR = ("a bright, tidy French apartment bedroom: white walls, light oak parquet floor, beige linen "
                 "bedding, a tall standing mirror with a thin black frame, a green plant near the window")

SHOTS = {
    "porte": {"label": "Portée (miroir)", "emoji": "🪞", "persona": True, "prompt":
              "Mirror selfie: the person from the PERSON reference image wears the garment from the PRODUCT "
              "reference image, standing in front of the full-length mirror in {decor}. She holds a phone that "
              "hides part of her face. Same person: identical face shape, hair, skin tone and body type. Full "
              "outfit visible, the garment is clearly the focus; add simple neutral shoes and bottoms only if needed. "},
    "porte_pose": {"label": "Portée (pose)", "emoji": "🧍", "persona": True, "prompt":
                   "Casual photo taken by a friend: the person from the PERSON reference image wears the garment "
                   "from the PRODUCT reference image, standing relaxed near the window in {decor}, three-quarter "
                   "view, natural pose, looking slightly away. Same person: identical face, hair, skin tone and "
                   "body type. The garment is fully visible. "},
    "plat": {"label": "À plat", "emoji": "🛏️", "persona": False, "prompt":
             "Flat lay photo seen from directly above: the garment from the PRODUCT reference image laid neatly "
             "flat on a light bedsheet, fully visible, smoothed out, centered, with a little space around it. "},
    "cintre": {"label": "Sur cintre", "emoji": "🧥", "persona": False, "prompt":
               "Photo of the garment from the PRODUCT reference image hanging on a simple wooden hanger on a white "
               "door or wardrobe, front view, whole garment visible, soft daylight. "},
    "detail": {"label": "Détail", "emoji": "🔍", "persona": False, "prompt":
               "Close-up photo of the garment from the PRODUCT reference image showing the fabric texture, "
               "stitching and its most distinctive detail (lace, buttons, print, collar...), held in a hand near a "
               "window. "},
}
SETS = {"standard": ["porte", "plat", "detail"], "complet": ["porte", "porte_pose", "plat", "cintre", "detail"]}

PERSONA_FULL = (
    "Realistic smartphone photo of {description}. Full body, standing straight facing the camera, neutral "
    "relaxed expression, plain fitted black t-shirt and blue jeans, in {decor}. This is a reference photo of "
    "the same person for future photos. " + AMATEUR
)
PERSONA_FACE = (
    "Close-up portrait (head and shoulders) of the exact same person as in the reference image: identical face, "
    "hair, skin tone and features, same black t-shirt, soft window light, looking at the camera. " + AMATEUR
)
CLEAN_PROMPT = (
    "Isolate the garment from the PRODUCT reference image and show it alone, front view, laid flat or as a "
    "ghost mannequin, on a plain light grey background. Remove any person, mannequin, text, price, logo, "
    "watermark, collage or other products. " + FIDELITY
)

QA_SYSTEM = """Tu es contrôleur qualité de photos d'annonces Vinted. Image 1 = produit d'origine du
fournisseur. Image 2 = photo générée par IA{persona_note}. Vérifie :
- fidélité du vêtement : couleur exacte, matière, motif, coupe, longueur, col, manches, détails ;
- défauts : mains/doigts déformés, membres en trop, visage incohérent, texte ou logo parasite,
  vêtement fondu dans le décor, image floue ;
- réalisme d'une photo prise au téléphone.
Réponds en JSON : {{"fidelity": entier 0-10 (10 = identique au produit), "realism": entier 0-10,
"issues": [liste courte en français], "verdict": "ok" ou "retry"}}"""

_lock = threading.Lock()


# ---------------- utilitaires ----------------
def product_dir(product_id: int) -> Path:
    d = config.MEDIA_DIR / f"product_{product_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(path: Path) -> str:
    return str(Path(path).resolve().relative_to(config.MEDIA_DIR.resolve()))


def abs_media(relpath: str) -> Path:
    return config.MEDIA_DIR / relpath


def finish(path: Path, size=(1200, 1600)) -> Path:
    """Recadre en 3:4 et enregistre en JPEG, comme une photo de téléphone."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return path
    path = Path(path)
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
            out = path.with_suffix(".jpg")
            im.save(out, "JPEG", quality=90, optimize=True)
    except OSError:
        return path
    if out != path:
        path.unlink(missing_ok=True)
    return out


def default_kinds(p: dict):
    """Enfants / animaux : jamais de photo portée par un modèle IA."""
    from . import niches
    n = niches.get(p.get("niche")) if p.get("niche") else None
    if n and n.get("photo_kinds"):
        return tuple(n["photo_kinds"])
    return tuple(SETS.get(db.get_setting("photo_set") or "standard", SETS["standard"]))


def _provider(provider):
    return provider or db.get_setting("photo_provider") or "auto"


# ---------------- modèles (personas) ----------------
POSE_PROMPT = (
    "The exact same person as in picture 1: identical face, hair, skin tone, body shape and the same plain "
    "clothes, in the same room with the same light. She now stands in a {pose}, full body visible from head to "
    "shoes, arms away from the torso so the outfit is clearly visible. Realistic smartphone photo. "
)
POSE_CHOICES = {"trois-quarts": "relaxed three-quarter view with one hand on her hip",
                "profil": "natural side view, looking towards the window",
                "marche": "natural walking pose towards the camera"}


def _free_text_to_image(prompt: str, out: Path) -> Path:
    """Photo de base gratuite du modèle : ton PC si ComfyUI est branché, sinon FLUX.1 [schnell] sur Hugging Face."""
    from . import hf_space
    from .image_providers import available
    if "local" in available():
        return generate(prompt, [], out, provider="local", kind="persona")["path"]
    try:
        raw = hf_space.text_to_image(prompt, "3:4")
    except hf_space.HFError as e:
        raise ImageError(str(e)) from e
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    db.log_cost("hf", "FLUX.1-schnell", "persona", 0.0, None)
    return out


def create_persona(name: str, description: str, decor: str | None = None, provider=None) -> dict:
    """Crée la personne récurrente d'une boutique : photo de base en pied (+ portrait si possible).
    En mode gratuit : ton PC ou Hugging Face (FLUX.1 [schnell], Apache 2.0), 0 €."""
    decor = decor or DEFAULT_DECOR
    pid = db.add_persona(name, description)
    db.update_persona(pid, decor=decor)
    d = config.MEDIA_DIR / "personas"
    prompt = PERSONA_FULL.format(description=description, decor=decor)
    prov = _provider(provider)
    if prov in ("gratuit", "studio"):
        full_path = finish(_free_text_to_image(prompt, d / f"persona_{pid}_full.png"))
        refs = [rel(full_path)]
        try:   # portrait serré : utile pour les photos sans cabine, facultatif
            face = generate(PERSONA_FACE, [full_path], d / f"persona_{pid}_face.png", provider="gratuit", kind="persona")
            refs.append(rel(finish(face["path"], size=(1200, 1200))))
        except ImageError:
            pass
    else:
        full = generate(prompt, [], d / f"persona_{pid}_full.png", provider=prov, kind="persona")
        full_path = finish(full["path"])
        face = generate(PERSONA_FACE, [full_path], d / f"persona_{pid}_face.png", provider=full["provider"],
                        kind="persona")
        refs = [rel(full_path), rel(finish(face["path"], size=(1200, 1200)))]
    db.update_persona(pid, reference_image=refs[0], refs=refs)
    return db.get_persona(pid)


def import_persona(name: str, data: bytes, decor: str | None = None) -> dict:
    """Modèle à partir d'une vraie photo (toi ou une personne majeure qui a donné son accord)."""
    from io import BytesIO
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
    except Exception as e:
        raise ValueError("Image illisible (JPEG, PNG ou WebP attendu)") from e
    if min(img.size) < 400:
        raise ValueError("Photo trop petite : au moins 400 px de large, en pied")
    img.thumbnail((1600, 2000))
    pid = db.add_persona(name, "photo importée")
    d = config.MEDIA_DIR / "personas"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"persona_{pid}_full.jpg"
    img.save(path, "JPEG", quality=92)
    db.update_persona(pid, decor=decor or "the room of the reference photo", reference_image=rel(path),
                      refs=[rel(path)])
    db.log_event("photo", f"Modèle importé depuis une photo : {name} (accord de la personne confirmé)")
    return db.get_persona(pid)


def add_persona_pose(persona_id: int, pose: str = "trois-quarts", provider=None) -> dict:
    """Ajoute une 2e (ou 3e) photo de base du même modèle dans une autre pose, pour varier les photos portées."""
    persona = db.get_persona(persona_id)
    base = tryon.bases(persona)
    if not base:
        raise ValueError("Ce modèle n'a pas encore de photo de base")
    refs = list(persona.get("refs") or [])
    out = config.MEDIA_DIR / "personas" / f"persona_{persona_id}_pose{len(refs) + 1}.png"
    prov = _provider(provider)
    res = generate(POSE_PROMPT.format(pose=POSE_CHOICES.get(pose, POSE_CHOICES["trois-quarts"])), [base[0]], out,
                   provider="gratuit" if prov in ("gratuit", "studio") else prov, kind="persona")
    new = rel(finish(res["path"]))
    faces = [r for r in refs if "_face" in Path(r).stem]
    bodies = [r for r in refs if "_face" not in Path(r).stem] + [new]
    db.update_persona(persona_id, refs=bodies + faces)
    return db.get_persona(persona_id)


def persona_for(p: dict):
    if not p.get("shop_id"):
        return None
    shop = db.get_shop(p["shop_id"])
    if shop and shop.get("persona_id"):
        return db.get_persona(shop["persona_id"])
    return None


# ---------------- contrôle qualité ----------------
def qa_check(original: Path, generated: Path, persona_ref: Path | None = None, garment: dict | None = None) -> dict:
    images = [original, generated] + ([persona_ref] if persona_ref else [])
    note = " ; image 3 = la personne de référence (doit être la même personne)" if persona_ref else ""
    facts = ""
    if garment:
        facts = ("Fiche du vêtement d'origine (tout ajout = défaut grave) : " + json.dumps(garment, ensure_ascii=False)
                 + "\nVérifie en particulier : aucun logo, étiquette, couture, fermeture, matière ou couleur inventés.")
    try:
        r = ai.chat_json(QA_SYSTEM.format(persona_note=note), "Évalue la photo générée.\n" + facts, images=images)
    except Exception as e:  # pas de contrôle possible : on n'invente pas une note
        return {"fidelity": None, "realism": None, "issues": [f"contrôle indisponible : {str(e)[:80]}"], "verdict": "ok"}
    r["fidelity"] = int(r.get("fidelity") or 0)
    r["realism"] = int(r.get("realism") or 0)
    return r


# ---------------- produit ----------------
def clean_reference(p: dict, provider=None) -> Path:
    """Image du vêtement seul (sans texte ni mannequin), créée une fois puis réutilisée."""
    from .image_providers import available
    original = abs_media(p["supplier_image"])
    mode = db.get_setting("photo_clean")
    if mode == "off" or (mode == "auto" and "local" not in available()):
        return original
    d = product_dir(p["id"])
    existing = next(iter(sorted(d.glob("clean.*"))), None)
    if existing:
        return existing
    try:
        res = generate(CLEAN_PROMPT, [original], d / "clean.png", provider=_provider(provider), aspect="3:4",
                       kind="clean", product_id=p["id"])
    except ImageError:
        return original
    clean = finish(res["path"])
    if db.get_setting("photo_qa") == "on":
        qa = qa_check(original, clean)
        if qa["fidelity"] is not None and qa["fidelity"] < config.PHOTO_QA_MIN:
            clean.unlink(missing_ok=True)  # le nettoyage a trahi le produit : on garde l'original
            return original
    return clean


SCENES = {
    "auto": None,  # décor du modèle de la boutique
    "chambre": "a bright tidy bedroom with white walls, light oak floor and a tall standing mirror",
    "salon": "a cosy living room with a beige sofa, plants and soft window light",
    "rue": "a calm Parisian street with stone buildings, natural daylight",
    "parc": "a park path with trees and soft natural daylight",
    "mur": "a plain white wall in natural daylight",
    "cafe": "the terrace of a small café, natural daylight",
}
SCENE_LABELS = {"auto": "Décor de la boutique", "chambre": "Chambre", "salon": "Salon", "rue": "Rue", "parc": "Parc",
                "mur": "Mur blanc", "cafe": "Terrasse de café"}
POSES = {
    "auto": "",
    "face": "standing straight facing the camera, ",
    "trois-quarts": "three-quarter view, relaxed natural pose, ",
    "marche": "walking naturally, candid photo, ",
    "assise": "sitting casually on a chair, the garment still fully visible, ",
}
POSE_LABELS = {"auto": "Automatique", "face": "De face", "trois-quarts": "Trois-quarts", "marche": "En marchant",
               "assise": "Assise"}

ANALYZE_SYSTEM = """Tu analyses la photo fournisseur d'un vêtement pour qu'une IA d'image le reproduise
fidèlement. Décris UNIQUEMENT ce qui est visible. Réponds en JSON :
{"type": str, "color": str, "pattern": str|null, "material_guess": str|null, "neckline": str|null,
"sleeves": str|null, "length": str|null, "closures": [str] (vide si aucune fermeture visible),
"details": [str], "visible_logos_or_labels": [str] (vide si aucun), "source_defects": [str]
(texte, prix, filigrane, collage, mannequin, autre produit...)}"""


def analyze_garment(p: dict) -> dict:
    """Identifie le vêtement (forme, couleur, détails) une fois, et le garde sur le produit."""
    if p.get("garment"):
        return p["garment"]
    try:
        g = ai.chat_json(ANALYZE_SYSTEM, "Analyse ce vêtement.", images=[abs_media(p["supplier_image"])])
    except Exception:
        return {}
    db.update_product(p["id"], garment=g)
    return g


def garment_rules(g: dict) -> str:
    """Contraintes explicites pour l'IA d'image : ce qui existe ET ce qui n'existe pas."""
    if not g:
        return ""
    parts = [f"Garment facts: {g.get('type') or 'garment'}, color {g.get('color') or 'as in reference'}"]
    for key, label in (("pattern", "pattern"), ("neckline", "neckline"), ("sleeves", "sleeves"), ("length", "length")):
        if g.get(key):
            parts.append(f"{label}: {g[key]}")
    if g.get("details"):
        parts.append("details: " + ", ".join(g["details"][:8]))
    parts.append("closures: " + (", ".join(g["closures"]) if g.get("closures") else "NONE (do not add any zipper or buttons)"))
    parts.append("logos/labels: " + (", ".join(g["visible_logos_or_labels"]) if g.get("visible_logos_or_labels")
                                    else "NONE (do not add any logo, tag or label)"))
    return ". ".join(parts) + ". "


def _record(p, kind, scene, res, duration, attempt, qa, status, path=None, error=None):
    db.log_generation(product_id=p["id"], kind=kind, scene=scene, provider=(res or {}).get("provider"),
                      model=(res or {}).get("model"), duration_s=round(duration, 1), cost=(res or {}).get("cost", 0),
                      attempt=attempt, fidelity=(qa or {}).get("fidelity"), realism=(qa or {}).get("realism"),
                      issues=(qa or {}).get("issues") or [], status=status, path=path, error=error)


class PhotoRejected(ImageError):
    """Aucune tentative n'a atteint la fidélité minimale : la photo n'est pas gardée."""


def _free_ai() -> list[str]:
    from .image_providers import FREE, available
    return [k for k in available() if k in FREE]


def studio_mode(provider=None) -> bool:
    """Studio instantané utilisé pour tout : choisi explicitement, ou mode gratuit sans IA gratuite branchée."""
    prov = _provider(provider)
    return prov == "studio" or (prov == "gratuit" and not _free_ai())


def _studio_shot(p, kind, n):
    """Photo « studio instantané » : la vraie photo fournisseur détourée sur fond propre (0 €, sans IA)."""
    from . import niches
    t0 = time.time()
    niche = niches.get(p["niche"]) if p.get("niche") else None
    bg = studio.background_for(niche, db.get_setting("studio_bg"))
    out = studio.render(kind, abs_media(p["supplier_image"]), product_dir(p["id"]) / f"{kind}_{n}.jpg", bg)
    res = {"provider": "studio", "model": f"studio · détourage {out['cutout']} · fond {bg}", "cost": 0.0}
    qa = {"fidelity": None, "realism": None, "issues": [], "verdict": "photo réelle"}
    _record(p, kind, "studio", res, time.time() - t0, 1, qa, "accepted", path=rel(out["path"]))
    return {"path": out["path"], "provider": "studio", "model": res["model"], "qa": qa, "kind": kind,
            "duration": round(time.time() - t0, 1), "cost": 0.0, "ok": True, "tries": 1}


def _shot(p, kind, provider, product_ref, persona, n, original, scene="auto", pose="auto"):
    if studio_mode(provider):
        return _studio_shot(p, kind, n)
    try:
        return _ai_shot(p, kind, provider, product_ref, persona, n, original, scene, pose)
    except ImageError:
        # mode gratuit : si l'IA gratuite échoue (PC éteint, quota épuisé), la vraie photo détourée prend le relais
        if _provider(provider) == "gratuit" and kind in studio.KINDS:
            return _studio_shot(p, kind, n)
        raise


def _ai_shot(p, kind, provider, product_ref, persona, n, original, scene="auto", pose="auto"):
    spec = SHOTS[kind]
    decor = SCENES.get(scene) or (persona or {}).get("decor") or DEFAULT_DECOR
    persona_refs = [abs_media(x) for x in ((persona or {}).get("refs") or ([persona["reference_image"]] if persona and persona.get("reference_image") else []))]
    persona_refs = [x for x in persona_refs if x.exists()]
    prompt = spec["prompt"].format(decor=decor)
    if spec["persona"]:
        prompt = prompt.replace("standing relaxed near the window", POSES.get(pose, "") + "near the window") \
            if pose not in ("auto", "") else prompt
    category = tryon.category_for(p) if spec["persona"] else None
    # cabine seulement avec le décor de la boutique : un décor choisi (rue, café…) demande une nouvelle scène
    use_cabin = spec["persona"] and category and tryon.enabled() and scene in ("auto", "", None)
    base_photos = tryon.bases(persona) if use_cabin else []
    if base_photos:
        # cabine d'essayage : on habille la vraie photo de base du modèle (même visage, même pièce)
        base = base_photos[(1 if kind == "porte_pose" else 0) % len(base_photos)]
        refs = [base, tryon.garment_ref(p, product_ref)]
        prompt = tryon.prompt_for(category)
        persona_refs = [base]
    elif spec["persona"] and persona_refs:
        refs = persona_refs[:2] + [product_ref]
        prompt = (f"Reference images: images 1-{len(persona_refs[:2])} = PERSON, last image = PRODUCT. " + prompt)
    else:
        refs = [product_ref]
        prompt = "Reference image = PRODUCT. " + prompt.replace("the person from the PERSON reference image",
                                                                  "a young woman")
    prompt += garment_rules(p.get("garment") or {}) + FIDELITY + ("" if base_photos else " " + AMATEUR)
    out_dir = product_dir(p["id"])
    from .image_providers import available
    attempts, best = [], None
    qa_on = db.get_setting("photo_qa") == "on"
    tries = 2 if qa_on else 1
    prov, last = _provider(provider), None
    for i in range(tries):
        if i and last:  # 2e essai : un autre fournisseur s'il y en a un
            others = [x for x in available() if x != last]
            prov = others[0] if others else last
        t0 = time.time()
        try:
            res = generate(prompt, refs, out_dir / f"{kind}_{n}{'_r' if i else ''}.png", provider=prov,
                           kind=kind, product_id=p["id"])
        except ImageError as e:
            attempts.append({"error": str(e)})
            _record(p, kind, scene, None, time.time() - t0, i + 1, None, "error", error=str(e)[:300])
            continue
        path = finish(res["path"])
        qa = qa_check(original, path, persona_refs[0] if spec["persona"] and persona_refs else None, p.get("garment")) \
            if qa_on else {"fidelity": None, "issues": [], "verdict": "ok"}
        last = res["provider"]
        ok = qa.get("fidelity") is None or qa["fidelity"] >= config.PHOTO_QA_MIN
        cand = {"path": path, "provider": res["provider"], "model": res["model"], "qa": qa, "kind": kind,
                "duration": round(time.time() - t0, 1), "cost": res.get("cost", 0), "ok": ok}
        _record(p, kind, scene, res, time.time() - t0, i + 1, qa, "accepted" if ok else "rejected",
                path=rel(path) if ok else None)
        attempts.append(cand)
        if best is None or (qa.get("fidelity") or 0) > (best["qa"].get("fidelity") or 0):
            if best:
                Path(best["path"]).unlink(missing_ok=True)
            best = cand
        else:
            path.unlink(missing_ok=True)
        if ok:
            break
    if not best:
        raise ImageError("; ".join(a.get("error", "") for a in attempts))
    if not best["ok"]:
        # fidélité insuffisante après tous les essais : on refuse la photo
        Path(best["path"]).unlink(missing_ok=True)
        raise PhotoRejected(f"photo refusée (fidélité {best['qa'].get('fidelity')}/10 < {config.PHOTO_QA_MIN}) : "
                            + ", ".join(best["qa"].get("issues") or []) + " → relance un essai ou change de fournisseur")
    best["tries"] = len(attempts)
    return best


def _meta(kind, res, scene):
    return {"kind": kind, "provider": res["provider"], "model": res["model"], "scene": scene,
            "fidelity": res["qa"].get("fidelity"), "realism": res["qa"].get("realism"),
            "issues": res["qa"].get("issues") or [], "tries": res["tries"], "duration": res.get("duration"),
            "cost": round(sum([res.get("cost") or 0]), 3)}


def generate_product_photos(product_id: int, kinds=None, provider=None, scene="auto", pose="auto") -> list[str]:
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    if not p.get("supplier_image"):
        raise ValueError("Ce produit n'a pas encore d'image fournisseur (envoie la photo au bot ou sur le dashboard).")
    kinds = list(kinds or default_kinds(p))
    original = abs_media(p["supplier_image"])
    if studio_mode(provider):
        # pas de photo portée sans IA : on garde les prises possibles (à plat, cintre, détail)
        keep = [k for k in kinds if k in studio.KINDS]
        if len(keep) < len(kinds) and "cintre" not in keep:     # la portée devient une photo sur fond n° 2
            keep.append("cintre")
        kinds = list(dict.fromkeys(keep or ["plat", "cintre", "detail"]))
        persona, product_ref = None, original                   # aucune IA appelée : 0 € et rapide
    else:
        persona = persona_for(p)
        p = {**p, "garment": analyze_garment(p)}
        product_ref = clean_reference(p, provider)
    existing = list(p.get("photos") or [])
    errors = []

    def run(kind):
        n = sum(1 for x in existing if Path(x).name.startswith(kind + "_")) + 1 + int(time.time() * 1000) % 100000
        try:
            res = _shot(p, kind, provider, product_ref, persona, n, original, scene, pose)
        except Exception as e:
            errors.append(f"{SHOTS[kind]['label']} : {e}")
            return
        with _lock:  # écriture en base une photo à la fois
            cur = db.get_product(product_id)
            photos, meta = list(cur.get("photos") or []), dict(cur.get("photo_meta") or {})
            r = rel(res["path"])
            photos.append(r)
            meta[r] = _meta(kind, res, scene)
            photos.sort(key=lambda x: list(SHOTS).index(meta.get(x, {}).get("kind", "detail"))
                        if meta.get(x, {}).get("kind") in SHOTS else 99)
            db.update_product(product_id, photos=photos, photo_meta=meta)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run, kinds))
    if errors and len(errors) == len(kinds):
        raise ImageError(" | ".join(errors))
    if errors:
        db.log_event("photo", "Photos partielles : " + " | ".join(errors), product_id=product_id, level="warning")
    db.log_event("photo", f"{len(kinds) - len(errors)} photo(s) générée(s)", product_id=product_id,
                 shop_id=p.get("shop_id"))
    return db.get_product(product_id)["photos"]


# ---------------- retouches ----------------
def regenerate_photo(product_id: int, relpath: str, provider=None) -> str:
    p = db.get_product(product_id)
    kind = (p.get("photo_meta") or {}).get(relpath, {}).get("kind") or Path(relpath).stem.split("_")[0]
    if kind not in SHOTS:
        kind = "plat"
    scene = (p.get("photo_meta") or {}).get(relpath, {}).get("scene") or "auto"
    n = int(time.time() * 1000) % 100000
    if studio_mode(provider):
        res = _shot(p, kind if kind in studio.KINDS else "plat", provider, None, None, n, None, scene)
    else:
        p = {**p, "garment": analyze_garment(p)}
        res = _shot(p, kind, provider, clean_reference(p, provider), persona_for(p), n,
                    abs_media(p["supplier_image"]), scene)
    with _lock:
        cur = db.get_product(product_id)
        photos, meta = list(cur["photos"]), dict(cur.get("photo_meta") or {})
        new = rel(res["path"])
        idx = photos.index(relpath) if relpath in photos else len(photos)
        if relpath in photos:
            photos[idx] = new
            abs_media(relpath).unlink(missing_ok=True)
            meta.pop(relpath, None)
        else:
            photos.append(new)
        meta[new] = _meta(kind, res, scene)
        db.update_product(product_id, photos=photos, photo_meta=meta)
    return new


def delete_photo(product_id: int, relpath: str):
    p = db.get_product(product_id)
    photos = [x for x in p["photos"] if x != relpath]
    meta = {k: v for k, v in (p.get("photo_meta") or {}).items() if k != relpath}
    abs_media(relpath).unlink(missing_ok=True)
    db.update_product(product_id, photos=photos, photo_meta=meta)


def set_cover(product_id: int, relpath: str):
    p = db.get_product(product_id)
    if relpath in p["photos"]:
        db.update_product(product_id, photos=[relpath] + [x for x in p["photos"] if x != relpath])


def summary(p: dict) -> list[dict]:
    """Photos + métadonnées, dans l'ordre (la 1re = couverture). Les photos sans métadonnées
    (ajoutées à la main, importées) ont des valeurs par défaut."""
    meta = p.get("photo_meta") or {}
    out = []
    for i, ph in enumerate(p.get("photos") or []):
        m = meta.get(ph, {})
        spec = SHOTS.get(m.get("kind"), {})
        out.append({"kind": None, "provider": None, "model": None, "scene": None, "fidelity": None, "realism": None,
                    "issues": [], "tries": None, "duration": None, "cost": None, **m,
                    "path": ph, "index": i, "cover": i == 0, "label": spec.get("label", "Photo"),
                    "emoji": spec.get("emoji", "📷")})
    return out
