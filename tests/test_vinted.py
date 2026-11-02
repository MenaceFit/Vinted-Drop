"""Tests du pilote automatique avec un faux Vinted (aucun appel réseau)."""
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from vinted_drop import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    (tmp_path / "media").mkdir()
    from vinted_drop import db, notify
    db.init()
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, photo_path=None, buttons=None: sent.append((text, buttons)) or True)
    yield sent


class FakeVinted:
    """Remplace VintedClient.call : répond selon le nom de l'endpoint."""
    def __init__(self):
        self.calls = []
        self.items = []
        self.orders = []
        self.inbox = []
        self.convs = {}

    def __call__(self, client, name, path_args=None, retry=True, **kw):
        self.calls.append((name, path_args, kw))
        if name == "me":
            return {"user": {"id": 42, "login": "sophie_goth"}}
        if name == "wardrobe":
            return {"items": self.items, "pagination": {"total_pages": 1}}
        if name == "sold_orders":
            return {"my_orders": self.orders}
        if name == "transaction":
            return {"transaction": {"item_id": 999, "buyer": {"login": "julie"}}}
        if name == "inbox":
            return {"conversations": self.inbox}
        if name == "conversation":
            return {"conversation": self.convs[path_args["cid"]]}
        if name == "upload_photo":
            return {"id": 7000 + len(self.calls)}
        if name == "create_item":
            return {"item": {"id": 555, "url": "https://www.vinted.fr/items/555-robe"}}
        if name == "get_own_item":
            return {"item": {"id": path_args["item_id"], "price": "40.0", "catalog_id": 1065, "brand_id": 14803,
                             "size_id": 207, "size_title": "M / 38", "package_size_id": 2,
                             "photos": [{"id": 1, "orientation": 0}]}}
        if name == "search":
            return {"items": [{"favourite_count": 12, "price": {"amount": "30.0"}},
                              {"favourite_count": 4, "price": "26"}], "pagination": {"total_entries": 800}}
        return {}


@pytest.fixture
def fake(monkeypatch):
    from vinted_drop import vinted_client
    f = FakeVinted()
    monkeypatch.setattr(vinted_client.VintedClient, "call", lambda self, name, path_args=None, retry=True, **kw: f(self, name, path_args, retry, **kw))
    monkeypatch.setattr(vinted_client.VintedClient, "refresh_session", lambda self: 200)
    return f


def _shop():
    from vinted_drop import db, vinted_client
    sid = db.add_shop("Sophie", "goth-classique")
    vinted_client.connect_shop(sid, "Cookie: access_token_web=abc; refresh_token_web=def; other=1")
    return sid


def test_parse_cookie_header():
    from vinted_drop.vinted_client import parse_cookie_header
    c = parse_cookie_header("Cookie: access_token_web=abc; refresh_token_web=d=ef")
    assert c == {"access_token_web": "abc", "refresh_token_web": "d=ef"}
    assert parse_cookie_header("eyJhbGciOi")["access_token_web"] == "eyJhbGciOi"


def test_connect_shop(fake):
    from vinted_drop import db
    sid = _shop()
    s = db.get_shop(sid)
    assert s["vinted_status"] == "connected" and s["vinted_login"] == "sophie_goth" and s["vinted_user_id"] == 42
    from vinted_drop.security import decrypt
    assert s["vinted_cookies"].startswith("enc:v1:")           # chiffré en base
    assert json.loads(decrypt(s["vinted_cookies"]))["refresh_token_web"] == "def"


def test_sync_detects_sale_and_notifies(fake, env):
    from vinted_drop import autopilot, db
    sid = _shop()
    pid = db.add_product(cost=12, supplier_url="https://fr.shein.com/robe", shop_id=sid)
    db.update_product(pid, status="published", vinted_item_id=999, price=34.9, title="Robe gothique")
    fake.items = [{"id": 999, "favourite_count": 6, "view_count": 80, "price": {"amount": "34.9"}},
                  {"id": 1234, "title": "Ancienne annonce", "price": "20"}]
    fake.orders = [{"transaction_id": 77, "title": "Robe gothique", "price": {"amount": "32.0"}, "status": "paid"}]
    autopilot.run_sync()
    sale = db.sale_by_transaction(77)
    assert sale and sale["sale_price"] == 32.0 and sale["buyer"] == "julie" and sale["source"] == "vinted"
    assert db.get_product(pid)["status"] == "sold" and db.get_product(pid)["favourites"] == 6
    assert db.product_by_vinted_id(1234)["status"] == "published"  # annonce existante importée
    text, buttons = [m for m in env if "Vente" in m[0]][0]
    assert buttons[0][0][1] == "https://fr.shein.com/robe"  # bouton Commander
    autopilot.run_sync()  # pas de doublon
    assert db.stats()["sales"] == 1


