"""Bot Telegram : pilote tout le business depuis ton téléphone.

- Boutons sur chaque produit, vente, message acheteur (validation en 1 clic)
- Pilote automatique (synchro, ventes, messages, publication, baisse des prix, analyse marché)
- Assistant : écris normalement (« combien j'ai gagné cette semaine ? »)
"""
import asyncio
import logging
from datetime import time
from functools import wraps
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import (assistant, autopilot, config, db, listing, niches, notify, photos, pricing,
               publisher, responder, supplier)

log = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Paris")

HELP = """<b>Vinted Drop</b> 🤖 — écris-moi normalement ou utilise les boutons.

<b>Produits</b>
📷 Envoie une capture du produit avec la légende <code>prix lien boutique taille</code>
/ajouter &lt;lien&gt; &lt;prix&gt; [boutique] [taille]
/produits [draft|ready|published|sold] · /voir &lt;id&gt;
/preparer &lt;id&gt; · /photos &lt;id&gt; · /fiche &lt;id&gt;
/publier &lt;id&gt; — publie sur Vinted maintenant
/prix &lt;achat&gt; · /offre &lt;id&gt; &lt;montant&gt;

<b>Ventes</b>
/vendu &lt;id&gt; &lt;prix&gt; · /acommander
(les ventes Vinted sont détectées toutes seules)

<b>Pilote automatique</b>
/auto — état et réglages (boutons)
/sync — synchro Vinted maintenant
/messages — messages acheteurs en attente
/vinted — état des connexions Vinted
/activite — dernières actions

<b>Pilotage</b>
/menu · /stats · /boutiques · /niches · /gagnants [niche]
"""


def authorized(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if config.TELEGRAM_ALLOWED_CHAT_IDS and chat_id not in config.TELEGRAM_ALLOWED_CHAT_IDS:
            if update.callback_query:
                await update.callback_query.answer("⛔")
            else:
                await update.effective_message.reply_text(
                    f"⛔ Accès refusé. Ton chat id : {chat_id} (ajoute-le dans TELEGRAM_ALLOWED_CHAT_IDS)")
            return
        try:
            return await func(update, ctx)
        except Exception as e:
            log.exception("Erreur")
            await update.effective_message.reply_text(f"❌ {e}")
    return wrapper


def kb(rows):
    return InlineKeyboardMarkup([[Btn(t, url=d) if str(d).startswith("http") else Btn(t, callback_data=d)
                                  for t, d in row] for row in rows])


async def reply(update: Update, text: str, buttons=None):
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                                              reply_markup=kb(buttons) if buttons else None)


def _int(args, i, name="id"):
    try:
        return int(args[i])
    except (IndexError, ValueError):
        raise ValueError(f"Il manque le {name}. Tape /aide")


def _float(s):
    return float(str(s).replace(",", ".").replace("€", ""))


def run(fn, *a):
    return asyncio.to_thread(fn, *a)


# ---------------- cartes ----------------
def product_card(p: dict) -> str:
    lines = [f"<b>#{p['id']}</b> · {p['status']} · {p.get('shop_name') or 'sans boutique'}",
             f"<b>{p.get('title') or p.get('supplier_title') or 'Sans titre'}</b>",
             f"Achat {p['cost']:.2f} €" + (f" · Prix {p['price']:.2f} € (mini {(p.get('min_price') or 0):.2f} €)"
                                           if p.get("price") else ""),
             f"Photos : {len(p.get('photos') or [])}"
             + (f" · ❤️ {p['favourites']} · 👁 {p['views']}" if p.get("vinted_item_id") else "")]
    return "\n".join(lines)


def product_buttons(p: dict):
    pid = p["id"]
    rows = []
    if p["status"] in ("draft", "ready"):
        rows.append([("⚡ Préparer", f"prep:{pid}"), ("🎨 Photos", f"photos:{pid}"), ("📝 Fiche", f"fiche:{pid}")])
    if p["status"] == "ready":
        rows.append([("✅ Publier sur Vinted", f"pub:{pid}")])
    if p["status"] == "published":
        rows.append([("💸 Vendu au prix affiché", f"sold:{pid}")])
    links = []
    if p.get("vinted_url"):
        links.append(("Annonce", p["vinted_url"]))
    if p.get("supplier_url"):
        links.append(("Fournisseur", p["supplier_url"]))
    if links:
        rows.append(links)
    if p["status"] != "archived":
        rows.append([("🗑 Archiver", f"arch:{pid}")])
    return rows


