"""Envoi de messages Telegram depuis n'importe où (dashboard, CLI, tâches planifiées)."""
import requests

from . import config


def send(text: str, photo_path=None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ALLOWED_CHAT_IDS:
        return False
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    ok = True
    for chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS:
        try:
            if photo_path:
                with open(photo_path, "rb") as f:
                    r = requests.post(f"{base}/sendPhoto", timeout=30, files={"photo": f},
                                      data={"chat_id": chat_id, "caption": text[:1024], "parse_mode": "HTML"})
            else:
                r = requests.post(f"{base}/sendMessage", timeout=30, json={
                    "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": True})
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
        + f"Vente #{sale['id']} → /commande {sale['id']} une fois commandé"
    )
