"""Analyse des niches : score saisonnier + performance réelle de tes boutiques."""
import json
from datetime import date

from . import config, db


def load():
    return json.loads((config.DATA_DIR / "niches.json").read_text(encoding="utf-8"))["niches"]


def score(n: dict, month: int) -> float:
    """Score /10 : demande x (10 - concurrence) pondéré par la saison."""
    in_season = month in n["months"]
    next_month = (month % 12) + 1 in n["months"]
    season = 1.0 if in_season else (0.7 if next_month else 0.3)
    raw = (n["demand"] * 0.6 + (10 - n["competition"]) * 0.4) * season
    return round(raw, 1)


def ranked(month: int | None = None):
    month = month or date.today().month
    perf = {r["niche"]: r for r in db.niche_breakdown()}
    out = []
    for n in load():
        s = score(n, month)
        p = perf.get(n["id"])
        # Bonus si la niche vend déjà bien chez toi (le système apprend de tes ventes)
        if p and p["listed"] >= 10:
            s = round(s + min(p["sell_through"] / 20, 2), 1)
        out.append({**n, "score": s, "in_season": month in n["months"], "perf": p})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def winning_products(niche_id: str | None = None):
    items = json.loads((config.DATA_DIR / "winning_products.json").read_text(encoding="utf-8"))
    return [i for i in items if niche_id is None or i["niche"] == niche_id]
