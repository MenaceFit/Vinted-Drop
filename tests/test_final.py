"""Version finale : preuves fournisseurs dans le score, caches, requêtes groupées, compression."""
import json

from fastapi.testclient import TestClient


def test_supplier_proof_feeds_demand_without_vinted():
    from vinted_drop import db, market, niches
    db.init()
    proof = niches.supplier_proof("soiree-grande-taille")
    assert proof and 0 < proof["score"] <= 1
    n = niches.get("soiree-grande-taille")
    demand = market.potential(n)["components"][0]
    assert demand["source"] == "fournisseur" and "preuve fournisseur" in demand["detail"]
    # niche sans produit gagnant : estimation seule
    assert niches.supplier_proof("niche-inexistante") is None


def test_sold_count_parsing():
    from vinted_drop import niches
    assert niches._sold_count("98 000+") == 98000
    assert niches._sold_count(None) == 0


def test_every_niche_scores_and_has_products():
    from vinted_drop import db, niches
    db.init()
    ranked = niches.ranked()
    assert len(ranked) >= 36
    for n in ranked:
        assert 0 <= n["score"] <= 100
    ids = {n["id"] for n in ranked}
    for new in ("costume-homme-mariage", "soiree-grande-taille", "veste-moto-homme", "robe-boheme-ete", "veste-poils"):
        assert new in ids
        assert niches.winning_products(new), new
    for p in niches.winning_products():
        assert p["url"].startswith("https://")


def test_json_cache_reloads_when_file_changes():
    from vinted_drop import config, niches
    first = niches.load()
    first[0]["name"] = "modifié en mémoire"          # une copie : le cache n'est pas touché
    assert niches.load()[0]["name"] != "modifié en mémoire"
    path = config.DATA_DIR / "niches.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["niches"] = data["niches"][:3]
    path.write_text(json.dumps(data), encoding="utf-8")
    import os
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert len(niches.load()) == 3


def test_snapshot_cache_sees_new_rows():
    from vinted_drop import db, market
    db.init()
    assert market.history("trench") == []
    with db.connect() as c:
        c.execute("INSERT INTO market_snapshots (niche, keyword, total, avg_fav, median_price, sample, taken_at) "
                  "VALUES ('trench','trench',500,4,60,40,?)", (db.now(),))
    market.clear_cache()      # dans l'appli, scan_all vide le cache ; un autre processus est vu sous 5 s
    assert len(market.history("trench")) == 1


def test_order_histories_batch():
    from vinted_drop import db
    db.init()
    sid = db.add_shop("A", "trench")
    pids = [db.add_product(cost=20, shop_id=sid) for _ in range(3)]
    for p in pids:
        db.update_product(p, title="x", price=80, status="published")
        db.record_sale(p, 80)
    orders = db.list_orders()
    h = db.order_histories(o["id"] for o in orders)
    for o in orders:
        assert h[o["id"]] == db.order_history(o["id"])
    assert db.order_histories([]) == {}


def test_gzip_and_static_cache_headers():
    from vinted_drop.dashboard import app as app_mod
    with TestClient(app_mod.app) as c:
        r = c.get("/niches", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
        r = c.get("/static/app.css?v=21")
        assert "immutable" in r.headers.get("cache-control", "")
