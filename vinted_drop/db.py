"""Base SQLite : boutiques, produits, ventes, commandes fournisseurs, finances, tâches, journal.

Règles :
- WAL + busy_timeout : plusieurs threads (studio photo, bot, dashboard) peuvent écrire sans « database is locked ».
- Toutes les dates sont en heure LOCALE au format "AAAA-MM-JJ HH:MM:SS" (helper now()).
- Les migrations ajoutent des colonnes / tables, ne suppriment jamais rien, et une sauvegarde est faite avant.
- Les noms de colonnes passés à _update sont vérifiés contre le schéma (pas d'injection possible).
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta

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
    status TEXT DEFAULT 'draft',
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
    status TEXT DEFAULT 'to_order',
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
    id INTEGER PRIMARY KEY,
    shop_id INTEGER, product_id INTEGER, transaction_id INTEGER,
    buyer TEXT, last_buyer_message TEXT, last_message_key TEXT,
    draft TEXT, intent TEXT, offer_price REAL,
    status TEXT DEFAULT 'new',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ===== 2.0 =====
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, label TEXT, dedupe_key TEXT,
    shop_id INTEGER, product_id INTEGER,
    status TEXT DEFAULT 'queued',        -- queued | running | success | error | cancelled
    attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 1,
    result TEXT, error TEXT, source TEXT DEFAULT 'dashboard',
    created_at TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER REFERENCES sales(id), product_id INTEGER REFERENCES products(id), shop_id INTEGER,
    status TEXT DEFAULT 'to_buy',
    supplier TEXT, supplier_url TEXT, supplier_ref TEXT, supplier_price REAL, supplier_fees REAL DEFAULT 0,
    ordered_at TEXT, tracking_number TEXT, carrier TEXT, delivered_at TEXT, notes TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES orders(id), status TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER, product_id INTEGER, category TEXT, label TEXT, amount REAL NOT NULL,
    spent_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS photo_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER, kind TEXT, scene TEXT, provider TEXT, model TEXT,
    duration_s REAL, cost REAL DEFAULT 0, attempt INTEGER, fidelity INTEGER, realism INTEGER,
    issues TEXT DEFAULT '[]', status TEXT,      -- accepted | rejected | error
    path TEXT, error TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche TEXT, keyword TEXT, total INTEGER, avg_fav REAL, median_price REAL, p25_price REAL,
    p75_price REAL, sample INTEGER, brands TEXT DEFAULT '[]', taken_at TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, ref TEXT, title TEXT, niche TEXT, score INTEGER, data TEXT DEFAULT '{}',
    favorite INTEGER DEFAULT 0, note TEXT, created_at TEXT, updated_at TEXT,
    UNIQUE(kind, ref)
);
CREATE TABLE IF NOT EXISTS notifications (
    key TEXT PRIMARY KEY, sent_at TEXT
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_products_status ON products(status);
CREATE INDEX IF NOT EXISTS ix_products_shop ON products(shop_id, status);
CREATE INDEX IF NOT EXISTS ix_products_vinted ON products(vinted_item_id);
CREATE INDEX IF NOT EXISTS ix_sales_sold_at ON sales(sold_at);
CREATE INDEX IF NOT EXISTS ix_sales_shop ON sales(shop_id, sold_at);
CREATE INDEX IF NOT EXISTS ix_sales_product ON sales(product_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_transaction ON sales(vinted_transaction_id) WHERE vinted_transaction_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS ix_costs_created ON ai_costs(created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, dedupe_key);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_gen_product ON photo_generations(product_id);
CREATE INDEX IF NOT EXISTS ix_snap_niche ON market_snapshots(niche, taken_at);
"""

