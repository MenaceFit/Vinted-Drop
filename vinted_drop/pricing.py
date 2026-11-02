"""Moteur de recommandation de prix.

Sur Vinted (compte particulier), la protection acheteur et le port sont payés par l'acheteur. Le vendeur
touche le prix affiché moins d'éventuels frais de plateforme (PLATFORM_FEE_PCT, 0 par défaut).

Prix minimum rentable = (coût de revient complet + bénéfice minimum) / (1 - frais plateforme)
Coût de revient = achat + frais fournisseur + port fournisseur + autres frais + emballage

Le prix conseillé part de l'objectif (achat × multiplicateur), puis est ajusté avec :
- les prix de produits comparables observés sur Vinted (analyse du marché),
- la demande observée (favoris moyens),
- l'historique de tes ventes dans la même niche,
sans jamais descendre sous le prix minimum rentable.
"""
import math
import statistics

from . import config


def _round_price(value: float) -> float:
    """Prix "propre" : 9, 12, 15... ou xx,90 au-dessus de 20 € (jamais sous la valeur visée)."""
    if value < 20:
        return float(math.ceil(value))
    return round(math.ceil(value + 0.10) - 0.10, 2)


def _settings():
    from . import db
    try:
        return {"min_profit": float(db.get_setting("min_profit") or config.MIN_PROFIT),
                "multiplier": float(db.get_setting("target_multiplier") or config.TARGET_MULTIPLIER),
                "buffer": float(db.get_setting("negotiation_buffer") or config.NEGOTIATION_BUFFER)}
    except Exception:  # base pas encore initialisée
        return {"min_profit": config.MIN_PROFIT, "multiplier": config.TARGET_MULTIPLIER,
                "buffer": config.NEGOTIATION_BUFFER}


def floor_price(total_cost: float, min_profit: float) -> float:
    fee = config.PLATFORM_FEE_PCT / 100
    return _round_price((total_cost + min_profit) / (1 - fee))


def net(sale_price: float, total_cost: float) -> float:
    return round(sale_price * (1 - config.PLATFORM_FEE_PCT / 100) - total_cost, 2)


def compute_prices(cost: float, multiplier: float | None = None, min_profit: float | None = None,
                   buffer: float | None = None, supplier_fees: float = 0, shipping_cost: float = 0,
                   other_fees: float = 0) -> dict:
    """Prix affiché, prix plancher (offre minimum acceptée) et bénéfice, sans données de marché."""
    s = _settings()
    multiplier = s["multiplier"] if multiplier is None else multiplier
    min_profit = s["min_profit"] if min_profit is None else min_profit
    buffer = s["buffer"] if buffer is None else buffer
    total_cost = cost + supplier_fees + shipping_cost + other_fees + config.PACKAGING_COST
    floor = floor_price(total_cost, min_profit)
    target = max(_round_price(cost * multiplier), floor)
    list_price = _round_price(target * (1 + buffer))
    return {
        "price": round(list_price, 2),
        "min_price": round(target, 2),
        "floor": floor,
        "total_cost": round(total_cost, 2),
        "profit_at_price": net(list_price, total_cost),
        "profit_at_min": net(target, total_cost),
        "margin_pct": round(100 * net(list_price, total_cost) / list_price, 1) if list_price else 0,
        "multiplier": round(list_price / cost, 2) if cost else None,
    }


def recommend(product: dict, market: dict | None = None, history: list | None = None,
              min_profit: float | None = None) -> dict:
    """Recommandation complète pour un produit.

    market  : {"median": float, "p25": float, "p75": float, "sample": int, "avg_fav": float, "age_days": int}
    history : prix de vente réels de produits de la même niche
    """
    from . import db
    s = _settings()
    if min_profit is None:
        shop = db.get_shop(product["shop_id"]) if product.get("shop_id") else None
        min_profit = float(db.shop_setting(shop, "min_profit", s["min_profit"]))
    base = compute_prices(product.get("cost") or 0, min_profit=min_profit,
                          supplier_fees=product.get("supplier_fees") or 0,
                          shipping_cost=product.get("shipping_cost") or 0, other_fees=product.get("other_fees") or 0)
    total_cost, floor = base["total_cost"], base["floor"]
    target = base["min_price"]
    reasons = [f"Coût de revient {total_cost:.2f} € + bénéfice minimum {min_profit:.0f} € → prix minimum rentable {floor:.2f} €"]
    warnings = []
    confidence = 0.25

    if market and market.get("median") and (market.get("sample") or 0) >= 5:
        m = market["median"]
        demand = market.get("avg_fav") or 0
        adj = 1.10 if demand >= 5 else (0.95 if demand < 1 else 1.0)
        market_target = _round_price(m * adj)
        reasons.append(f"Prix médian de {market['sample']} annonces comparables : {m:.2f} €"
                       + (" (demande forte, +10 %)" if adj > 1 else " (demande faible, -5 %)" if adj < 1 else ""))
        # on reste proche du marché, sans passer sous le minimum rentable
        target = max(floor, min(max(target, market_target), _round_price(market_target * 1.15)))
        if floor > (market.get("p75") or m) * 1.05:
            warnings.append(f"Le prix minimum rentable ({floor:.2f} €) est au-dessus de 75 % des annonces comparables : "
                            f"peu compétitif avec {min_profit:.0f} € de bénéfice.")
        confidence += 0.35 if market["sample"] >= 30 else 0.2
        if (market.get("age_days") or 99) <= 14:
            confidence += 0.1
    else:
        reasons.append("Pas de données de marché récentes : prix basé sur le multiplicateur cible")

    if history and len(history) >= 3:
        h = statistics.median(history)
        reasons.append(f"Tes {len(history)} ventes dans cette niche : prix médian {h:.2f} €")
        target = max(floor, _round_price((target * 2 + h) / 3))
        confidence += 0.2 if len(history) >= 5 else 0.1

    list_price = _round_price(target * (1 + s["buffer"]))
    confidence = min(confidence, 0.95)
    return {
        "price": list_price, "min_price": round(target, 2), "floor": floor, "total_cost": total_cost,
        "profit_at_price": net(list_price, total_cost), "profit_at_min": net(target, total_cost),
        "margin_pct": round(100 * net(list_price, total_cost) / list_price, 1) if list_price else 0,
        "min_profit": min_profit,
        "confidence": round(confidence, 2),
        "confidence_label": "haute" if confidence >= 0.7 else ("moyenne" if confidence >= 0.45 else "faible"),
        "reasons": reasons, "warnings": warnings,
        "multiplier": round(list_price / product["cost"], 2) if product.get("cost") else None,
    }


def recommend_for(product_id: int) -> dict:
    """Recommandation avec les vraies données disponibles (marché de la niche + ventes passées)."""
    from . import db, market
    p = db.get_product(product_id)
    mk = market.niche_market(p.get("niche")) if p.get("niche") else None
    with db.connect() as c:
        history = [r[0] for r in c.execute(
            "SELECT sa.sale_price FROM sales sa JOIN products p ON p.id=sa.product_id WHERE p.niche=? "
            "ORDER BY sa.id DESC LIMIT 50", (p.get("niche"),))] if p.get("niche") else []
    return recommend(p, mk, history)


def accept_offer(offer: float, min_price: float) -> bool:
    return offer >= min_price


def profit(sale_price: float, cost: float) -> float:
    """Compatibilité 1.x : bénéfice avec le seul prix d'achat + emballage."""
    return net(sale_price, cost + config.PACKAGING_COST)
