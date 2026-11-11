import importlib

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from vinted_drop import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "PACKAGING_COST", 0.5)
    from vinted_drop import db
    db.init()
    yield


def test_prices_respect_min_profit_and_multiplier():
    from vinted_drop.pricing import compute_prices
    r = compute_prices(10, multiplier=2.5, min_profit=8, buffer=0.15)
    assert r["min_price"] >= 25
    assert r["price"] > r["min_price"]
    assert r["profit_at_min"] >= 8
    cheap = compute_prices(3, multiplier=2.5, min_profit=8, buffer=0)
    assert cheap["profit_at_min"] >= 8  # le bénéfice mini l'emporte sur le x2.5


def test_sale_flow_and_stats():
    from vinted_drop import db
    sid = db.add_shop("Sophie", "goth-classique")
    pid = db.add_product(cost=12.0, supplier_url="https://fr.shein.com/x", shop_id=sid, niche="goth-classique")
    db.update_product(pid, status="published", price=34.9, min_price=29)
    sale_id = db.record_sale(pid, 32.0)
    sale = db.get_sale(sale_id)
    assert sale["profit"] == 17.0          # 32 - 12 - 3 € de coût fixe par vente (Vinted : 0 frais)
    assert db.get_product(pid)["status"] == "sold"
    s = db.stats()
    assert s["sales"] == 1 and s["revenue"] == 32.0
    assert db.today_stats()["sales"] == 1
    assert db.shop_breakdown()[0]["profit"] == 17.0
    assert len(db.daily_series(30)) == 30


def test_niches_ranked_by_season():
    from vinted_drop import niches
    top = niches.ranked(month=12)
    assert top[0]["in_season"]
    assert all(n["in_season"] for n in top[:5])
    assert niches.winning_products("noel-pulls")


def test_dashboard_pages_render():
    from fastapi.testclient import TestClient
    from vinted_drop.dashboard import app as app_mod
    importlib.reload(app_mod)
    from vinted_drop import db
    db.add_shop("Luna", "medieval")
    pid = db.add_product(cost=15, supplier_url="https://fr.shein.com/y")
    with TestClient(app_mod.app) as c:
        for url in ["/", "/produits", f"/produit/{pid}", "/ventes", "/boutiques", "/niches", "/api/stats"]:
            assert c.get(url).status_code == 200, url
        r = c.post(f"/produit/{pid}/action/vendu", data={"sale_price": "39"}, follow_redirects=False)
        assert r.status_code == 303
    assert db.stats()["sales"] == 1


def test_product_score_rewards_proof_and_margin():
    from vinted_drop import niches
    n = niches.get("noel-pulls")
    strong = niches.product_score({"reviews": 15000, "rating": 4.8, "cost": 10}, n, 12)
    weak = niches.product_score({"cost": 30}, n, 12)
    assert strong["score"] > weak["score"]
    ranked = niches.winning_products(month=11)
    assert ranked[0]["score"] >= ranked[-1]["score"]