# Colonnes ajoutées après la v1 (migration automatique, jamais destructive)
MIGRATIONS = {
    "shops": {
        "vinted_domain": "TEXT DEFAULT 'vinted.fr'", "vinted_cookies": "TEXT",
        "vinted_user_agent": "TEXT", "vinted_user_id": "INTEGER", "vinted_login": "TEXT",
        "vinted_status": "TEXT DEFAULT 'disconnected'", "vinted_checked_at": "TEXT",
        "template_id": "INTEGER", "daily_quota": "INTEGER DEFAULT 20",
        # 2.0 : environnement propre à chaque boutique
        "publish_start": "INTEGER", "publish_end": "INTEGER", "monthly_goal": "REAL",
        "min_profit": "REAL", "listing_style": "TEXT", "photo_provider": "TEXT", "photo_set": "TEXT",
        "notes": "TEXT",
    },
    "products": {
        "vinted_item_id": "INTEGER", "template_id": "INTEGER", "favourites": "INTEGER DEFAULT 0",
        "views": "INTEGER DEFAULT 0", "last_price_drop_at": "TEXT", "approval_sent_at": "TEXT",
        "photo_meta": "TEXT DEFAULT '{}'",
        # 2.0 : fiche financière + annonce enrichie
        "supplier_fees": "REAL DEFAULT 0", "shipping_cost": "REAL DEFAULT 0", "other_fees": "REAL DEFAULT 0",
        "cost_unknown": "INTEGER DEFAULT 0", "brand": "TEXT", "keywords": "TEXT DEFAULT '[]'",
        "garment": "TEXT DEFAULT '{}'", "price_info": "TEXT DEFAULT '{}'", "updated_at": "TEXT",
    },
    "personas": {"decor": "TEXT", "refs": "TEXT DEFAULT '[]'"},
    "sales": {"vinted_transaction_id": "INTEGER", "source": "TEXT DEFAULT 'manuel'",
              "fees": "REAL DEFAULT 0"},
    "events": {"level": "TEXT DEFAULT 'info'", "source": "TEXT", "detail": "TEXT"},
}

PRODUCT_STATUSES = ["draft", "ready", "published", "sold", "archived"]
SALE_STATUSES = ["to_order", "ordered", "received", "shipped", "done"]
ORDER_STATUSES = ["to_buy", "pending", "ordered", "preparing", "shipped", "delivered", "cancelled", "problem"]
ORDER_LABELS = {"to_buy": "À acheter", "pending": "Commande en attente", "ordered": "Commandé",
                "preparing": "En préparation", "shipped": "Expédié", "delivered": "Livré",
                "cancelled": "Annulé", "problem": "Problème fournisseur"}
JSON_COLS = {"photos": "[]", "photo_meta": "{}", "refs": "[]", "keywords": "[]", "garment": "{}",
             "price_info": "{}", "issues": "[]", "brands": "[]", "data": "{}"}

_columns_cache: dict = {}
_init_lock = threading.Lock()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with _init_lock:
        fresh = not config.DB_PATH.exists() or config.DB_PATH.stat().st_size == 0
        if not fresh:
            _backup_before_migration()
        with connect() as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.executescript(SCHEMA)
            for table, cols in MIGRATIONS.items():
                existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in existing:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            c.executescript(INDEXES)
        _columns_cache.clear()


def _backup_before_migration():
    """Sauvegarde avant toute migration si le schéma va changer."""
    try:
        with connect() as c:
            missing = False
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "tasks" not in tables:
                missing = True
            else:
                for table, cols in MIGRATIONS.items():
                    existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                    if set(cols) - existing:
                        missing = True
                        break
        if missing:
            from .backup import backup
            backup(reason="avant-migration")
    except sqlite3.DatabaseError:
        pass


def columns(table) -> set:
    if table not in _columns_cache:
        with connect() as c:
            _columns_cache[table] = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    return _columns_cache[table]


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k, default in JSON_COLS.items():
        if k in d:
            try:
                d[k] = json.loads(d[k] or default)
            except (TypeError, ValueError):
                d[k] = json.loads(default)
    return d


def _dump(fields):
    for k in list(fields):
        if k in JSON_COLS and not isinstance(fields[k], str) and fields[k] is not None:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    return fields


def _update(table, rid, fields):
    if not fields:
        return
    bad = set(fields) - columns(table)
    if bad:
        raise ValueError(f"Colonnes inconnues pour {table} : {', '.join(sorted(bad))}")
    fields = _dump(dict(fields))
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), rid))


def _insert(table, **fields):
    bad = set(fields) - columns(table)
    if bad:
        raise ValueError(f"Colonnes inconnues pour {table} : {', '.join(sorted(bad))}")
    fields = _dump(fields)
    with connect() as c:
        cur = c.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                        tuple(fields.values()))
        return cur.lastrowid


# ================= périodes =================
PERIODS = {"today": "Aujourd'hui", "7d": "7 jours", "30d": "30 jours", "90d": "90 jours", "year": "Cette année",
           "all": "Tout"}


def period_range(period="30d", start=None, end=None):
    """Renvoie (début, fin) inclusifs en "AAAA-MM-JJ HH:MM:SS". `custom` utilise start/end (AAAA-MM-JJ)."""
    today = date.today()
    if period == "custom" and start:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end) if end else today
        if e < s:
            s, e = e, s
    elif period == "today":
        s = e = today
    elif period == "7d":
        s, e = today - timedelta(days=6), today
    elif period == "90d":
        s, e = today - timedelta(days=89), today
    elif period == "year":
        s, e = date(today.year, 1, 1), today
    elif period == "all":
        s, e = date(2000, 1, 1), today
    else:
        s, e = today - timedelta(days=29), today
    return f"{s} 00:00:00", f"{e} 23:59:59"


