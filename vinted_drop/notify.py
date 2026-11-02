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


def send(text: str, photo_path=None, buttons=None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ALLOWED_CHAT_IDS:
        return False
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    ok = True
    for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
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
        f"💸 <b>Vente !</b> {sale.get('shop_name') or ''}\n"
        f"{sale.get('title') or 'Produit #' + str(sale['product_id'])}\n"
        f"Taille : {sale.get('size') or '?'}\n"
        f"Prix : {sale['sale_price']:.2f} € · Achat : {sale['cost']:.2f} € · "
        f"<b>Bénéfice : {sale['profit']:.2f} €</b>\n"
        + (f'🛒 <a href="{link}">Commander chez {sale.get("supplier") or "le fournisseur"}</a>\n' if link else "")
        + f"Vente #{sale['id']}" + (" · détectée sur Vinted" if sale.get("source") == "vinted" else "")
    )


def sale_buttons(sale: dict):
    rows = []
    if sale.get("supplier_url"):
        rows.append([("🛒 Commander", sale["supplier_url"])])
    rows.append([("✅ Commandé", f"ss:{sale['id']}:ordered"), ("📦 Reçu", f"ss:{sale['id']}:received"),
                 ("🚚 Envoyé", f"ss:{sale['id']}:shipped")])
    return rows


def notify_sale(sale: dict):
    return send(sale_message(sale), buttons=sale_buttons(sale))
