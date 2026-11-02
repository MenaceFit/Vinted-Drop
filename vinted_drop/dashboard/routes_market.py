"""Market Intelligence : niches, produits gagnants, opportunités."""
from datetime import date

from fastapi import APIRouter, Form, Request

from .. import config, db, market, niches
from .common import background, redirect, render

router = APIRouter()


@router.get("/niches")
def niches_page(request: Request, rentables: str = "1", saison: str = ""):
    month = date.today().month
    ranked = niches.ranked(month, profitable_only=rentables == "1")
    if saison == "1":
        ranked = [n for n in ranked if n["in_season"] or any(0 < (m - month) % 12 <= 2 for m in n["months"])]
    for n in ranked:
        n["history"] = market.history(n["id"])
    min_profit = float(db.get_setting("min_profit") or config.MIN_PROFIT)
    return render(request, "niches.html", niches=ranked, month=month, rentables=rentables, saison=saison,
                  min_profit=min_profit, scan=market.last_scan(), perf=db.niche_breakdown(),
                  connected=any(s.get("vinted_status") == "connected" for s in db.list_shops()))


@router.get("/gagnants")
def winners(request: Request, niche: str | None = None):
    allp = niches.winning_products()
    counts = {}
    for w in allp:
        counts[w["niche"]] = counts.get(w["niche"], 0) + 1
    ranked = niches.ranked(profitable_only=False)
    return render(request, "winners.html", niches=ranked, sel=niche, shops=db.list_shops(), counts=counts,
                  products=[w for w in allp if not niche or w["niche"] == niche],
                  min_profit=float(db.get_setting("min_profit") or config.MIN_PROFIT))


@router.get("/opportunites")
def opportunities(request: Request, type: str | None = None, favoris: str = ""):
    opps = market.list_opportunities(kind=type or None, favorite=favoris == "1")
    return render(request, "opportunities.html", opps=opps, kinds=market.OPP_KINDS, sel=type, favoris=favoris)


@router.post("/opportunites/actualiser")
def refresh_opportunities():
    background("opportunities", market.detect_opportunities, label="Détection des opportunités",
               dedupe_key="opportunities")
    return redirect("/opportunites", ok="Détection lancée")


@router.post("/opportunite/{oid}/favori")
def favorite(oid: int, note: str = Form(""), back: str = Form("/opportunites")):
    market.toggle_favorite(oid, note or None)
    return redirect(back if back.startswith("/") else "/opportunites")