def previous_range(start, end):
    """Période précédente de même durée (pour les comparaisons)."""
    s = datetime.strptime(start, "%Y-%m-%d %H:%M:%S").date()
    e = datetime.strptime(end, "%Y-%m-%d %H:%M:%S").date()
    days = (e - s).days + 1
    ps, pe = s - timedelta(days=days), s - timedelta(days=1)
    return f"{ps} 00:00:00", f"{pe} 23:59:59"


# ================= personas =================
def add_persona(name, description, reference_image=None):
    return _insert("personas", name=name, description=description, reference_image=reference_image, created_at=now())


def get_persona(pid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM personas WHERE id=?", (pid,)).fetchone())


def list_personas():
    with connect() as c:
        return [_row(r) for r in c.execute("SELECT * FROM personas ORDER BY id")]


def update_persona(pid, **fields):
    _update("personas", pid, fields)


# ================= boutiques =================
def add_shop(name, niche=None, persona_id=None, vinted_url=None):
    return _insert("shops", name=name, niche=niche, persona_id=persona_id, vinted_url=vinted_url, created_at=now())


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


def update_shop(sid, **fields):
    _update("shops", sid, fields)


def shop_setting(shop, key, default=None):
    """Réglage propre à la boutique s'il est défini, sinon réglage global."""
    if shop and shop.get(key) not in (None, ""):
        return shop[key]
    glob = get_setting(key)
    return glob if glob not in (None, "") else default


# ================= produits =================
def add_product(cost, supplier_url=None, supplier=None, supplier_title=None, supplier_image=None,
                shop_id=None, niche=None, size=None, supplier_fees=0, shipping_cost=0):
    return _insert("products", cost=cost, supplier_url=supplier_url, supplier=supplier, supplier_title=supplier_title,
                   supplier_image=supplier_image, shop_id=shop_id, niche=niche, size=size,
                   supplier_fees=supplier_fees or 0, shipping_cost=shipping_cost or 0,
                   created_at=now(), updated_at=now())


def get_product(pid):
    with connect() as c:
        return _row(c.execute(
            "SELECT p.*, s.name AS shop_name FROM products p LEFT JOIN shops s ON s.id=p.shop_id "
            "WHERE p.id=?", (pid,)).fetchone())


SORTS = {"recent": "p.id DESC", "old": "p.id ASC", "price": "p.price DESC", "price_asc": "p.price ASC",
         "favs": "p.favourites DESC", "title": "p.title COLLATE NOCASE ASC"}


def list_products(status=None, shop_id=None, limit=200, offset=0, q=None, sort="recent", niche=None):
    sql = ("SELECT p.*, s.name AS shop_name FROM products p LEFT JOIN shops s ON s.id=p.shop_id WHERE 1=1")
    args = []
    if status:
        sql += " AND p.status=?"
        args.append(status)
    if shop_id:
        sql += " AND p.shop_id=?"
        args.append(shop_id)
    if niche:
        sql += " AND p.niche=?"
        args.append(niche)
    if q:
        sql += " AND (p.title LIKE ? OR p.supplier_title LIKE ? OR CAST(p.id AS TEXT)=?)"
        args += [f"%{q}%", f"%{q}%", q.strip("#")]
    sql += f" ORDER BY {SORTS.get(sort, SORTS['recent'])} LIMIT ? OFFSET ?"
    args += [limit, offset]
    with connect() as c:
        return [_row(r) for r in c.execute(sql, args)]


def count_products(status=None, shop_id=None, q=None, niche=None):
    sql, args = "SELECT COUNT(*) FROM products p WHERE 1=1", []
    for col, val in (("status", status), ("shop_id", shop_id), ("niche", niche)):
        if val:
            sql += f" AND p.{col}=?"
            args.append(val)
    if q:
        sql += " AND (p.title LIKE ? OR p.supplier_title LIKE ? OR CAST(p.id AS TEXT)=?)"
        args += [f"%{q}%", f"%{q}%", q.strip("#")]
    with connect() as c:
        return c.execute(sql, args).fetchone()[0]


def update_product(pid, **fields):
    if fields.get("status") == "published":
        fields.setdefault("published_at", now())
    fields["updated_at"] = now()
    _update("products", pid, fields)


def product_by_vinted_id(item_id):
    with connect() as c:
        return _row(c.execute("SELECT * FROM products WHERE vinted_item_id=?", (item_id,)).fetchone())


def published_today(shop_id):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM products WHERE shop_id=? AND date(published_at)=date(?)",
                         (shop_id, now())).fetchone()[0]


