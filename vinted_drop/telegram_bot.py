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
/preparer &lt;id&gt; · /photos &lt;id&gt; [local|gemini|openai] · /fiche &lt;id&gt;
/studio [id] — réglages photo, ou retouches d'un produit (⭐ couverture, 🔄 refaire, 🗑 supprimer)
/couts — dépenses IA
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
/menu · /stats · /finances [7d|30d…] · /boutiques · /niches · /gagnants [niche]
/commandes · /parametres · /stop (arrêt d'urgence) · /reprendre
"""


_ADMIN_CACHE: dict = {}


async def _is_channel_admin(bot, chat_id, user_id) -> bool:
    key = (chat_id, user_id)
    if key not in _ADMIN_CACHE:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            _ADMIN_CACHE[key] = member.status in ("administrator", "creator")
        except Exception:
            _ADMIN_CACHE[key] = False
    return _ADMIN_CACHE[key]


def authorized(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        allowed = config.telegram_chats()
        if not allowed:
            # bot fermé par défaut : on indique seulement l'id à mettre dans .env
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"🔒 Bot pas encore configuré. Id de cette discussion : {chat.id}\n"
                    "Ajoute-le dans TELEGRAM_ALLOWED_CHAT_IDS (fichier .env) puis relance le bot.")
            elif update.callback_query:
                await update.callback_query.answer("🔒 Bot non configuré", show_alert=True)
            return
        ok = chat.id in allowed
        # Dans un canal, n'importe quel abonné peut toucher un bouton : on vérifie qu'il est admin
        if ok and update.callback_query and chat.type == "channel":
            ok = await _is_channel_admin(ctx.bot, chat.id, update.callback_query.from_user.id)
        if not ok:
            if update.callback_query:
                await update.callback_query.answer("⛔ Réservé aux administrateurs", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(
                    f"⛔ Accès refusé. Id de cette discussion : {chat.id} "
                    "(ajoute-le dans TELEGRAM_ALLOWED_CHAT_IDS ou TELEGRAM_CHANNEL_ID)")
            return
        try:
            return await func(update, ctx)
        except Exception as e:
            log.exception("Erreur")
            if update.effective_message:
                await update.effective_message.reply_text(f"❌ {e}")
    return wrapper


def store(ctx) -> dict:
    """Mémoire de la discussion (marche en privé, en groupe et dans un canal)."""
    return ctx.chat_data if ctx.chat_data is not None else {}


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
    return [[("📊 Dashboard", "m:dashboard"), ("💰 Finances", "m:finance")],
            [("🛍 Boutiques", "m:shops"), ("📦 Produits", "m:products")],
            [("📸 Studio photo", "m:studio"), ("🔎 Niches", "m:niches")],
            [("🏆 Produits gagnants", "m:winners"), ("⚙️ Pilote automatique", "m:auto")],
            [("📈 Statistiques", "m:stats"), ("🛠 Paramètres", "m:settings")],
            [("🛒 Commandes à passer", "m:order"), ("💬 Messages", "m:msgs")]]


def dashboard_text():
    from . import finance
    t = finance.summary(*db.period_range("today"))
    w = finance.summary(*db.period_range("7d"))
    oc = db.order_counts()
    lines = ["📊 <b>Tableau de bord</b>",
             f"Aujourd'hui : {t['sales']} ventes · bénéfice net <b>{t['net_profit']:.2f} €</b>",
             f"7 jours : {w['sales']} ventes · CA {w['revenue']:.2f} € · bénéfice net <b>{w['net_profit']:.2f} €</b> · marge {w['net_margin_pct']} %",
             f"En ligne : {w['products']['published']} · prêts : {w['products']['ready']} · brouillons : {w['products']['draft']}",
             f"Commandes à passer : {oc.get('to_buy', 0)}" + (f" · ⚠️ problèmes : {oc['problem']}" if oc.get("problem") else "")]
    if db.get_setting("emergency_stop") == "on":
        lines.append("🛑 Arrêt d'urgence activé")
    elif db.get_setting("simulation") == "on":
        lines.append("🧪 Mode simulation (rien n'est envoyé à Vinted)")
    return "\n".join(lines)


def finance_text(period="30d"):
    from . import finance
    f = finance.summary(*db.period_range(period))
    return (f"💰 <b>Finances · {db.PERIODS.get(period, period)}</b>\n"
            f"CA {f['revenue']:.2f} € · {f['sales']} ventes · panier {f['avg_basket']:.2f} €\n"
            f"Bénéfice des ventes {f['profit']:.2f} €\n− coûts IA {f['ai_costs']:.2f} € · − dépenses {f['expenses']:.2f} €\n"
            f"= <b>bénéfice net {f['net_profit']:.2f} €</b> (marge {f['net_margin_pct']} %)\n"
            f"Achats fournisseurs {f['supplier_spend']:.2f} € · capital engagé {f['listed_value']:.2f} € · "
            f"en transit {f['in_transit_value']:.2f} €")


def finance_buttons():
    return [[(l, f"fin:{k}") for k, l in (("today", "Jour"), ("7d", "7 j"), ("30d", "30 j"), ("90d", "90 j"), ("year", "Année"))],
            [("📥 Export Excel", f"{config.DASHBOARD_URL}/finances/export.xlsx")]] if config.DASHBOARD_URL.startswith("http") else \
           [[(l, f"fin:{k}") for k, l in (("today", "Jour"), ("7d", "7 j"), ("30d", "30 j"), ("90d", "90 j"), ("year", "Année"))]]


def settings_text():
    s = db.all_settings()
    return ("🛠 <b>Paramètres</b>\n"
            f"Bénéfice minimum : {s['min_profit']} € · style des annonces : {s['listing_style']}\n"
            f"Simulation : {s['simulation']} · arrêt d'urgence : {s['emergency_stop']}\n"
            f"Budget IA / jour : {s['daily_budget']} € · fournisseur photo : {s['photo_provider']}\n"
            f"Réglages complets : {config.DASHBOARD_URL}/parametres")


def settings_buttons():
    s = db.all_settings()
    return [[(f"🧪 Simulation : {s['simulation']}", "tg:simulation")],
            [("🛑 Arrêt d'urgence" if s["emergency_stop"] != "on" else "▶️ Lever l'arrêt d'urgence", "tg:emergency")],
            [("⚙️ Pilote automatique", "m:auto"), ("📸 Studio", "m:studio")]]


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
            raise ValueError("Ajoute une légende à la photo : prix_achat [lien] [boutique] [taille]\n"
                             "Exemple : 14.90 https://fr.shein.com/... Sophie M")
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


async def do_photos(update, pid, provider=None):
    label = {"gemini": "Gemini", "openai": "OpenAI", "local": "ton PC (gratuit)"}.get(provider, "auto")
    await reply(update, f"🎨 Studio photo · produit #{pid} · {label}\nNettoyage, prises de vue en parallèle et "
                        "contrôle qualité… (1 à 3 min)")
    await run(photos.generate_product_photos, pid, None, provider)
    await send_studio(update, pid)


def studio_text(p):
    lines = [f"📸 <b>Photos du produit #{p['id']}</b> · coût IA {db.product_cost(p['id']):.2f} €"]
    for g in photos.summary(p):
        score = f" · fidélité <b>{g['fidelity']}/10</b>" if g.get("fidelity") is not None else ""
        lines.append(f"{g['index'] + 1}. {g['emoji']} {g['label']}{' ⭐' if g['cover'] else ''} · {g.get('provider', '?')}{score}"
                     + (f"\n   ⚠️ {', '.join(g['issues'][:3])}" if g.get("issues") else ""))
    return "\n".join(lines)


def studio_buttons(p):
    rows = []
    for g in photos.summary(p):
        n, i = g["index"] + 1, g["index"]
        row = [] if g["cover"] else [(f"⭐ {n}", f"pc:{p['id']}:{i}")]
        row += [(f"🔄 {n}", f"pr:{p['id']}:{i}"), (f"🗑 {n}", f"pd:{p['id']}:{i}")]
        rows.append(row)
    rows.append([("➕ PC gratuit", f"ph:{p['id']}:local"), ("➕ Gemini", f"ph:{p['id']}:gemini"),
                 ("➕ OpenAI", f"ph:{p['id']}:openai")])
    if p["status"] in ("draft", "ready"):
        rows.append([("📝 Fiche", f"fiche:{p['id']}"), ("✅ Publier", f"pub:{p['id']}")])
    return rows


async def send_studio(update, pid, with_album=True):
    p = db.get_product(pid)
    if with_album:
        await _send_photos(update.effective_message, p)
    await reply(update, studio_text(p), studio_buttons(p))


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
    pid = _int(ctx.args, 0)
    provider = ctx.args[1].lower() if len(ctx.args) > 1 and ctx.args[1].lower() in ("gemini", "openai", "local") else None
    if not db.get_product(pid):
        raise ValueError("Produit introuvable")
    if provider or len(ctx.args) > 1:
        return await do_photos(update, pid, provider)
    await reply(update, f"Avec quelle IA faire les photos du produit #{pid} ?",
                [[("🤖 Auto", f"ph:{pid}:auto"), ("🖥 PC gratuit", f"ph:{pid}:local")],
                 [("✨ Gemini", f"ph:{pid}:gemini"), ("🎨 OpenAI", f"ph:{pid}:openai")]])


@authorized
async def cmd_studio(update, ctx):
    if ctx.args:
        return await send_studio(update, _int(ctx.args, 0))
    from . import comfyui
    from .image_providers import available
    s, c = db.all_settings(), db.cost_stats(30)
    keys = available()
    local = await run(comfyui.status, 3)
    pc = (f"✅ PC : {local['gpu']} ({local['vram_gb']} Go)" if local["ok"]
          else ("❌ PC éteint ou ComfyUI fermé" if "local" in keys else "⚪ PC non configuré"))
    await reply(update,
                "📸 <b>Studio photo</b>\n"
                f"{pc}\nClés : {'✅ Gemini' if 'gemini' in keys else '❌ Gemini'} · {'✅ OpenAI' if 'openai' in keys else '❌ OpenAI'}\n"
                f"Aujourd'hui : {db.cost_today():.2f} € / budget {s['daily_budget']} €\n"
                f"Fournisseur : <b>{s['photo_provider']}</b> · jeu : <b>{s['photo_set']}</b> · nettoyage : {s['photo_clean']} · "
                f"contrôle qualité : {s['photo_qa']}\n30 jours : {c['images']} images · {c['eur']:.2f} €\n\n"
                "/studio &lt;id&gt; pour retoucher les photos d'un produit",
                [[(f"Fournisseur : {s['photo_provider']}", "st:photo_provider"), (f"Jeu : {s['photo_set']}", "st:photo_set")],
                 [(f"Nettoyage : {s['photo_clean']}", "st:photo_clean"), (f"Contrôle : {s['photo_qa']}", "st:photo_qa")]])


@authorized
async def cmd_finances(update, ctx):
    period = ctx.args[0] if ctx.args and ctx.args[0] in db.PERIODS else "30d"
    await reply(update, finance_text(period), finance_buttons())


@authorized
async def cmd_parametres(update, ctx):
    await reply(update, settings_text(), settings_buttons())


@authorized
async def cmd_stop(update, ctx):
    db.set_setting("emergency_stop", "on")
    db.log_event("security", "ARRÊT D'URGENCE activé (Telegram)", level="warning")
    await reply(update, "🛑 <b>Arrêt d'urgence activé</b> : plus aucune action automatique. /reprendre pour relancer.")


@authorized
async def cmd_reprendre(update, ctx):
    db.set_setting("emergency_stop", "off")
    db.log_event("security", "Arrêt d'urgence levé (Telegram)", level="warning")
    await reply(update, "▶️ Pilote automatique relancé.")


@authorized
async def cmd_couts(update, ctx):
    c7, c30 = db.cost_stats(7), db.cost_stats(30)
    prof = db.stats(30)["profit"]
    await reply(update, f"💰 <b>Coûts IA</b>\n7 jours : {c7['images']} images · {c7['eur']:.2f} €\n"
                        f"30 jours : {c30['images']} images · {c30['eur']:.2f} €\n"
                        + "\n".join(f"   {b['provider']} : {b['n']} images · {b['eur']:.2f} €" for b in c30["by_provider"])
                        + f"\n\nBénéfice 30 jours : {prof:.2f} € → l'IA coûte "
                        f"{(100 * c30['eur'] / prof) if prof else 0:.1f} % du bénéfice")


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
    items = [o for o in db.list_orders(open_only=True, limit=30) if o["status"] in ("to_buy", "problem")]
    if not items:
        return await reply(update, "Rien à commander 👌")
    for o in items[:10]:
        sale = db.get_sale(o["sale_id"]) if o.get("sale_id") else None
        await reply(update, f"🛒 Commande #{o['id']} · <b>{db.ORDER_LABELS[o['status']]}</b>\n{(o.get('title') or '?')[:60]}\n"
                            f"Taille {o.get('size') or '?'} · {o.get('shop_name') or ''} · vendu {o.get('sale_price') or 0:.2f} €",
                    notify.sale_buttons(sale) if sale else None)


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
    min_profit = float(db.get_setting("min_profit") or 30)
    lines = [f"🔎 <b>Meilleures niches</b> (≥ {min_profit:.0f} € de bénéfice estimé)"]
    rows = []
    for n in niches.ranked(profitable_only=True)[:8]:
        pot = n["potential"]
        live = n.get("live") or {}
        lines.append(f"{'🔥' if n['in_season'] else '⏳'} <b>{n['name']}</b> — {n['score']}/100 · ≈ {pot['est_profit']:.0f} €/vente"
                     f" · confiance {pot['confidence']}" + (f" · médiane Vinted {live['median']:.0f} €" if live.get("median") else "")
                     + f"\n   <i>{n['why'][:160]}</i>")
        rows.append([(f"🏆 {n['name'][:30]}", f"win:{n['id']}")])
    await reply(update, "\n".join(lines), rows)


async def _unsold(update):
    items = db.unsold_products(14, 15)
    if not items:
        return await reply(update, "Aucun invendu de plus de 14 jours 👌")
    await reply(update, "💤 <b>Invendus depuis 14 jours</b>",
                [[(f"#{p['id']} {(p.get('title') or '?')[:26]} · {p.get('price') or '-'} €", f"view:{p['id']}")] for p in items])


@authorized
async def cmd_gagnants(update, ctx):
    await _winners(update, ctx.args[0] if ctx.args else None)


async def _winners(update, niche_id):
    items = niches.winning_products(niche_id)[:15]
    if not items:
        return await reply(update, "Aucun produit pour cette niche.")
    lines = ["🏆 <b>Produits gagnants</b> (score /100 : preuves de vente, saison, marge)"]
    for i in items:
        proof = (f"{i['rating']}★ · {i['reviews']:,} avis".replace(",", " ") if i.get("reviews")
                 else i.get("sold") or "")
        cost = f"{i['cost']:.2f} €" if i.get("cost") else "prix à vérifier"
        lines.append(f'<b>{i["score"]}</b> · <a href="{i["url"]}">{i["title"]}</a>\n   {i["supplier"]} · {cost}'
                     + (f" · {proof}" if proof else ""))
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
        handlers = {"dashboard": lambda: reply(update, dashboard_text(), [[("🔄 Actualiser", "m:dashboard"), ("💰 Finances", "m:finance")],
                                                                            [("🌐 Ouvrir le dashboard", config.DASHBOARD_URL)] if config.DASHBOARD_URL.startswith("https") else [("📈 Stats", "m:stats")]]),
                    "finance": lambda: reply(update, finance_text(), finance_buttons()),
                    "products": lambda: reply(update, "📦 <b>Produits</b>", [[("📝 Brouillons", "m:draft"), ("📤 Prêts", "m:ready")],
                                                                           [("🟢 En ligne", "m:published"), ("💤 Invendus", "m:unsold")]]),
                    "unsold": lambda: _unsold(update),
                    "studio": lambda: cmd_studio(update, ctx),
                    "winners": lambda: _winners(update, None),
                    "settings": lambda: reply(update, settings_text(), settings_buttons()),
                    "stats": lambda: reply(update, stats_text()), "order": lambda: _to_order(update),
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
    if action == "ss":  # anciens boutons (1.x)
        sid, status = arg.split(":")
        db.update_sale(int(sid), status=status)
        return await reply(update, f"Vente #{sid} mise à jour")
    if action == "os":  # commande fournisseur liée à une vente
        sid, status = arg.split(":")
        o = db.order_for_sale(int(sid))
        if not o:
            return await reply(update, "Commande introuvable")
        db.update_order(o["id"], status=status, note="depuis Telegram")
        db.log_event("telegram", f"Commande #{o['id']} : {db.ORDER_LABELS[status]}", product_id=o.get("product_id"))
        return await reply(update, f"📦 Commande #{o['id']} : <b>{db.ORDER_LABELS[status]}</b>")
    if action == "fin":
        return await q.edit_message_text(finance_text(arg), parse_mode=ParseMode.HTML, reply_markup=kb(finance_buttons()))
    if action == "opp":
        from . import market
        market.toggle_favorite(int(arg))
        return await reply(update, "⭐ Opportunité suivie (ou retirée des favoris)")
    if action == "tg":
        if arg == "simulation":
            db.set_setting("simulation", "off" if db.get_setting("simulation") == "on" else "on")
        elif arg == "emergency":
            on = db.get_setting("emergency_stop") != "on"
            db.set_setting("emergency_stop", "on" if on else "off")
            db.log_event("security", ("ARRÊT D'URGENCE activé" if on else "Arrêt d'urgence levé") + " (Telegram)", level="warning")
        return await q.edit_message_text(settings_text(), parse_mode=ParseMode.HTML, reply_markup=kb(settings_buttons()))
    if action == "cf":  # confirmation d'une action demandée à l'assistant
        mem = store(ctx)
        item = (mem.get("pending") or {}).pop(arg, None)
        if not item:
            return await reply(update, "Cette demande a expiré, redemande-la.")
        await q.edit_message_reply_markup(kb([[("⏳ En cours…", "noop:0")]]))
        try:
            result = await run(assistant.execute, item["name"], item["args"])
        except Exception as e:
            return await reply(update, f"❌ {e}")
        db.log_event("telegram", f"Action confirmée : {item['summary']}")
        await q.edit_message_reply_markup(kb([[("✅ Fait", "noop:0")]]))
        return await reply(update, "✅ Fait : " + item["summary"] + (f"\n<code>{str(result)[:300]}</code>" if result else ""))
    if action == "cx":
        (store(ctx).get("pending") or {}).pop(arg, None)
        return await q.edit_message_reply_markup(kb([[("Annulé", "noop:0")]]))
    if action in ("rs", "ri", "re"):
        cid = int(arg)
        if action == "rs":
            await run(responder.send_pending, cid)
            return await q.edit_message_reply_markup(kb([[("✅ Envoyé", "noop:0")]]))
        if action == "ri":
            db.upsert_conversation(cid, status="ignored")
            return await q.edit_message_reply_markup(kb([[("Ignoré", "noop:0")]]))
        store(ctx)["edit_reply"] = cid
        return await reply(update, "✏️ Écris la réponse à envoyer (elle partira telle quelle) :")
    if action == "noop":
        return
    if action == "st":
        cycles = {"photo_provider": ["auto", "local", "gemini", "openai"], "photo_set": ["standard", "complet"],
                  "photo_clean": ["auto", "on", "off"], "photo_qa": ["on", "off"]}
        vals = cycles[arg]
        cur = db.get_setting(arg)
        db.set_setting(arg, vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else vals[0])
        return await cmd_studio(update, ctx)
    if action == "ph":
        pid_s, _, prov = arg.partition(":")
        return await do_photos(update, int(pid_s), None if prov == "auto" else prov)
    if action in ("pc", "pr", "pd"):
        pid_s, _, idx = arg.partition(":")
        pid, idx = int(pid_s), int(idx)
        p = db.get_product(pid)
        if not p or idx >= len(p["photos"]):
            return await reply(update, "Photo introuvable (la liste a changé), tape /studio " + pid_s)
        path = p["photos"][idx]
        if action == "pc":
            photos.set_cover(pid, path)
        elif action == "pd":
            photos.delete_photo(pid, path)
        else:
            await reply(update, f"🔄 Je refais la photo {idx + 1}…")
            await run(photos.regenerate_photo, pid, path)
            return await send_studio(update, pid)
        p = db.get_product(pid)
        return await q.edit_message_text(studio_text(p), parse_mode=ParseMode.HTML, reply_markup=kb(studio_buttons(p)))

    pid = int(arg)
    p = db.get_product(pid)
    if not p:
        return await reply(update, "Produit introuvable")
    if action == "view":
        return await send_product(update, p, with_photos=True)
    if action == "prep":
        return await do_prepare(update, pid)
    if action == "photos":
        return await reply(update, f"Avec quelle IA faire les photos du produit #{pid} ?",
                           [[("🤖 Auto", f"ph:{pid}:auto"), ("🖥 PC gratuit", f"ph:{pid}:local")],
                 [("✨ Gemini", f"ph:{pid}:gemini"), ("🎨 OpenAI", f"ph:{pid}:openai")]])
    if action == "redo":
        db.update_product(pid, approval_sent_at=None)
        return await send_studio(update, pid)
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
    mem = store(ctx)
    cid = mem.pop("edit_reply", None)
    if cid:
        await run(responder.send_pending, cid, text)
        return await reply(update, "✅ Réponse envoyée sur Vinted.")
    if not config.OPENAI_API_KEY:
        return await reply(update, "Tape /menu ou /aide.")
    await update.effective_chat.send_action("typing")
    answer, mem["history"], pending = await run(assistant.ask, text, mem.get("history"))
    await update.effective_message.reply_text(answer[:4000], disable_web_page_preview=True)
    import secrets
    for item in pending:  # actions importantes : confirmation explicite
        token = secrets.token_hex(4)
        mem.setdefault("pending", {})[token] = item
        await reply(update, f"⚠️ <b>Confirmer ?</b>\n{item['summary']}", [[("✅ Confirmer", f"cf:{token}"), ("❌ Annuler", f"cx:{token}")]])


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
    for chat_id in config.notify_targets():
        await ctx.bot.send_message(chat_id, stats_text(), parse_mode=ParseMode.HTML)


_TICK_LOCK = asyncio.Lock()


async def job_tick(ctx: ContextTypes.DEFAULT_TYPE):
    """Chaque minute : lance les tâches du planning arrivées à échéance (réglables dans Pilote auto)."""
    if _TICK_LOCK.locked():
        return  # le passage précédent tourne encore : pas d'exécution concurrente
    async with _TICK_LOCK:
        try:
            await asyncio.to_thread(autopilot.tick)
        except Exception:
            log.exception("planning")


def schedule(app: Application):
    jq = app.job_queue
    if not jq:
        log.warning("job-queue absent : pip install 'python-telegram-bot[job-queue]'")
        return
    jq.run_repeating(job_tick, interval=60, first=20, name="autopilot-tick")


def build_app() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env")
    db.init()
    from . import tasks
    tasks.recover()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    commands = {
        "start": cmd_start, "aide": cmd_start, "help": cmd_start, "menu": cmd_menu, "ajouter": cmd_ajouter,
        "produits": cmd_produits, "voir": cmd_voir, "photos": cmd_photos, "fiche": cmd_fiche,
        "preparer": cmd_preparer, "publier": cmd_publier, "publie": cmd_publie, "prix": cmd_prix,
        "offre": cmd_offre, "vendu": cmd_vendu, "acommander": cmd_acommander, "stats": cmd_stats,
        "boutiques": cmd_boutiques, "niches": cmd_niches, "gagnants": cmd_gagnants, "auto": cmd_auto,
        "sync": cmd_sync, "messages": cmd_messages, "vinted": cmd_vinted, "activite": cmd_activite,
        "studio": cmd_studio, "couts": cmd_couts, "finances": cmd_finances, "parametres": cmd_parametres,
        "commandes": cmd_acommander, "stop": cmd_stop, "reprendre": cmd_reprendre,
    }
    # messages privés, groupes ET publications du canal
    where = filters.UpdateType.MESSAGES | filters.UpdateType.CHANNEL_POSTS
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, fn, filters=where))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(where & filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(where & filters.TEXT & ~filters.COMMAND, on_text))
    schedule(app)
    return app


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .security import install_log_masking
    install_log_masking()
    # httpx écrit chaque URL Telegram, qui contient le token du bot : on le fait taire
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def welcome(app: Application):
    """Message de démarrage dans le canal / la discussion de notification."""
    me = await app.bot.get_me()
    log.info("Bot @%s connecté", me.username)
    for chat_id in config.notify_targets():
        try:
            await app.bot.send_message(chat_id, f"🤖 Vinted Drop démarré (@{me.username}). Tape /menu")
        except Exception as e:
            hint = ("le bot doit être ADMINISTRATEUR du canal, avec le droit de publier"
                    if str(chat_id).startswith("-100") else
                    "un id de canal commence par -100 : vérifie TELEGRAM_CHANNEL_ID dans .env")
            log.warning("Impossible d'écrire dans %s : %s → %s", chat_id, e, hint)


def main():
    setup_logging()
    app = build_app()
    app.post_init = welcome
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def run_with_dashboard(server, url=None, open_browser=False):
    """Bot + dashboard dans la même boucle asyncio (évite les conflits de threads sous Windows).
    Le dashboard démarre en premier : il reste accessible même si Telegram ne répond pas."""
    setup_logging()
    serve_task = asyncio.create_task(server.serve())   # s'arrête avec Ctrl+C
    for _ in range(100):
        if getattr(server, "started", False) or serve_task.done():
            break
        await asyncio.sleep(0.1)
    if url and not serve_task.done():
        print(f"\n✅ Dashboard prêt : {url}\n   (ouvre cette adresse dans ton navigateur, pas 0.0.0.0)\n")
        if open_browser:
            import webbrowser
            webbrowser.open(url)
    from telegram.error import InvalidToken, NetworkError
    try:
        app = build_app()
        async with app:
            await app.start()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            await welcome(app)
            try:
                await serve_task
            finally:
                await app.updater.stop()
                await app.stop()
    except (InvalidToken, NetworkError, SystemExit) as e:
        # le bot ne démarre pas, mais le dashboard reste ouvert
        msg = ("token Telegram refusé : vérifie TELEGRAM_BOT_TOKEN dans .env" if isinstance(e, InvalidToken)
               else f"Telegram injoignable ({e})" if isinstance(e, NetworkError) else str(e))
        print(f"\n❌ Bot Telegram non démarré : {msg}\n   Le dashboard reste accessible. Corrige puis relance.\n")
        await serve_task
    finally:
        if not serve_task.done():
            server.should_exit = True
            await serve_task


if __name__ == "__main__":
    main()
