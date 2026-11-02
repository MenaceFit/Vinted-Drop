"""Ventes, commandes fournisseurs, finances, activité, pilote automatique, paramètres, sauvegardes."""
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response

from .. import autopilot, backup, config, db, finance, listing, tasks
from ..notify import NOTIFY_CATEGORIES
from .common import background, period, redirect, render

router = APIRouter()
PAGE = 50


# ---------------- ventes ----------------
@router.get("/ventes")
def sales(request: Request, statut: str | None = None, boutique: int | None = None, page: int = 1):
    per = period(request, "90d")
    page = max(1, page)
    rows = db.list_sales(limit=PAGE + 1, status=statut or None, shop_id=boutique, start=per["start"], end=per["end"],
                         offset=(page - 1) * PAGE)
    return render(request, "sales.html", sales=rows[:PAGE], has_next=len(rows) > PAGE, page=page, statut=statut,
                  boutique=boutique, shops=db.list_shops(), per=per, statuses=db.SALE_STATUSES,
                  fin=finance.summary(per["start"], per["end"], boutique))


@router.post("/vente/{sid}/statut")
def sale_status(sid: int, status_: str = Form(..., alias="status")):
    if status_ not in db.SALE_STATUSES:
        raise HTTPException(400)
    db.update_sale(sid, status=status_)
    return redirect("/ventes", ok="Statut mis à jour")


# ---------------- commandes fournisseurs ----------------
@router.get("/commandes")
def orders(request: Request, statut: str | None = None, boutique: int | None = None):
    rows = db.list_orders(status=statut or None, shop_id=boutique, open_only=not statut)
    for o in rows:
        o["history"] = db.order_history(o["id"])
    return render(request, "orders.html", orders=rows, counts=db.order_counts(), statut=statut, boutique=boutique,
                  shops=db.list_shops(), statuses=db.ORDER_STATUSES)


@router.post("/commande/{oid}")
def update_order(oid: int, status_: str = Form("", alias="status"), supplier_ref: str = Form(""),
                 tracking_number: str = Form(""), carrier: str = Form(""), supplier_price: str = Form(""),
                 notes: str = Form(""), note: str = Form("")):
    o = db.get_order(oid)
    if not o:
        raise HTTPException(404)
    fields = {}
    if status_:
        if status_ not in db.ORDER_STATUSES:
            raise HTTPException(400, "Statut inconnu")
        fields["status"] = status_
    for k, v in (("supplier_ref", supplier_ref), ("tracking_number", tracking_number), ("carrier", carrier),
                 ("notes", notes)):
        if v != "":
            fields[k] = v.strip()[:500]
    if supplier_price:
        fields["supplier_price"] = float(supplier_price.replace(",", "."))
    db.update_order(oid, note=note or None, **fields)
    db.log_event("order", f"Commande #{oid} : {db.ORDER_LABELS.get(fields.get('status'), 'mise à jour')}",
                 shop_id=o.get("shop_id"), product_id=o.get("product_id"))
    return redirect("/commandes", ok="Commande mise à jour")


# ---------------- finances ----------------
@router.get("/finances")
def finances(request: Request, boutique: int | None = None):
    per = period(request)
    s, e = per["start"], per["end"]
    return render(request, "finance.html", per=per, fin=finance.summary(s, e, boutique),
                  prev=finance.summary(*db.previous_range(s, e), boutique) if per["key"] != "all" else None,
                  by_shop=finance.by_shop(s, e), tx=finance.transactions(s, e, boutique, 300),
                  expenses=db.list_expenses(s, e, boutique, 100), categories=finance.EXPENSE_CATEGORIES,
                  shops=db.list_shops(), boutique=boutique, series=db.series(s, e, boutique),
                  unsold=db.unsold_products(14, 20, boutique), niches_perf=db.niche_breakdown(s, e))