async def send_product(update, p, with_photos=False):
    if with_photos:
        await _send_photos(update.effective_message, p)
    await reply(update, product_card(p), product_buttons(p))


async def _send_photos(message, p):
    files = [photos.abs_media(x) for x in (p.get("photos") or [])]
    files = [f for f in files if f.exists()][:10]
    if files:
        await message.reply_media_group([InputMediaPhoto(f.read_bytes()) for f in files])


def _listing_text(pid, res):
    return (f"📝 <b>Fiche #{pid}</b>\n\n<b>Titre</b>\n<code>{res['title']}</code>\n\n"
            f"<b>Description</b>\n<code>{res['description']}</code>\n\n"
            f"💶 Prix affiché <b>{res['price']:.2f} €</b> · offre mini {res['min_price']:.2f} € "
            f"(bénéfice {res['profit_at_min']:.2f} → {res['profit_at_price']:.2f} €)")


# ---------------- commandes ----------------
@authorized
async def cmd_start(update: Update, ctx):
    await reply(update, HELP + f"\nTon chat id : <code>{update.effective_chat.id}</code>", main_menu())


def main_menu():
    return [[("📊 Stats", "m:stats"), ("🛒 À commander", "m:order"), ("💬 Messages", "m:msgs")],
            [("📤 Prêts", "m:ready"), ("📝 Brouillons", "m:draft"), ("🟢 En ligne", "m:published")],
            [("🤖 Pilote auto", "m:auto"), ("🔄 Synchro", "m:sync"), ("🔎 Niches", "m:niches")]]


@authorized
async def cmd_menu(update, ctx):
    await reply(update, "Que veux-tu faire ?", main_menu())


@authorized
async def cmd_ajouter(update: Update, ctx):
    args = ctx.args
    if len(args) < 2:
        raise ValueError("Usage : /ajouter <lien> <prix_achat> [boutique] [taille]")
    url, cost = args[0], _float(args[1])
    shop = db.find_shop(args[2]) if len(args) > 2 else None
    size = args[3] if len(args) > 3 else None
    pid = await run(_create_from_url, url, cost, shop, size)
    p = db.get_product(pid)
    msg = "✅ Produit ajouté\n" + product_card(p)
    if not p.get("supplier_image"):
        msg += f"\n\n⚠️ Image introuvable : envoie la capture du produit avec la légende <code>/image {pid}</code>"
    await reply(update, msg, product_buttons(p))


def _create_from_url(url, cost, shop, size):
    info = supplier.fetch_info(url)
    pid = db.add_product(cost=cost, supplier_url=url, supplier=info["supplier"], supplier_title=info["title"],
                         shop_id=shop["id"] if shop else None, niche=shop["niche"] if shop else None, size=size)
    if info["image_url"]:
        img = supplier.download_image(info["image_url"], pid)
        if img:
            db.update_product(pid, supplier_image=photos.rel(img))
    return pid


@authorized
async def on_photo(update: Update, ctx):
    """Photo + légende "prix [lien] [boutique] [taille]" → nouveau produit ; "/image <id>" → remplace l'image."""
    msg = update.effective_message
    parts = (msg.caption or "").split()
    tg_file = await msg.photo[-1].get_file()
    if parts and parts[0].lower() in ("/image", "image"):
        pid = _int(parts, 1)
        if not db.get_product(pid):
            raise ValueError(f"Produit #{pid} introuvable")
    else:
        if not parts:
            raise ValueError("Ajoute une légende : <prix_achat> [lien] [boutique] [taille]")
        cost = _float(parts[0])
        url = next((x for x in parts[1:] if x.startswith("http")), None)
        rest = [x for x in parts[1:] if not x.startswith("http")]
        shop = db.find_shop(rest[0]) if rest else None
        size = rest[1] if len(rest) > 1 else None
        pid = db.add_product(cost=cost, supplier_url=url, supplier=supplier.supplier_name(url) if url else None,
                             shop_id=shop["id"] if shop else None, niche=shop["niche"] if shop else None, size=size)
    out = photos.product_dir(pid) / "supplier.jpg"
    await tg_file.download_to_drive(out)
    db.update_product(pid, supplier_image=photos.rel(out))
    p = db.get_product(pid)
    await reply(update, "✅ Image enregistrée\n" + product_card(p), product_buttons(p))


@authorized
async def cmd_produits(update: Update, ctx):
    await _list_products(update, ctx.args[0] if ctx.args else None)


