"""Market Intelligence : analyse du marché Vinted, score de potentiel explicable, opportunités.

Données utilisées, par ordre de fiabilité :
1. Relevés réels de Vinted (recherche par mots-clés avec TA session connectée, à faible fréquence) :
   nombre d'annonces, favoris, prix (médiane, quartiles), marques les plus présentes. Historisés.
2. Tes propres ventes (taux de vente, bénéfice par niche).
3. Estimations manuelles de data/niches.json (prix d'achat / de vente, saison) — signalées « faible confiance ».

Aucun contournement : si Vinted refuse ou limite, le relevé s'arrête et le score repose sur les autres données.
"""
import json
import math
import statistics
import time
from collections import Counter
from datetime import date, datetime, timedelta

from . import config, db, niches
from .vinted_client import VintedClient, price_of


# ---------------- relevés ----------------
def _quantiles(prices):
    if len(prices) < 4:
        m = statistics.median(prices) if prices else None
        return m, m, m
    q = statistics.quantiles(prices, n=4)
    return round(q[0], 2), round(statistics.median(prices), 2), round(q[2], 2)


def scan_keyword(client: VintedClient, kw: str) -> dict:
    data = client.search(kw)
    items = data.get("items") or []
    total = (data.get("pagination") or {}).get("total_entries") or len(items)
    favs = [i.get("favourite_count") or 0 for i in items]
    prices = [p for p in (price_of(i) for i in items) if p]
    brands = Counter((i.get("brand_title") or "").strip() for i in items if (i.get("brand_title") or "").strip())
    p25, med, p75 = _quantiles(prices)
    return {"keyword": kw, "total": total, "avg_fav": round(statistics.mean(favs), 2) if favs else 0,
            "median_price": med, "p25_price": p25, "p75_price": p75, "sample": len(items),
            "brands": [b for b, _ in brands.most_common(5)]}


def opportunity(rows: list[dict]) -> float:
    """Score 0-10 (compatibilité 1.x) : beaucoup de favoris par annonce et peu d'annonces."""
    if not rows:
        return 0
    fav = statistics.mean(r["avg_fav"] for r in rows)
    total = statistics.mean(r["total"] for r in rows)
    return round(min(10, 4 * math.log1p(fav) + 6 / math.log10(total + 10)), 1)


def scan_all(client: VintedClient, max_keywords=3) -> dict:
    taken = db.now()
    result = {"date": date.today().isoformat(), "niches": {}}
    for n in niches.load():
        rows = []
        for kw in n["keywords"][:max_keywords]:
            try:
                r = scan_keyword(client, kw)
            except Exception as e:
                rows.append({"keyword": kw, "error": str(e)[:200], "total": 0, "avg_fav": 0, "median_price": None})
                if "429" in str(e) or "403" in str(e):
                    break  # Vinted limite : on arrête proprement
                continue
            rows.append(r)
            with db.connect() as c:
                c.execute("INSERT INTO market_snapshots (niche, keyword, total, avg_fav, median_price, p25_price, "
                          "p75_price, sample, brands, taken_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (n["id"], kw, r["total"], r["avg_fav"], r["median_price"], r["p25_price"], r["p75_price"],
                           r["sample"], json.dumps(r["brands"], ensure_ascii=False), taken))
            clear_cache()
        ok = [r for r in rows if "error" not in r]
        meds = [r["median_price"] for r in ok if r.get("median_price")]
        result["niches"][n["id"]] = {"score": opportunity(ok), "keywords": rows,
                                     "median_price": statistics.median(meds) if meds else None}
    db.set_setting("market_scan", json.dumps(result, ensure_ascii=False))
    db.log_event("market", f"Analyse marché : {len(result['niches'])} niches relevées")
    detect_opportunities()
    return result


def last_scan():
    raw = db.get_setting("market_scan")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


_snap_cache: dict = {}
SNAP_RECHECK_S = 5   # un relevé ajouté par un autre processus (pilote auto lancé à part) est vu sous 5 s


def clear_cache():
    _snap_cache.clear()


