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
            notify.send(f"🔑 <b>{shop['name']}</b> : {e}", category="auth", dedupe=f"auth-{shop['id']}",
                        within_hours=6, buttons=[[("🔑 Reconnecter", f"{_dash()}/boutiques")]])
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


def _dash():
    from . import config
    return config.DASHBOARD_URL


def in_publish_window(now=None, shop=None) -> bool:
    now = now or datetime.now()
    start = int(db.shop_setting(shop, "publish_start", 10))
    end = int(db.shop_setting(shop, "publish_end", 15))
    return start <= now.hour < end


def run_publish(now=None):
    """Appelé toutes les 15 min : au plus 1 article par boutique et par passage → publications étalées."""
    mode = db.get_setting("publish_mode")
    if mode == "off":
        return {}
    out = {}
    for shop in connected_shops():
        if not in_publish_window(now, shop):
            continue
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
            if res.get("simulated"):
                out[shop["name"]] = "simulation"
                continue
            notify.send(f"🟢 Publié sur <b>{shop['name']}</b> : {p['title']} — {p['price']:.2f} €",
                        buttons=[[("Voir l'annonce", res["url"])]], category="publish")
        except Exception as e:
            # on garde le produit prêt, nouvel essai dans 6 h (pas de spam)
            db.update_product(p["id"], approval_sent_at=datetime.now().isoformat(timespec="seconds"))
            db.log_event("error", f"Publication #{p['id']} : {e}", shop_id=shop["id"], product_id=p["id"])
            notify.send(f"⚠️ Publication du produit #{p['id']} impossible : {e}", category="publish",
                        dedupe=f"pubfail-{p['id']}", within_hours=12)
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
    do_photos = db.get_setting("prepare_photos") != "off"
    do_listing = db.get_setting("prepare_listing") != "off"
    from . import photos as photos_mod
    for p in reversed(db.list_products(status="draft", limit=200)):
        if len(done) >= limit:
            break
        if not p.get("supplier_image") or not p.get("shop_id"):
            continue
        try:
            if do_photos and not p.get("photos"):
                photos_mod.generate_product_photos(p["id"])
            if do_listing:
                listing.generate_listing(p["id"])
            if db.get_product(p["id"]).get("photos") and db.get_product(p["id"]).get("title"):
                db.update_product(p["id"], status="ready")
            done.append(p["id"])
        except Exception as e:
            db.log_event("error", f"Préparation #{p['id']} : {e}", product_id=p["id"])
    if done:
        notify.send(f"🌙 {len(done)} produit(s) préparés (photos + fiches) : " + ", ".join(f"#{i}" for i in done),
                    category="photos", buttons=[[("📤 Voir les produits prêts", "m:ready")]])
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
        notify.send("📉 Prix baissés : " + ", ".join(f"#{i} {a:.0f}→{b:.0f} €" for i, a, b in dropped[:20]),
                    category="publish")
    return dropped


def run_market_scan():
    from . import market, niches
    shops = connected_shops()
    if not shops:
        return None
    market.scan_all(VintedClient(shops[0]))
    top = niches.ranked(profitable_only=True)[:5]
    notify.send("🔎 <b>Analyse du marché</b>\n" + "\n".join(
        f"{i + 1}. {n['name']} — {n['score']}/100"
        + (f" · {n['live']['median']:.0f} € médian" if n.get("live") and n["live"].get("median") else "")
        for i, n in enumerate(top)), category="opportunities", buttons=[[("💡 Opportunités", f"{_dash()}/opportunites")]])
    return top


def cycle():
    """Un passage complet (utile en test ou via cron)."""
    return {"sync": run_sync(), "inbox": run_inbox(), "publish": run_publish()}


# ======================= planning configurable =======================
def run_report():
    from . import finance
    s, e = db.period_range("today")
    f = finance.summary(s, e)
    m = finance.summary(*db.period_range("30d"))
    notify.send(f"📊 <b>Rapport du jour</b>\n{f['sales']} ventes · CA {f['revenue']:.2f} € · bénéfice net "
                f"<b>{f['net_profit']:.2f} €</b>\nCoûts IA {f['ai_costs']:.2f} € · achats fournisseurs {f['supplier_spend']:.2f} €"
                f"\n\n30 jours : bénéfice net {m['net_profit']:.2f} € · marge {m['net_margin_pct']} %",
                category="reports", dedupe=f"report-{s[:10]}", buttons=[[("💰 Finances", "m:finance")]])
    return f


def run_backup():
    from .backup import backup
    path = backup("auto")
    db.log_event("backup", f"Sauvegarde : {path.name if path else 'aucune base'}")
    return str(path)


def run_opportunities():
    from . import market
    n = market.detect_opportunities()
    top = [o for o in market.list_opportunities(limit=50) if o["score"] >= 70 and not o.get("favorite")][:3]
    for o in top:
        notify.send(f"💡 <b>Opportunité</b> · {o['kind_label']}\n{o['title']} — {o['score']}/100\n{o['data'].get('why', '')}",
                    category="opportunities", dedupe=f"opp-{o['id']}", within_hours=24 * 7,
                    buttons=[[("⭐ Suivre", f"opp:{o['id']}"), ("💡 Voir", f"{_dash()}/opportunites")]])
    return n