def product_total_cost(p: dict) -> float:
    """Coût de revient complet d'un produit."""
    return round((p.get("cost") or 0) + (p.get("supplier_fees") or 0) + (p.get("shipping_cost") or 0)
                 + (p.get("other_fees") or 0) + config.PACKAGING_COST, 2)


# ================= ventes =================
class AlreadySold(ValueError):
    pass


def record_sale(product_id, sale_price, buyer=None, vinted_transaction_id=None, source="manuel", fees=None):
    """Enregistre une vente (une seule par produit) et crée la commande fournisseur à passer."""
    p = get_product(product_id)
    if not p:
        raise ValueError(f"Produit #{product_id} introuvable")
    if sale_price is None or float(sale_price) <= 0:
        raise ValueError("Prix de vente invalide")
    sale_price = float(sale_price)
    fees = float(fees if fees is not None else round(sale_price * config.PLATFORM_FEE_PCT / 100, 2))
    total_cost = product_total_cost(p)
    profit = round(sale_price - total_cost - fees, 2)
    ts = now()
    with connect() as c:
        if c.execute("SELECT 1 FROM sales WHERE product_id=?", (product_id,)).fetchone():
            raise AlreadySold(f"Le produit #{product_id} est déjà enregistré comme vendu")
        cur = c.execute(
            "INSERT INTO sales (product_id, shop_id, sale_price, cost, profit, buyer, vinted_transaction_id, source,"
            " fees, sold_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (product_id, p["shop_id"], sale_price, total_cost, profit, buyer, vinted_transaction_id, source, fees, ts))
        sid = cur.lastrowid
        c.execute("UPDATE products SET status='sold', updated_at=? WHERE id=?", (ts, product_id))
        oc = c.execute(
            "INSERT INTO orders (sale_id, product_id, shop_id, status, supplier, supplier_url, supplier_price,"
            " supplier_fees, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, product_id, p["shop_id"], "to_buy", p.get("supplier"), p.get("supplier_url"), p.get("cost"),
             p.get("supplier_fees") or 0, ts, ts))
        c.execute("INSERT INTO order_events (order_id, status, note, created_at) VALUES (?,?,?,?)",
                  (oc.lastrowid, "to_buy", "Vente enregistrée", ts))
    return sid


def get_sale(sid):
    with connect() as c:
        return _row(c.execute(
            """SELECT sa.*, p.title, p.supplier_url, p.size, p.supplier, s.name AS shop_name
               FROM sales sa LEFT JOIN products p ON p.id=sa.product_id
               LEFT JOIN shops s ON s.id=sa.shop_id WHERE sa.id=?""", (sid,)).fetchone())


def list_sales(limit=100, status=None, shop_id=None, start=None, end=None, offset=0):
    q = """SELECT sa.*, p.title, p.supplier_url, p.size, p.supplier, s.name AS shop_name
           FROM sales sa LEFT JOIN products p ON p.id=sa.product_id
           LEFT JOIN shops s ON s.id=sa.shop_id WHERE 1=1"""
    args = []
    if status:
        q += " AND sa.status=?"
        args.append(status)
    if shop_id:
        q += " AND sa.shop_id=?"
        args.append(shop_id)
    if start:
        q += " AND sa.sold_at BETWEEN ? AND ?"
        args += [start, end]
    q += " ORDER BY sa.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with connect() as c:
        return [_row(r) for r in c.execute(q, args)]


def update_sale(sid, **fields):
    _update("sales", sid, fields)


