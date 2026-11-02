"""Base SQLite : modèles (personas), boutiques, produits, ventes."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    reference_image TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    niche TEXT,
    persona_id INTEGER REFERENCES personas(id),
    vinted_url TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER REFERENCES shops(id),
    niche TEXT,
    supplier TEXT,
    supplier_url TEXT,
    supplier_title TEXT,
    supplier_image TEXT,
    cost REAL NOT NULL DEFAULT 0,
    size TEXT,
    title TEXT,
    description TEXT,
    category TEXT,
    color TEXT,
    material TEXT,
    condition TEXT DEFAULT 'Neuf avec étiquette',
    price REAL,
    min_price REAL,
    photos TEXT DEFAULT '[]',
    status TEXT DEFAULT 'draft',  -- draft | ready | published | sold | archived
    vinted_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    shop_id INTEGER REFERENCES shops(id),
    sale_price REAL NOT NULL,
    cost REAL NOT NULL,
    profit REAL NOT NULL,
    buyer TEXT,
    status TEXT DEFAULT 'to_order',  -- to_order | ordered | received | shipped | done
    sold_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ai_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT, model TEXT, kind TEXT, cost REAL, product_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, shop_id INTEGER, product_id INTEGER, message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    catalog_id INTEGER, brand_id INTEGER, brand TEXT, size_id INTEGER,
    size_map TEXT DEFAULT '{}', package_size_id INTEGER DEFAULT 2, status_id INTEGER DEFAULT 6,
    color_ids TEXT DEFAULT '[]', item_attributes TEXT DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,               -- id de conversation Vinted
    shop_id INTEGER, product_id INTEGER, transaction_id INTEGER,
    buyer TEXT, last_buyer_message TEXT, last_message_key TEXT,
    draft TEXT, intent TEXT, offer_price REAL,
    status TEXT DEFAULT 'new',            -- new | pending | sent | ignored | escalated
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Colonnes ajoutées après la v1 (migration automatique des bases existantes)
MIGRATIONS = {
    "shops": {
        "vinted_domain": "TEXT DEFAULT 'vinted.fr'", "vinted_cookies": "TEXT",
        "vinted_user_agent": "TEXT", "vinted_user_id": "INTEGER", "vinted_login": "TEXT",
        "vinted_status": "TEXT DEFAULT 'disconnected'", "vinted_checked_at": "TEXT",
        "template_id": "INTEGER", "daily_quota": "INTEGER DEFAULT 20",
    },
    "products": {
        "vinted_item_id": "INTEGER", "template_id": "INTEGER", "favourites": "INTEGER DEFAULT 0",
        "views": "INTEGER DEFAULT 0", "last_price_drop_at": "TEXT", "approval_sent_at": "TEXT",
        "photo_meta": "TEXT DEFAULT '{}'",
    },
    "personas": {"decor": "TEXT", "refs": "TEXT DEFAULT '[]'"},
    "sales": {"vinted_transaction_id": "INTEGER", "source": "TEXT DEFAULT 'manuel'"},
}

PRODUCT_STATUSES = ["draft", "ready", "published", "sold", "archived"]
SALE_STATUSES = ["to_order", "ordered", "received", "shipped", "done"]


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _row(r):
    if r is None:
        return None
    d = dict(r)
    if "photos" in d:
        d["photos"] = json.loads(d["photos"] or "[]")
    if "photo_meta" in d:
        d["photo_meta"] = json.loads(d["photo_meta"] or "{}")
    if "refs" in d:
        d["refs"] = json.loads(d["refs"] or "[]")
    return d


# ---------- personas ----------
def add_persona(name, description, reference_image=None):
    with connect() as c:
        cur = c.execute("INSERT INTO personas (name, description, reference_image) VALUES (?,?,?)",
                        (name, description, reference_image))
        return cur.lastrowid


def get_persona(pid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM personas WHERE id=?", (pid,)).fetchone())


def list_personas():
    with connect() as c:
        return [_row(r) for r in c.execute("SELECT * FROM personas ORDER BY id")]


def update_persona(pid, **fields):
    if "refs" in fields and not isinstance(fields["refs"], str):
        fields["refs"] = json.dumps(fields["refs"])
    _update("personas", pid, fields)


# ---------- shops ----------
def add_shop(name, niche=None, persona_id=None, vinted_url=None):
    with connect() as c:
        cur = c.execute("INSERT INTO shops (name, niche, persona_id, vinted_url) VALUES (?,?,?,?)",
                        (name, niche, persona_id, vinted_url))
        return cur.lastrowid


def get_shop(sid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM shops WHERE id=?", (sid,)).fetchone())


def find_shop(name_or_id):
    with connect() as c:
        r = c.execute("SELECT * FROM shops WHERE id=? OR lower(name)=lower(?)",
                      (name_or_id, str(name_or_id))).fetchone()
        return _row(r)


def list_shops():
    with connect() as c:
        return [_row(r) for r in c.execute("SELECT * FROM shops ORDER BY id")]


# ---------- products ----------
def add_product(cost, supplier_url=None, supplier=None, supplier_title=None, supplier_image=None,
                shop_id=None, niche=None, size=None):
    with connect() as c:
        cur = c.execute(
            """INSERT INTO products (cost, supplier_url, supplier, supplier_title, supplier_image,
               shop_id, niche, size) VALUES (?,?,?,?,?,?,?,?)""",
            (cost, supplier_url, supplier, supplier_title, supplier_image, shop_id, niche, size))
        return cur.lastrowid


def get_product(pid):
    with connect() as c:
        return _row(c.execute(
            "SELECT p.*, s.name AS shop_name FROM products p LEFT JOIN shops s ON s.id=p.shop_id "
            "WHERE p.id=?", (pid,)).fetchone())


def list_products(status=None, shop_id=None, limit=200):
    q = ("SELECT p.*, s.name AS shop_name FROM products p LEFT JOIN shops s ON s.id=p.shop_id "
         "WHERE 1=1")
    args = []
    if status:
        q += " AND p.status=?"
        args.append(status)
    if shop_id:
        q += " AND p.shop_id=?"
        args.append(shop_id)
    q += " ORDER BY p.id DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args)]


def update_product(pid, **fields):
    for k in ("photos", "photo_meta"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    if fields.get("status") == "published":
        fields.setdefault("published_at", datetime.now().isoformat(timespec="seconds"))
    _update("products", pid, fields)


def _update(table, rid, fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), rid))


# ---------- sales ----------
def record_sale(product_id, sale_price, buyer=None, vinted_transaction_id=None, source="manuel"):
    from .pricing import profit
    p = get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    gain = profit(sale_price, p["cost"])
    with connect() as c:
        cur = c.execute(
            "INSERT INTO sales (product_id, shop_id, sale_price, cost, profit, buyer, vinted_transaction_id,"
            " source) VALUES (?,?,?,?,?,?,?,?)",
            (product_id, p["shop_id"], sale_price, p["cost"], gain, buyer, vinted_transaction_id, source))
        c.execute("UPDATE products SET status='sold' WHERE id=?", (product_id,))
        return cur.lastrowid


def get_sale(sid):
    with connect() as c:
        return _row(c.execute(
            """SELECT sa.*, p.title, p.supplier_url, p.size, p.supplier, s.name AS shop_name
               FROM sales sa LEFT JOIN products p ON p.id=sa.product_id
               LEFT JOIN shops s ON s.id=sa.shop_id WHERE sa.id=?""", (sid,)).fetchone())


def list_sales(limit=100, status=None):
    q = """SELECT sa.*, p.title, p.supplier_url, p.size, p.supplier, s.name AS shop_name
           FROM sales sa LEFT JOIN products p ON p.id=sa.product_id
           LEFT JOIN shops s ON s.id=sa.shop_id"""
    args = []
    if status:
        q += " WHERE sa.status=?"
        args.append(status)
    q += " ORDER BY sa.id DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args)]


def update_sale(sid, **fields):
    _update("sales", sid, fields)


# ---------- stats ----------
def stats(days=None):
    where, args = "", []
    if days is not None:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        where, args = " WHERE sold_at >= ?", [since]
    with connect() as c:
        r = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(sale_price),0) ca, COALESCE(SUM(profit),0) "
                      f"benef, COALESCE(SUM(cost),0) cout FROM sales{where}", args).fetchone()
        counts = {row["status"]: row["n"] for row in
                  c.execute("SELECT status, COUNT(*) n FROM products GROUP BY status")}
    ca = r["ca"]
    return {
        "sales": r["n"], "revenue": round(ca, 2), "profit": round(r["benef"], 2),
        "cost": round(r["cout"], 2), "margin_pct": round(100 * r["benef"] / ca, 1) if ca else 0,
        "avg_basket": round(ca / r["n"], 2) if r["n"] else 0,
        "products": {s: counts.get(s, 0) for s in PRODUCT_STATUSES},
    }


def today_stats():
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    hours = (datetime.now() - start).total_seconds() / 86400
    return stats(days=hours)


def daily_series(days=30):
    since = (datetime.now() - timedelta(days=days - 1)).date()
    with connect() as c:
        rows = {r["d"]: r for r in c.execute(
            "SELECT date(sold_at) d, COUNT(*) n, SUM(sale_price) ca, SUM(profit) benef FROM sales "
            "WHERE date(sold_at) >= ? GROUP BY date(sold_at)", (since.isoformat(),))}
    out = []
    for i in range(days):
        d = (since + timedelta(days=i)).isoformat()
        r = rows.get(d)
        out.append({"date": d, "sales": r["n"] if r else 0,
                    "revenue": round(r["ca"], 2) if r else 0, "profit": round(r["benef"], 2) if r else 0})
    return out


def shop_breakdown():
    with connect() as c:
        return [dict(r) for r in c.execute(
            """SELECT s.id, s.name, s.niche,
                 (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id AND p.status='published') online,
                 COUNT(sa.id) sales, COALESCE(SUM(sa.sale_price),0) revenue,
                 COALESCE(SUM(sa.profit),0) profit
               FROM shops s LEFT JOIN sales sa ON sa.shop_id=s.id
               GROUP BY s.id ORDER BY profit DESC""")]


def niche_breakdown():
    """Performance par niche : sert à décider quoi republier."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            """SELECT COALESCE(p.niche,'(sans niche)') niche, COUNT(DISTINCT p.id) listed,
                 COUNT(sa.id) sales, COALESCE(SUM(sa.profit),0) profit,
                 ROUND(100.0*COUNT(sa.id)/MAX(COUNT(DISTINCT p.id),1),1) sell_through
               FROM products p LEFT JOIN sales sa ON sa.product_id=p.id
               WHERE p.status IN ('published','sold')
               GROUP BY niche ORDER BY profit DESC""")]


def sale_by_transaction(tid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM sales WHERE vinted_transaction_id=?", (tid,)).fetchone())


def update_shop(sid, **fields):
    _update("shops", sid, fields)


def product_by_vinted_id(item_id):
    with connect() as c:
        return _row(c.execute("SELECT * FROM products WHERE vinted_item_id=?", (item_id,)).fetchone())


def published_today(shop_id):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM products WHERE shop_id=? AND date(published_at)=date('now','localtime')",
                         (shop_id,)).fetchone()[0]


# ---------- réglages ----------
DEFAULT_SETTINGS = {
    "autopilot": "on",            # interrupteur général
    "publish_mode": "validate",   # off | validate (bouton Telegram) | auto
    "reply_mode": "validate",     # off | validate | auto
    "offer_mode": "auto",         # off | validate | auto : accepte >= prix mini, contre-propose sinon
    "auto_prepare": "on",         # prépare les brouillons la nuit (photos + fiche)
    "prepare_per_night": "15",
    "publish_start": "10",        # heure de début de publication
    "publish_end": "15",
    "price_drop": "on",
    "price_drop_days": "7",
    "price_drop_pct": "10",
    "sync_minutes": "15",
    "inbox_minutes": "10",
    "daily_goal": "100",          # objectif de bénéfice par jour (€), affiché sur le dashboard
    "photo_provider": "auto",     # auto | gemini | openai
    "photo_set": "standard",      # standard (3 photos) | complet (5 photos)
    "photo_clean": "auto",        # nettoyage de l'image fournisseur : auto = seulement si gratuit (local)
    "photo_qa": "on",             # contrôle qualité IA + nouvel essai si la photo n'est pas fidèle
    "daily_budget": "2",          # dépense max par jour (€) en images payantes ; 0 = illimité
}


def get_setting(key):
    with connect() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else DEFAULT_SETTINGS.get(key)


def set_setting(key, value):
    with connect() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


def all_settings():
    return {k: get_setting(k) for k in DEFAULT_SETTINGS}


# ---------- journal d'activité ----------
def log_event(kind, message, shop_id=None, product_id=None):
    with connect() as c:
        c.execute("INSERT INTO events (kind, message, shop_id, product_id) VALUES (?,?,?,?)",
                  (kind, message, shop_id, product_id))


def list_events(limit=30):
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]


# ---------- modèles d'annonce Vinted (catégorie, marque, taille...) ----------
def add_template(name, **f):
    for k in ("size_map", "color_ids", "item_attributes"):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k])
    cols = ["name", *f.keys()]
    with connect() as c:
        cur = c.execute(f"INSERT INTO templates ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        (name, *f.values()))
        return cur.lastrowid


def get_template(tid):
    with connect() as c:
        r = c.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("size_map", "color_ids", "item_attributes"):
        d[k] = json.loads(d[k] or ("{}" if k == "size_map" else "[]"))
    return d


def list_templates():
    with connect() as c:
        return [get_template(r["id"]) for r in c.execute("SELECT id FROM templates ORDER BY id")]


# ---------- conversations ----------
def get_conversation(cid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone())


def upsert_conversation(cid, **f):
    f["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with connect() as c:
        if c.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
            cols = ", ".join(f"{k}=?" for k in f)
            c.execute(f"UPDATE conversations SET {cols} WHERE id=?", (*f.values(), cid))
        else:
            cols = ["id", *f.keys()]
            c.execute(f"INSERT INTO conversations ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      (cid, *f.values()))


def list_conversations(status=None, limit=50):
    q, args = "SELECT * FROM conversations", []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(q, args)]


# ---------- coûts IA ----------
def log_cost(provider, model, kind, cost, product_id=None):
    with connect() as c:
        c.execute("INSERT INTO ai_costs (provider, model, kind, cost, product_id) VALUES (?,?,?,?,?)",
                  (provider, model, kind, cost, product_id))


def cost_stats(days=30):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as c:
        total = c.execute("SELECT COUNT(*) n, COALESCE(SUM(cost),0) eur FROM ai_costs WHERE created_at >= ?",
                          (since,)).fetchone()
        by = [dict(r) for r in c.execute(
            "SELECT provider, COUNT(*) n, ROUND(SUM(cost),2) eur FROM ai_costs WHERE created_at >= ? "
            "GROUP BY provider ORDER BY eur DESC", (since,))]
    return {"images": total["n"], "eur": round(total["eur"], 2), "by_provider": by}


def product_cost(pid):
    with connect() as c:
        return round(c.execute("SELECT COALESCE(SUM(cost),0) FROM ai_costs WHERE product_id=?", (pid,)).fetchone()[0], 2)


def cost_today():
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost),0) FROM ai_costs WHERE date(created_at)=date('now')").fetchone()[0]