@router.post("/finances/depense")
def add_expense(amount: str = Form(...), category: str = Form("other"), label: str = Form(""),
                shop_id: str = Form(""), spent_at: str = Form("")):
    try:
        value = float(amount.replace(",", "."))
    except ValueError:
        return redirect("/finances", err="Montant invalide")
    if value <= 0:
        return redirect("/finances", err="Le montant doit être positif")
    when = f"{spent_at} 12:00:00" if spent_at else None
    db.add_expense(value, category if category in finance.EXPENSE_CATEGORIES else "other", label.strip()[:120] or None,
                   int(shop_id) if shop_id else None, spent_at=when)
    db.log_event("finance", f"Dépense ajoutée : {value:.2f} € ({label or category})")
    return redirect("/finances", ok="Dépense ajoutée")


@router.post("/finances/depense/{eid}/supprimer")
def delete_expense(eid: int):
    db.delete_expense(eid)
    db.log_event("finance", f"Dépense #{eid} supprimée")
    return redirect("/finances", ok="Dépense supprimée")


@router.get("/finances/export.{fmt}")
def export(request: Request, fmt: str, table: str = "Transactions"):
    per = period(request)
    stamp = datetime.now().strftime("%Y%m%d")
    if fmt == "xlsx":
        data = finance.export_xlsx(per["start"], per["end"])
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="vinted-drop-{stamp}.xlsx"'})
    if fmt == "csv":
        if table not in finance.datasets(per["start"], per["end"]):
            raise HTTPException(400, "Table inconnue")
        data = finance.export_csv(table, per["start"], per["end"])
        safe = table.lower().replace("é", "e")
        return Response(data, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="vinted-drop-{safe}-{stamp}.csv"'})
    raise HTTPException(404)


# ---------------- activité ----------------
@router.get("/activite")
def activity(request: Request, type: str | None = None, boutique: int | None = None, niveau: str | None = None,
             q: str | None = None, page: int = 1):
    per = period(request, "30d")
    page = max(1, page)
    rows = db.list_events(PAGE + 1, kind=type or None, shop_id=boutique, level=niveau or None, start=per["start"],
                          end=per["end"], q=q or None, offset=(page - 1) * PAGE)
    return render(request, "activity.html", events=rows[:PAGE], has_next=len(rows) > PAGE, page=page, sel=type,
                  boutique=boutique, niveau=niveau, q=q or "", per=per, shops=db.list_shops(),
                  categories=db.EVENT_CATEGORIES, task_list=tasks.list_tasks(40))


# ---------------- pilote automatique ----------------
@router.get("/pilote")
def autopilot_page(request: Request):
    return render(request, "autopilot.html", settings=db.all_settings(), jobs=autopilot.jobs_overview(),
                  task_list=tasks.list_tasks(30), errors=tasks.list_tasks(15, status="error"),
                  notify_categories=NOTIFY_CATEGORIES, shops=db.list_shops())


@router.post("/pilote")
async def autopilot_save(request: Request):
    form = await request.form()
    for k in db.DEFAULT_SETTINGS:
        if k in form and k not in ("emergency_stop",):
            db.set_setting(k, form[k])
    for k in ("prepare_photos", "prepare_listing", *(f"notify_{c}" for c in NOTIFY_CATEGORIES)):
        if k in form and form[k] in ("on", "off"):
            db.set_setting(k, form[k])
    db.log_event("settings", "Réglages du pilote automatique modifiés")
    return redirect("/pilote", ok="Réglages enregistrés")


@router.post("/pilote/tache/{key}")
async def job_save(key: str, request: Request):
    if key not in autopilot.JOBS:
        raise HTTPException(404)
    form = await request.form()
    cfg = {"enabled": form.get("enabled") == "1", "alert": form.get("alert") == "1",
           "retries": max(1, min(5, int(form.get("retries") or 1)))}
    if form.get("every"):
        cfg["every"] = int(form["every"])
    if form.get("at"):
        cfg["at"] = form["at"]
    try:
        autopilot.save_job_config(key, **cfg)
    except ValueError as e:
        return redirect("/pilote", err=str(e))
    return redirect("/pilote", ok=f"Tâche « {autopilot.JOBS[key]['label']} » enregistrée")


@router.post("/pilote/lancer/{job}")
def autopilot_run(job: str):
    alias = {"messages": "inbox", "publier": "publish", "preparer": "prepare", "prix": "price", "marche": "market"}
    key = alias.get(job, job)
    if key not in autopilot.JOBS:
        raise HTTPException(404)
    _, created = background(f"job:{key}", autopilot.run_job, key, "dashboard", label=autopilot.JOBS[key]["label"],
                            dedupe_key=f"job:{key}")
    return redirect("/pilote", ok=f"« {autopilot.JOBS[key]['label']} » lancée" if created else "Déjà en cours")


@router.post("/pilote/urgence")
def emergency(action: str = Form(...)):
    on = action == "stop"
    db.set_setting("emergency_stop", "on" if on else "off")
    db.log_event("security", "ARRÊT D'URGENCE activé" if on else "Arrêt d'urgence levé", level="warning")
    return redirect("/pilote", ok="Arrêt d'urgence activé : plus aucune action automatique" if on
                    else "Pilote automatique relancé")


@router.post("/tache/{tid}/annuler")
def cancel_task(tid: int):
    if tasks.cancel(tid):
        return redirect("/activite", ok="Tâche annulée")
    return redirect("/activite", err="Impossible d'annuler une tâche déjà lancée ou terminée")


# ---------------- paramètres ----------------
@router.get("/parametres")
def settings_page(request: Request):
    from .. import comfyui
    from ..image_providers import available
    keys = {"OpenAI": bool(config.OPENAI_API_KEY), "Gemini": bool(config.GEMINI_API_KEY),
            "ComfyUI (PC)": bool(config.COMFYUI_URL), "Telegram": bool(config.TELEGRAM_BOT_TOKEN)}
    sec = {"password": bool(config.DASHBOARD_PASSWORD), "host": config.DASHBOARD_HOST,
           "secret_key": "variable SECRET_KEY" if config.SECRET_KEY else "fichier data/secret.key",
           "channel": config.TELEGRAM_CHANNEL_ID, "chats": sorted(config.TELEGRAM_ALLOWED_CHAT_IDS)}
    return render(request, "settings.html", settings=db.all_settings(), keys=keys, sec=sec,
                  backups=backup.list_backups(), providers=available(), local=comfyui.status(timeout=2),
                  styles=listing.STYLES, config=config)


@router.post("/parametres")
async def settings_save(request: Request):
    form = await request.form()
    numeric = {"min_profit", "target_multiplier", "negotiation_buffer", "daily_goal", "daily_budget"}
    for k, v in form.items():
        if k not in db.DEFAULT_SETTINGS:
            continue
        if k in numeric:
            try:
                float(str(v).replace(",", "."))
            except ValueError:
                return redirect("/parametres", err=f"Valeur invalide pour {k}")
            v = str(v).replace(",", ".")
        db.set_setting(k, v)
    db.log_event("settings", "Paramètres généraux modifiés")
    return redirect("/parametres", ok="Paramètres enregistrés")


@router.post("/sauvegardes/creer")
def backup_now():
    path = backup.backup("manuelle")
    db.log_event("backup", f"Sauvegarde manuelle : {path.name}")
    return redirect("/parametres", ok=f"Sauvegarde créée : {path.name}")


@router.post("/sauvegardes/restaurer")
def backup_restore(name: str = Form(...), confirm: str = Form("")):
    if confirm != "RESTAURER":
        return redirect("/parametres", err="Tape RESTAURER pour confirmer")
    try:
        safety = backup.restore(name)
    except (FileNotFoundError, ValueError) as e:
        return redirect("/parametres", err=str(e))
    db.init()
    db.log_event("backup", f"Base restaurée depuis {name} (état précédent sauvegardé : {safety.name})", level="warning")
    return redirect("/parametres", ok=f"Base restaurée. L'état précédent est dans {safety.name}")