def sale_by_transaction(tid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM sales WHERE vinted_transaction_id=?", (tid,)).fetchone())


# ================= commandes fournisseurs =================
def list_orders(status=None, shop_id=None, limit=200, open_only=False):
    q = """SELECT o.*, p.title, p.size, p.supplier_title, s.name AS shop_name, sa.sale_price, sa.buyer, sa.sold_at
           FROM orders o LEFT JOIN products p ON p.id=o.product_id LEFT JOIN shops s ON s.id=o.shop_id
           LEFT JOIN sales sa ON sa.id=o.sale_id WHERE 1=1"""
    args = []
    if status:
        q += " AND o.status=?"
        args.append(status)
    if shop_id:
        q += " AND o.shop_id=?"
        args.append(shop_id)
    if open_only:
        q += " AND o.status NOT IN ('delivered','cancelled')"
    q += " ORDER BY CASE o.status WHEN 'problem' THEN 0 WHEN 'to_buy' THEN 1 ELSE 2 END, o.id DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args)]


def get_order(oid):
    with connect() as c:
        return _row(c.execute(
            """SELECT o.*, p.title, p.size, p.supplier_title, s.name AS shop_name, sa.sale_price, sa.buyer, sa.sold_at
               FROM orders o LEFT JOIN products p ON p.id=o.product_id LEFT JOIN shops s ON s.id=o.shop_id
               LEFT JOIN sales sa ON sa.id=o.sale_id WHERE o.id=?""", (oid,)).fetchone())


def order_for_sale(sale_id):
    with connect() as c:
        r = c.execute("SELECT id FROM orders WHERE sale_id=?", (sale_id,)).fetchone()
    return get_order(r["id"]) if r else None


def update_order(oid, note=None, **fields):
    if "status" in fields and fields["status"] not in ORDER_STATUSES:
        raise ValueError("Statut de commande inconnu")
    ts = now()
    if fields.get("status") == "ordered":
        fields.setdefault("ordered_at", ts)
    if fields.get("status") == "delivered":
        fields.setdefault("delivered_at", ts)
    fields["updated_at"] = ts
    _update("orders", oid, fields)
    if "status" in fields or note:
        with connect() as c:
            c.execute("INSERT INTO order_events (order_id, status, note, created_at) VALUES (?,?,?,?)",
                      (oid, fields.get("status"), note, ts))
    # garde le statut de la vente aligné (compatibilité 1.x)
    o = get_order(oid)
    mapping = {"ordered": "ordered", "preparing": "ordered", "shipped": "ordered", "delivered": "received"}
    if o and o.get("sale_id") and fields.get("status") in mapping:
        update_sale(o["sale_id"], status=mapping[fields["status"]])


def order_history(oid):
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id", (oid,))]


def order_counts():
    with connect() as c:
        return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM orders GROUP BY status")}


# ================= dépenses =================
def add_expense(amount, category, label=None, shop_id=None, product_id=None, spent_at=None):
    return _insert("expenses", amount=float(amount), category=category, label=label, shop_id=shop_id,
                   product_id=product_id, spent_at=spent_at or now(), created_at=now())


def list_expenses(start=None, end=None, shop_id=None, limit=500):
    q, args = "SELECT e.*, s.name shop_name FROM expenses e LEFT JOIN shops s ON s.id=e.shop_id WHERE 1=1", []
    if start:
        q += " AND e.spent_at BETWEEN ? AND ?"
        args += [start, end]
    if shop_id:
        q += " AND e.shop_id=?"
        args.append(shop_id)
    q += " ORDER BY e.spent_at DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(q, args)]


def delete_expense(eid):
    with connect() as c:
        c.execute("DELETE FROM expenses WHERE id=?", (eid,))


# ================= statistiques =================
def _shop_filter(alias, shop_id, args):
    if shop_id:
        args.append(shop_id)
        return f" AND {alias}.shop_id=?"
    return ""


def stats(days=None, start=None, end=None, shop_id=None):
    """Statistiques réelles sur une période. `days` reste accepté (compatibilité 1.x)."""
    if days is not None and start is None:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        end = now()
    args = []
    where = " WHERE 1=1"
    if start:
        where += " AND sold_at BETWEEN ? AND ?"
        args += [start, end]
    where += _shop_filter("sales", shop_id, args)
    with connect() as c:
        r = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(sale_price),0) ca, COALESCE(SUM(profit),0) benef, "
                      f"COALESCE(SUM(cost),0) cout, COALESCE(SUM(fees),0) fees FROM sales{where}", args).fetchone()
        pargs = []
        pw = " WHERE 1=1" + _shop_filter("products", shop_id, pargs)
        counts = {row["status"]: row["n"] for row in
                  c.execute(f"SELECT status, COUNT(*) n FROM products{pw} GROUP BY status", pargs)}
    ca = r["ca"]
    return {
        "sales": r["n"], "revenue": round(ca, 2), "profit": round(r["benef"], 2),
        "cost": round(r["cout"], 2), "fees": round(r["fees"], 2),
        "margin_pct": round(100 * r["benef"] / ca, 1) if ca else 0,
        "avg_basket": round(ca / r["n"], 2) if r["n"] else 0,
        "avg_profit": round(r["benef"] / r["n"], 2) if r["n"] else 0,
        "products": {s: counts.get(s, 0) for s in PRODUCT_STATUSES},
    }


def today_stats(shop_id=None):
    s, e = period_range("today")
    return stats(start=s, end=e, shop_id=shop_id)


