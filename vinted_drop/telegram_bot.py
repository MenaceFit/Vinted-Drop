"""Bot Telegram : ajoute des produits, génère photos et fiches, enregistre les ventes,
envoie le lien fournisseur à commander et fait la compta."""
import asyncio
import logging
from datetime import time
from functools import wraps
from zoneinfo import ZoneInfo

from telegram import InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import config, db, listing, niches, notify, photos, pricing, supplier

log = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Paris")

HELP = """<b>Vinted Drop</b> 🤖

<b>Produits</b>
/ajouter &lt;lien&gt; &lt;prix_achat&gt; [boutique] [taille] — ajoute un produit
📷 Ou envoie une photo du produit avec en légende : <code>prix lien [boutique]</code>
/produits [draft|ready|published|sold] — liste
/voir &lt;id&gt; — détail d'un produit
/photos &lt;id&gt; — génère les photos IA (portée + à plat + détail)
/fiche &lt;id&gt; — génère titre, description et prix
/preparer &lt;id&gt; — photos + fiche d'un coup
/publie &lt;id&gt; [lien vinted] — marque comme publié
/prix &lt;prix_achat&gt; — calcule le prix de vente conseillé
/offre &lt;id&gt; &lt;montant&gt; — accepter ou contre-proposer ?

<b>Ventes</b>
/vendu &lt;id&gt; &lt;prix&gt; [acheteur] — enregistre une vente
/acommander — ventes pas encore commandées
/commande &lt;vente&gt; · /recu &lt;vente&gt; · /envoye &lt;vente&gt;

<b>Pilotage</b>
/stats — CA, bénéfice, marge (jour / 7j / 30j / total)
/boutiques — performances par boutique
/niches — meilleures niches du moment
/gagnants [niche] — liens fournisseurs des produits gagnants
"""


def authorized(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if config.TELEGRAM_ALLOWED_CHAT_IDS and chat_id not in config.TELEGRAM_ALLOWED_CHAT_IDS:
            await update.effective_message.reply_text("⛔ Accès refusé.")
            return
        try:
            return await func(update, ctx)
        except Exception as e:  # on renvoie l'erreur dans Telegram pour savoir quoi corriger
            log.exception("Erreur commande")
            await update.effective_message.reply_text(f"❌ {e}")
    return wrapper


async def reply(update: Update, text: str):
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML,
                                              disable_web_page_preview=True)


def _int(args, i, name="id"):
    try:
        return int(args[i])
    except (IndexError, ValueError):
        raise ValueError(f"Il manque le {name}. Tape /aide")


def _float(s):
    return float(str(s).replace(",", ".").replace("€", ""))


def product_card(p: dict) -> str:
    lines = [f"<b>#{p['id']}</b> · {p['status']} · {p.get('shop_name') or 'sans boutique'}",
             f"<b>{p.get('title') or p.get('supplier_title') or 'Sans titre'}</b>",
             f"Achat {p['cost']:.2f} €" + (f" · Prix {p['price']:.2f} € (mini {p['min_price']:.2f} €)"
                                           if p.get("price") else ""),
             f"Photos : {len(p.get('photos') or [])}"]
    if p.get("supplier_url"):
        lines.append(f'<a href="{p["supplier_url"]}">Lien fournisseur</a>')
    return "\n".join(lines)


# ---------------- commandes ----------------
@authorized
async def cmd_start(update: Update, ctx):
    await reply(update, HELP + f"\nTon chat id : <code>{update.effective_chat.id}</code>")


@authorized
async def cmd_ajouter(update: Update, ctx):
    args = ctx.args
    if len(args) < 2:
        raise ValueError("Usage : /ajouter <lien> <prix_achat> [boutique] [taille]")
    url, cost = args[0], _float(args[1])
    shop = db.find_shop(args[2]) if len(args) > 2 else None
    size = args[3] if len(args) > 3 else None
    pid = await asyncio.to_thread(_create_from_url, url, cost, shop, size)
    p = db.get_product(pid)
    msg = product_card(p)
    if not p.get("supplier_image"):
        msg += "\n\n⚠️ Image introuvable sur le site : réponds avec la photo du produit et la légende " \
               f"<code>/image {pid}</code>"
    await reply(update, "✅ Produit ajouté\n" + msg + f"\n\n→ /preparer {pid}")


