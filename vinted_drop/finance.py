"""Gestion financière : résumé, fiche par produit, journal des transactions, exports CSV / Excel.

Toutes les valeurs viennent de la base (ventes, commandes fournisseurs, coûts IA, dépenses saisies).
Bénéfice d'une vente = prix de vente − frais plateforme − coût de revient (achat + frais + port + autres + emballage).
Bénéfice net de la période = bénéfice des ventes − coûts IA − autres dépenses saisies.
Les exports ne contiennent jamais de token ni de cookie.
"""
import csv
import io

from . import config, db

EXPENSE_CATEGORIES = {"supplier": "Fournisseur", "shipping": "Envoi / emballage", "tools": "Outils / abonnements",
                      "ads": "Mise en avant", "other": "Autre"}


def summary(start, end, shop_id=None) -> dict:
    s = db.stats(start=start, end=end, shop_id=shop_id)
    args = [start, end]
    shop_sql = ""
    if shop_id:
        shop_sql = " AND shop_id=?"
        args.append(shop_id)
    with db.connect() as c:
        supplier = c.execute(
            f"SELECT COUNT(*) n, COALESCE(SUM(COALESCE(supplier_price,0)+COALESCE(supplier_fees,0)),0) v FROM orders "
            f"WHERE status NOT IN ('to_buy','cancelled') AND COALESCE(ordered_at, created_at) BETWEEN ? AND ?{shop_sql}",
            args).fetchone()
        ai_args = [start, end]
        ai_sql = ""
        if shop_id:
            ai_sql = " AND product_id IN (SELECT id FROM products WHERE shop_id=?)"
            ai_args.append(shop_id)
        ai = c.execute(f"SELECT COALESCE(SUM(cost),0) FROM ai_costs WHERE created_at BETWEEN ? AND ?{ai_sql}",
                       ai_args).fetchone()[0]
        exp = c.execute(f"SELECT COALESCE(SUM(amount),0) FROM expenses WHERE spent_at BETWEEN ? AND ?{shop_sql}",
                        args).fetchone()[0]
    cap = db.capital_stats(shop_id)
    net_profit = round(s["profit"] - ai - exp, 2)
    return {**s, "supplier_spend": round(supplier["v"], 2), "supplier_orders": supplier["n"], "ai_costs": round(ai, 2),
            "expenses": round(exp, 2), "net_profit": net_profit,
            "net_margin_pct": round(100 * net_profit / s["revenue"], 1) if s["revenue"] else 0, **cap}


def product_sheet(pid) -> dict:
    p = db.get_product(pid)
    if not p:
        raise ValueError("Produit introuvable")
    with db.connect() as c:
        sale = c.execute("SELECT * FROM sales WHERE product_id=?", (pid,)).fetchone()
    ai = db.product_cost(pid)
    lines = [("Prix d'achat", p.get("cost") or 0), ("Frais fournisseur", p.get("supplier_fees") or 0),
             ("Port fournisseur", p.get("shipping_cost") or 0), ("Autres frais", p.get("other_fees") or 0),
             ("Emballage", config.PACKAGING_COST)]
    total_cost = round(sum(v for _, v in lines), 2)
    sale_price = sale["sale_price"] if sale else (p.get("price") or 0)
    fees = sale["fees"] if sale else round(sale_price * config.PLATFORM_FEE_PCT / 100, 2)
    profit = round(sale_price - fees - total_cost, 2)
    return {"product": p, "lines": lines, "total_cost": total_cost, "sale_price": sale_price, "sold": bool(sale),
            "platform_fees": fees, "ai_cost": ai, "profit": profit, "net_profit": round(profit - ai, 2),
            "margin_pct": round(100 * (profit - ai) / sale_price, 1) if sale_price else 0,
            "cost_unknown": bool(p.get("cost_unknown"))}


def transactions(start, end, shop_id=None, limit=1000) -> list[dict]:
    """Journal unique : ventes (+), achats fournisseurs (−), coûts IA (−), dépenses (−)."""
    out = []
    for s in db.list_sales(limit=limit, shop_id=shop_id, start=start, end=end):
        out.append({"date": s["sold_at"], "type": "Vente", "label": s.get("title") or f"Produit #{s['product_id']}",
                    "shop": s.get("shop_name"), "amount": s["sale_price"], "ref": f"vente #{s['id']}"})
        if s.get("fees"):
            out.append({"date": s["sold_at"], "type": "Frais plateforme", "label": s.get("title"),
                        "shop": s.get("shop_name"), "amount": -s["fees"], "ref": f"vente #{s['id']}"})
    for o in db.list_orders(limit=limit, shop_id=shop_id):
        when = o.get("ordered_at") or o.get("created_at")
        if o["status"] in ("to_buy", "cancelled") or not (start <= (when or "") <= end):
            continue
        out.append({"date": when, "type": "Achat fournisseur", "label": o.get("title") or o.get("supplier_title"),
                    "shop": o.get("shop_name"), "amount": -round((o.get("supplier_price") or 0) + (o.get("supplier_fees") or 0), 2),
                    "ref": f"commande #{o['id']}"})
    with db.connect() as c:
        for r in c.execute("SELECT a.*, p.title FROM ai_costs a LEFT JOIN products p ON p.id=a.product_id "
                           "WHERE a.created_at BETWEEN ? AND ? AND a.cost > 0 ORDER BY a.id DESC LIMIT ?",
                           (start, end, limit)):
            out.append({"date": r["created_at"], "type": "Coût IA", "label": f"{r['provider']} · {r['kind']}"
                        + (f" · {r['title']}" if r["title"] else ""), "shop": None, "amount": -r["cost"],
                        "ref": f"produit #{r['product_id']}" if r["product_id"] else ""})
    for e in db.list_expenses(start, end, shop_id):
        out.append({"date": e["spent_at"], "type": "Dépense", "label": f"{EXPENSE_CATEGORIES.get(e['category'], e['category'])}"
                    + (f" · {e['label']}" if e["label"] else ""), "shop": e.get("shop_name"), "amount": -e["amount"],
                    "ref": f"dépense #{e['id']}"})
    return sorted(out, key=lambda x: x["date"] or "", reverse=True)


