"""Publication automatique des produits prêts sur Vinted."""
from datetime import datetime

from . import db
from .photos import abs_media
from .vinted_client import VintedClient, VintedError


def build_fields(p: dict, tpl: dict) -> dict:
    size_id = (tpl.get("size_map") or {}).get((p.get("size") or "").upper()) or tpl.get("size_id")
    return {
        "title": p["title"][:100],
        "description": p["description"],
        "price": float(p["price"]),
        "catalog_id": tpl.get("catalog_id"),
        "brand_id": tpl.get("brand_id"),
        "brand": tpl.get("brand"),
        "size_id": size_id,
        "status_id": tpl.get("status_id") or 6,  # 6 = Neuf avec étiquette
        "package_size_id": tpl.get("package_size_id") or 2,
        "color_ids": tpl.get("color_ids") or [],
        "item_attributes": tpl.get("item_attributes") or [],
    }


def check_ready(p: dict) -> list[str]:
    problems = []
    if not p.get("photos"):
        problems.append("pas de photos")
    for f in ("title", "description", "price"):
        if not p.get(f):
            problems.append(f"{f} manquant")
    if not p.get("shop_id"):
        problems.append("pas de boutique")
    return problems


def template_for(p: dict):
    tid = p.get("template_id")
    if not tid and p.get("shop_id"):
        tid = (db.get_shop(p["shop_id"]) or {}).get("template_id")
    return db.get_template(tid) if tid else None


def publish(product_id: int) -> dict:
    p = db.get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    if p.get("vinted_item_id"):
        raise ValueError("Déjà publié sur Vinted")
    problems = check_ready(p)
    tpl = template_for(p)
    if not tpl:
        problems.append("aucun modèle d'annonce (catégorie/marque/taille) : dashboard → Boutiques → "
                        "« Apprendre d'une annonce »")
    if problems:
        raise ValueError("Impossible de publier : " + ", ".join(problems))
    shop = db.get_shop(p["shop_id"])
    client = VintedClient(shop)
    photos = [abs_media(x) for x in p["photos"]][:20]
    item = client.create_item(build_fields(p, tpl), photos)
    item_id = item.get("id")
    if not item_id:
        raise VintedError(f"Vinted n'a pas renvoyé d'identifiant d'annonce : {str(item)[:200]}")
    url = item.get("url") or f"https://www.{shop.get('vinted_domain') or 'vinted.fr'}/items/{item_id}"
    db.update_product(product_id, status="published", vinted_item_id=item_id, vinted_url=url,
                      published_at=datetime.now().isoformat(timespec="seconds"))
    db.log_event("publish", f"Publié : {p['title']} à {p['price']:.2f} €", shop_id=shop["id"], product_id=product_id)
    return {"item_id": item_id, "url": url}


def learn_template(shop_id: int, item_ref: str, name: str | None = None) -> int:
    """Copie catégorie, marque, taille, état et format de colis depuis une de TES annonces existantes."""
    item_id = int("".join(ch for ch in item_ref.split("/items/")[-1].split("-")[0] if ch.isdigit()))
    client = VintedClient.for_shop(shop_id)
    it = client.get_own_item(item_id)
    if not it:
        raise VintedError("Annonce introuvable (elle doit appartenir au compte connecté)")
    size_label = (it.get("size_title") or it.get("size") or "")
    size_label = size_label.get("title", "") if isinstance(size_label, dict) else str(size_label)
    tid = db.add_template(
        name or f"{it.get('title', 'Annonce')[:30]} ({item_id})",
        catalog_id=it.get("catalog_id"), brand_id=it.get("brand_id"),
        brand=(it.get("brand_dto") or {}).get("title") or it.get("brand"),
        size_id=it.get("size_id"), size_map={size_label.split(" ")[0].upper(): it.get("size_id")} if size_label else {},
        package_size_id=it.get("package_size_id") or 2, status_id=6,
        color_ids=[c for c in (it.get("color1_id"), it.get("color2_id")) if c],
        item_attributes=it.get("item_attributes") or [],
    )
    shop = db.get_shop(shop_id)
    if not shop.get("template_id"):
        db.update_shop(shop_id, template_id=tid)
    return tid
