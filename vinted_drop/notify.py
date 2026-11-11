"""Envoi de messages Telegram depuis n'importe où (dashboard, CLI, tâches planifiées)."""
import json

import requests

from . import config


def _markup(buttons):
    """buttons = [[("Texte", "callback_data") ou ("Texte", "https://...")], ...]"""
    if not buttons:
        return None
    return json.dumps({"inline_keyboard": [[
        {"text": t, "url": d} if str(d).startswith("http") else {"text": t, "callback_data": d}
        for t, d in row] for row in buttons]})


NOTIFY_CATEGORIES = {"sales": "Ventes détectées", "offers": "Offres reçues", "messages": "Messages acheteurs",
                     "publish": "Publications (validées / échouées)", "auth": "Token expiré / connexion",
                     "photos": "Génération photo terminée", "budget": "Budget IA", "opportunities": "Opportunités",
                     "errors": "Erreurs des tâches automatiques", "reports": "Rapports financiers"}


def send(text: str, photo_path=None, buttons=None, category=None, dedupe=None, within_hours=24) -> bool:
    """Envoie une notification. `category` : réglable on/off dans Pilote auto ; `dedupe` : clé anti-doublon."""
    from . import db
    if category and db.get_setting(f"notify_{category}") == "off":
        return False
    if dedupe and not db.notify_once(dedupe, within_hours):
        return False
    targets = config.notify_targets()
    if not config.TELEGRAM_BOT_TOKEN or not targets:
        return False
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    ok = True
    for chat_id in targets:
        try:
            if photo_path:
                with open(photo_path, "rb") as f:
                    r = requests.post(f"{base}/sendPhoto", timeout=30, files={"photo": f},
                                      data={"chat_id": chat_id, "caption": text[:1024], "parse_mode": "HTML",
                                            **({"reply_markup": _markup(buttons)} if buttons else {})})
            else:
                r = requests.post(f"{base}/sendMessage", timeout=30, json={
                    "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    **({"reply_markup": json.loads(_markup(buttons))} if buttons else {})})
            ok = ok and r.ok
        except requests.RequestException:
            ok = False
    return ok


def sale_message(sale: dict) -> str:
    link = sale.get("supplier_url")
    return (
        f"💸 <b>Vente{' Depop' if sale.get('platform') == 'depop' else ''} !</b> {sale.get('shop_name') or ''}\n"
        f"{sale.get('title') or 'Produit #' + str(sale['product_id'])}\n"
        f"Taille : {sale.get('size') or '?'}\n"
        f"Prix : {sale['sale_price']:.2f} € · Achat : {sale['cost']:.2f} € · "
        f"<b>Bénéfice : {sale['profit']:.2f} €</b>"
        + (f" (frais {sale['fees']:.2f} € déduits)" if sale.get("fees") else "") + "\n"
        + (f'🛒 <a href="{link}">Commander chez {sale.get("supplier") or "le fournisseur"}</a>\n' if link else "")
        + f"Vente #{sale['id']}" + (" · détectée sur Vinted" if sale.get("source") == "vinted" else "")
    )


def sale_buttons(sale: dict):
    rows = []
    if sale.get("supplier_url"):
        rows.append([("🛒 Commander", sale["supplier_url"])])
    rows.append([("✅ Commandé", f"os:{sale['id']}:ordered"), ("🚚 Expédié", f"os:{sale['id']}:shipped"),
                 ("📦 Livré", f"os:{sale['id']}:delivered")])
    rows.append([("⚠️ Problème fournisseur", f"os:{sale['id']}:problem")])
    return rows


def notify_sale(sale: dict):
    return send(sale_message(sale), buttons=sale_buttons(sale), category="sales", dedupe=f"sale-{sale['id']}",
                within_hours=24 * 365)
