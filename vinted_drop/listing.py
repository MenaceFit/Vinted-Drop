"""Génération des fiches Vinted : titre, description, caractéristiques, mots-clés, catégorie, prix.

- Plusieurs styles rédactionnels (réglage global ou par boutique).
- Anti-répétition : l'IA reçoit les derniers titres de la boutique et doit s'en démarquer.
- Le prix vient du moteur de recommandation (coûts complets, marché, historique, bénéfice minimum).
- Mention honnête des photos de présentation (réglage photo_disclosure, activé par défaut).
"""
import json

from . import ai, db, pricing
from .photos import abs_media

STYLES = {
    "naturel": "Ton d'une particulière soigneuse qui vend ses vêtements : simple, chaleureux, phrases courtes.",
    "enthousiaste": "Ton dynamique et positif, quelques mots forts (coup de cœur, parfaite pour...), sans exagérer.",
    "minimaliste": "Très sobre : une phrase d'accroche puis l'essentiel en liste à puces, sans adjectifs superflus.",
    "detaille": "Ton précis et rassurant : matière, coupe, conseils de taille et d'entretien, idées de tenue.",
}

DISCLOSURE = "Photos de présentation réalisées à partir des photos du produit."

SYSTEM = """Tu es une vendeuse Vinted expérimentée. Tu rédiges en français l'annonce d'un vêtement NEUF.
Style : {style}
Règles :
- Titre : 40 à 70 caractères, les mots que les acheteurs tapent (type de pièce, style, couleur, matière,
  occasion). Pas de majuscules partout, pas d'emojis, pas de formule toute faite (« magnifique », « superbe »
  en premier mot sont interdits). Il doit être DIFFÉRENT des titres récents fournis (structure et mots).
- Description : naturelle, pas de phrases génériques d'IA (« Découvrez », « Sublimez », « Parfait pour toutes
  les occasions » sont interdits). Varie la structure. État (neuf), matière, coupe et conseil de taille, idées
  de tenue, puis 5 à 8 hashtags pertinents.
- N'invente JAMAIS une matière, une mesure, une marque ou un détail que tu ne vois pas. Inconnu = non cité.
  Si aucune marque n'est visible, "brand" vaut "Sans marque".
- Ne mentionne jamais le fournisseur (Shein, Temu, AliExpress...).
Réponds en JSON : {{"title": str, "description": str, "category": str, "color": str, "material": str|null,
"brand": str, "size_advice": str|null, "keywords": [str], "features": [str]}}"""


def _recent_titles(shop_id, exclude_id, n=15):
    with db.connect() as c:
        return [r[0] for r in c.execute(
            "SELECT title FROM products WHERE shop_id IS ? AND id != ? AND title IS NOT NULL ORDER BY id DESC LIMIT ?",
            (shop_id, exclude_id, n))]


