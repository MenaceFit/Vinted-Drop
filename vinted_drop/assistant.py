"""Assistant Telegram en langage naturel.

Il interroge les vraies données (outils de lecture) et n'exécute que des actions autorisées.
Les actions importantes (publier, enregistrer une vente, lancer des générations payantes, changer
l'automatisation) ne sont JAMAIS exécutées directement : l'outil renvoie une demande de confirmation
que le bot affiche avec des boutons ✅ / ❌.
"""
import json

from . import ai, autopilot, db, finance, listing, market, niches, pricing, publisher

SYSTEM = """Tu es l'assistant de pilotage d'un business de revente sur Vinted. Tu parles français,
tu es bref et concret (chiffres, prochaines actions). Utilise TOUJOURS les outils pour les données ;
n'invente jamais un chiffre. Pour une action (publier, préparer, vendre, réglage), appelle l'outil :
il demandera lui-même la confirmation à l'utilisateur. Formatage : texte simple, pas de markdown,
listes avec des tirets. Périodes acceptées : today, 7d, 30d, 90d, year, all."""

ALLOWED_SETTINGS = {k for k in db.DEFAULT_SETTINGS if not k.startswith("_")}
PERIOD = {"type": "string", "enum": list(db.PERIODS)}


def _tool(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


TOOLS = [
    _tool("finance_summary", "CA, bénéfice net, marge, ventes, coûts IA, dépenses sur une période", {"period": PERIOD}),
    _tool("shop_ranking", "Classement des boutiques (bénéfice, ventes, marge) sur une période", {"period": PERIOD}),
    _tool("top_products", "Produits les plus rentables sur une période", {"period": PERIOD, "limit": {"type": "integer"}}),
    _tool("unsold_products", "Annonces en ligne pas encore vendues depuis N jours", {"days": {"type": "integer"}}),
    _tool("ai_costs", "Dépenses de génération IA (photos) sur une période", {"period": PERIOD}),
    _tool("list_products", "Liste des produits par statut", {"status": {"type": "string", "enum": db.PRODUCT_STATUSES}}),
    _tool("get_product", "Détail et fiche financière d'un produit", {"id": {"type": "integer"}}, ["id"]),
    _tool("orders_to_place", "Commandes fournisseurs à passer ou en problème"),
    _tool("top_niches", "Meilleures niches (score /100) avec bénéfice estimé"),
    _tool("opportunities", "Opportunités commerciales détectées"),
    _tool("winning_products", "Produits gagnants avec liens fournisseurs", {"niche": {"type": "string"}}),
    _tool("price_advice", "Prix de vente conseillé d'un produit existant", {"id": {"type": "integer"}}, ["id"]),
    _tool("get_settings", "Réglages du pilote automatique"),
    _tool("recent_activity", "Dernières actions et erreurs du bot"),
    # actions : confirmation obligatoire
    _tool("prepare_product", "Préparer un produit (photos + fiche)", {"id": {"type": "integer"}}, ["id"]),
    _tool("prepare_pending", "Préparer toutes les annonces en brouillon (photos + fiches)", {"limit": {"type": "integer"}}),
    _tool("publish_product", "Publier un produit prêt sur Vinted", {"id": {"type": "integer"}}, ["id"]),
    _tool("record_sale", "Enregistrer une vente", {"id": {"type": "integer"}, "price": {"type": "number"}}, ["id", "price"]),
    _tool("set_setting", "Changer un réglage", {"key": {"type": "string", "enum": sorted(ALLOWED_SETTINGS)},
                                                "value": {"type": "string"}}, ["key", "value"]),
    _tool("sync_now", "Lancer une synchronisation Vinted maintenant"),
]
SENSITIVE = {"prepare_product", "prepare_pending", "publish_product", "record_sale", "set_setting", "sync_now"}


def _range(args):
    return db.period_range(args.get("period") or "7d")


def describe(name, args) -> str:
    """Phrase affichée dans la demande de confirmation."""
    if name == "publish_product":
        p = db.get_product(args["id"]) or {}
        return f"Publier sur Vinted le produit #{args['id']} « {p.get('title') or '?'} » à {p.get('price') or '?'} €"
    if name == "record_sale":
        return f"Enregistrer la vente du produit #{args['id']} à {args['price']:.2f} € (une commande fournisseur sera créée)"
    if name == "prepare_product":
        return f"Préparer le produit #{args['id']} (photos IA + fiche, peut coûter quelques centimes)"
    if name == "prepare_pending":
        n = len([p for p in db.list_products(status="draft", limit=500) if p.get("supplier_image")])
        return f"Préparer {min(n, args.get('limit') or 15)} brouillon(s) (photos IA + fiches)"
    if name == "set_setting":
        return f"Changer le réglage « {args['key']} » en « {args['value']} »"
    if name == "sync_now":
        return "Lancer une synchronisation Vinted (annonces, ventes, messages) maintenant"
    return name


def execute(name, args):
    """Exécution réelle (appelée par le bot après confirmation, ou directement pour les lectures)."""
    if name == "finance_summary":
        return finance.summary(*_range(args))
    if name == "shop_ranking":
        return [{k: r.get(k) for k in ("name", "sales", "revenue", "profit", "margin_pct", "online")}
                for r in db.shop_breakdown(*_range(args))]
    if name == "top_products":
        return db.top_products(*_range(args), limit=min(int(args.get("limit") or 10), 30))
    if name == "unsold_products":
        return [{k: p.get(k) for k in ("id", "title", "price", "published_at", "favourites", "shop_name")}
                for p in db.unsold_products(int(args.get("days") or 14), 30)]
    if name == "ai_costs":
        return db.cost_stats(start=_range(args)[0], end=_range(args)[1])
    if name == "list_products":
        return [{k: x.get(k) for k in ("id", "title", "status", "price", "cost", "shop_name", "favourites")}
                for x in db.list_products(status=args.get("status"), limit=30)]
    if name == "get_product":
        sheet = finance.product_sheet(args["id"])
        p = sheet.pop("product")
        return {**{k: p.get(k) for k in ("id", "title", "status", "price", "min_price", "shop_name", "niche")}, **sheet}
    if name == "orders_to_place":
        return [{k: o.get(k) for k in ("id", "title", "size", "status", "supplier_url", "sale_price", "shop_name")}
                for o in db.list_orders(open_only=True, limit=30) if o["status"] in ("to_buy", "problem")]
    if name == "top_niches":
        return [{"id": n["id"], "name": n["name"], "score": n["score"], "est_profit": n["potential"]["est_profit"],
                 "confidence": n["potential"]["confidence"], "in_season": n["in_season"]}
                for n in niches.ranked(profitable_only=True)[:8]]
    if name == "opportunities":
        return [{k: o.get(k) for k in ("title", "kind_label", "score")} | {"why": o["data"].get("why")}
                for o in market.list_opportunities(limit=10)]
    if name == "winning_products":
        return [{k: w.get(k) for k in ("title", "url", "cost", "score", "rating", "reviews")}
                for w in niches.winning_products(args.get("niche"))[:12]]
    if name == "price_advice":
        return pricing.recommend_for(args["id"])
    if name == "get_settings":
        return db.all_settings()
    if name == "recent_activity":
        return [{k: e[k] for k in ("created_at", "kind", "level", "message")} for e in db.list_events(15)]
    if name == "prepare_product":
        return listing.prepare(args["id"])
    if name == "prepare_pending":
        done = []
        for p in [p for p in db.list_products(status="draft", limit=500) if p.get("supplier_image")][:int(args.get("limit") or 15)]:
            listing.prepare(p["id"])
            done.append(p["id"])
        return {"prepared": done}
    if name == "publish_product":
        return publisher.publish(args["id"])
    if name == "record_sale":
        return db.get_sale(db.record_sale(args["id"], args["price"]))
    if name == "set_setting":
        if args["key"] not in ALLOWED_SETTINGS:
            return {"error": "réglage inconnu"}
        db.set_setting(args["key"], args["value"])
        db.log_event("telegram", f"Réglage {args['key']} = {args['value']} (assistant)")
        return {"ok": True, args["key"]: args["value"]}
    if name == "sync_now":
        return autopilot.cycle()
    return {"error": "outil inconnu"}


def run_tool(name, args, pending: list | None = None):
    """Lecture : exécutée tout de suite. Action sensible : mise en attente de confirmation."""
    if name in SENSITIVE:
        if pending is not None:
            pending.append({"name": name, "args": args, "summary": describe(name, args)})
        return {"needs_confirmation": True, "summary": describe(name, args),
                "message": "Demande de confirmation envoyée à l'utilisateur (boutons). N'affirme pas que c'est fait."}
    return execute(name, args)


def ask(question: str, history: list | None = None) -> tuple[str, list, list]:
    """Renvoie (réponse, nouvel historique, actions en attente de confirmation)."""
    messages = [{"role": "system", "content": SYSTEM}, *(history or [])[-8:], {"role": "user", "content": question}]
    pending = []
    for _ in range(6):
        msg = ai.chat_tools(messages, TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            answer = msg.get("content") or "…"
            new_hist = [*(history or []), {"role": "user", "content": question}, {"role": "assistant", "content": answer}]
            return answer, new_hist[-8:], pending
        messages.append(msg)
        for call in calls:
            try:
                result = run_tool(call["function"]["name"], json.loads(call["function"]["arguments"] or "{}"), pending)
            except Exception as e:
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
    return "Je n'ai pas réussi à terminer, reformule ta demande.", history or [], pending