def capital_stats(shop_id=None):
    """Capital immobilisé = coût des produits achetés mais pas encore vendus (commandes en stock) ;
    capital engagé = coût de revient des annonces en ligne (dropshipping : pas encore dépensé)."""
    args = []
    sf = _shop_filter("p", shop_id, args)
    with connect() as c:
        online = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(p.cost + COALESCE(p.supplier_fees,0) + COALESCE(p.shipping_cost,0)),0) v "
                           f"FROM products p WHERE p.status IN ('published','ready'){sf}", args).fetchone()
        args2 = []
        sf2 = _shop_filter("o", shop_id, args2)
        stock = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(COALESCE(o.supplier_price,0)+COALESCE(o.supplier_fees,0)),0) v "
                          f"FROM orders o WHERE o.status IN ('ordered','preparing','shipped'){sf2}", args2).fetchone()
        unknown = c.execute(f"SELECT COUNT(*) FROM products p WHERE p.cost_unknown=1{sf}", args).fetchone()[0]
    return {"listed_count": online["n"], "listed_value": round(online["v"], 2),
            "in_transit_count": stock["n"], "in_transit_value": round(stock["v"], 2), "cost_unknown": unknown}


def series(start, end, shop_id=None):
    """Série quotidienne (CA, bénéfice, ventes) entre deux dates, jours vides compris."""
    s = datetime.strptime(start, "%Y-%m-%d %H:%M:%S").date()
    e = datetime.strptime(end, "%Y-%m-%d %H:%M:%S").date()
    if (e - s).days > 400:  # regroupement par mois au-delà d'un an
        return _monthly_series(start, end, shop_id)
    args = [start, end]
    sf = _shop_filter("sales", shop_id, args)
    with connect() as c:
        rows = {r["d"]: r for r in c.execute(
            f"SELECT date(sold_at) d, COUNT(*) n, SUM(sale_price) ca, SUM(profit) benef FROM sales "
            f"WHERE sold_at BETWEEN ? AND ?{sf} GROUP BY date(sold_at)", args)}
    out = []
    for i in range((e - s).days + 1):
        d = (s + timedelta(days=i)).isoformat()
        r = rows.get(d)
        out.append({"date": d, "sales": r["n"] if r else 0, "revenue": round(r["ca"] or 0, 2) if r else 0,
                    "profit": round(r["benef"] or 0, 2) if r else 0})
    return out


def _monthly_series(start, end, shop_id=None):
    args = [start, end]
    sf = _shop_filter("sales", shop_id, args)
    with connect() as c:
        return [{"date": r["m"] + "-01", "sales": r["n"], "revenue": round(r["ca"] or 0, 2),
                 "profit": round(r["benef"] or 0, 2)} for r in c.execute(
            f"SELECT strftime('%Y-%m', sold_at) m, COUNT(*) n, SUM(sale_price) ca, SUM(profit) benef FROM sales "
            f"WHERE sold_at BETWEEN ? AND ?{sf} GROUP BY m ORDER BY m", args)]


def daily_series(days=30):
    s, e = period_range("custom", (date.today() - timedelta(days=days - 1)).isoformat(), date.today().isoformat())
    return series(s, e)


def shop_breakdown(start=None, end=None):
    args, sw = [], ""
    if start:
        sw = " AND sa.sold_at BETWEEN ? AND ?"
        args = [start, end]
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            f"""SELECT s.id, s.name, s.niche, s.vinted_status, s.vinted_login, s.daily_quota, s.monthly_goal,
                 (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id AND p.status='published') online,
                 (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id AND p.status IN ('draft','ready')) drafts,
                 (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id AND p.status='published' AND p.favourites>=3) performing,
                 COUNT(sa.id) sales, COALESCE(SUM(sa.sale_price),0) revenue, COALESCE(SUM(sa.profit),0) profit
               FROM shops s LEFT JOIN sales sa ON sa.shop_id=s.id{sw}
               GROUP BY s.id ORDER BY profit DESC""", args)]
    for r in rows:
        r["margin_pct"] = round(100 * r["profit"] / r["revenue"], 1) if r["revenue"] else 0
        r["avg_profit"] = round(r["profit"] / r["sales"], 2) if r["sales"] else 0
    return rows


def niche_breakdown(start=None, end=None):
    """Performance par niche : sert à décider quoi republier."""
    args, sw = [], ""
    if start:
        sw = " AND sa.sold_at BETWEEN ? AND ?"
        args = [start, end]
    with connect() as c:
        return [dict(r) for r in c.execute(
            f"""SELECT COALESCE(p.niche,'(sans niche)') niche, COUNT(DISTINCT p.id) listed,
                 COUNT(sa.id) sales, COALESCE(SUM(sa.profit),0) profit, COALESCE(SUM(sa.sale_price),0) revenue,
                 ROUND(100.0*COUNT(sa.id)/MAX(COUNT(DISTINCT p.id),1),1) sell_through
               FROM products p LEFT JOIN sales sa ON sa.product_id=p.id{sw}
               WHERE p.status IN ('published','sold')
               GROUP BY niche ORDER BY profit DESC""", args)]