async def _list_products(update, status):
    items = db.list_products(status=status, limit=20)
    if not items:
        return await reply(update, "Aucun produit.")
    rows = [[(f"#{p['id']} {(p.get('title') or p.get('supplier_title') or '?')[:28]} · {p['price'] or '-'} €",
              f"view:{p['id']}")] for p in items]
    await reply(update, f"<b>Produits{' ' + status if status else ''}</b> ({len(items)})", rows)


@authorized
async def cmd_voir(update: Update, ctx):
    p = db.get_product(_int(ctx.args, 0))
    if not p:
        raise ValueError("Produit introuvable")
    await send_product(update, p, with_photos=True)


async def do_photos(update, pid):
    await reply(update, f"🎨 Génération des photos du produit #{pid}… (1 à 3 min)")
    await run(photos.generate_product_photos, pid)
    p = db.get_product(pid)
    await _send_photos(update.effective_message, p)
    await reply(update, "✅ Photos prêtes", product_buttons(p))


async def do_fiche(update, pid, extra=""):
    res = await run(listing.generate_listing, pid, extra)
    await reply(update, _listing_text(pid, res), product_buttons(db.get_product(pid)))


async def do_prepare(update, pid):
    await reply(update, f"⚙️ Préparation du produit #{pid} (photos + fiche)…")
    res = await run(listing.prepare, pid)
    p = db.get_product(pid)
    await _send_photos(update.effective_message, p)
    await reply(update, _listing_text(pid, res), product_buttons(p))


async def do_publish(update, pid):
    await reply(update, f"📤 Publication du produit #{pid} sur Vinted…")
    res = await run(publisher.publish, pid)
    await reply(update, "🟢 En ligne !", [[("Voir l'annonce", res["url"])]])


@authorized
async def cmd_photos(update, ctx):
    await do_photos(update, _int(ctx.args, 0))


@authorized
async def cmd_fiche(update, ctx):
    await do_fiche(update, _int(ctx.args, 0), " ".join(ctx.args[1:]))


@authorized
async def cmd_preparer(update, ctx):
    await do_prepare(update, _int(ctx.args, 0))


@authorized
async def cmd_publier(update, ctx):
    await do_publish(update, _int(ctx.args, 0))


@authorized
async def cmd_publie(update, ctx):
    """Marque comme publié à la main (si tu as publié toi-même)."""
    pid = _int(ctx.args, 0)
    fields = {"status": "published"}
    if len(ctx.args) > 1:
        fields["vinted_url"] = ctx.args[1]
        digits = "".join(ch for ch in ctx.args[1].split("/items/")[-1].split("-")[0] if ch.isdigit())
        if digits:
            fields["vinted_item_id"] = int(digits)
    db.update_product(pid, **fields)
    await reply(update, f"🟢 Produit #{pid} marqué en ligne.")


@authorized
async def cmd_prix(update, ctx):
    if not ctx.args:
        raise ValueError("Usage : /prix <prix_achat>")
    cost = _float(ctx.args[0])
    r = pricing.compute_prices(cost)
    await reply(update, f"Achat {cost:.2f} € → affiche <b>{r['price']:.2f} €</b> (x{r['multiplier']}), "
                        f"accepte jusqu'à {r['min_price']:.2f} €\nBénéfice : {r['profit_at_min']:.2f} → "
                        f"{r['profit_at_price']:.2f} €")


@authorized
async def cmd_offre(update, ctx):
    pid = _int(ctx.args, 0)
    offer = _float(ctx.args[1]) if len(ctx.args) > 1 else None
    p = db.get_product(pid)
    if not p or offer is None:
        raise ValueError("Usage : /offre <id> <montant>")
    d = responder._price_decision(p, offer)
    if d["action"] == "accept":
        txt = f"✅ Accepte : bénéfice {pricing.profit(offer, p['cost']):.2f} €"
    else:
        txt = (f"❌ Trop bas. Contre-propose <b>{d['price']:.2f} €</b> "
               f"(bénéfice {pricing.profit(d['price'], p['cost']):.2f} €)")
    await reply(update, txt)


@authorized
async def cmd_vendu(update, ctx):
    pid = _int(ctx.args, 0)
    p = db.get_product(pid)
    if not p:
        raise ValueError("Produit introuvable")
    price = _float(ctx.args[1]) if len(ctx.args) > 1 else p.get("price")
    if not price:
        raise ValueError("Usage : /vendu <id> <prix>")
    await _record_sale(update, pid, price, ctx.args[2] if len(ctx.args) > 2 else None)


