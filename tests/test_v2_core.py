"""Version 2.0 : base, finances, commandes, tâches, sauvegardes, planning, prix, marché."""
import threading
import time
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def init_db():
    from vinted_drop import db
    db.init()


def _sold_product(cost=10, price=60, fees=2, ship=1, shop="Sophie", niche="trench"):
    from vinted_drop import db
    sid = db.find_shop(shop)["id"] if db.find_shop(shop) else db.add_shop(shop, niche)
    pid = db.add_product(cost=cost, supplier_url="https://www.temu.com/x", supplier="Temu", shop_id=sid,
                         niche=niche, supplier_fees=fees, shipping_cost=ship)
    db.update_product(pid, status="published", title="Trench beige", price=price)
    return sid, pid


# ---------- base ----------
def test_wal_indexes_and_local_time():
    from vinted_drop import db
    with db.connect() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        idx = {r[1] for r in c.execute("SELECT * FROM sqlite_master WHERE type='index'")}
    assert {"ix_sales_sold_at", "ix_products_status"} <= idx
    sid, pid = _sold_product()
    db.record_sale(pid, 60)
    sale = db.list_sales()[0]
    assert sale["sold_at"][:13] == datetime.now().strftime("%Y-%m-%d %H")  # heure locale


def test_update_rejects_unknown_columns():
    from vinted_drop import db
    sid, pid = _sold_product()
    with pytest.raises(ValueError):
        db.update_product(pid, **{"title=1; DROP TABLE products; --": "x"})


def test_sale_profit_counts_all_costs_and_no_double_sale():
    from vinted_drop import config, db
    sid, pid = _sold_product(cost=10, price=60, fees=2, ship=1)
    sale_id = db.record_sale(pid, 60)
    sale = db.get_sale(sale_id)
    assert sale["profit"] == round(60 - (10 + 2 + 1 + config.PACKAGING_COST), 2)
    with pytest.raises(db.AlreadySold):
        db.record_sale(pid, 60)
    assert db.stats()["sales"] == 1


def test_concurrent_writes_do_not_lock():
    from vinted_drop import db
    sid, _ = _sold_product()
    errors = []

    def writer(i):
        try:
            for j in range(15):
                db.log_event("task", f"t{i}-{j}")
                db.add_product(cost=5, shop_id=sid)
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and db.count_products() >= 90


def test_periods_and_comparison():
    from vinted_drop import db
    s, e = db.period_range("7d")
    ps, pe = db.previous_range(s, e)
    assert (datetime.fromisoformat(s) - datetime.fromisoformat(ps)).days == 7
    cs, ce = db.period_range("custom", "2026-10-01", "2026-10-31")
    assert cs.startswith("2026-10-01") and ce.startswith("2026-10-31")
    assert len(db.series(cs, ce)) == 31


# ---------- commandes fournisseurs ----------
def test_sale_creates_supplier_order_with_history():
    from vinted_drop import db
    sid, pid = _sold_product()
    sale_id = db.record_sale(pid, 60)
    o = db.order_for_sale(sale_id)
    assert o["status"] == "to_buy" and o["supplier_url"].startswith("https://")
    db.update_order(o["id"], status="ordered", supplier_ref="PO-123", note="commandé sur Temu")
    db.update_order(o["id"], status="shipped", tracking_number="LP0001")
    o = db.get_order(o["id"])
    assert o["ordered_at"] and o["tracking_number"] == "LP0001"
    assert [h["status"] for h in db.order_history(o["id"])] == ["to_buy", "ordered", "shipped"]
    with pytest.raises(ValueError):
        db.update_order(o["id"], status="paid_automatically")


# ---------- finances ----------
def test_finance_summary_sheet_and_exports():
    from vinted_drop import db, finance
    sid, pid = _sold_product(cost=9, price=95, fees=2, ship=0)
    db.update_product(pid, other_fees=5)
    sale_id = db.record_sale(pid, 95)
    db.log_cost("gemini", "m", "porte", 0.10, pid)
    db.add_expense(3, "tools", "abonnement")
    s, e = db.period_range("30d")
    f = finance.summary(s, e)
    sheet = finance.product_sheet(pid)
    assert sheet["total_cost"] == 9 + 2 + 5 + 0.5 and sheet["profit"] == round(95 - 16.5, 2)
    assert f["ai_costs"] == 0.10 and f["expenses"] == 3
    assert f["net_profit"] == round(f["profit"] - 0.10 - 3, 2)
    types = {t["type"] for t in finance.transactions(s, e)}
    assert {"Vente", "Coût IA", "Dépense"} <= types
    csv_bytes = finance.export_csv("Ventes", s, e)
    assert csv_bytes.startswith("﻿".encode()) and b"Trench" in csv_bytes
    xlsx = finance.export_xlsx(s, e)
    assert xlsx[:2] == b"PK"  # fichier Excel (zip)
    assert b"access_token" not in xlsx and b"enc:v1" not in csv_bytes


# ---------- tâches ----------
def test_tasks_dedupe_success_error_and_emergency_stop():
    from vinted_drop import db, tasks
    gate = threading.Event()
    t1, c1 = tasks.submit("photos", lambda: gate.wait(5) and "ok", dedupe_key="photos:1")
    t2, c2 = tasks.submit("photos", lambda: "x", dedupe_key="photos:1")
    assert c1 and not c2 and t1 == t2            # pas de doublon
    gate.set()
    tasks.wait_all()
    assert tasks.get(t1)["status"] == "success" and tasks.get(t1)["finished_at"]
    t3, _ = tasks.submit("fiche", lambda: 1 / 0)
    tasks.wait_all()
    t = tasks.get(t3)
    assert t["status"] == "error" and "division" in t["error"]
    db.set_setting("emergency_stop", "on")
    t4, _ = tasks.submit("auto", lambda: "should not run", source="autopilot")
    tasks.wait_all()
    assert tasks.get(t4)["status"] == "cancelled"