def _all_snapshots():
    """Tous les relevés des 120 derniers jours, groupés par niche. Rechargés seulement quand un relevé
    est ajouté (clé = base + nombre + dernier id), donc une page Niches ne fait qu'une requête."""
    since = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    if (_snap_cache.get("db") == str(config.DB_PATH) and _snap_cache.get("since") == since
            and time.monotonic() - _snap_cache.get("checked", 0) < SNAP_RECHECK_S):
        return _snap_cache["data"]
    with db.connect() as c:
        cnt, last = c.execute("SELECT COUNT(*), MAX(id) FROM market_snapshots").fetchone()
        key = (str(config.DB_PATH), cnt, last, since)
        if _snap_cache.get("key") != key:
            grouped = {}
            for r in c.execute("SELECT * FROM market_snapshots WHERE taken_at >= ? ORDER BY taken_at", (since,)):
                row = db._row(r)
                grouped.setdefault(row["niche"], []).append(row)
            _snap_cache.update(key=key, data=grouped, history={})
        _snap_cache.update(db=str(config.DB_PATH), since=since, checked=time.monotonic())
    return _snap_cache["data"]


def _snapshots(niche_id, since_days=120):
    rows = _all_snapshots().get(niche_id, [])
    if since_days >= 120:
        return list(rows)
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d %H:%M:%S")
    return [r for r in rows if r["taken_at"] >= since]


def history(niche_id, since_days=120) -> list[dict]:
    """Un point par relevé : annonces, favoris moyens, prix médian."""
    snaps = _all_snapshots()
    memo = _snap_cache.setdefault("history", {})
    if since_days == 120 and niche_id in memo:
        return [dict(x) for x in memo[niche_id]]
    if not snaps.get(niche_id):
        return []
    by_day = {}
    for s in _snapshots(niche_id, since_days):
        by_day.setdefault(s["taken_at"][:10], []).append(s)
    out = []
    for day, rows in sorted(by_day.items()):
        meds = [r["median_price"] for r in rows if r["median_price"]]
        out.append({"date": day, "total": sum(r["total"] or 0 for r in rows),
                    "avg_fav": round(statistics.mean(r["avg_fav"] or 0 for r in rows), 2),
                    "median_price": round(statistics.median(meds), 2) if meds else None,
                    "sample": sum(r["sample"] or 0 for r in rows)})
    if since_days == 120:
        memo[niche_id] = [dict(x) for x in out]
    return out


def niche_market(niche_id) -> dict | None:
    """Derniers chiffres réels d'une niche (pour le moteur de prix)."""
    h = history(niche_id)
    if not h:
        return None
    last = h[-1]
    snaps = [s for s in _snapshots(niche_id) if s["taken_at"][:10] == last["date"]]
    p25 = [s["p25_price"] for s in snaps if s["p25_price"]]
    p75 = [s["p75_price"] for s in snaps if s["p75_price"]]
    brands = Counter(b for s in snaps for b in (s.get("brands") or []))
    return {"median": last["median_price"], "p25": statistics.median(p25) if p25 else None,
            "p75": statistics.median(p75) if p75 else None, "sample": last["sample"], "avg_fav": last["avg_fav"],
            "total": last["total"], "brands": [b for b, _ in brands.most_common(5)],
            "age_days": (date.today() - date.fromisoformat(last["date"])).days, "date": last["date"]}


def trend(niche_id) -> dict | None:
    """Évolution entre les deux derniers relevés (favoris = demande, annonces = concurrence)."""
    h = history(niche_id)
    if len(h) < 2:
        return None
    a, b = h[-2], h[-1]
    def pct(x, y):
        return round(100 * (y - x) / x, 1) if x else None
    return {"demand_pct": pct(a["avg_fav"], b["avg_fav"]), "supply_pct": pct(a["total"], b["total"]),
            "price_pct": pct(a["median_price"] or 0, b["median_price"] or 0), "from": a["date"], "to": b["date"]}


# ---------------- score de potentiel /100 ----------------
def _mid(r):
    return niches._midpoint(r)


