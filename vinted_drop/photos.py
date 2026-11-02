"""Studio photo : un modèle récurrent par boutique, des photos fidèles au produit, contrôlées par l'IA.

Étapes pour un produit :
  1. Nettoyage (option) : on isole le vêtement de l'image fournisseur (texte, collage, mannequin retirés).
  2. Prises de vue en parallèle : portée (miroir), pose, à plat, sur cintre, détail.
  3. Contrôle qualité : une IA compare chaque photo au produit d'origine (couleur, coupe, détails,
     défauts comme mains déformées ou texte parasite). Note < PHOTO_QA_MIN → nouvel essai, avec
     l'autre fournisseur si disponible. On garde la meilleure.
  4. Finition : format 3:4 (1200x1600) en JPEG, comme une photo de téléphone.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai, config, db
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
def create_persona(name: str, description: str, decor: str | None = None, provider=None) -> dict:
    """Crée la personne récurrente d'une boutique : photo en pied + portrait (2 références)."""
    decor = decor or DEFAULT_DECOR
    pid = db.add_persona(name, description)
    db.update_persona(pid, decor=decor)
    d = config.MEDIA_DIR / "personas"
    full = generate(PERSONA_FULL.format(description=description, decor=decor), [], d / f"persona_{pid}_full.png",
                    provider=_provider(provider), kind="persona")
    full_path = finish(full["path"])
    face = generate(PERSONA_FACE, [full_path], d / f"persona_{pid}_face.png", provider=full["provider"], kind="persona")
    face_path = finish(face["path"], size=(1200, 1200))
    db.update_persona(pid, reference_image=rel(full_path), refs=[rel(full_path), rel(face_path)])
    return db.get_persona(pid)


def persona_for(p: dict):
    if not p.get("shop_id"):
        return None
    shop = db.get_shop(p["shop_id"])
    if shop and shop.get("persona_id"):
        return db.get_persona(shop["persona_id"])
    return None


# ---------------- contrôle qualité ----------------
def qa_check(original: Path, generated: Path, persona_ref: Path | None = None) -> dict:
    images = [original, generated] + ([persona_ref] if persona_ref else [])
    note = " ; image 3 = la personne de référence (doit être la même personne)" if persona_ref else ""
    try:
        r = ai.chat_json(QA_SYSTEM.format(persona_note=note), "Évalue la photo générée.", images=images)
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


def _shot(p, kind, provider, product_ref, persona, n, original):
    spec = SHOTS[kind]
    decor = (persona or {}).get("decor") or DEFAULT_DECOR
    persona_refs = [abs_media(x) for x in ((persona or {}).get("refs") or ([persona["reference_image"]] if persona and persona.get("reference_image") else []))]
    persona_refs = [x for x in persona_refs if x.exists()]
    prompt = spec["prompt"].format(decor=decor)
    if spec["persona"] and persona_refs:
        refs = persona_refs[:2] + [product_ref]
        prompt = (f"Reference images: images 1-{len(persona_refs[:2])} = PERSON, last image = PRODUCT. " + prompt)
    else:
        refs = [product_ref]
        prompt = "Reference image = PRODUCT. " + prompt.replace("the person from the PERSON reference image",
                                                                  "a young woman")
    prompt += FIDELITY + " " + AMATEUR
    out_dir = product_dir(p["id"])
    from .image_providers import available
    attempts, best = [], None
    tries = 2 if db.get_setting("photo_qa") == "on" else 1
    prov, last = _provider(provider), None
    for i in range(tries):
        if i and last:  # 2e essai : un autre fournisseur s'il y en a un
            others = [x for x in available() if x != last]
            prov = others[0] if others else last
        try:
            res = generate(prompt, refs, out_dir / f"{kind}_{n}{'_r' if i else ''}.png", provider=prov,
                           kind=kind, product_id=p["id"])
        except ImageError as e:
            attempts.append({"error": str(e)})
            continue
        path = finish(res["path"])
        qa = qa_check(original, path, persona_refs[0] if spec["persona"] and persona_refs else None) \
            if db.get_setting("photo_qa") == "on" else {"fidelity": None, "issues": [], "verdict": "ok"}
        last = res["provider"]
        cand = {"path": path, "provider": res["provider"], "model": res["model"], "qa": qa, "kind": kind}
        attempts.append(cand)
        if best is None or (qa.get("fidelity") or 0) > (best["qa"].get("fidelity") or 0):
            if best:
                Path(best["path"]).unlink(missing_ok=True)
            best = cand
        else:
            path.unlink(missing_ok=True)
        if qa.get("fidelity") is None or qa["fidelity"] >= config.PHOTO_QA_MIN:
            break
    if not best:
        raise ImageError("; ".join(a.get("error", "") for a in attempts))
    best["tries"] = len(attempts)
    return best