JOBS = {
    "sync": {"label": "Synchronisation Vinted (annonces, ventes)", "fn": run_sync, "every": 15},
    "inbox": {"label": "Messages et offres", "fn": run_inbox, "every": 10},
    "publish": {"label": "Publication des produits prêts", "fn": run_publish, "every": 15},
    "prepare": {"label": "Préparation des annonces (photos + descriptions)", "fn": run_prepare_night, "at": "02:00"},
    "price": {"label": "Révision des prix", "fn": run_price_drops, "at": "03:00"},
    "market": {"label": "Analyse du marché", "fn": run_market_scan, "at": "04:00"},
    "opportunities": {"label": "Détection des opportunités", "fn": run_opportunities, "at": "04:30"},
    "backup": {"label": "Sauvegarde de la base", "fn": run_backup, "at": "05:00"},
    "report": {"label": "Rapport financier", "fn": run_report, "at": "21:00"},
}


def job_config(key) -> dict:
    j = JOBS[key]
    cfg = {"enabled": True, "every": j.get("every"), "at": j.get("at"), "retries": 1, "alert": True}
    cfg.update(db.get_json_setting(f"job:{key}", {}) or {})
    return cfg


def save_job_config(key, **cfg):
    if key not in JOBS:
        raise ValueError("Tâche inconnue")
    cur = job_config(key)
    cur.update({k: v for k, v in cfg.items() if k in ("enabled", "every", "at", "retries", "alert")})
    if cur.get("every") is not None:
        cur["every"] = max(5, int(cur["every"]))
    if cur.get("at"):
        h, m = (int(x) for x in str(cur["at"]).split(":"))
        cur["at"] = f"{h % 24:02d}:{m % 60:02d}"
    import json
    db.set_setting(f"job:{key}", json.dumps(cur))
    return cur


def job_state(key) -> dict:
    return db.get_json_setting(f"_jobstate:{key}", {}) or {}


def _set_state(key, **st):
    import json
    cur = job_state(key)
    cur.update(st)
    db.set_setting(f"_jobstate:{key}", json.dumps(cur, default=str))


def is_due(key, now=None) -> bool:
    now = now or datetime.now()
    cfg, st = job_config(key), job_state(key)
    if not cfg["enabled"]:
        return False
    last = datetime.fromisoformat(st["last_run"]) if st.get("last_run") else None
    if cfg.get("every"):
        return last is None or now - last >= timedelta(minutes=int(cfg["every"]))
    h, m = (int(x) for x in cfg["at"].split(":"))
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return now >= slot and (last is None or last < slot)


def next_run(key, now=None):
    now = now or datetime.now()
    cfg, st = job_config(key), job_state(key)
    if not cfg["enabled"]:
        return None
    if cfg.get("every"):
        last = datetime.fromisoformat(st["last_run"]) if st.get("last_run") else now
        return max(now, last + timedelta(minutes=int(cfg["every"])))
    h, m = (int(x) for x in cfg["at"].split(":"))
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return slot if slot > now else slot + timedelta(days=1)


def run_job(key, source="autopilot"):
    """Exécute une tâche du planning avec traçabilité (table tasks), réessais et alerte en cas d'échec."""
    from . import tasks
    cfg = job_config(key)
    reason = tasks.blocked(source)
    if reason:
        return {"skipped": reason}
    _set_state(key, last_run=datetime.now().isoformat(timespec="seconds"), last_status="running")

    def alert(t, msg):
        if cfg.get("alert"):
            notify.send(f"⚠️ <b>{JOBS[key]['label']}</b> a échoué : {msg}", category="errors",
                        dedupe=f"jobfail-{key}", within_hours=6)

    tid, created = tasks._create(f"job:{key}", JOBS[key]["label"], f"job:{key}", None, None,
                                 int(cfg.get("retries") or 1), source)
    if not created:
        return {"skipped": "déjà en cours"}
    result = tasks.execute(tid, JOBS[key]["fn"], on_error=alert)
    t = tasks.get(tid)
    _set_state(key, last_status=t["status"], last_error=t.get("error"), last_task=tid,
               last_finished=datetime.now().isoformat(timespec="seconds"))
    return result


def tick(now=None):
    """Appelée chaque minute : lance les tâches arrivées à échéance (une à la fois, dans ce thread)."""
    if db.get_setting("emergency_stop") == "on" or not enabled():
        return []
    ran = []
    for key in JOBS:
        if is_due(key, now):
            run_job(key)
            ran.append(key)
    return ran


def jobs_overview():
    out = []
    for key, j in JOBS.items():
        cfg, st = job_config(key), job_state(key)
        out.append({"key": key, "label": j["label"], **cfg, "state": st, "next_run": next_run(key)})
    return out