def platform_profits(n: dict, mk: dict | None, live: bool, per_sale: float) -> dict:
    """Bénéfice estimé par vente sur chaque plateforme, après frais et coût fixe par vente.
    Vinted : prix médian relevé si dispo, sinon estimation. Depop : médiane relevée dans data/niches.json."""
    from . import pricing
    buy = _mid(n.get("buy_price"))
    out = {}
    if not buy:
        return out
    v_sell = mk["median"] if live and mk.get("median") else _mid(n.get("sell_price"))
    if v_sell:
        out["vinted"] = {"sell": round(v_sell, 2), "profit": pricing.net(v_sell, buy + per_sale, "vinted"),
                         "source": "Vinted" if live and mk.get("median") else "estimation"}
    dp = n.get("depop") or {}
    if dp.get("median"):
        out["depop"] = {"sell": dp["median"], "profit": pricing.net(dp["median"], buy + per_sale, "depop"),
                        "source": f"Depop (relevé du {dp.get('date', '?')}, {dp.get('n', '?')} annonces)",
                        "p75": dp.get("p75"),
                        "profit_p75": pricing.net(dp["p75"], buy + per_sale, "depop") if dp.get("p75") else None}
    return out


def potential(n: dict, month: int | None = None, perf: dict | None = None, min_profit: float | None = None,
              per_sale: float | None = None) -> dict:
    """Score sur 100 détaillé composante par composante, avec le niveau de confiance des données."""
    month = month or date.today().month
    mk = niche_market(n["id"])
    tr = trend(n["id"])
    if min_profit is None:
        min_profit = float(db.get_setting("min_profit") or config.MIN_PROFIT)
    if per_sale is None:
        per_sale = db.cost_per_sale()
    comps = []
    live = bool(mk and mk.get("sample", 0) >= 10 and mk.get("age_days", 99) <= 30)

    # 1. Demande (25)
    if live:
        d = min(25, round(25 * math.log1p(mk["avg_fav"]) / math.log1p(15)))
        comps.append({"label": "Demande", "points": d, "max": 25,
                      "detail": f"{mk['avg_fav']} favoris en moyenne par annonce (relevé du {mk['date']})", "source": "Vinted"})
    else:
        est = n.get("demand", 5) / 10
        proof = niches.supplier_proof(n["id"])
        if proof:
            # estimation manuelle complétée par les ventes prouvées chez le fournisseur
            d = round(25 * (0.6 * est + 0.4 * proof["score"]))
            sold = str(proof.get("sold") or "")
            what = ((sold if ("vendu" in sold or "commande" in sold) else f"{sold} vendus") if sold
                    else f"{proof['reviews']:,} avis".replace(",", " "))
            comps.append({"label": "Demande", "points": d, "max": 25,
                          "detail": f"estimation {n.get('demand')}/10 + preuve fournisseur ({what})",
                          "source": "fournisseur"})
        else:
            d = round(est * 25)
            comps.append({"label": "Demande", "points": d, "max": 25, "detail": f"estimation {n.get('demand')}/10",
                          "source": "estimation"})

    # 2. Concurrence (20) : moins d'annonces = mieux
    if live:
        c = max(0, min(20, round(20 - 4 * math.log10(max(mk["total"], 1)))))
        comps.append({"label": "Concurrence", "points": c, "max": 20,
                      "detail": f"{mk['total']:,} annonces sur les mots-clés".replace(",", " "), "source": "Vinted"})
    else:
        c = round((10 - n.get("competition", 5)) * 2)
        comps.append({"label": "Concurrence", "points": c, "max": 20,
                      "detail": f"estimation {n.get('competition')}/10 (10 = saturé)", "source": "estimation"})

    # 3. Marge (25) : bénéfice estimé par vente vs bénéfice minimum, sur la meilleure plateforme
    buy = _mid(n.get("buy_price"))
    profits = platform_profits(n, mk, live, per_sale)
    best_platform = max(profits, key=lambda k: profits[k]["profit"]) if profits else None
    est_profit = profits[best_platform]["profit"] if best_platform else None
    sell = profits[best_platform]["sell"] if best_platform else None
    if est_profit is not None:
        m = max(0, min(25, round(25 * est_profit / (min_profit * 1.5))))
        detail = " · ".join(f"{config.PLATFORMS[k]['label']} ≈ {v['profit']:.0f} € (vente ~{v['sell']:.0f} €)"
                            for k, v in profits.items())
        comps.append({"label": "Marge", "points": m, "max": 25,
                      "detail": f"achat ~{buy:.0f} € + {per_sale:.0f} € par vente → {detail}",
                      "source": profits[best_platform]["source"]})
    else:
        m = 0
        comps.append({"label": "Marge", "points": 0, "max": 25, "detail": "prix inconnus", "source": "—"})

    # 4. Saison (15)
    months = n.get("months") or []
    nxt, nxt2 = month % 12 + 1, (month + 1) % 12 + 1
    sea = 15 if month in months else (10 if nxt in months else (7 if nxt2 in months else 3))
    comps.append({"label": "Saison", "points": sea, "max": 15,
                  "detail": "en pleine saison" if month in months else ("démarre le mois prochain" if nxt in months
                            else ("dans 2 mois" if nxt2 in months else "hors saison")), "source": "calendrier"})

    # 5. Tendance (10)
    if tr and tr["demand_pct"] is not None:
        t = max(0, min(10, round(5 + tr["demand_pct"] / 10)))
        comps.append({"label": "Tendance", "points": t, "max": 10,
                      "detail": f"demande {tr['demand_pct']:+.0f} % entre le {tr['from']} et le {tr['to']}", "source": "Vinted"})
    else:
        t = 5
        comps.append({"label": "Tendance", "points": 5, "max": 10, "detail": "pas encore assez de relevés (neutre)",
                      "source": "—"})

    # 6. Tes résultats (5)
    if perf and perf.get("listed", 0) >= 10:
        r = max(0, min(5, round(perf["sell_through"] / 6)))
        comps.append({"label": "Tes résultats", "points": r, "max": 5,
                      "detail": f"{perf['sell_through']} % vendus sur {perf['listed']} annonces", "source": "tes ventes"})
    else:
        r = 2
        comps.append({"label": "Tes résultats", "points": 2, "max": 5, "detail": "moins de 10 annonces (neutre)",
                      "source": "—"})

    score = sum(x["points"] for x in comps)
    conf = "haute" if live and mk["sample"] >= 30 and tr else ("moyenne" if live else "faible")
    return {"score": score, "components": comps, "confidence": conf, "est_profit": est_profit,
            "profits": profits, "best_platform": best_platform,
            "meets_min_profit": est_profit is not None and est_profit >= min_profit, "market": mk, "trend": tr,
            "sell_estimate": sell, "buy_estimate": buy}


