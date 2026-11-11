"""Cabine d'essayage : ton modèle porte le vêtement du fournisseur.

Au lieu d'inventer une scène entière, l'IA part d'une vraie photo de base du modèle de la boutique
(en pied, toujours le même visage, la même pièce) et remplace uniquement ses vêtements par le produit.
Résultat : même personne d'une annonce à l'autre, vêtement plus fidèle, moins de calcul.

Fonctionne avec toutes les IA d'édition du bot : ton PC (ComfyUI), Hugging Face (gratuit) ou payant.
Modèle utilisé : Qwen-Image-Edit (Apache 2.0). Le vêtement est d'abord détouré par le studio instantané
pour que l'IA ne recopie pas le mannequin ni le décor du fournisseur.

Non pris en charge : chaussures, sacs, bijoux (la photo portée est alors créée sans cabine).
"""
import re
from pathlib import Path

from . import db, studio

CATEGORY_WORDS = {
    None: r"botte|bottine|santiag|chaussure|basket|sneaker|escarpin|mary.?jane|sandale|sac\b|bijou|collier|"
          r"boucle d.oreille|bague|ceinture|cagoule|chapeau|bonnet|lunette|boots|shoes|heels|bag\b",
    "full": r"robe|dress|combinaison|jumpsuit|abaya|ensemble|tailleur|costume|pyjama|déguisement|cosplay|"
            r"salopette|tenue",
    "bottom": r"jupe|skirt|jean|pantalon|trouser|short|legging|jogging",
}
ZONES = {"top": "upper-body clothing (top, jacket or coat)", "bottom": "lower-body clothing (trousers, skirt or "
         "shorts)", "full": "whole outfit"}
PROMPT = (
    "Picture 1 shows a person, picture 2 shows a garment. Dress the person from picture 1 in the garment from "
    "picture 2: replace only her {zone} with this exact garment, worn naturally with a realistic fit and soft "
    "natural folds. The garment must stay identical to picture 2: same colour, fabric, texture, pattern, cut, "
    "length, neckline, sleeves, closures and every detail; do not add or remove anything. Keep everything else "
    "from picture 1 exactly the same: face, hair, skin tone, body shape, pose, hands, shoes, background, "
    "lighting and camera framing. Realistic smartphone photo. "
)


def category_for(p: dict) -> str | None:
    """top / bottom / full, ou None si le produit ne se porte pas sur le corps (chaussures, sac…)."""
    from . import niches
    n = niches.get(p.get("niche")) if p.get("niche") else None
    garment = p.get("garment") if isinstance(p.get("garment"), dict) else {}
    text = " ".join(str(x or "") for x in (p.get("title"), p.get("supplier_title"), garment.get("type"),
                                           (n or {}).get("name"), p.get("niche"))).lower()
    if re.search(CATEGORY_WORDS[None], text):
        return None
    if re.search(CATEGORY_WORDS["full"], text):
        return "full"
    if re.search(CATEGORY_WORDS["bottom"], text):
        return "bottom"
    return "top"


def bases(persona: dict | None) -> list[Path]:
    """Photos de base en pied du modèle (le portrait serré n'est pas utilisable pour l'essayage)."""
    from .photos import abs_media
    refs = (persona or {}).get("refs") or ([persona["reference_image"]] if persona and persona.get("reference_image") else [])
    out = [abs_media(r) for r in refs if "_face" not in Path(r).stem]
    return [x for x in out if x.exists()]


def garment_ref(p: dict, fallback: Path) -> Path:
    """Vêtement seul sur fond blanc (détourage du studio instantané), créé une fois. Sinon l'image d'origine."""
    from .photos import abs_media, product_dir
    d = product_dir(p["id"])
    cached = d / "garment_ref.jpg"
    if cached.exists():
        return cached
    try:
        res = studio.packshot(abs_media(p["supplier_image"]), cached, "blanc", fill=0.9)
        if res["cutout"] != "aucun":
            return cached
        cached.unlink(missing_ok=True)
    except Exception:
        pass
    return fallback


def enabled() -> bool:
    return (db.get_setting("tryon") or "on") != "off"


def prompt_for(category: str) -> str:
    return PROMPT.format(zone=ZONES.get(category, ZONES["top"]))
