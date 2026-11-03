"""Analyse des niches : score saisonnier + performance réelle de tes boutiques."""
import copy
import json
import math
import re
from datetime import date

from . import config, db

_json_cache: dict = {}


def _read(name, copy_=True):
    """Lit un fichier de data/ une seule fois tant qu'il n'est pas modifié (cache par date de modification)."""
    path = config.DATA_DIR / name
    key = (str(path), path.stat().st_mtime_ns)
    if _json_cache.get(name, (None,))[0] != key:
        _json_cache[name] = (key, json.loads(path.read_text(encoding="utf-8")))
    data = _json_cache[name][1]
    return copy.deepcopy(data) if copy_ else data


def load():
    return _read("niches.json")["niches"]


def _sold_count(sold) -> int:
    """« 98 000+ vendus » -> 98000."""
    digits = re.sub(r"[^\d]", "", str(sold or ""))
    return int(digits) if digits else 0


def supplier_proof(niche_id: str) -> dict | None:
    """Preuves de vente chez les fournisseurs pour une niche : meilleurs avis / ventes de ses produits gagnants.
    Sert de signal de demande quand Vinted n'est pas connecté. Score 0..1 (10 000 avis ou 50 000 ventes = 1)."""
    best, best_item = 0.0, None
    for i in _read("winning_products.json", copy_=False):
        if i.get("niche") != niche_id:
            continue
        reviews, sold = i.get("reviews") or 0, _sold_count(i.get("sold"))
        p_sold = math.log10(sold + 1) / math.log10(50001)
        if "vendus" in str(i.get("sold") or ""):
            # « (N vendus) » sur Temu est souvent le total de la boutique du vendeur : poids limité
            p_sold = min(0.7, p_sold)
        p = max(math.log10(reviews + 1) / 4, p_sold)
        if i.get("rating") and reviews:
            p *= 0.85 + 0.15 * max(0, min(1, (i["rating"] - 4)))
        if p > best:
            best, best_item = p, i
    if not best_item:
        return None
    return {"score": round(min(1, best), 2), "reviews": best_item.get("reviews") or 0,
            "sold": best_item.get("sold"), "rating": best_item.get("rating"), "title": best_item.get("title")}


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
    min_profit = float(db.get_setting("min_profit") or config.MIN_PROFIT)
    out = []
    for n in load():
        pot = market.potential(n, month, perf.get(n["id"]), min_profit)
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
    items = _read("winning_products.json")
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