async def _record_sale(update, pid, price, buyer=None):
    sale = db.get_sale(db.record_sale(pid, price, buyer))
    await reply(update, notify.sale_message(sale), notify.sale_buttons(sale))


@authorized
async def cmd_acommander(update, ctx):
    await _to_order(update)


async def _to_order(update):
    items = db.list_sales(status="to_order")
    if not items:
        return await reply(update, "Rien à commander 👌")
    for s in items[:10]:
        await reply(update, f"#{s['id']} · {(s.get('title') or '?')[:60]}\nTaille {s.get('size') or '?'} · "
                            f"{s.get('shop_name') or ''} · bénéfice {s['profit']:.2f} €", notify.sale_buttons(s))


def stats_text():
    blocks = [("Aujourd'hui", db.today_stats()), ("7 jours", db.stats(7)), ("30 jours", db.stats(30)), ("Total", db.stats())]
    lines = ["📊 <b>Compta</b>"]
    for label, s in blocks:
        lines.append(f"<b>{label}</b> : {s['sales']} ventes · CA {s['revenue']:.2f} € · "
                     f"bénéfice <b>{s['profit']:.2f} €</b> · marge {s['margin_pct']}%")
    pc = db.stats()["products"]
    lines.append(f"\nEn ligne : {pc['published']} · prêts : {pc['ready']} · brouillons : {pc['draft']}")
    todo = len(db.list_sales(status="to_order"))
    if todo:
        lines.append(f"⚠️ {todo} vente(s) à commander")
    return "\n".join(lines)


@authorized
async def cmd_stats(update, ctx):
    await reply(update, stats_text(), [[("🛒 À commander", "m:order"), ("🏪 Boutiques", "m:shops")]])


@authorized
async def cmd_boutiques(update, ctx):
    await _shops(update)


async def _shops(update):
    rows = db.shop_breakdown()
    if not rows:
        return await reply(update, "Aucune boutique : crée-en une dans le dashboard (Boutiques).")
    shops = {s["id"]: s for s in db.list_shops()}
    await reply(update, "\n".join(
        f"🏪 <b>{r['name']}</b> ({r['niche'] or '-'}) {_vinted_icon(shops[r['id']])}\n"
        f"   {r['online']} en ligne · {r['sales']} ventes · {r['profit']:.2f} € · publiés aujourd'hui "
        f"{db.published_today(r['id'])}/{shops[r['id']].get('daily_quota') or 20}" for r in rows))


def _vinted_icon(shop):
    return {"connected": "🟢", "expired": "🔑", "error": "🔴"}.get(shop.get("vinted_status"), "⚪")


@authorized
async def cmd_vinted(update, ctx):
    shops = db.list_shops()
    if not shops:
        return await reply(update, "Aucune boutique.")
    await reply(update, "<b>Connexions Vinted</b>\n" + "\n".join(
        f"{_vinted_icon(s)} {s['name']} : " + (f"@{s['vinted_login']}" if s.get("vinted_login") else "non connectée")
        + (f" ({s['vinted_status']})" if s.get("vinted_status") not in (None, "connected") else "")
        for s in shops) + f"\n\nPour connecter : {config.DASHBOARD_URL}/boutiques")


@authorized
async def cmd_niches(update, ctx):
    await _niches(update)


async def _niches(update):
    lines = ["🔎 <b>Top niches maintenant</b>"]
    rows = []
    for n in niches.ranked()[:8]:
        live = n.get("live") or {}
        lines.append(f"{'🔥' if n['in_season'] else '⏳'} <b>{n['name']}</b> — {n['score']}/10 · achat {n['buy_price']} "
                     f"→ vente {n['sell_price']}" + (f" · médian Vinted {live['median_price']} €" if live.get("median_price") else "")
                     + f"\n   <i>{n['why']}</i>")
        rows.append([(f"🔗 {n['name'][:30]}", f"win:{n['id']}")])
    await reply(update, "\n".join(lines), rows)


@authorized
async def cmd_gagnants(update, ctx):
    await _winners(update, ctx.args[0] if ctx.args else None)


async def _winners(update, niche_id):
    items = niches.winning_products(niche_id)[:15]
    if not items:
        return await reply(update, "Aucun produit pour cette niche.")
    lines = [f'• <a href="{i["url"]}">{i["title"]}</a> ({i["supplier"]}, '
             f'{f"{i["cost"]:.2f} €" if i.get("cost") else "prix à vérifier"})' for i in items]
    lines.append("\nAjoute-en un : envoie sa capture avec la légende <code>prix lien boutique</code>")
    await reply(update, "\n".join(lines))