def test_publish_with_learned_template(fake, tmp_path):
    from vinted_drop import config, db, publisher
    sid = _shop()
    tid = publisher.learn_template(sid, "https://www.vinted.fr/items/31337-robe-noire")
    assert db.get_shop(sid)["template_id"] == tid and db.get_template(tid)["size_map"] == {"M": 207}
    img = config.MEDIA_DIR / "product_1" / "porte_1.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"png")
    pid = db.add_product(cost=12, shop_id=sid, size="M")
    db.update_product(pid, title="Robe", description="Neuve", price=34.9, min_price=29.9,
                      photos=["product_1/porte_1.png"], status="ready")
    res = publisher.publish(pid)
    assert res["item_id"] == 555
    payload = [kw["json"] for n, _, kw in fake.calls if n == "create_item"][0]["item"]
    assert payload["catalog_id"] == 1065 and payload["size_id"] == 207 and payload["status_id"] == 6
    assert len(payload["assigned_photos"]) == 1
    assert db.get_product(pid)["status"] == "published"


def test_publish_without_template_fails_cleanly(fake):
    from vinted_drop import db, publisher
    sid = _shop()
    pid = db.add_product(cost=12, shop_id=sid)
    with pytest.raises(ValueError, match="modèle d'annonce"):
        publisher.publish(pid)


def test_publish_window_and_validation(fake, env):
    from vinted_drop import autopilot, db
    sid = _shop()
    pid = db.add_product(cost=12, shop_id=sid)
    db.update_product(pid, title="Robe", description="x", price=30, status="ready")
    assert autopilot.run_publish(now=datetime(2026, 11, 2, 22, 0)) == {}  # hors plage horaire
    autopilot.run_publish(now=datetime(2026, 11, 2, 11, 0))  # mode validation par défaut
    assert db.get_product(pid)["approval_sent_at"]
    assert any(b and b[0][0][1] == f"pub:{pid}" for _, b in env)


def test_price_drop_never_below_min(fake):
    from vinted_drop import autopilot, db
    sid = _shop()
    pid = db.add_product(cost=12, shop_id=sid)
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    db.update_product(pid, status="published", vinted_item_id=31337, price=31.9, min_price=30.9, title="Robe",
                      published_at=old)
    autopilot.run_price_drops()
    p = db.get_product(pid)
    assert p["price"] == 30.9
    put = [kw["json"] for n, _, kw in fake.calls if n == "update_item"][0]
    assert put["item"]["price"] == 30.9


def test_inbox_offer_counter_and_draft(fake, env, monkeypatch):
    from vinted_drop import ai, autopilot, db
    sid = _shop()
    db.set_setting("offer_mode", "auto")   # par défaut : validation humaine
    pid = db.add_product(cost=12, shop_id=sid)
    db.update_product(pid, status="published", vinted_item_id=999, price=34.9, min_price=29.9, title="Robe")
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {"intent": "offer", "reply": "Je peux te la faire à 32 € 😊", "confidence": 0.9})
    fake.inbox = [{"id": 5, "unread": True}]
    fake.convs[5] = {"opposite_user": {"login": "julie"}, "transaction": {"id": 77, "item_id": 999},
                     "messages": [{"entity_type": "offer_request_message", "id": 1,
                                   "entity": {"user_id": 100, "price": {"amount": "25.0"}, "body": ""}}]}
    autopilot.run_inbox()
    offer = [kw["json"] for n, _, kw in fake.calls if n == "offer"][0]
    assert float(offer["offer"]["price"]) >= 29.9  # contre-offre jamais sous le mini
    conv = db.get_conversation(5)
    assert conv["status"] == "pending" and conv["draft"].startswith("Je peux")
    assert any(b and b[0][0][1] == "rs:5" for _, b in env)  # validation Telegram
    n = len(fake.calls)
    autopilot.run_inbox()  # même message : pas retraité
    assert not [c for c in fake.calls[n:] if c[0] == "offer"]


def test_market_scan_updates_ranking(fake):
    from vinted_drop import autopilot, niches
    _shop()
    top = autopilot.run_market_scan()
    assert top and all(n.get("live") is not None for n in niches.ranked()[:3])


def test_assistant_tools_run():
    from vinted_drop import assistant, db
    db.add_shop("Luna", "medieval")
    assert "sales" in assistant.run_tool("get_stats", {"period": "7d"})
    assert assistant.run_tool("set_setting", {"key": "publish_mode", "value": "auto"})["ok"]
    assert db.get_setting("publish_mode") == "auto"
    assert assistant.run_tool("top_niches", {})


def test_new_dashboard_pages(fake):
    from fastapi.testclient import TestClient
    from vinted_drop.dashboard import app as app_mod
    sid = _shop()
    with TestClient(app_mod.app) as c:
        for url in ["/", "/boutiques", "/pilote", "/messages", "/niches"]:
            assert c.get(url).status_code == 200, url
        r = c.post(f"/boutique/{sid}/vinted", data={"cookie": "access_token_web=zzz", "domain": "vinted.fr"},
                   follow_redirects=False)
        assert r.status_code == 303 and "ok=" in r.headers["location"]
        assert c.post("/pilote", data={"publish_mode": "auto"}, follow_redirects=False).status_code == 303
    from vinted_drop import db
    assert db.get_setting("publish_mode") == "auto"


def test_kids_niche_never_uses_ai_model():
    from vinted_drop import db, photos
    pid = db.add_product(cost=10, niche="enfant-fetes")
    assert "porte" not in photos.default_kinds(db.get_product(pid))