def _create_from_url(url, cost, shop, size):
    info = supplier.fetch_info(url)
    pid = db.add_product(cost=cost, supplier_url=url, supplier=info["supplier"],
                         supplier_title=info["title"], shop_id=shop["id"] if shop else None,
                         niche=shop["niche"] if shop else None, size=size)
    if info["image_url"]:
        img = supplier.download_image(info["image_url"], pid)
        if img:
            db.update_product(pid, supplier_image=photos.rel(img))
    return pid


@authorized
async def on_photo(update: Update, ctx):
    """Photo + légende "prix lien [boutique]" -> nouveau produit ; "/image <id>" -> remplace l'image."""
    msg = update.effective_message
    caption = (msg.caption or "").strip()
    parts = caption.split()
    tg_file = await msg.photo[-1].get_file()

    if parts and parts[0].lower() in ("/image", "image"):
        pid = _int(parts, 1)
        if not db.get_product(pid):
            raise ValueError(f"Produit #{pid} introuvable")
    else:
        if not parts:
            raise ValueError("Ajoute une légende : <prix_achat> [lien] [boutique]")
        cost = _float(parts[0])
        url = next((x for x in parts[1:] if x.startswith("http")), None)
        rest = [x for x in parts[1:] if not x.startswith("http")]
        shop = db.find_shop(rest[0]) if rest else None
        pid = db.add_product(cost=cost, supplier_url=url, supplier=supplier.supplier_name(url) if url else None,
                             shop_id=shop["id"] if shop else None, niche=shop["niche"] if shop else None)
    out = photos.product_dir(pid) / "supplier.jpg"
    await tg_file.download_to_drive(out)
    db.update_product(pid, supplier_image=photos.rel(out))
    await reply(update, "✅ Image enregistrée\n" + product_card(db.get_product(pid)) + f"\n\n→ /preparer {pid}")


@authorized
async def cmd_produits(update: Update, ctx):
    status = ctx.args[0] if ctx.args else None
    items = db.list_products(status=status, limit=25)
    if not items:
        return await reply(update, "Aucun produit.")
    lines = [f"#{p['id']} [{p['status']}] {(p.get('title') or p.get('supplier_title') or '?')[:45]} · "
             f"{p['price'] or '-'} €" for p in items]
    await reply(update, "\n".join(lines))


@authorized
async def cmd_voir(update: Update, ctx):
    p = db.get_product(_int(ctx.args, 0))
    if not p:
        raise ValueError("Produit introuvable")
    text = product_card(p)
    if p.get("description"):
        text += "\n\n" + p["description"][:1500]
    await reply(update, text)
    await _send_photos(update, p)


async def _send_photos(update, p):
    files = [photos.abs_media(x) for x in (p.get("photos") or [])]
    files = [f for f in files if f.exists()][:10]
    if not files:
        return
    media = [InputMediaPhoto(f.read_bytes()) for f in files]
    await update.effective_message.reply_media_group(media)


@authorized
async def cmd_photos(update: Update, ctx):
    pid = _int(ctx.args, 0)
    await reply(update, f"🎨 Génération des photos du produit #{pid}… (1 à 3 min)")
    await asyncio.to_thread(photos.generate_product_photos, pid)
    await _send_photos(update, db.get_product(pid))
    await reply(update, f"✅ Photos prêtes → /fiche {pid}")


@authorized
async def cmd_fiche(update: Update, ctx):
    pid = _int(ctx.args, 0)
    extra = " ".join(ctx.args[1:])
    res = await asyncio.to_thread(listing.generate_listing, pid, extra)
    await reply(update, _listing_text(pid, res))


def _listing_text(pid, res):
    return (f"📝 <b>Fiche #{pid}</b>\n\n<b>Titre</b>\n<code>{res['title']}</code>\n\n"
            f"<b>Description</b>\n<code>{res['description']}</code>\n\n"
            f"Catégorie : {res.get('category')} · Couleur : {res.get('color')} · "
            f"Matière : {res.get('material') or '-'}\n"
            f"💶 Prix affiché <b>{res['price']:.2f} €</b> · offre mini {res['min_price']:.2f} € "
            f"(bénéfice {res['profit_at_min']:.2f} → {res['profit_at_price']:.2f} €)\n\n"
            f"Une fois en ligne → /publie {pid}")