# ---------------- opportunités ----------------
def _upsert_opportunity(kind, ref, title, niche, score, data):
    ts = db.now()
    with db.connect() as c:
        old = c.execute("SELECT data FROM opportunities WHERE kind=? AND ref=?", (kind, ref)).fetchone()
        hist = (json.loads(old["data"]).get("history", []) if old else [])
        hist = (hist + [{"date": ts[:10], "score": score}])[-30:]
        data = {**data, "history": hist}
        c.execute("INSERT INTO opportunities (kind, ref, title, niche, score, data, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(kind, ref) DO UPDATE SET title=excluded.title, "
                  "score=excluded.score, data=excluded.data, updated_at=excluded.updated_at",
                  (kind, ref, title, niche, score, json.dumps(data, ensure_ascii=False), ts, ts))


OPP_KINDS = {"emerging": "Niche émergente", "undervalued": "Produit sous-évalué", "seasonal": "Saison qui arrive",
             "low_competition": "Forte demande, peu de concurrence", "brand": "Marque en progression",
             "high_margin": "Forte marge"}


def detect_opportunities(month=None) -> int:
    """Calcule les opportunités à partir des données réelles et des estimations, et les enregistre."""
    month = month or date.today().month
    min_profit = float(db.get_setting("min_profit") or config.MIN_PROFIT)
    perf = {r["niche"]: r for r in db.niche_breakdown()}
    found = 0
    for n in niches.load():
        pot = potential(n, month, perf.get(n["id"]), min_profit)
        tr, mk = pot["trend"], pot["market"]
        base = {"potential": pot["score"], "est_profit": pot["est_profit"], "confidence": pot["confidence"]}
        if tr and (tr["demand_pct"] or 0) >= 20:
            _upsert_opportunity("emerging", n["id"], n["name"], n["id"], pot["score"],
                                {**base, "why": f"Demande en hausse de {tr['demand_pct']:.0f} % depuis le dernier relevé"})
            found += 1
        nxt = [m for m in n.get("months", []) if 0 < (m - month) % 12 <= 2]
        if nxt and month not in n.get("months", []) and pot["meets_min_profit"]:
            _upsert_opportunity("seasonal", n["id"], n["name"], n["id"], pot["score"],
                                {**base, "why": f"La saison démarre dans {min((m - month) % 12 for m in nxt)} mois : "
                                                f"prépare tes annonces maintenant"})
            found += 1
        if mk and mk["avg_fav"] >= 4 and mk["total"] <= 5000:
            _upsert_opportunity("low_competition", n["id"], n["name"], n["id"], pot["score"],
                                {**base, "why": f"{mk['avg_fav']} favoris/annonce pour seulement {mk['total']} annonces"})
            found += 1
        if pot["est_profit"] and pot["est_profit"] >= min_profit * 1.5:
            _upsert_opportunity("high_margin", n["id"], n["name"], n["id"], pot["score"],
                                {**base, "why": f"≈ {pot['est_profit']:.0f} € de bénéfice estimé par vente"})
            found += 1
        # marques en progression entre les deux derniers relevés
        snaps = _snapshots(n["id"])
        days = sorted({s["taken_at"][:10] for s in snaps})
        if len(days) >= 2:
            before = Counter(b for s in snaps if s["taken_at"][:10] == days[-2] for b in s["brands"])
            after = Counter(b for s in snaps if s["taken_at"][:10] == days[-1] for b in s["brands"])
            for brand, cnt in after.items():
                if cnt > before.get(brand, 0):
                    _upsert_opportunity("brand", f"{n['id']}:{brand}", f"{brand} ({n['name']})", n["id"], pot["score"],
                                        {**base, "why": f"{brand} apparaît plus souvent dans les annonces de la niche"})
                    found += 1
    # produits sous-évalués : prix d'achat connu, prix du marché nettement au-dessus du minimum rentable
    for w in niches.winning_products():
        mk = niche_market(w["niche"])
        sell = mk["median"] if mk and mk.get("median") else w.get("est_sell")
        if w.get("cost") and sell:
            profit = sell - w["cost"] - db.cost_per_sale()
            if profit >= min_profit * 1.3:
                _upsert_opportunity("undervalued", w["url"], w["title"], w["niche"], w["score"],
                                    {"why": f"Achat {w['cost']:.2f} € → vente ~{sell:.0f} € : ≈ {profit:.0f} € de bénéfice",
                                     "url": w["url"], "est_profit": round(profit, 2),
                                     "confidence": "moyenne" if mk else "faible"})
                found += 1
    return found


def list_opportunities(kind=None, favorite=None, limit=100):
    q, args = "SELECT * FROM opportunities WHERE 1=1", []
    if kind:
        q += " AND kind=?"
        args.append(kind)
    if favorite:
        q += " AND favorite=1"
    q += " ORDER BY favorite DESC, score DESC, updated_at DESC LIMIT ?"
    args.append(limit)
    with db.connect() as c:
        rows = [db._row(r) for r in c.execute(q, args)]
    for r in rows:
        r["kind_label"] = OPP_KINDS.get(r["kind"], r["kind"])
        hist = (r.get("data") or {}).get("history") or []
        r["delta"] = hist[-1]["score"] - hist[-2]["score"] if len(hist) >= 2 else None
    return rows


def toggle_favorite(oid, note=None):
    with db.connect() as c:
        c.execute("UPDATE opportunities SET favorite = 1 - favorite, note = COALESCE(?, note), updated_at=? WHERE id=?",
                  (note, db.now(), oid))
