"""Pilote automatique : le planning qui tourne 24h/24 sur le VPS.

  toutes les 15 min    synchro annonces + détection des ventes
  toutes les 10 min    messages acheteurs + offres
  10h → 15h            publication des produits prêts (quota par boutique, réparti sur la plage)
  02h00                préparation des brouillons (photos + fiches)
  03h00                baisse des prix des articles qui ne bougent pas
  04h00                analyse du marché Vinted + niches
  21h00                récap compta (géré par le bot)
"""
import logging
from datetime import datetime, timedelta

from . import db, listing, notify, publisher, pricing, responder, sync
from .vinted_client import VintedAuthError, VintedClient

log = logging.getLogger(__name__)
_AUTH_WARNED: set[int] = set()


def enabled() -> bool:
    return db.get_setting("autopilot") == "on"


def connected_shops():
    return [s for s in db.list_shops() if s.get("active") and s.get("vinted_cookies")
            and s.get("vinted_status") not in ("expired",)]


def _for_each_shop(label, fn):
    results = {}
    for shop in connected_shops():
        try:
            results[shop["name"]] = fn(VintedClient(shop))
            _AUTH_WARNED.discard(shop["id"])
        except VintedAuthError as e:
            if shop["id"] not in _AUTH_WARNED:  # un seul message, pas un spam toutes les 10 min
                notify.send(f"🔑 <b>{shop['name']}</b> : {e}")
                _AUTH_WARNED.add(shop["id"])
            results[shop["name"]] = f"erreur : {e}"
        except Exception as e:
            log.exception("%s %s", label, shop["name"])
            db.log_event("error", f"{label} : {e}", shop_id=shop["id"])
            results[shop["name"]] = f"erreur : {e}"
    return results


def run_sync():
    def job(c):
        listings = sync.sync_listings(c)
        sales = sync.sync_sales(c)
        return {**listings, "sales": len(sales)}
    return _for_each_shop("synchro", job)


def run_inbox():
    if db.get_setting("reply_mode") == "off" and db.get_setting("offer_mode") == "off":
        return {}
    return _for_each_shop("messages", responder.process_shop)


def in_publish_window(now=None) -> bool:
    now = now or datetime.now()
    return int(db.get_setting("publish_start")) <= now.hour < int(db.get_setting("publish_end"))


