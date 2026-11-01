"""Calcul des prix de vente.

Sur Vinted les frais (protection acheteur + port) sont payés par l'acheteur :
le vendeur touche le prix affiché. Le bénéfice = prix de vente - prix d'achat - emballage.
"""
import math

from . import config


def _round_price(value: float) -> float:
    """Arrondit à un prix "propre" : 9, 12, 15... ou xx,90 au-dessus de 20 €."""
    if value < 20:
        return float(math.ceil(value))
    return round(math.ceil(value + 0.10) - 0.10, 2)  # jamais sous la valeur visée


def compute_prices(cost: float, multiplier: float | None = None, min_profit: float | None = None,
                   buffer: float | None = None) -> dict:
    """Retourne le prix affiché, le prix plancher (offre minimum acceptée) et le bénéfice visé."""
    multiplier = config.TARGET_MULTIPLIER if multiplier is None else multiplier
    min_profit = config.MIN_PROFIT if min_profit is None else min_profit
    buffer = config.NEGOTIATION_BUFFER if buffer is None else buffer
    total_cost = cost + config.PACKAGING_COST

    target = max(cost * multiplier, total_cost + min_profit)
    floor_price = _round_price(target)
    list_price = _round_price(floor_price * (1 + buffer))
    return {
        "price": round(list_price, 2),
        "min_price": round(floor_price, 2),
        "profit_at_price": round(list_price - total_cost, 2),
        "profit_at_min": round(floor_price - total_cost, 2),
        "multiplier": round(list_price / cost, 2) if cost else None,
    }


def accept_offer(offer: float, min_price: float) -> bool:
    return offer >= min_price


def profit(sale_price: float, cost: float) -> float:
    return round(sale_price - cost - config.PACKAGING_COST, 2)
