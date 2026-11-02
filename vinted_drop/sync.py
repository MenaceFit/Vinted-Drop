"""Synchronisation avec Vinted : annonces (vues, favoris), ventes, articles déjà en ligne."""
import logging

from . import db, notify
from .vinted_client import VintedClient, price_of

log = logging.getLogger(__name__)


def _item_url(shop, item_id):
    return f"https://www.{shop.get('vinted_domain') or 'vinted.fr'}/items/{item_id}"


def sync_listings(client: VintedClient, import_unknown=True) -> dict:
    shop = client.shop
    stats = {"updated": 0, "imported": 0}
    for it in client.my_items():
        item_id = it.get("id")
        if not item_id:
            continue
        p = db.product_by_vinted_id(item_id)
        fav = it.get("favourite_count") or 0
        views = it.get("view_count") or 0
        if p:
            fields = {"favourites": fav, "views": views}
            price = price_of(it)
            if price:
                fields["price"] = price
            db.update_product(p["id"], **fields)
            stats["updated"] += 1
        elif import_unknown and not it.get("is_closed"):
            photo = (it.get("photo") or {}).get("url")
            pid = db.add_product(cost=0, supplier_title=it.get("title"), shop_id=shop["id"], niche=shop.get("niche"))
            db.update_product(pid, title=it.get("title"), price=price_of(it), status="published",
                              vinted_item_id=item_id, vinted_url=it.get("url") or _item_url(shop, item_id),
                              favourites=fav, views=views)
            db.log_event("import", f"Annonce Vinted importée : {it.get('title')} (prix d'achat à renseigner)"
                         + (f" {photo}" if photo else ""), shop_id=shop["id"], product_id=pid)
            stats["imported"] += 1
    return stats


def _first(d, *keys):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, ""):
            return cur
    return None


def sync_sales(client: VintedClient) -> list[int]:
    """Détecte les nouvelles ventes, les enregistre et prévient sur Telegram avec le lien fournisseur."""
    shop = client.shop
    new_sales = []
    for order in client.sold_orders():
        tid = _first(order, "transaction_id", "id")
        if not tid or db.sale_by_transaction(tid):
            continue
        status = str(_first(order, "status", "transaction_user_status") or "").lower()
        if any(x in status for x in ("cancel", "annul", "refund", "rembours")):
            continue
        item_id = _first(order, "item_id", "item.id")
        buyer = _first(order, "buyer.login", "opposite_user.login")
        price = price_of(order)
        if not item_id or not buyer:
            try:
                tr = client.transaction(tid)
                item_id = item_id or _first(tr, "item_id", "item.id", "order.items.0.id")
                buyer = buyer or _first(tr, "buyer.login", "opposite_user.login")
                price = price or price_of(_first(tr, "offer") or {}) or price_of(tr)
            except Exception as e:  # l'enrichissement est facultatif
                log.warning("transaction %s : %s", tid, e)
        p = db.product_by_vinted_id(item_id) if item_id else None
        if not p:
            title = _first(order, "title", "item.title")
            matches = [x for x in db.list_products(status="published", shop_id=shop["id"], limit=500)
                       if title and x.get("title") == title]
            p = matches[0] if matches else None
        if not p:
            notify.send(f"💸 Vente sur <b>{shop['name']}</b> d'un article non suivi : "
                        f"{_first(order, 'title') or tid} ({price or '?'} €). Ajoute-le dans le dashboard.")
            db.log_event("sale", f"Vente non suivie {tid}", shop_id=shop["id"])
            continue
        if p["status"] == "sold":
            continue
        sid = db.record_sale(p["id"], price or p.get("price") or 0, buyer, vinted_transaction_id=tid, source="vinted")
        sale = db.get_sale(sid)
        notify.notify_sale(sale)
        db.log_event("sale", f"Vente : {p.get('title')} à {sale['sale_price']:.2f} € "
                     f"(+{sale['profit']:.2f} €)", shop_id=shop["id"], product_id=p["id"])
        new_sales.append(sid)
    return new_sales
