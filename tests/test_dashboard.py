"""Dashboard 2.0 : chaque page s'affiche (vide et remplie) et chaque formulaire agit sur la base."""
import pytest
from fastapi.testclient import TestClient

PAGES = ["/", "/?period=today", "/?period=7d", "/?period=90d", "/?period=year", "/?period=all",
         "/?period=custom&start=2026-10-01&end=2026-10-31", "/produits", "/produits?vue=liste&tri=price",
         "/studio", "/ventes", "/commandes", "/finances", "/finances?period=7d", "/boutiques", "/boutiques/comparer",
         "/connexions", "/niches", "/niches?rentables=0", "/niches?saison=1", "/gagnants", "/opportunites",
         "/messages", "/activite", "/pilote", "/parametres", "/api/stats", "/api/search?q=trench", "/api/tasks", "/health"]


@pytest.fixture
def client():
    from vinted_drop.dashboard import app as app_mod
    with TestClient(app_mod.app) as c:
        yield c


def seed():
    from vinted_drop import db, market, tasks
    sid = db.add_shop("Sophie", "trench")
    pid = db.add_product(cost=25, supplier_url="https://www.temu.com/a", supplier="Temu", shop_id=sid, niche="trench",
                         supplier_fees=2)
    db.update_product(pid, title="Trench beige ceinturé", price=79.9, min_price=69.9, status="published",
                      photos=[], keywords=["trench", "beige"])
    pid2 = db.add_product(cost=30, shop_id=sid, niche="trench")
    db.update_product(pid2, title="Trench noir", price=89.9, status="published")
    sale = db.record_sale(pid, 75)
    db.log_cost("gemini", "m", "porte", 0.03, pid2)
    db.log_generation(product_id=pid2, kind="plat", provider="gemini", model="m", duration_s=12.5, cost=0.03,
                      attempt=1, fidelity=8, status="accepted")
    db.log_event("sale", "Vente test", shop_id=sid, product_id=pid)
    db.log_event("error", "Erreur test", level="error")
    db.add_expense(5, "tools", "abonnement", sid)
    db.upsert_conversation(9, shop_id=sid, buyer="julie", last_buyer_message="Dispo ?", draft="Oui !", status="pending")
    db.add_persona("Sophie", "a woman")
    market.detect_opportunities(month=2)
    tasks.submit("test", lambda: "ok")
    tasks.wait_all()
    return sid, pid, pid2, sale


def test_all_pages_empty(client):
    for url in PAGES:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:300])


def test_all_pages_with_data(client):
    sid, pid, pid2, sale = seed()
    for url in PAGES + [f"/produit/{pid}", f"/produit/{pid2}", f"/boutique/{sid}", f"/boutique/{sid}?period=7d"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:400])
    home = client.get("/").text
    assert "Trench beige" in home and "Centre de contrôle" in home
    assert "75,00" in client.get("/ventes").text
    assert "À acheter" in client.get("/commandes").text


def test_no_fake_numbers_on_empty_dashboard(client):
    html = client.get("/").text
    assert "0,00 €" in html and "Trench" not in html