def run_publish(now=None):
    """Appelé toutes les 15 min : au plus 1 article par boutique et par passage → publications étalées."""
    mode = db.get_setting("publish_mode")
    if mode == "off" or not in_publish_window(now):
        return {}
    out = {}
    for shop in connected_shops():
        quota = shop.get("daily_quota") or 20
        if db.published_today(shop["id"]) >= quota:
            continue
        recent = (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds")
        ready = [p for p in db.list_products(status="ready", shop_id=shop["id"], limit=50)
                 if not p.get("vinted_item_id")
                 and (mode == "validate" or not p.get("approval_sent_at") or p["approval_sent_at"] < recent)]
        if not ready:
            continue
        p = ready[-1]  # le plus ancien
        if mode == "validate":
            if not p.get("approval_sent_at"):
                request_approval(p)
            continue
        try:
            res = publisher.publish(p["id"])
            out[shop["name"]] = res["url"]
            notify.send(f"🟢 Publié sur <b>{shop['name']}</b> : {p['title']} — {p['price']:.2f} €",
                        buttons=[[("Voir l'annonce", res["url"])]])
        except Exception as e:
            # on garde le produit prêt, nouvel essai dans 6 h (pas de spam)
            db.update_product(p["id"], approval_sent_at=datetime.now().isoformat(timespec="seconds"))
            db.log_event("error", f"Publication #{p['id']} : {e}", shop_id=shop["id"], product_id=p["id"])
            notify.send(f"⚠️ Publication du produit #{p['id']} impossible : {e}")
    return out


def request_approval(p: dict):
    photo = None
    if p.get("photos"):
        from .photos import abs_media
        photo = abs_media(p["photos"][0])
    notify.send(f"📤 <b>À valider</b> · {p.get('shop_name') or ''}\n<b>{p['title']}</b>\n"
                f"{p['price']:.2f} € (mini {p.get('min_price') or 0:.2f} €) · achat {p['cost']:.2f} €\n\n"
                f"{(p.get('description') or '')[:500]}",
                photo_path=photo,
                buttons=[[("✅ Publier", f"pub:{p['id']}"), ("🔄 Refaire photos", f"redo:{p['id']}")],
                         [("👀 Voir tout", f"view:{p['id']}"), ("❌ Ne pas publier", f"skip:{p['id']}")]])
    db.update_product(p["id"], approval_sent_at=datetime.now().isoformat(timespec="seconds"))


def run_prepare_night():
    if db.get_setting("auto_prepare") != "on":
        return []
    done = []
    limit = int(db.get_setting("prepare_per_night") or 15)
    for p in reversed(db.list_products(status="draft", limit=200)):
        if len(done) >= limit:
            break
        if not p.get("supplier_image") or not p.get("shop_id"):
            continue
        try:
            listing.prepare(p["id"])
            done.append(p["id"])
        except Exception as e:
            db.log_event("error", f"Préparation #{p['id']} : {e}", product_id=p["id"])
    if done:
        notify.send(f"🌙 {len(done)} produit(s) préparés cette nuit (photos + fiches) : "
                    + ", ".join(f"#{i}" for i in done))
    return done


def run_price_drops(now=None):
    if db.get_setting("price_drop") != "on":
        return []
    now = now or datetime.now()
    days = int(db.get_setting("price_drop_days") or 7)
    pct = float(db.get_setting("price_drop_pct") or 10) / 100
    limit = now - timedelta(days=days)
    dropped = []
    for p in db.list_products(status="published", limit=1000):
        if not p.get("vinted_item_id") or not p.get("price") or not p.get("published_at"):
            continue
        last = p.get("last_price_drop_at") or p["published_at"]
        if datetime.fromisoformat(last) > limit or (p.get("favourites") or 0) >= 5:
            continue
        mini = p.get("min_price") or pricing.compute_prices(p["cost"])["min_price"]
        new = max(mini, pricing._round_price(p["price"] * (1 - pct)))
        if new >= p["price"]:
            continue
        try:
            client = VintedClient.for_shop(p["shop_id"])
            client.update_price(p["vinted_item_id"], new)
            if client.simulated:
                db.log_event("price", f"[simulation] {p['title']} : {p['price']:.2f} → {new:.2f} € (non appliqué)",
                             shop_id=p["shop_id"], product_id=p["id"], source="simulation")
                continue
            db.update_product(p["id"], price=new, last_price_drop_at=now.isoformat(timespec="seconds"))
            db.log_event("price", f"Prix baissé : {p['title']} {p['price']:.2f} → {new:.2f} €",
                         shop_id=p["shop_id"], product_id=p["id"])
            dropped.append((p["id"], p["price"], new))
        except Exception as e:
            db.log_event("error", f"Baisse de prix #{p['id']} : {e}", product_id=p["id"])
    if dropped:
        notify.send("📉 Prix baissés : " + ", ".join(f"#{i} {a:.0f}→{b:.0f} €" for i, a, b in dropped[:20]))
    return dropped


def run_market_scan():
    from . import market, niches
    shops = connected_shops()
    if not shops:
        return None
    market.scan_all(VintedClient(shops[0]))
    top = niches.ranked()[:5]
    notify.send("🔎 <b>Analyse du marché de la nuit</b>\n" + "\n".join(
        f"{i + 1}. {n['name']} — {n['score']}/10"
        + (f" · {n['live']['median_price']} € médian" if n.get("live") and n["live"].get("median_price") else "")
        for i, n in enumerate(top)))
    return top


def cycle():
    """Un passage complet (utile en test ou via cron)."""
    return {"sync": run_sync(), "inbox": run_inbox(), "publish": run_publish()}