def top_products(start=None, end=None, limit=10, shop_id=None):
    args, w = [], ""
    if start:
        w = " AND sa.sold_at BETWEEN ? AND ?"
        args = [start, end]
    w += _shop_filter("sa", shop_id, args)
    args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(
            f"""SELECT p.id, p.title, p.supplier_title, p.niche, s.name shop_name, sa.sale_price, sa.profit,
                 ROUND(100.0*sa.profit/NULLIF(sa.sale_price,0),1) margin_pct, sa.sold_at
               FROM sales sa JOIN products p ON p.id=sa.product_id LEFT JOIN shops s ON s.id=sa.shop_id
               WHERE 1=1{w} ORDER BY sa.profit DESC LIMIT ?""", args)]


def unsold_products(days=14, limit=50, shop_id=None):
    """Annonces en ligne depuis plus de `days` jours sans vente."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    args = [since]
    sf = _shop_filter("p", shop_id, args)
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(
            f"SELECT p.*, s.name shop_name FROM products p LEFT JOIN shops s ON s.id=p.shop_id "
            f"WHERE p.status='published' AND p.published_at <= ?{sf} ORDER BY p.published_at LIMIT ?", args)]


# ================= réglages =================
DEFAULT_SETTINGS = {
    "autopilot": "on",            # interrupteur général
    "emergency_stop": "off",      # arrêt d'urgence : bloque toute action automatique
    "simulation": "off",          # mode simulation : aucune écriture réelle sur Vinted
    "publish_mode": "validate",   # off | validate (bouton Telegram) | auto
    "reply_mode": "validate",     # off | validate | auto
    "offer_mode": "validate",     # off | validate | auto : accepte >= prix mini, contre-propose sinon
    "auto_prepare": "on",         # prépare les brouillons la nuit (photos + fiche)
    "prepare_per_night": "15",
    "publish_start": "10",
    "publish_end": "15",
    "price_drop": "on",
    "price_drop_days": "7",
    "price_drop_pct": "10",
    "sync_minutes": "15",
    "inbox_minutes": "10",
    "daily_goal": "100",
    "min_profit": "30",           # bénéfice minimum par vente (€)
    "target_multiplier": "2.5",
    "negotiation_buffer": "0.15",
    "listing_style": "naturel",   # naturel | enthousiaste | minimaliste | detaille
    "photo_disclosure": "on",     # mention honnête des photos de présentation dans la description
    "photo_provider": "auto",
    "photo_set": "standard",
    "photo_clean": "auto",
    "photo_qa": "on",
    "photo_scene": "auto",        # décor des photos portées
    "daily_budget": "2",
    "budget_alert_pct": "80",
    "ship_delay": "envoi sous 5 à 8 jours",
    "prepare_photos": "on",
    "prepare_listing": "on",
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
    with connect() as c:
        stored = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if not k.startswith("_")}}


def get_json_setting(key, default=None):
    raw = get_setting(key)
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# ================= journal d'activité =================
EVENT_CATEGORIES = {"sale": "Ventes", "publish": "Publications", "prepare": "Préparations", "photo": "Photos IA",
                    "reply": "Messages", "offer": "Offres", "price": "Prix", "import": "Imports",
                    "market": "Marché", "vinted": "Connexion Vinted", "order": "Commandes", "finance": "Finances",
                    "telegram": "Telegram", "task": "Tâches", "settings": "Réglages", "error": "Erreurs",
                    "security": "Sécurité", "backup": "Sauvegardes"}


def log_event(kind, message, shop_id=None, product_id=None, level=None, source=None, detail=None):
    level = level or ("error" if kind == "error" else "info")
    with connect() as c:
        c.execute("INSERT INTO events (kind, message, shop_id, product_id, level, source, detail, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (kind, message, shop_id, product_id, level, source, detail, now()))


def list_events(limit=30, kind=None, shop_id=None, level=None, start=None, end=None, q=None, offset=0):
    sql = "SELECT e.*, s.name shop_name FROM events e LEFT JOIN shops s ON s.id=e.shop_id WHERE 1=1"
    args = []
    for col, val in (("kind", kind), ("shop_id", shop_id), ("level", level)):
        if val:
            sql += f" AND e.{col}=?"
            args.append(val)
    if start:
        sql += " AND e.created_at BETWEEN ? AND ?"
        args += [start, end]
    if q:
        sql += " AND e.message LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY e.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args)]


def count_events(**filters):
    return len(list_events(limit=100000, **filters))


# ================= modèles d'annonce =================
def add_template(name, **f):
    return _insert("templates", name=name, created_at=now(), **{
        k: (json.dumps(v) if k in ("size_map", "color_ids", "item_attributes") and not isinstance(v, str) else v)
        for k, v in f.items()})


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


# ================= conversations =================
def get_conversation(cid):
    with connect() as c:
        return _row(c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone())


def upsert_conversation(cid, **f):
    f["updated_at"] = now()
    bad = set(f) - columns("conversations")
    if bad:
        raise ValueError(f"Colonnes inconnues : {bad}")
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


# ================= coûts IA =================
def log_cost(provider, model, kind, cost, product_id=None):
    with connect() as c:
        c.execute("INSERT INTO ai_costs (provider, model, kind, cost, product_id, created_at) VALUES (?,?,?,?,?,?)",
                  (provider, model, kind, cost, product_id, now()))


def cost_stats(days=30, start=None, end=None):
    if start is None:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        end = now()
    with connect() as c:
        total = c.execute("SELECT COUNT(*) n, COALESCE(SUM(cost),0) eur FROM ai_costs WHERE created_at BETWEEN ? AND ?",
                          (start, end)).fetchone()
        by = [dict(r) for r in c.execute(
            "SELECT provider, COUNT(*) n, ROUND(SUM(cost),2) eur FROM ai_costs WHERE created_at BETWEEN ? AND ? "
            "GROUP BY provider ORDER BY eur DESC", (start, end))]
    return {"images": total["n"], "eur": round(total["eur"], 2), "by_provider": by}


def product_cost(pid):
    with connect() as c:
        return round(c.execute("SELECT COALESCE(SUM(cost),0) FROM ai_costs WHERE product_id=?", (pid,)).fetchone()[0], 2)


def cost_today():
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost),0) FROM ai_costs WHERE date(created_at)=date(?)",
                         (now(),)).fetchone()[0]


# ================= historique photo =================
def log_generation(**f):
    f.setdefault("created_at", now())
    return _insert("photo_generations", **f)


def list_generations(product_id=None, limit=100):
    q, args = "SELECT * FROM photo_generations", []
    if product_id:
        q += " WHERE product_id=?"
        args.append(product_id)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args)]


def generation_stats(days=30):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT provider, COUNT(*) n, SUM(status='accepted') ok, SUM(status='rejected') rejected, "
            "ROUND(AVG(duration_s),1) avg_s, ROUND(AVG(fidelity),1) avg_fid, ROUND(SUM(cost),2) eur "
            "FROM photo_generations WHERE created_at >= ? GROUP BY provider", (since,))]
    return rows


# ================= notifications (anti-doublon) =================
def notify_once(key, within_hours=24) -> bool:
    """True si la notification `key` n'a pas déjà été envoyée dans la fenêtre donnée (et la marque comme envoyée)."""
    limit = (datetime.now() - timedelta(hours=within_hours)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as c:
        r = c.execute("SELECT sent_at FROM notifications WHERE key=?", (key,)).fetchone()
        if r and r["sent_at"] >= limit:
            return False
        c.execute("INSERT INTO notifications (key, sent_at) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET sent_at=excluded.sent_at",
                  (key, now()))
    return True


# ================= recherche globale =================
def search(q, limit=8):
    q = (q or "").strip()
    if not q:
        return {"products": [], "shops": [], "sales": []}
    like = f"%{q}%"
    with connect() as c:
        products = [dict(r) for r in c.execute(
            "SELECT id, title, supplier_title, status, price FROM products WHERE title LIKE ? OR supplier_title LIKE ? "
            "OR CAST(id AS TEXT)=? ORDER BY id DESC LIMIT ?", (like, like, q.strip("#"), limit))]
        shops = [dict(r) for r in c.execute("SELECT id, name, niche FROM shops WHERE name LIKE ? OR niche LIKE ? LIMIT ?",
                                            (like, like, limit))]
        sales = [dict(r) for r in c.execute(
            "SELECT sa.id, sa.sale_price, sa.buyer, p.title FROM sales sa LEFT JOIN products p ON p.id=sa.product_id "
            "WHERE p.title LIKE ? OR sa.buyer LIKE ? ORDER BY sa.id DESC LIMIT ?", (like, like, limit))]
    return {"products": products, "shops": shops, "sales": sales}
