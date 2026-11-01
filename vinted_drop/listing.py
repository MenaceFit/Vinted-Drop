"""Génération des fiches Vinted : titre SEO, description complète, prix."""
from . import ai, db, pricing
from .photos import abs_media

SYSTEM = """Tu es un vendeur Vinted expert en SEO. Tu rédiges des annonces en français pour des
vêtements NEUFS. Règles :
- Titre : 40 à 70 caractères, mots-clés que les acheteurs tapent (type de pièce, style, couleur,
  matière, occasion). Pas de majuscules partout, pas d'emojis, pas de nom de marque inventé.
- Description : naturelle, comme un particulier sérieux. Structure :
  1 phrase d'accroche, puis état (neuf), matière, coupe/taille (conseil : taille normalement / petit /
  grand), mesures si connues, idées de tenue / occasion, puis 5 à 8 hashtags pertinents.
- N'invente JAMAIS une matière, une mesure ou une marque que tu ne vois pas. Si inconnu, ne le cite pas.
- Ne mentionne jamais le fournisseur (Shein, Temu...).
Réponds en JSON : {"title": str, "description": str, "category": str, "color": str,
"material": str|null, "style_keywords": [str]}"""


def generate_listing(product_id: int, extra_info: str = "") -> dict:
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    user = (f"Titre fournisseur : {p.get('supplier_title') or 'inconnu'}\n"
            f"Taille disponible : {p.get('size') or 'à préciser'}\n"
            f"Niche de la boutique : {p.get('niche') or 'mode femme'}\n"
            f"Infos supplémentaires : {extra_info or 'aucune'}\n"
            "Rédige l'annonce Vinted.")
    image = abs_media(p["supplier_image"]) if p.get("supplier_image") else None
    data = ai.chat_json(SYSTEM, user, image=image)
    prices = pricing.compute_prices(p["cost"])
    fields = {
        "title": data.get("title", "")[:100],
        "description": data.get("description", ""),
        "category": data.get("category"),
        "color": data.get("color"),
        "material": data.get("material"),
        "price": prices["price"],
        "min_price": prices["min_price"],
    }
    if p["status"] == "draft" and p.get("photos"):
        fields["status"] = "ready"
    db.update_product(product_id, **fields)
    return {**fields, **prices}


def prepare(product_id: int) -> dict:
    """Photos + fiche en une fois : le produit passe en statut "prêt à publier"."""
    from .photos import generate_product_photos
    p = db.get_product(product_id)
    if not p.get("photos"):
        generate_product_photos(product_id)
    result = generate_listing(product_id)
    db.update_product(product_id, status="ready")
    return result
