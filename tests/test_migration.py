"""Migration d'une base 1.x vers 2.0 : données gardées, sauvegarde créée, commandes reprises, cookies chiffrés."""
import json
import sqlite3


V1_SCHEMA = """
CREATE TABLE shops (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, niche TEXT, persona_id INTEGER,
  vinted_url TEXT, active INTEGER DEFAULT 1, created_at TEXT, vinted_cookies TEXT, vinted_status TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER, niche TEXT, supplier TEXT, supplier_url TEXT,
  supplier_title TEXT, supplier_image TEXT, cost REAL NOT NULL DEFAULT 0, size TEXT, title TEXT, description TEXT,
  category TEXT, color TEXT, material TEXT, condition TEXT, price REAL, min_price REAL, photos TEXT DEFAULT '[]',
  status TEXT DEFAULT 'draft', vinted_url TEXT, created_at TEXT, published_at TEXT);
CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, shop_id INTEGER, sale_price REAL NOT NULL,
  cost REAL NOT NULL, profit REAL NOT NULL, buyer TEXT, status TEXT DEFAULT 'to_order', sold_at TEXT);
CREATE TABLE personas (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL,
  reference_image TEXT, created_at TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
"""


def make_v1(path):
    c = sqlite3.connect(path)
    c.executescript(V1_SCHEMA)
    c.execute("INSERT INTO shops (name, niche, vinted_cookies, vinted_status) VALUES ('Sophie','goth',?, 'connected')",
              (json.dumps({"access_token_web": "abc"}),))
    c.execute("INSERT INTO products (shop_id, cost, title, status, supplier_url) VALUES (1, 12, 'Robe', 'sold', 'https://x')")
    c.execute("INSERT INTO products (shop_id, cost, title, status) VALUES (1, 9, 'Pull', 'published')")
    c.execute("INSERT INTO sales (product_id, shop_id, sale_price, cost, profit, status, sold_at) "
              "VALUES (1, 1, 35, 12, 22.5, 'ordered', '2026-10-20 10:00:00')")
    c.execute("INSERT INTO settings VALUES ('publish_mode','auto')")
    c.commit()
    c.close()


def test_v1_database_is_migrated_without_loss():
    from vinted_drop import backup, config, db, vinted_client
    make_v1(config.DB_PATH)
    db.init()
    assert [b for b in backup.list_backups() if "avant-migration" in b["name"]]   # sauvegarde avant migration
    assert db.get_product(1)["title"] == "Robe" and db.get_product(2)["title"] == "Pull"
    assert db.get_setting("publish_mode") == "auto"                              # réglages conservés
    o = db.order_for_sale(1)
    assert o and o["status"] == "ordered"                                        # commande reprise
    db.init()                                                                    # 2e démarrage : pas de doublon
    assert len(db.list_orders(limit=100)) == 1
    client = vinted_client.VintedClient(db.get_shop(1))                          # ancien cookie en clair lisible
    assert client.s.cookies.get("access_token_web") == "abc"
    client._save_cookies()
    assert db.get_shop(1)["vinted_cookies"].startswith("enc:v1:")               # chiffré au premier enregistrement
