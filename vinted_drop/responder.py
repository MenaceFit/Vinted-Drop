"""Réponses aux acheteurs et négociation.

L'IA rédige le texte ; les décisions de prix suivent des règles fixes (prix mini de chaque
article). Tout ce qui est incertain est envoyé sur Telegram pour validation.
"""
import hashlib
import json
import logging

from . import ai, db, notify, pricing
from .vinted_client import VintedClient, price_of

log = logging.getLogger(__name__)

SYSTEM = """Tu réponds aux acheteurs d'une boutique Vinted, en français, comme une vendeuse
sympa et sérieuse. Messages courts (1 à 3 phrases), tutoiement si l'acheteur tutoie.
Règles strictes :
- Utilise UNIQUEMENT les infos de l'article fournies. Si on te demande une mesure, une matière ou
  un détail que tu n'as pas : intent = "unknown" et propose de vérifier.
- L'article est neuf. Délai d'envoi : {ship_delay}.
- Ne donne jamais de coordonnées, ne propose jamais de paiement ou d'échange en dehors de Vinted.
- Ne décide pas toi-même d'un prix : si on te parle de prix, utilise exactement le prix indiqué
  dans "décision prix".
- Sujet sensible (litige, colis non reçu, remboursement, insulte, demande de photo portée réelle) :
  intent = "sensitive".
Réponds en JSON : {{"intent": "question|offer|purchase|thanks|unknown|sensitive|other",
"reply": str, "confidence": nombre entre 0 et 1}}"""


def _messages(conv: dict) -> list[dict]:
    out = []
    for m in conv.get("messages") or []:
        e = m.get("entity") or m
        out.append({"type": m.get("entity_type") or e.get("entity_type") or "message",
                    "body": e.get("body") or e.get("title") or "",
                    "user_id": e.get("user_id") or (e.get("user") or {}).get("id"),
                    "price": price_of(e) if e.get("price") else None,
                    "id": m.get("id") or e.get("id"), "created": e.get("created_at_ts") or e.get("created_at"),
                    "request_id": e.get("offer_request_id") or e.get("id")})
    return out


def _price_decision(product, offer_price):
    if not product or not offer_price:
        return None
    mini = product.get("min_price") or pricing.compute_prices(product["cost"])["min_price"]
    listed = product.get("price") or mini
    if offer_price >= mini:
        return {"action": "accept", "price": offer_price}
    counter = max(mini, round((offer_price + listed) / 2))
    return {"action": "counter", "price": float(counter)}


def draft(product, history, offer_decision):
    info = {k: product.get(k) for k in ("title", "description", "size", "color", "material", "price")} if product else {}
    ship = db.get_setting("ship_delay") or "envoi sous 5 à 8 jours"
    text = (f"Article : {json.dumps(info, ensure_ascii=False)}\n"
            f"Décision prix : {json.dumps(offer_decision, ensure_ascii=False) if offer_decision else 'aucune'}\n"
            "Conversation (du plus ancien au plus récent) :\n" +
            "\n".join(f"{'ACHETEUR' if h['from_buyer'] else 'MOI'}: {h['body']}" for h in history[-10:]))
    return ai.chat_json(SYSTEM.format(ship_delay=ship), text)


def process_shop(client: VintedClient, limit=15) -> dict:
    shop = client.shop
    if not client.user_id:
        client.me()
    stats = {"seen": 0, "sent": 0, "pending": 0, "offers": 0}
    reply_mode = db.get_setting("reply_mode")
    offer_mode = db.get_setting("offer_mode")
    for c in client.inbox()[:limit]:
        cid = c.get("id")
        if not cid or not (c.get("unread") or c.get("is_unread")):
            continue
        conv = client.conversation(cid)
        msgs = _messages(conv)
        if not msgs:
            continue
        last = msgs[-1]
        if last["user_id"] == client.user_id:
            continue
        key = hashlib.sha1(f"{last['id']}{last['body']}{last['created']}".encode()).hexdigest()
        known = db.get_conversation(cid)
        if known and known.get("last_message_key") == key:
            continue
        stats["seen"] += 1
        tr = conv.get("transaction") or {}
        tid = tr.get("id")
        item_id = tr.get("item_id") or (tr.get("item") or {}).get("id")
        product = db.product_by_vinted_id(item_id) if item_id else None
        buyer = (conv.get("opposite_user") or {}).get("login")
        offer_price = last["price"] if "offer" in last["type"] else None
        decision = _price_decision(product, offer_price)
        history = [{"from_buyer": m["user_id"] != client.user_id, "body": m["body"] or f"[{m['type']}]"} for m in msgs]
        try:
            res = draft(product, history, decision)
        except Exception as e:
            res = {"intent": "other", "reply": "", "confidence": 0}
            log.warning("IA indisponible : %s", e)
        db.upsert_conversation(cid, shop_id=shop["id"], product_id=product and product["id"], transaction_id=tid,
                               buyer=buyer, last_buyer_message=last["body"] or f"[{last['type']}]",
                               last_message_key=key, draft=res.get("reply"), intent=res.get("intent"),
                               offer_price=offer_price, status="pending")

        # 1) offres : règle de prix automatique
        if decision and offer_mode == "auto" and tid:
            try:
                if decision["action"] == "accept":
                    client.accept_offer_request(tid, last["request_id"])
                else:
                    client.counter_offer(tid, decision["price"])
                stats["offers"] += 1
                db.log_event("offer", f"Offre {offer_price} € de {buyer} → {decision['action']} "
                             f"{decision['price']} €", shop_id=shop["id"], product_id=product and product["id"])
            except Exception as e:
                notify.send(f"⚠️ Offre de {offer_price} € de {buyer} non traitée automatiquement ({e}). "
                            f"Décision conseillée : {decision['action']} {decision['price']} €")

        # 2) message texte
        safe = res.get("intent") in ("question", "offer", "purchase", "thanks") and res.get("confidence", 0) >= 0.75
        if reply_mode == "auto" and safe and res.get("reply"):
            send_reply(client, cid, res["reply"])
            stats["sent"] += 1
        elif reply_mode != "off":
            notify.send(
                f"💬 <b>{shop['name']}</b> · {buyer or 'acheteur'}"
                + (f" · {product.get('title')}" if product else "") +
                f"\n\n« {(last['body'] or last['type'])[:500]} »"
                + (f"\n\n💶 Offre {offer_price} € → {decision['action']} {decision['price']} €" if decision else "")
                + f"\n\n✍️ Réponse proposée :\n{res.get('reply') or '(à écrire)'}",
                buttons=[[("✅ Envoyer", f"rs:{cid}"), ("✏️ Modifier", f"re:{cid}"), ("❌ Ignorer", f"ri:{cid}")]])
            stats["pending"] += 1
    return stats


def send_reply(client: VintedClient, cid: int, text: str):
    client.reply(cid, text)
    db.upsert_conversation(cid, status="sent", draft=text)
    db.log_event("reply", f"Réponse envoyée : {text[:80]}", shop_id=client.shop["id"])


def send_pending(cid: int, text: str | None = None):
    conv = db.get_conversation(cid)
    if not conv:
        raise ValueError("Conversation inconnue")
    client = VintedClient.for_shop(conv["shop_id"])
    send_reply(client, cid, text or conv["draft"])