def test_forms_change_the_database(client):
    from vinted_drop import autopilot, backup, db
    sid, pid, pid2, sale = seed()
    nr = lambda r: (r.status_code, r.headers.get("location", ""))
    assert nr(client.post("/produits", data={"url": "", "cost": "12,5", "shop_id": str(sid)}, follow_redirects=False))[0] == 303
    newest = db.list_products(limit=1)[0]
    assert newest["cost"] == 12.5 and newest["shop_id"] == sid
    client.post(f"/produit/{pid2}/modifier", data={"title": "Trench noir long", "supplier_fees": "3", "other_fees": "1",
                                                   "cost": "30", "status": "ready"}, follow_redirects=False)
    p = db.get_product(pid2)
    assert p["title"] == "Trench noir long" and p["supplier_fees"] == 3 and p["status"] == "ready"
    client.post(f"/produit/{pid2}/prix-conseille", follow_redirects=False)
    assert db.get_product(pid2)["min_price"] >= 30 + 3 + 1 + 0.5 + 30 - 0.01
    r = client.post("/produits/lot", data={"ids": [str(pid2)], "action": "archiver"}, follow_redirects=False)
    assert r.status_code == 303 and db.get_product(pid2)["status"] == "archived"
    o = db.order_for_sale(sale)
    client.post(f"/commande/{o['id']}", data={"status": "ordered", "supplier_ref": "PO-9", "note": "ok"}, follow_redirects=False)
    assert db.get_order(o["id"])["status"] == "ordered" and db.get_order(o["id"])["supplier_ref"] == "PO-9"
    client.post("/finances/depense", data={"amount": "7,5", "category": "ads", "label": "boost"}, follow_redirects=False)
    assert any(e["amount"] == 7.5 for e in db.list_expenses())
    assert client.post("/finances/depense", data={"amount": "-3"}, follow_redirects=False).headers["location"].count("err=")
    r = client.get("/finances/export.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get("/finances/export.csv?table=Ventes")
    assert r.status_code == 200 and "Trench" in r.content.decode("utf-8-sig")
    assert client.get("/finances/export.csv?table=secrets").status_code == 400
    client.post("/pilote/tache/sync", data={"enabled": "0", "every": "30", "retries": "2", "alert": "1"}, follow_redirects=False)
    cfg = autopilot.job_config("sync")
    assert cfg["enabled"] is False and cfg["every"] == 30 and cfg["retries"] == 2
    client.post("/pilote/urgence", data={"action": "stop"}, follow_redirects=False)
    assert db.get_setting("emergency_stop") == "on"
    assert "Arrêt d'urgence activé" in client.get("/").text
    client.post("/pilote/urgence", data={"action": "resume"}, follow_redirects=False)
    assert db.get_setting("emergency_stop") == "off"
    client.post("/pilote", data={"simulation": "on", "notify_sales": "off"}, follow_redirects=False)
    assert db.get_setting("simulation") == "on" and db.get_setting("notify_sales") == "off"
    client.post("/parametres", data={"min_profit": "35", "listing_style": "minimaliste"}, follow_redirects=False)
    assert db.get_setting("min_profit") == "35" and db.get_setting("listing_style") == "minimaliste"
    assert "err=" in client.post("/parametres", data={"min_profit": "abc"}, follow_redirects=False).headers["location"]
    client.post(f"/boutique/{sid}/reglages", data={"monthly_goal": "500", "publish_start": "9", "publish_end": "18",
                                                    "listing_style": "detaille", "daily_quota": "25"}, follow_redirects=False)
    shop = db.get_shop(sid)
    assert shop["monthly_goal"] == 500 and shop["publish_start"] == 9 and shop["daily_quota"] == 25
    assert "err=" in client.post("/boutiques", data={"name": "Sophie"}, follow_redirects=False).headers["location"]
    client.post("/sauvegardes/creer", follow_redirects=False)
    name = backup.list_backups()[0]["name"]
    assert "err=" in client.post("/sauvegardes/restaurer", data={"name": name}, follow_redirects=False).headers["location"]
    assert "ok=" in client.post("/sauvegardes/restaurer", data={"name": name, "confirm": "RESTAURER"},
                                follow_redirects=False).headers["location"]


def test_csrf_blocks_foreign_origin(client):
    from vinted_drop import db
    r = client.post("/pilote/urgence", data={"action": "stop"}, headers={"Origin": "https://evil.example"},
                    follow_redirects=False)
    assert r.status_code == 403 and db.get_setting("emergency_stop") == "off"
    r = client.post("/pilote/urgence", data={"action": "stop"}, headers={"Origin": "http://testserver"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_password_protects_everything(client, monkeypatch):
    from vinted_drop import config
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/api/stats").status_code == 401
    assert client.get("/", auth=("admin", "s3cret")).status_code == 200


def test_record_sale_twice_from_dashboard_is_refused(client):
    sid, pid, pid2, sale = seed()
    r = client.post(f"/produit/{pid}/action/vendu", data={"sale_price": "70"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_product_page_with_photos_without_metadata(client):
    from vinted_drop import db
    sid, pid, pid2, sale = seed()
    db.update_product(pid2, photos=["product_2/manual.jpg"], photo_meta={})
    assert client.get(f"/produit/{pid2}").status_code == 200