def test_tasks_retry_then_success(monkeypatch):
    from vinted_drop import tasks
    monkeypatch.setattr(tasks.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("réseau")
        return "ok"
    tid, _ = tasks.submit("sync", flaky, max_attempts=3)
    tasks.wait_all()
    assert tasks.get(tid)["status"] == "success" and tasks.get(tid)["attempts"] == 2


def test_recover_marks_interrupted_tasks():
    from vinted_drop import db, tasks
    with db.connect() as c:
        c.execute("INSERT INTO tasks (kind, status, created_at) VALUES ('x','running',?)", (db.now(),))
    tasks.recover()
    assert tasks.list_tasks(1)[0]["status"] == "error"


# ---------- sauvegardes ----------
def test_backup_and_restore_roundtrip():
    from vinted_drop import backup, db
    sid, pid = _sold_product()
    path = backup.backup("test")
    assert path.exists() and backup.check(path)
    db.update_product(pid, title="MODIFIÉ")
    backup.restore(path.name)
    assert db.get_product(pid)["title"] == "Trench beige"
    assert any("avant-restauration" in b["name"] for b in backup.list_backups())
    with pytest.raises(FileNotFoundError):
        backup.restore("../../etc/passwd")


# ---------- planning ----------
def test_scheduler_due_logic_and_config():
    from vinted_drop import autopilot
    now = datetime(2026, 11, 3, 4, 10)
    autopilot.save_job_config("market", at="04:00", enabled=True)
    assert autopilot.is_due("market", now)
    autopilot._set_state("market", last_run=now.isoformat())
    assert not autopilot.is_due("market", now + timedelta(minutes=30))
    assert autopilot.is_due("market", now + timedelta(days=1))
    autopilot.save_job_config("sync", every=20)
    autopilot._set_state("sync", last_run=now.isoformat())
    assert not autopilot.is_due("sync", now + timedelta(minutes=10))
    assert autopilot.is_due("sync", now + timedelta(minutes=21))
    autopilot.save_job_config("sync", enabled=False)
    assert not autopilot.is_due("sync", now + timedelta(days=2))


def test_tick_runs_due_jobs_and_respects_emergency_stop(monkeypatch):
    from vinted_drop import autopilot, db, tasks
    ran = []
    monkeypatch.setitem(autopilot.JOBS, "backup", {**autopilot.JOBS["backup"], "fn": lambda: ran.append(1) or "ok"})
    for k in autopilot.JOBS:
        autopilot.save_job_config(k, enabled=(k == "backup"))
    autopilot.save_job_config("backup", at="00:00")
    assert autopilot.tick() == ["backup"] and ran == [1]
    assert tasks.list_tasks(1)[0]["kind"] == "job:backup"
    autopilot._set_state("backup", last_run=None)
    db.set_setting("emergency_stop", "on")
    assert autopilot.tick() == [] and ran == [1]


# ---------- prix ----------
def test_pricing_min_profit_30_and_market_adjustment():
    from vinted_drop import pricing
    p = {"cost": 25, "supplier_fees": 2, "shipping_cost": 0, "other_fees": 0, "shop_id": None}
    alone = pricing.recommend(p, None, None, min_profit=30)
    assert alone["profit_at_min"] >= 30 and alone["confidence_label"] == "faible"
    market = {"median": 95, "p25": 80, "p75": 110, "sample": 60, "avg_fav": 6, "age_days": 2}
    rec = pricing.recommend(p, market, [90, 100, 95, 92, 98], min_profit=30)
    assert rec["min_price"] >= rec["floor"] and rec["confidence_label"] == "haute"
    cheap_market = {"median": 40, "p25": 35, "p75": 45, "sample": 40, "avg_fav": 1, "age_days": 3}
    warn = pricing.recommend(p, cheap_market, None, min_profit=30)
    assert warn["warnings"] and warn["profit_at_min"] >= 30   # jamais sous le minimum rentable


# ---------- marché ----------
def test_potential_score_is_explainable_and_uses_snapshots():
    from vinted_drop import db, market, niches
    n = niches.get("trench")
    before = market.potential(n, month=3)
    assert before["confidence"] == "faible" and sum(c["max"] for c in before["components"]) == 100
    for day, fav in (("2026-10-20", 2.0), ("2026-11-02", 3.0)):
        with db.connect() as c:
            c.execute("INSERT INTO market_snapshots (niche, keyword, total, avg_fav, median_price, p25_price, "
                      "p75_price, sample, brands, taken_at) VALUES ('trench','trench femme',1200,?,85,70,99,96,'[\"Zara\"]',?)",
                      (fav, f"{day} 04:00:00"))
    monkey_today = market.niche_market("trench")
    assert monkey_today["median"] == 85 and monkey_today["brands"] == ["Zara"]
    tr = market.trend("trench")
    assert tr["demand_pct"] == 50.0


def test_opportunities_detected_and_favorite():
    from vinted_drop import market
    n = market.detect_opportunities(month=1)
    assert n > 0
    opps = market.list_opportunities()
    assert all(o["kind_label"] for o in opps)
    market.toggle_favorite(opps[0]["id"])
    assert market.list_opportunities(favorite=True)[0]["id"] == opps[0]["id"]
    market.detect_opportunities(month=1)            # 2e passage : historique conservé
    again = [o for o in market.list_opportunities() if o["id"] == opps[0]["id"]][0]
    assert again["favorite"] == 1 and len(again["data"]["history"]) == 2