# ---------- pilote automatique ----------
SETTING_LABELS = {
    "autopilot": "Pilote automatique", "publish_mode": "Publication", "reply_mode": "Réponses aux messages",
    "offer_mode": "Offres", "auto_prepare": "Préparation la nuit", "price_drop": "Baisse des prix",
}
CYCLE = {"on": "off", "off": "on", "validate": "auto", "auto": "off"}
MODE_LABEL = {"on": "✅ activé", "off": "⛔ désactivé", "validate": "👆 avec validation", "auto": "🤖 automatique"}


def auto_text():
    s = db.all_settings()
    lines = ["🤖 <b>Pilote automatique</b>"]
    for k, label in SETTING_LABELS.items():
        lines.append(f"{label} : {MODE_LABEL.get(s[k], s[k])}")
    lines.append(f"\nPublication de {s['publish_start']}h à {s['publish_end']}h · baisse de "
                 f"{s['price_drop_pct']} % après {s['price_drop_days']} jours sans favoris")
    lines.append("\nTouche un bouton pour changer (validation → auto → désactivé).")
    return "\n".join(lines)


def auto_buttons():
    s = db.all_settings()
    return [[(f"{label} : {MODE_LABEL.get(s[k], s[k]).split(' ')[0]}", f"set:{k}")] for k, label in SETTING_LABELS.items()]


@authorized
async def cmd_auto(update, ctx):
    await reply(update, auto_text(), auto_buttons())


@authorized
async def cmd_sync(update, ctx):
    await _sync(update)


async def _sync(update):
    await reply(update, "🔄 Synchro Vinted…")
    res = await run(autopilot.cycle)
    lines = []
    for part, shops in res.items():
        for shop, r in (shops or {}).items():
            lines.append(f"{part} · {shop} : {r}")
    await reply(update, "✅ Synchro terminée\n" + ("\n".join(lines) if lines else "Aucune boutique connectée à Vinted."))


@authorized
async def cmd_messages(update, ctx):
    await _messages(update)


async def _messages(update):
    convs = db.list_conversations(status="pending", limit=10)
    if not convs:
        return await reply(update, "Aucun message en attente 👌")
    for c in convs:
        await reply(update, f"💬 {c.get('buyer') or 'acheteur'} : « {(c.get('last_buyer_message') or '')[:300]} »\n\n"
                            f"✍️ {c.get('draft') or '(pas de brouillon)'}",
                    [[("✅ Envoyer", f"rs:{c['id']}"), ("✏️ Modifier", f"re:{c['id']}"), ("❌ Ignorer", f"ri:{c['id']}")]])


@authorized
async def cmd_activite(update, ctx):
    ev = db.list_events(15)
    await reply(update, "<b>Dernières actions</b>\n" + ("\n".join(
        f"{e['created_at'][5:16]} · {e['message'][:120]}" for e in ev) or "Rien pour l'instant."))