@authorized
async def cmd_preparer(update: Update, ctx):
    pid = _int(ctx.args, 0)
    await reply(update, f"⚙️ Préparation du produit #{pid} (photos + fiche)…")
    res = await asyncio.to_thread(listing.prepare, pid)
    await _send_photos(update, db.get_product(pid))
    await reply(update, _listing_text(pid, res))


@authorized
async def cmd_publie(update: Update, ctx):
    pid = _int(ctx.args, 0)
    fields = {"status": "published"}
    if len(ctx.args) > 1:
        fields["vinted_url"] = ctx.args[1]
    db.update_product(pid, **fields)
    await reply(update, f"🟢 Produit #{pid} en ligne.")


@authorized
async def cmd_prix(update: Update, ctx):
    cost = _float(ctx.args[0]) if ctx.args else None
    if cost is None:
        raise ValueError("Usage : /prix <prix_achat>")
    r = pricing.compute_prices(cost)
    await reply(update, f"Achat {cost:.2f} € → affiche <b>{r['price']:.2f} €</b> (x{r['multiplier']}), "
                        f"accepte jusqu'à {r['min_price']:.2f} €\nBénéfice : {r['profit_at_min']:.2f} → "
                        f"{r['profit_at_price']:.2f} €")


@authorized
async def cmd_offre(update: Update, ctx):
    pid, offer = _int(ctx.args, 0), _float(ctx.args[1]) if len(ctx.args) > 1 else None
    p = db.get_product(pid)
    if not p or offer is None:
        raise ValueError("Usage : /offre <id> <montant>")
    mini = p.get("min_price") or pricing.compute_prices(p["cost"])["min_price"]
    if pricing.accept_offer(offer, mini):
        txt = f"✅ Accepte : bénéfice {pricing.profit(offer, p['cost']):.2f} €"
    else:
        counter = round((offer + (p.get("price") or mini)) / 2)
        counter = max(counter, mini)
        txt = (f"❌ Trop bas (mini {mini:.2f} €). Contre-propose <b>{counter:.2f} €</b> "
               f"(bénéfice {pricing.profit(counter, p['cost']):.2f} €)")
    await reply(update, txt)


@authorized
async def cmd_vendu(update: Update, ctx):
    pid = _int(ctx.args, 0)
    if len(ctx.args) < 2:
        p = db.get_product(pid)
        price = p and p.get("price")
        if not price:
            raise ValueError("Usage : /vendu <id> <prix>")
    else:
        price = _float(ctx.args[1])
    buyer = ctx.args[2] if len(ctx.args) > 2 else None
    sid = db.record_sale(pid, price, buyer)
    await reply(update, notify.sale_message(db.get_sale(sid)))


async def _sale_status(update, ctx, status, label):
    sid = _int(ctx.args, 0, "numéro de vente")
    if not db.get_sale(sid):
        raise ValueError("Vente introuvable")
    db.update_sale(sid, status=status)
    await reply(update, f"{label} Vente #{sid}")


@authorized
async def cmd_commande(update, ctx):
    await _sale_status(update, ctx, "ordered", "🛒 Commandée :")


@authorized
async def cmd_recu(update, ctx):
    await _sale_status(update, ctx, "received", "📦 Reçue :")


@authorized
async def cmd_envoye(update, ctx):
    await _sale_status(update, ctx, "shipped", "🚚 Envoyée :")


@authorized
async def cmd_acommander(update, ctx):
    items = db.list_sales(status="to_order")
    if not items:
        return await reply(update, "Rien à commander 👌")
    lines = [f"#{s['id']} · {(s.get('title') or '?')[:40]} · T.{s.get('size') or '?'} · "
             + (f'<a href="{s["supplier_url"]}">commander</a>' if s.get("supplier_url") else "pas de lien")
             for s in items]
    await reply(update, "🛒 <b>À commander</b>\n" + "\n".join(lines))


