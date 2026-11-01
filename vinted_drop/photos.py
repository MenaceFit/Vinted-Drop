"""Système de photos : un modèle récurrent par boutique + photos portées / à plat.

Chaque produit reçoit :
  - "porte"   : le modèle de la boutique porte le vêtement (selfie miroir, style amateur)
  - "plat"    : photo à plat (flat lay) sur un lit / parquet, lumière naturelle
  - "detail"  : gros plan sur la matière / les finitions
"""
from pathlib import Path

from . import ai, config, db

# Règle commune : le produit doit rester fidèle à la réalité (couleur, coupe, motifs).
FIDELITY = (
    "The garment must be reproduced EXACTLY as in the product reference image: same color, "
    "fabric, pattern, cut, length, neckline, sleeves, buttons and details. Do not add, remove "
    "or embellish anything. No text, no watermark, no logo that is not on the product."
)

AMATEUR = (
    "Looks like a genuine smartphone photo taken by a regular person selling clothes on Vinted: "
    "natural window daylight, slightly imperfect framing, realistic skin and fabric texture, "
    "ordinary tidy French apartment, no studio lighting, no professional retouching."
)

PERSONA_PROMPT = (
    "Realistic smartphone photo of {description}. Neutral expression, standing in a bright "
    "bedroom, plain fitted black t-shirt and jeans, full body visible, natural daylight. "
    "This image is a reference sheet for the same person in future photos. " + AMATEUR
)

SHOT_PROMPTS = {
    "porte": (
        "Mirror selfie: the person from the first reference image is wearing the garment from the "
        "second reference image, standing in front of a full-length bedroom mirror, holding a phone "
        "that hides part of the face. Keep the same person (body type, hair, skin tone). "
        "Full outfit visible, garment clearly the focus. Add simple matching shoes/bottoms if needed. "
    ),
    "plat": (
        "Flat lay photo seen from above: the garment from the reference image laid neatly flat on "
        "a light bedsheet (or light wooden floor), fully visible, smoothed out, centered. "
    ),
    "detail": (
        "Close-up photo of the garment from the reference image showing the fabric texture, "
        "stitching and main detail (lace, buttons, print...), held in a hand near a window. "
    ),
}


def product_dir(product_id: int) -> Path:
    d = config.MEDIA_DIR / f"product_{product_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(path: Path) -> str:
    """Chemin relatif au dossier media (utilisé par le dashboard /media/...)."""
    return str(Path(path).resolve().relative_to(config.MEDIA_DIR.resolve()))


def abs_media(relpath: str) -> Path:
    return config.MEDIA_DIR / relpath


def create_persona(name: str, description: str) -> dict:
    """Crée le "skin" de la boutique : une personne récurrente sur toutes les photos portées.

    description : ex. "a 25 year old woman, pale skin, long straight black hair with bangs,
    slim, alternative gothic style, silver rings"
    """
    pid = db.add_persona(name, description)
    out = config.MEDIA_DIR / "personas" / f"persona_{pid}.png"
    ai.generate_image(PERSONA_PROMPT.format(description=description), out)
    db.update_persona(pid, reference_image=rel(out))
    return db.get_persona(pid)


def generate_product_photos(product_id: int, kinds=("porte", "plat", "detail")) -> list[str]:
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    if not p.get("supplier_image"):
        raise ValueError("Ce produit n'a pas encore d'image fournisseur (envoie la photo au bot).")
    product_img = abs_media(p["supplier_image"])

    persona_img = None
    if p.get("shop_id"):
        shop = db.get_shop(p["shop_id"])
        if shop and shop.get("persona_id"):
            persona = db.get_persona(shop["persona_id"])
            if persona and persona.get("reference_image"):
                persona_img = abs_media(persona["reference_image"])

    outdir = product_dir(product_id)
    photos = list(p.get("photos") or [])
    for kind in kinds:
        prompt = SHOT_PROMPTS[kind] + FIDELITY + " " + AMATEUR
        if kind == "porte":
            if persona_img is None:
                prompt = prompt.replace("the person from the first reference image",
                                        "a young woman").replace("second reference image",
                                                                 "reference image")
                refs = [product_img]
            else:
                refs = [persona_img, product_img]
        else:
            refs = [product_img]
        n = sum(1 for x in photos if Path(x).name.startswith(kind)) + 1
        out = ai.edit_image(prompt, refs, outdir / f"{kind}_{n}.png")
        photos.append(rel(out))
        db.update_product(product_id, photos=photos)
    return photos