def by_shop(start, end):
    rows = db.shop_breakdown(start, end)
    for r in rows:
        r["finance"] = summary(start, end, r["id"])
    return rows


# ---------------- exports ----------------
def _products_rows():
    out = []
    for p in db.list_products(limit=100000):
        sheet = product_sheet(p["id"])
        out.append({"id": p["id"], "titre": p.get("title") or p.get("supplier_title"), "boutique": p.get("shop_name"),
                    "niche": p.get("niche"), "statut": p["status"], "fournisseur": p.get("supplier"),
                    "lien_fournisseur": p.get("supplier_url"), "prix_achat": p.get("cost"),
                    "frais_fournisseur": p.get("supplier_fees"), "port": p.get("shipping_cost"),
                    "autres_frais": p.get("other_fees"), "cout_revient": sheet["total_cost"], "prix_affiche": p.get("price"),
                    "prix_mini": p.get("min_price"), "cout_ia": sheet["ai_cost"], "benefice_net": sheet["net_profit"],
                    "cout_inconnu": "oui" if p.get("cost_unknown") else ""})
    return out


def _sales_rows(start, end):
    return [{"id": s["id"], "date": s["sold_at"], "produit": s.get("title"), "boutique": s.get("shop_name"),
             "acheteur": s.get("buyer"), "prix": s["sale_price"], "frais": s.get("fees"), "cout": s["cost"],
             "benefice": s["profit"], "source": s.get("source")} for s in db.list_sales(100000, start=start, end=end)]


def _orders_rows():
    return [{"id": o["id"], "vente": o.get("sale_id"), "produit": o.get("title"), "boutique": o.get("shop_name"),
             "statut": db.ORDER_LABELS.get(o["status"], o["status"]), "fournisseur": o.get("supplier"),
             "reference": o.get("supplier_ref"), "prix": o.get("supplier_price"), "frais": o.get("supplier_fees"),
             "commande_le": o.get("ordered_at"), "suivi": o.get("tracking_number"), "livre_le": o.get("delivered_at"),
             "notes": o.get("notes")} for o in db.list_orders(limit=100000)]


def datasets(start, end):
    s = summary(start, end)
    resume = [{"indicateur": k, "valeur": v} for k, v in (
        ("Période", f"{start[:10]} → {end[:10]}"), ("Chiffre d'affaires", s["revenue"]), ("Ventes", s["sales"]),
        ("Bénéfice des ventes", s["profit"]), ("Coûts IA", s["ai_costs"]), ("Autres dépenses", s["expenses"]),
        ("Bénéfice net", s["net_profit"]), ("Marge nette %", s["net_margin_pct"]), ("Panier moyen", s["avg_basket"]),
        ("Achats fournisseurs", s["supplier_spend"]), ("Annonces en ligne (coût engagé)", s["listed_value"]),
        ("Stock en transit", s["in_transit_value"]))]
    return {"Résumé": resume, "Transactions": transactions(start, end, limit=100000), "Ventes": _sales_rows(start, end),
            "Produits": _products_rows(), "Commandes": _orders_rows(),
            "Dépenses": [{k: e[k] for k in ("id", "spent_at", "category", "label", "amount", "shop_name")}
                         for e in db.list_expenses(start, end, limit=100000)]}


def export_csv(name, start, end) -> bytes:
    rows = datasets(start, end).get(name) or []
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM : Excel lit les accents correctement


def export_xlsx(start, end) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in datasets(start, end).items():
        ws = wb.create_sheet(name[:31])
        if not rows:
            ws.append(["Aucune donnée"])
            continue
        headers = list(rows[0].keys())
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in rows:
            ws.append([r.get(h) for h in headers])
        for i, h in enumerate(headers, 1):
            width = max(len(str(h)), *(len(str(r.get(h) or "")) for r in rows[:200]))
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(60, width + 2)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