def stats_text():
    blocks = [("Aujourd'hui", db.today_stats()), ("7 jours", db.stats(7)),
              ("30 jours", db.stats(30)), ("Total", db.stats())]
    lines = ["📊 <b>Compta</b>"]
    for label, s in blocks:
        lines.append(f"<b>{label}</b> : {s['sales']} ventes · CA {s['revenue']:.2f} € · "
                     f"bénéfice <b>{s['profit']:.2f} €</b> · marge {s['margin_pct']}%")
    pc = db.stats()["products"]
    lines.append(f"\nEn ligne : {pc['published']} · prêts à publier : {pc['ready']} · brouillons : {pc['draft']}")
    todo = len(db.list_sales(status="to_order"))
    if todo:
        lines.append(f"⚠️ {todo} vente(s) à commander → /acommander")
    return "\n".join(lines)


@authorized
async def cmd_stats(update, ctx):
    await reply(update, stats_text())


@authorized
async def cmd_boutiques(update, ctx):
    rows = db.shop_breakdown()
    if not rows:
        return await reply(update, "Aucune boutique. Crée-en une : python -m vinted_drop.cli shop ...")
    await reply(update, "\n".join(
        f"🏪 <b>{r['name']}</b> ({r['niche'] or '-'}) · {r['online']} en ligne · {r['sales']} ventes · "
        f"{r['profit']:.2f} € de bénéfice" for r in rows))


@authorized
async def cmd_niches(update, ctx):
    lines = ["🔎 <b>Top niches ce mois-ci</b>"]
    for n in niches.ranked()[:8]:
        flag = "🔥" if n["in_season"] else "⏳"
        lines.append(f"{flag} <b>{n['name']}</b> — {n['score']}/10 · achat {n['buy_price']} → "
                     f"vente {n['sell_price']}\n   <i>{n['why']}</i>  (/gagnants {n['id']})")
    await reply(update, "\n".join(lines))


@authorized
async def cmd_gagnants(update, ctx):
    niche_id = ctx.args[0] if ctx.args else None
    items = niches.winning_products(niche_id)[:15]
    if not items:
        return await reply(update, "Aucun produit pour cette niche.")
    lines = []
    for i in items:
        cost = f"{i['cost']:.2f} €" if i.get("cost") else "prix à vérifier"
        lines.append(f'• <a href="{i["url"]}">{i["title"]}</a> ({i["supplier"]}, {cost})')
    lines.append("\nPour l'ajouter : /ajouter &lt;lien&gt; &lt;prix&gt; &lt;boutique&gt;")
    await reply(update, "\n".join(lines))


# ---------------- tâches planifiées ----------------
async def job_daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
        await ctx.bot.send_message(chat_id, stats_text(), parse_mode=ParseMode.HTML)


async def job_ready_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    ready = db.list_products(status="ready", limit=50)
    if not ready:
        return
    txt = f"📤 {len(ready)} produit(s) prêts à publier : " + ", ".join(f"#{p['id']}" for p in ready[:20])
    for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
        await ctx.bot.send_message(chat_id, txt)


def build_app() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env")
    db.init()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    commands = {
        "start": cmd_start, "aide": cmd_start, "help": cmd_start, "ajouter": cmd_ajouter,
        "produits": cmd_produits, "voir": cmd_voir, "photos": cmd_photos, "fiche": cmd_fiche,
        "preparer": cmd_preparer, "publie": cmd_publie, "prix": cmd_prix, "offre": cmd_offre,
        "vendu": cmd_vendu, "commande": cmd_commande, "recu": cmd_recu, "envoye": cmd_envoye,
        "acommander": cmd_acommander, "stats": cmd_stats, "boutiques": cmd_boutiques,
        "niches": cmd_niches, "gagnants": cmd_gagnants,
    }
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    if app.job_queue and config.TELEGRAM_ALLOWED_CHAT_IDS:
        h, m = (int(x) for x in config.DAILY_REPORT_TIME.split(":"))
        app.job_queue.run_daily(job_daily_report, time(h, m, tzinfo=TZ))
        app.job_queue.run_daily(job_ready_reminder, time(10, 0, tzinfo=TZ))
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build_app().run_polling()


if __name__ == "__main__":
    main()
