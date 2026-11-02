"""Assistant en langage naturel pour Telegram : « combien j'ai gagné cette semaine ? »,
« prépare le produit 12 », « passe la publication en automatique »…"""
import json

from . import ai, autopilot, db, listing, niches, pricing, publisher

ALLOWED_SETTINGS = set(db.DEFAULT_SETTINGS) - {"market_scan"}

SYSTEM = """Tu es l'assistant de pilotage d'un business de revente sur Vinted. Tu parles
français, tu es bref et concret (chiffres, prochaines actions). Utilise les outils pour toute
donnée ; n'invente jamais un chiffre. Avant une action irréversible (publier, enregistrer une
vente), vérifie que l'utilisateur l'a clairement demandée. Formatage : texte simple, pas de markdown."""


def _tool(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


TOOLS = [
    _tool("get_stats", "CA, bénéfice, marge, ventes", {"period": {"type": "string", "enum": ["today", "7d", "30d", "total"]}}),
    _tool("list_products", "Liste des produits", {"status": {"type": "string", "enum": db.PRODUCT_STATUSES}}),
    _tool("get_product", "Détail d'un produit", {"id": {"type": "integer"}}, ["id"]),
    _tool("prepare_product", "Génère photos + fiche d'un produit", {"id": {"type": "integer"}}, ["id"]),
    _tool("publish_product", "Publie un produit prêt sur Vinted", {"id": {"type": "integer"}}, ["id"]),
    _tool("record_sale", "Enregistre une vente", {"id": {"type": "integer"}, "price": {"type": "number"}}, ["id", "price"]),
    _tool("shops", "Performances par boutique"),
    _tool("top_niches", "Meilleures niches du moment"),
    _tool("winning_products", "Liens fournisseurs des produits gagnants", {"niche": {"type": "string"}}),
    _tool("to_order", "Ventes à commander chez le fournisseur"),
    _tool("price_advice", "Prix de vente conseillé", {"cost": {"type": "number"}}, ["cost"]),
    _tool("get_settings", "Réglages du pilote automatique"),
    _tool("set_setting", "Change un réglage du pilote automatique",
          {"key": {"type": "string", "enum": sorted(ALLOWED_SETTINGS)}, "value": {"type": "string"}}, ["key", "value"]),
    _tool("sync_now", "Lance une synchro Vinted maintenant (ventes, annonces, messages)"),
    _tool("recent_activity", "Dernières actions du bot"),
]


def run_tool(name, args):
    if name == "get_stats":
        p = args.get("period", "7d")
        return db.today_stats() if p == "today" else db.stats({"7d": 7, "30d": 30}.get(p))
    if name == "list_products":
        return [{k: x.get(k) for k in ("id", "title", "status", "price", "cost", "shop_name", "favourites")}
                for x in db.list_products(status=args.get("status"), limit=30)]
    if name == "get_product":
        return db.get_product(args["id"])
    if name == "prepare_product":
        return listing.prepare(args["id"])
    if name == "publish_product":
        return publisher.publish(args["id"])
    if name == "record_sale":
        return db.get_sale(db.record_sale(args["id"], args["price"]))
    if name == "shops":
        return db.shop_breakdown()
    if name == "top_niches":
        return [{k: n.get(k) for k in ("id", "name", "score", "why", "buy_price", "sell_price")} for n in niches.ranked()[:8]]
    if name == "winning_products":
        return niches.winning_products(args.get("niche"))[:15]
    if name == "to_order":
        return db.list_sales(status="to_order")
    if name == "price_advice":
        return pricing.compute_prices(args["cost"])
    if name == "get_settings":
        return db.all_settings()
    if name == "set_setting":
        if args["key"] not in ALLOWED_SETTINGS:
            return {"error": "réglage inconnu"}
        db.set_setting(args["key"], args["value"])
        return {"ok": True, args["key"]: args["value"]}
    if name == "sync_now":
        return autopilot.cycle()
    if name == "recent_activity":
        return db.list_events(15)
    return {"error": "outil inconnu"}


def ask(question: str, history: list | None = None) -> tuple[str, list]:
    messages = [{"role": "system", "content": SYSTEM}, *(history or [])[-8:], {"role": "user", "content": question}]
    for _ in range(5):
        msg = ai.chat_tools(messages, TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            answer = msg.get("content") or "…"
            new_hist = [*(history or []), {"role": "user", "content": question}, {"role": "assistant", "content": answer}]
            return answer, new_hist[-8:]
        messages.append(msg)
        for call in calls:
            try:
                result = run_tool(call["function"]["name"], json.loads(call["function"]["arguments"] or "{}"))
            except Exception as e:
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
    return "Je n'ai pas réussi à terminer, reformule ta demande.", history or []
