"""2.2 : Depop (frais), coût fixe par vente, bénéfice minimum 20 €, niches alternatives."""
import json

from fastapi.testclient import TestClient


def test_platform_fees_and_net():
    from vinted_drop import db, pricing
    db.init()
    assert db.platform_fee(60, "vinted") == 0
    assert db.platform_fee(60, "depop") == round(60 * 0.129 + 0.35, 2)
    assert pricing.net(60, 18.22, "depop") == round(60 - 60 * 0.129 - 0.35 - 18.22, 2)
    # le prix Depop équivalent laisse au moins le bénéfice Vinted
    total = 15.22 + 3
    v = 59.9
    d = pricing.equivalent_price(v, total, "depop")
    assert d > v and pricing.net(d, total, "depop") >= pricing.net(v, total, "vinted")


def test_cost_per_sale_and_min_profit_defaults():
    from vinted_drop import db
    db.init()
    assert db.cost_per_sale() == 3
    assert float(db.get_setting("min_profit")) == 20
    db.set_setting("cost_per_sale", "2.5")
    assert db.cost_per_sale() == 2.5


def test_v22_migration_only_changes_old_default(tmp_path):
    from vinted_drop import db
    db.init()
    db.set_setting("min_profit", "35")
    with db.connect() as c:
        c.execute("DELETE FROM settings WHERE key='_v22_defaults'")
    db.init()
    assert db.get_setting("min_profit") == "35"            # valeur personnalisée gardée
    db.set_setting("min_profit", "30")
    with db.connect() as c:
        c.execute("DELETE FROM settings WHERE key='_v22_defaults'")
    db.init()
    assert db.get_setting("min_profit") == "20"            # ancien défaut → 20 €
    db.set_setting("min_profit", "30")
    db.init()                                              # une seule fois
    assert db.get_setting("min_profit") == "30"


def test_depop_sale_deducts_fees_and_breakdown():
    from vinted_drop import db
    db.init()
    sid = db.add_shop("Alt", "bottes-plateforme-goth")
    p1 = db.add_product(cost=18.04, shop_id=sid, niche="bottes-plateforme-goth")
    p2 = db.add_product(cost=18.04, shop_id=sid, niche="bottes-plateforme-goth")
    for p in (p1, p2):
        db.update_product(p, title="Bottes", price=60, status="published")
    s1 = db.get_sale(db.record_sale(p1, 60, platform="depop"))
    s2 = db.get_sale(db.record_sale(p2, 60))
    assert s1["platform"] == "depop" and s1["fees"] == round(60 * 0.129 + 0.35, 2)
    assert s1["profit"] == round(60 - 18.04 - 3 - s1["fees"], 2)
    assert s2["platform"] == "vinted" and s2["profit"] == round(60 - 18.04 - 3, 2)
    rows = {r["platform"]: r for r in db.platform_breakdown()}
    assert rows["depop"]["sales"] == 1 and rows["vinted"]["sales"] == 1
    try:
        db.record_sale(db.add_product(cost=5, shop_id=sid), 30, platform="ebay")
        raise AssertionError("plateforme inconnue acceptée")
    except ValueError:
        pass


def test_compute_prices_has_both_platforms():
    from vinted_drop import db, pricing
    db.init()
    r = pricing.compute_prices(15.22)
    assert set(r["platforms"]) == {"vinted", "depop"}
    assert r["platforms"]["depop"]["price"] >= r["platforms"]["vinted"]["price"]
    assert r["platforms"]["vinted"]["profit_at_min"] >= 20


def test_alt_niches_use_depop_data_and_respect_min_profit():
    from vinted_drop import db, niches
    db.init()
    ranked = {n["id"]: n for n in niches.ranked(11)}
    nap = ranked["veste-militaire-napoleon"]["potential"]
    assert "depop" in nap["profits"] and nap["meets_min_profit"]
    assert any("Depop" in c["source"] for c in nap["components"])
    for bad in ("corset-goth", "jeans-baggy-y2k", "sweat-zip-strass"):
        assert not ranked[bad]["potential"]["meets_min_profit"], bad
    for good in ("bottes-plateforme-goth", "tenue-whimsigoth-lot"):
        assert ranked[good]["potential"]["meets_min_profit"], good
    # chaque niche a au moins un produit, chaque lien AliExpress est une fiche produit
    prods = niches.winning_products()
    assert {p["niche"] for p in prods} >= set(ranked)
    for p in prods:
        if p["supplier"] == "AliExpress":
            assert p["url"].startswith("https://fr.aliexpress.com/item/") and p["sold"].endswith("commandes")


def test_winning_product_profit_badge():
    from vinted_drop import db, niches
    db.init()
    prods = {p["url"]: p for p in niches.winning_products("veste-militaire-napoleon")}
    cheap = prods["https://fr.aliexpress.com/item/1005010797735275.html"]
    pricey = prods["https://fr.aliexpress.com/item/1005013002583595.html"]
    assert cheap["meets_min_profit"] and cheap["best_platform"] == "depop"
    assert not pricey["meets_min_profit"]


def test_depop_listing_and_product_page():
    from vinted_drop import db, listing
    from vinted_drop.dashboard import app as app_mod
    db.init()
    sid = db.add_shop("Alt", "veste-militaire-napoleon")
    pid = db.add_product(cost=15.22, shop_id=sid, niche="veste-militaire-napoleon", size="M")
    db.update_product(pid, title="Veste militaire noire col montant", description="Veste courte, boutons dorés. #goth",
                      keywords=["veste militaire", "goth"])
    d = listing.depop_listing(pid)
    assert d["text"].startswith("Veste militaire noire col montant")
    assert "#napoleonjacket" in d["text"] and "Taille : M" in d["text"] and len(d["hashtags"]) <= 5
    assert d["price"] and d["profit_at_price"] >= 20
    with TestClient(app_mod.app) as c:
        r = c.get(f"/produit/{pid}")
        assert r.status_code == 200 and "Annonce Depop" in r.text and "depop-text" in r.text
        r = c.post(f"/produit/{pid}/action/vendu", data={"sale_price": "60", "platform": "depop"},
                   headers={"origin": "http://testserver"}, follow_redirects=False)
        assert r.status_code in (302, 303)
        assert db.list_sales()[0]["platform"] == "depop"
        assert "Par plateforme" in c.get("/finances").text
        assert "Coût fixe par vente" in c.get("/parametres").text
        assert "Depop (relevé du" in c.get("/niches?rentables=0").text


def test_niches_json_is_valid():
    from vinted_drop import config
    data = json.loads((config.DATA_DIR / "niches.json").read_text(encoding="utf-8"))
    ids = [n["id"] for n in data["niches"]]
    assert len(ids) == len(set(ids))
    for n in data["niches"]:
        if n.get("depop"):
            d = n["depop"]
            assert d["p25"] <= d["median"] <= d["p75"] and d["n"] >= 10


def test_bundle_niche_profit_uses_full_outfit_cost():
    from vinted_drop import db, niches
    db.init()
    items = niches.winning_products("tenue-whimsigoth-lot")
    profits = {round(p["est_profit"], 2) for p in items if p["supplier"] == "AliExpress"}
    assert len(profits) == 1                      # même tenue, même bénéfice, quelle que soit la pièce
    assert all(p["bundle_cost"] == 29.31 for p in items)
