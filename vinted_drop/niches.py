"""Analyse des niches : score saisonnier + performance réelle de tes boutiques."""
import json
import re
from datetime import date

from . import config, db


def load():
    return json.loads((config.DATA_DIR / "niches.json").read_text(encoding="utf-8"))["niches"]


def score(n: dict, month: int) -> float:
    """Score /10 : demande x (10 - concurrence) pondéré par la saison."""
    in_season = month in n["months"]
    next_month = (month % 12) + 1 in n["months"]
    in_two = ((month + 1) % 12) + 1 in n["months"]
    season = 1.0 if in_season else (0.7 if next_month else (0.5 if in_two else 0.3))
    raw = (n["demand"] * 0.6 + (10 - n["competition"]) * 0.4) * season
    return round(raw, 1)


def ranked(month: int | None = None, profitable_only=False):
    """Niches triées par score de potentiel /100 (voir market.potential pour le détail)."""
    from . import market
    month = month or date.today().month
    perf = {r["niche"]: r for r in db.niche_breakdown()}
    out = []
    for n in load():
        pot = market.potential(n, month, perf.get(n["id"]))
        out.append({**n, "score": pot["score"], "potential": pot, "in_season": month in n["months"],
                    "perf": perf.get(n["id"]), "live": pot["market"]})
    if profitable_only:
        out = [n for n in out if n["potential"]["meets_min_profit"]]
    return sorted(out, key=lambda x: x["score"], reverse=True)


def _midpoint(price_range: str):
    nums = [float(x) for x in re.findall(r"\d+(?:[.,]\d+)?", (price_range or "").replace(",", "."))]
    return sum(nums) / len(nums) if nums else None


def product_score(item: dict, niche: dict | None, month: int) -> dict:
    """Score /100 d'un produit gagnant :
    preuve de vente (avis, note, ventes) 45 % · saison de la niche 35 % · marge estimée 20 %."""
    import math
    reviews = item.get("reviews") or 0
    proof = min(1, math.log10(reviews + 1) / 4)                 # 10 000 avis = max
    if item.get("rating"):
        proof = proof * 0.7 + max(0, (item["rating"] - 4) / 1) * 0.3
    if item.get("sold"):
        proof = max(proof, 0.8)
    niche_part = min(1, score(niche, month) / 8) if niche else 0.4
    sell = _midpoint(niche.get("sell_price")) if niche else None
    margin = None
    if item.get("cost") and sell:
        margin = sell / item["cost"]
        margin_part = min(1, max(0, (margin - 1.2) / 1.8))      # x3 = max
    else:
        margin_part = 0.5
    total = round(100 * (0.45 * proof + 0.35 * niche_part + 0.20 * margin_part))
    return {"score": total, "multiplier": round(margin, 1) if margin else None, "est_sell": sell}


def winning_products(niche_id: str | None = None, month: int | None = None, ranked_only=False):
    items = json.loads((config.DATA_DIR / "winning_products.json").read_text(encoding="utf-8"))
    month = month or date.today().month
    by_id = {n["id"]: n for n in load()}
    out = []
    for i in items:
        if niche_id and i["niche"] != niche_id:
            continue
        n = by_id.get(i["niche"])
        out.append({**i, **product_score(i, n, month), "niche_name": n["name"] if n else i["niche"],
                    "in_season": bool(n and month in n["months"])})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def get(niche_id):
    return next((n for n in load() if n["id"] == niche_id), None)