def generate_listing(product_id: int, extra_info: str = "", style: str | None = None) -> dict:
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    shop = db.get_shop(p["shop_id"]) if p.get("shop_id") else None
    style = style or db.shop_setting(shop, "listing_style", "naturel")
    garment = p.get("garment") or {}
    recent = _recent_titles(p.get("shop_id"), product_id)
    user = (f"Titre fournisseur : {p.get('supplier_title') or 'inconnu'}\n"
            f"Taille disponible : {p.get('size') or 'à préciser'}\n"
            f"Niche de la boutique : {p.get('niche') or 'mode'}\n"
            f"Analyse du vêtement : {json.dumps(garment, ensure_ascii=False) if garment else 'aucune'}\n"
            f"Infos supplémentaires : {extra_info or 'aucune'}\n"
            f"Titres récents de la boutique (à ne pas imiter) : {json.dumps(recent, ensure_ascii=False)}\n"
            "Rédige l'annonce.")
    images = [abs_media(p["supplier_image"])] if p.get("supplier_image") else []
    data = ai.chat_json(SYSTEM.format(style=STYLES.get(style, STYLES["naturel"])), user, images=images)
    title = (data.get("title") or "").strip()[:100]
    if title and title.lower() in {t.lower() for t in recent}:
        title = f"{title[:85]} {p.get('size') or ''}".strip()  # jamais deux titres identiques
    description = (data.get("description") or "").strip()
    if db.get_setting("photo_disclosure") == "on" and p.get("photos") and DISCLOSURE not in description:
        description = f"{description}\n\n{DISCLOSURE}"
    rec = pricing.recommend_for(product_id)
    fields = {
        "title": title, "description": description, "category": data.get("category"), "color": data.get("color"),
        "material": data.get("material"), "brand": data.get("brand") or "Sans marque",
        "keywords": data.get("keywords") or [], "price": rec["price"], "min_price": rec["min_price"],
        "price_info": {k: rec[k] for k in ("floor", "total_cost", "profit_at_price", "profit_at_min", "margin_pct",
                                          "confidence", "confidence_label", "reasons", "warnings", "min_profit")},
    }
    if p["status"] == "draft" and p.get("photos"):
        fields["status"] = "ready"
    db.update_product(product_id, **fields)
    db.log_event("prepare", f"Fiche générée ({style}) : {title}", shop_id=p.get("shop_id"), product_id=product_id)
    return {**fields, **{k: rec[k] for k in ("profit_at_price", "profit_at_min", "margin_pct", "confidence_label")},
            "features": data.get("features") or [], "size_advice": data.get("size_advice")}


def prepare(product_id: int) -> dict:
    """Photos + fiche en une fois : le produit passe en statut "prêt à publier"."""
    from .photos import generate_product_photos
    p = db.get_product(product_id)
    if not p.get("photos"):
        generate_product_photos(product_id)
    result = generate_listing(product_id)
    if db.get_product(product_id).get("photos"):
        db.update_product(product_id, status="ready")
    return result


# ---------------- Depop ----------------
def _tag(word: str) -> str:
    import re
    import unicodedata
    w = unicodedata.normalize("NFKD", str(word)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", w)


def depop_listing(product_id: int) -> dict:
    """Annonce Depop prête à copier (Depop n'a pas d'API vendeur ouverte en France : publication à la main).

    Description : titre en 1re ligne (Depop n'a pas de champ titre), détails, taille, état, envoi,
    mention des photos, puis 5 hashtags (mots-clés de la niche en anglais + mots-clés du produit).
    Prix : celui qui laisse le même bénéfice qu'au prix Vinted, frais Depop compris."""
    from . import niches
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    niche = niches.get(p["niche"]) if p.get("niche") else None
    tags = []
    for w in (niche or {}).get("depop_tags", []) + list(p.get("keywords") or []):
        t = _tag(w)
        if t and t not in tags and len(t) <= 25:
            tags.append(t)
    lines = [p.get("title") or p.get("supplier_title") or "Article neuf"]
    if p.get("description"):
        body = p["description"].split("#")[0].strip()      # sans les hashtags Vinted
        if body and body != lines[0]:
            lines += ["", body]
    lines += [""]
    if p.get("size"):
        lines.append(f"Taille : {p['size']}")
    lines.append("État : neuf")
    lines.append(f"Envoi : {db.get_setting('ship_delay') or 'rapide'}")
    if db.get_setting("photo_disclosure") != "off" and DISCLOSURE not in "\n".join(lines):
        lines += ["", DISCLOSURE]
    if tags:
        lines += ["", " ".join(f"#{t}" for t in tags[:5])]
    try:
        rec = pricing.recommend_for(product_id)
    except Exception:
        rec = pricing.compute_prices(p.get("cost") or 0)
    dp = rec.get("platforms", {}).get("depop", {})
    return {"text": "\n".join(lines), "price": dp.get("price"), "min_price": dp.get("min_price"),
            "profit_at_price": dp.get("profit_at_price"), "fees_at_price": dp.get("fees_at_price"),
            "hashtags": tags[:5]}