def generate_product_photos(product_id: int, kinds=None, provider=None) -> list[str]:
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    if not p.get("supplier_image"):
        raise ValueError("Ce produit n'a pas encore d'image fournisseur (envoie la photo au bot ou sur le dashboard).")
    kinds = list(kinds or default_kinds(p))
    original = abs_media(p["supplier_image"])
    persona = persona_for(p)
    product_ref = clean_reference(p, provider)
    existing = list(p.get("photos") or [])
    errors = []

    def run(kind):
        n = sum(1 for x in existing if Path(x).name.startswith(kind + "_")) + 1 + int(time.time()) % 1000
        try:
            res = _shot(p, kind, provider, product_ref, persona, n, original)
        except Exception as e:
            errors.append(f"{SHOTS[kind]['label']} : {e}")
            return
        with _lock:  # écriture en base une photo à la fois
            cur = db.get_product(product_id)
            photos, meta = list(cur.get("photos") or []), dict(cur.get("photo_meta") or {})
            r = rel(res["path"])
            photos.append(r)
            meta[r] = {"kind": kind, "provider": res["provider"], "model": res["model"],
                       "fidelity": res["qa"].get("fidelity"), "realism": res["qa"].get("realism"),
                       "issues": res["qa"].get("issues") or [], "tries": res["tries"]}
            photos.sort(key=lambda x: list(SHOTS).index(meta.get(x, {}).get("kind", "detail"))
                        if meta.get(x, {}).get("kind") in SHOTS else 99)
            db.update_product(product_id, photos=photos, photo_meta=meta)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run, kinds))
    if errors and len(errors) == len(kinds):
        raise ImageError(" | ".join(errors))
    if errors:
        db.log_event("error", "Photos partielles : " + " | ".join(errors), product_id=product_id)
    return db.get_product(product_id)["photos"]


# ---------------- retouches ----------------
def regenerate_photo(product_id: int, relpath: str, provider=None) -> str:
    p = db.get_product(product_id)
    kind = (p.get("photo_meta") or {}).get(relpath, {}).get("kind") or Path(relpath).stem.split("_")[0]
    if kind not in SHOTS:
        kind = "plat"
    res = _shot(p, kind, provider, clean_reference(p, provider), persona_for(p), int(time.time()) % 100000,
                abs_media(p["supplier_image"]))
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
        meta[new] = {"kind": kind, "provider": res["provider"], "model": res["model"],
                     "fidelity": res["qa"].get("fidelity"), "realism": res["qa"].get("realism"),
                     "issues": res["qa"].get("issues") or [], "tries": res["tries"]}
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
    """Photos + métadonnées, dans l'ordre (la 1re = couverture)."""
    meta = p.get("photo_meta") or {}
    out = []
    for i, ph in enumerate(p.get("photos") or []):
        m = meta.get(ph, {})
        spec = SHOTS.get(m.get("kind"), {})
        out.append({"path": ph, "index": i, "cover": i == 0, "label": spec.get("label", "Photo"),
                    "emoji": spec.get("emoji", "📷"), **m})
    return out
