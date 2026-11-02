"""Analyse du marché Vinted en direct, avec ta session connectée.

Pour chaque niche on cherche ses mots-clés sur Vinted et on mesure :
  - concurrence : nombre d'annonces
  - intérêt : favoris moyens par annonce (les acheteurs mettent en favori ce qu'ils veulent)
  - prix médian : ce que les gens acceptent de payer
Le résultat ajuste le score des niches (voir niches.ranked).
"""
import json
import math
import statistics
from datetime import date

from . import db, niches
from .vinted_client import VintedClient, price_of


def scan_keyword(client: VintedClient, kw: str) -> dict:
    data = client.search(kw)
    items = data.get("items") or []
    total = (data.get("pagination") or {}).get("total_entries") or len(items)
    favs = [i.get("favourite_count") or 0 for i in items]
    prices = [p for p in (price_of(i) for i in items) if p]
    return {"keyword": kw, "total": total, "avg_fav": round(statistics.mean(favs), 2) if favs else 0,
            "median_price": round(statistics.median(prices), 2) if prices else None}


def opportunity(rows: list[dict]) -> float:
    """Score 0-10 : beaucoup de favoris par annonce et peu d'annonces = opportunité."""
    if not rows:
        return 0
    fav = statistics.mean(r["avg_fav"] for r in rows)
    total = statistics.mean(r["total"] for r in rows)
    return round(min(10, 4 * math.log1p(fav) + 6 / math.log10(total + 10)), 1)


def scan_all(client: VintedClient, max_keywords=3) -> dict:
    result = {"date": date.today().isoformat(), "niches": {}}
    for n in niches.load():
        rows = []
        for kw in n["keywords"][:max_keywords]:
            try:
                rows.append(scan_keyword(client, kw))
            except Exception as e:
                rows.append({"keyword": kw, "error": str(e), "total": 0, "avg_fav": 0, "median_price": None})
        ok = [r for r in rows if "error" not in r]
        result["niches"][n["id"]] = {"score": opportunity(ok), "keywords": rows,
                                     "median_price": statistics.median([r["median_price"] for r in ok if r["median_price"]])
                                     if any(r["median_price"] for r in ok) else None}
    db.set_setting("market_scan", json.dumps(result, ensure_ascii=False))
    db.log_event("market", f"Analyse marché : {len(result['niches'])} niches scannées")
    return result


def last_scan():
    raw = db.get_setting("market_scan")
    return json.loads(raw) if raw else None