# ---------------- boutons ----------------
@authorized
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, _, arg = q.data.partition(":")

    if action == "m":
        handlers = {"stats": lambda: reply(update, stats_text()), "order": lambda: _to_order(update),
                    "msgs": lambda: _messages(update), "ready": lambda: _list_products(update, "ready"),
                    "draft": lambda: _list_products(update, "draft"),
                    "published": lambda: _list_products(update, "published"),
                    "auto": lambda: reply(update, auto_text(), auto_buttons()), "sync": lambda: _sync(update),
                    "niches": lambda: _niches(update), "shops": lambda: _shops(update)}
        return await handlers[arg]()
    if action == "set":
        cur = db.get_setting(arg)
        new = CYCLE.get(cur, "on") if arg in ("publish_mode", "reply_mode", "offer_mode") else ("off" if cur == "on" else "on")
        db.set_setting(arg, new)
        return await q.edit_message_text(auto_text(), parse_mode=ParseMode.HTML, reply_markup=kb(auto_buttons()))
    if action == "win":
        return await _winners(update, arg)
    if action == "ss":
        sid, status = arg.split(":")
        db.update_sale(int(sid), status=status)
        return await reply(update, {"ordered": "🛒 Commandée", "received": "📦 Reçue", "shipped": "🚚 Envoyée"}[status]
                           + f" : vente #{sid}")
    if action in ("rs", "ri", "re"):
        cid = int(arg)
        if action == "rs":
            await run(responder.send_pending, cid)
            return await q.edit_message_reply_markup(kb([[("✅ Envoyé", "noop:0")]]))
        if action == "ri":
            db.upsert_conversation(cid, status="ignored")
            return await q.edit_message_reply_markup(kb([[("Ignoré", "noop:0")]]))
        ctx.user_data["edit_reply"] = cid
        return await reply(update, "✏️ Écris la réponse à envoyer (elle partira telle quelle) :")
    if action == "noop":
        return

    pid = int(arg)
    p = db.get_product(pid)
    if not p:
        return await reply(update, "Produit introuvable")
    if action == "view":
        return await send_product(update, p, with_photos=True)
    if action == "prep":
        return await do_prepare(update, pid)
    if action == "photos":
        return await do_photos(update, pid)
    if action == "redo":
        db.update_product(pid, photos=[], approval_sent_at=None)
        return await do_photos(update, pid)
    if action == "fiche":
        return await do_fiche(update, pid)
    if action == "pub":
        return await do_publish(update, pid)
    if action == "skip":
        db.update_product(pid, status="draft", approval_sent_at=None)
        return await reply(update, f"Produit #{pid} remis en brouillon.")
    if action == "sold":
        return await _record_sale(update, pid, p.get("price") or 0)
    if action == "arch":
        db.update_product(pid, status="archived")
        return await reply(update, f"🗑 Produit #{pid} archivé.")


# ---------------- texte libre ----------------
@authorized
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text
    cid = ctx.user_data.pop("edit_reply", None)
    if cid:
        await run(responder.send_pending, cid, text)
        return await reply(update, "✅ Réponse envoyée sur Vinted.")
    if not config.OPENAI_API_KEY:
        return await reply(update, "Tape /menu ou /aide.")
    await update.effective_chat.send_action("typing")
    answer, ctx.user_data["history"] = await run(assistant.ask, text, ctx.user_data.get("history"))
    await update.effective_message.reply_text(answer[:4000], disable_web_page_preview=True)


# ---------------- tâches planifiées ----------------
def _job(fn):
    async def job(ctx: ContextTypes.DEFAULT_TYPE):
        if not autopilot.enabled():
            return
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("tâche %s", fn.__name__)
    job.__name__ = fn.__name__
    return job


async def job_daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
        await ctx.bot.send_message(chat_id, stats_text(), parse_mode=ParseMode.HTML)


def schedule(app: Application):
    jq = app.job_queue
    if not jq:
        log.warning("job-queue absent : pip install 'python-telegram-bot[job-queue]'")
        return
    jq.run_repeating(_job(autopilot.run_sync), interval=60 * int(db.get_setting("sync_minutes") or 15), first=30)
    jq.run_repeating(_job(autopilot.run_inbox), interval=60 * int(db.get_setting("inbox_minutes") or 10), first=60)
    jq.run_repeating(_job(autopilot.run_publish), interval=15 * 60, first=90)
    jq.run_daily(_job(autopilot.run_prepare_night), time(2, 0, tzinfo=TZ))
    jq.run_daily(_job(autopilot.run_price_drops), time(3, 0, tzinfo=TZ))
    jq.run_daily(_job(autopilot.run_market_scan), time(4, 0, tzinfo=TZ))
    if config.TELEGRAM_ALLOWED_CHAT_IDS:
        h, m = (int(x) for x in config.DAILY_REPORT_TIME.split(":"))
        jq.run_daily(job_daily_report, time(h, m, tzinfo=TZ))


def build_app() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env")
    db.init()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    commands = {
        "start": cmd_start, "aide": cmd_start, "help": cmd_start, "menu": cmd_menu, "ajouter": cmd_ajouter,
        "produits": cmd_produits, "voir": cmd_voir, "photos": cmd_photos, "fiche": cmd_fiche,
        "preparer": cmd_preparer, "publier": cmd_publier, "publie": cmd_publie, "prix": cmd_prix,
        "offre": cmd_offre, "vendu": cmd_vendu, "acommander": cmd_acommander, "stats": cmd_stats,
        "boutiques": cmd_boutiques, "niches": cmd_niches, "gagnants": cmd_gagnants, "auto": cmd_auto,
        "sync": cmd_sync, "messages": cmd_messages, "vinted": cmd_vinted, "activite": cmd_activite,
    }
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    schedule(app)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build_app().run_polling()


if __name__ == "__main__":
    main()
