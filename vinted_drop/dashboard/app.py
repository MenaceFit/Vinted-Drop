"""Dashboard web : suivi des ventes, boutiques, produits, niches.

Lancer : python -m vinted_drop.cli dashboard   (puis http://localhost:8000)
"""
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import (autopilot, config, db, listing, market, niches, notify, photos, pricing, publisher,
               responder, supplier, vinted_client)

security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)):
    if not config.DASHBOARD_PASSWORD:
        return
    ok = creds and secrets.compare_digest(creds.username, config.DASHBOARD_USER) and \
        secrets.compare_digest(creds.password, config.DASHBOARD_PASSWORD)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="Vinted Drop", dependencies=[Depends(auth)], lifespan=lifespan)
app.mount("/media", StaticFiles(directory=config.MEDIA_DIR), name="media")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["eur"] = lambda v: f"{(v or 0):,.2f} €".replace(",", " ").replace(".", ",")

# Erreurs des tâches IA en arrière-plan, affichées sur la fiche produit
JOB_ERRORS: dict[int, str] = {}
JOBS_RUNNING: set[int] = set()


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, {"page": name.split(".")[0], **ctx})


@app.get("/")
def home(request: Request):
    return render(
        request, "home.html",
        today=db.today_stats(), week=db.stats(7), month=db.stats(30), total=db.stats(),
        series=json.dumps(db.daily_series(30)), shops=db.shop_breakdown(),
        sales=db.list_sales(limit=10), to_order=db.list_sales(status="to_order"),
        top_niches=niches.ranked()[:4], events=db.list_events(12), settings=db.all_settings(),
        pending_msgs=len(db.list_conversations(status="pending")),
        disconnected=[x for x in db.list_shops() if x.get("vinted_status") != "connected"],
    )


@app.get("/api/stats")
def api_stats():
    return JSONResponse({"today": db.today_stats(), "7d": db.stats(7), "30d": db.stats(30),
                         "total": db.stats(), "series": db.daily_series(30), "shops": db.shop_breakdown()})


# ---------------- produits ----------------
@app.get("/produits")
def products(request: Request, statut: str | None = None, boutique: int | None = None):
    return render(request, "products.html", products=db.list_products(status=statut or None,
                  shop_id=boutique), shops=db.list_shops(), statut=statut, boutique=boutique,
                  statuses=db.PRODUCT_STATUSES, running=JOBS_RUNNING)


@app.post("/produits")
def add_product(url: str = Form(""), cost: float = Form(...), shop_id: str = Form(""),
                size: str = Form("")):
    shop = db.get_shop(int(shop_id)) if shop_id else None
    info = supplier.fetch_info(url) if url else {"supplier": None, "title": None, "image_url": None}
    pid = db.add_product(cost=cost, supplier_url=url or None, supplier=info["supplier"],
                         supplier_title=info["title"], shop_id=shop["id"] if shop else None,
                         niche=shop["niche"] if shop else None, size=size or None)
    if info.get("image_url"):
        img = supplier.download_image(info["image_url"], pid)
        if img:
            db.update_product(pid, supplier_image=photos.rel(img))
    return RedirectResponse(f"/produit/{pid}", status_code=303)


@app.post("/produits/importer-gagnant")
def import_winner(url: str = Form(...), cost: float = Form(...), title: str = Form(""),
                  niche: str = Form(""), shop_id: str = Form("")):
    shop = db.get_shop(int(shop_id)) if shop_id else None
    pid = db.add_product(cost=cost, supplier_url=url, supplier=supplier.supplier_name(url),
                         supplier_title=title or None, niche=niche or (shop and shop["niche"]),
                         shop_id=shop["id"] if shop else None)
    info = supplier.fetch_info(url)
    if info.get("image_url"):
        img = supplier.download_image(info["image_url"], pid)
        if img:
            db.update_product(pid, supplier_image=photos.rel(img))
    return RedirectResponse(f"/produit/{pid}", status_code=303)


@app.get("/produit/{pid}")
def product(request: Request, pid: int):
    p = db.get_product(pid)
    if not p:
        raise HTTPException(404)
    return render(request, "product.html", p=p, shops=db.list_shops(), prices=pricing.compute_prices(p["cost"]),
                  error=JOB_ERRORS.pop(pid, None), running=pid in JOBS_RUNNING, statuses=db.PRODUCT_STATUSES)


@app.post("/produit/{pid}/image")
async def upload_image(pid: int, request: Request):
    form = await request.form()
    f = form.get("image")
    if not f or not getattr(f, "filename", ""):
        return RedirectResponse(f"/produit/{pid}", status_code=303)
    ext = Path(f.filename).suffix.lower() or ".jpg"
    out = photos.product_dir(pid) / f"supplier{ext}"
    out.write_bytes(await f.read())
    db.update_product(pid, supplier_image=photos.rel(out))
    return RedirectResponse(f"/produit/{pid}", status_code=303)


@app.post("/produit/{pid}/modifier")
def edit_product(pid: int, title: str = Form(""), description: str = Form(""), price: str = Form(""),
                 min_price: str = Form(""), cost: str = Form(""), size: str = Form(""),
                 shop_id: str = Form(""), status_: str = Form("", alias="status"), vinted_url: str = Form("")):
    fields = {"title": title or None, "description": description or None, "size": size or None,
              "vinted_url": vinted_url or None, "shop_id": int(shop_id) if shop_id else None}
    for k, v in (("price", price), ("min_price", min_price), ("cost", cost)):
        if v:
            fields[k] = float(v.replace(",", "."))
    if fields["shop_id"]:
        fields["niche"] = db.get_shop(fields["shop_id"])["niche"]
    if status_:
        fields["status"] = status_
    db.update_product(pid, **fields)
    return RedirectResponse(f"/produit/{pid}", status_code=303)


def _run(pid, fn, *args):
    JOBS_RUNNING.add(pid)
    try:
        fn(*args)
        p = db.get_product(pid)
        if fn is listing.prepare:
            notify.send(f"✅ Produit #{pid} prêt à publier : {p.get('title')}\n{config.DASHBOARD_URL}/produit/{pid}")
    except Exception as e:  # affiché sur la page produit
        JOB_ERRORS[pid] = str(e)
    finally:
        JOBS_RUNNING.discard(pid)


@app.post("/produit/{pid}/action/{action}")
def product_action(pid: int, action: str, bg: BackgroundTasks, sale_price: str = Form(""),
                   buyer: str = Form("")):
    if action == "photos":
        bg.add_task(_run, pid, photos.generate_product_photos, pid)
    elif action == "fiche":
        bg.add_task(_run, pid, listing.generate_listing, pid)
    elif action == "preparer":
        bg.add_task(_run, pid, listing.prepare, pid)
    elif action == "publie":
        db.update_product(pid, status="published")
    elif action == "publier-vinted":
        try:
            publisher.publish(pid)
        except Exception as e:
            JOB_ERRORS[pid] = str(e)
    elif action == "vendu":
        p = db.get_product(pid)
        price = float(sale_price.replace(",", ".")) if sale_price else p.get("price")
        if not price:
            raise HTTPException(400, "Prix de vente manquant")
        sid = db.record_sale(pid, price, buyer or None)
        notify.notify_sale(db.get_sale(sid))
    else:
        raise HTTPException(400, "Action inconnue")
    if action in ("photos", "fiche", "preparer"):
        JOBS_RUNNING.add(pid)
    return RedirectResponse(f"/produit/{pid}", status_code=303)


# ---------------- ventes ----------------
@app.get("/ventes")
def sales(request: Request, statut: str | None = None):
    return render(request, "sales.html", sales=db.list_sales(limit=300, status=statut or None),
                  statut=statut, statuses=db.SALE_STATUSES)


@app.post("/vente/{sid}/statut")
def sale_status(sid: int, status_: str = Form(..., alias="status")):
    if status_ not in db.SALE_STATUSES:
        raise HTTPException(400)
    db.update_sale(sid, status=status_)
    return RedirectResponse("/ventes", status_code=303)


# ---------------- boutiques & modèles ----------------
@app.get("/boutiques")
def shops(request: Request):
    return render(request, "shops.html", shops=db.shop_breakdown(), all_shops=db.list_shops(),
                  personas=db.list_personas(), niches=niches.load(), error=JOB_ERRORS.pop(0, None),
                  templates=db.list_templates(), ok=request.query_params.get("ok"))


@app.post("/boutiques")
def add_shop(name: str = Form(...), niche: str = Form(""), persona_id: str = Form(""),
             vinted_url: str = Form("")):
    db.add_shop(name, niche or None, int(persona_id) if persona_id else None, vinted_url or None)
    return RedirectResponse("/boutiques", status_code=303)


@app.post("/modeles")
def add_persona(bg: BackgroundTasks, name: str = Form(...), description: str = Form(...)):
    def job():
        try:
            photos.create_persona(name, description)
        except Exception as e:
            JOB_ERRORS[0] = f"Modèle « {name} » : {e}"
    bg.add_task(job)
    return RedirectResponse("/boutiques", status_code=303)


@app.post("/boutique/{sid}/modele")
def set_shop_persona(sid: int, persona_id: str = Form("")):
    with db.connect() as c:
        c.execute("UPDATE shops SET persona_id=? WHERE id=?", (int(persona_id) if persona_id else None, sid))
    return RedirectResponse("/boutiques", status_code=303)


# ---------------- niches ----------------
@app.get("/niches")
def niches_page(request: Request):
    ranked = niches.ranked()
    winners = {}
    for w in niches.winning_products():
        winners.setdefault(w["niche"], []).append(w)
    return render(request, "niches.html", niches=ranked, winners=winners, shops=db.list_shops(),
                  perf=db.niche_breakdown())


# ---------------- connexion Vinted ----------------
@app.post("/boutique/{sid}/vinted")
def connect_vinted(sid: int, cookie: str = Form(...), domain: str = Form("vinted.fr"), user_agent: str = Form(""),
                   daily_quota: int = Form(20)):
    db.update_shop(sid, daily_quota=daily_quota)
    try:
        user = vinted_client.connect_shop(sid, cookie, domain, user_agent or None)
        return RedirectResponse(f"/boutiques?ok=Connecté à @{user.get('login')}", status_code=303)
    except Exception as e:
        JOB_ERRORS[0] = f"Connexion Vinted : {e}"
        return RedirectResponse("/boutiques", status_code=303)


@app.post("/boutique/{sid}/deconnecter")
def disconnect_vinted(sid: int):
    db.update_shop(sid, vinted_cookies=None, vinted_status="disconnected")
    return RedirectResponse("/boutiques", status_code=303)


@app.post("/boutique/{sid}/apprendre")
def learn_template(sid: int, item_url: str = Form(...), name: str = Form("")):
    try:
        publisher.learn_template(sid, item_url, name or None)
        return RedirectResponse("/boutiques?ok=Modèle d'annonce enregistré", status_code=303)
    except Exception as e:
        JOB_ERRORS[0] = f"Apprentissage : {e}"
        return RedirectResponse("/boutiques", status_code=303)


@app.post("/boutique/{sid}/modele-annonce")
def set_shop_template(sid: int, template_id: str = Form("")):
    db.update_shop(sid, template_id=int(template_id) if template_id else None)
    return RedirectResponse("/boutiques", status_code=303)


@app.post("/modeles-annonce")
def add_template_manual(name: str = Form(...), catalog_id: int = Form(...), brand_id: str = Form(""),
                        brand: str = Form(""), size_map: str = Form(""), package_size_id: int = Form(2)):
    sizes = {}
    for part in size_map.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            sizes[k.strip().upper()] = int(v)
    db.add_template(name, catalog_id=catalog_id, brand_id=int(brand_id) if brand_id else None,
                    brand=brand or None, size_map=sizes, size_id=next(iter(sizes.values()), None),
                    package_size_id=package_size_id)
    return RedirectResponse("/boutiques?ok=Modèle d'annonce créé", status_code=303)


# ---------------- pilote automatique ----------------
@app.get("/pilote")
def autopilot_page(request: Request):
    return render(request, "autopilot.html", settings=db.all_settings(), events=db.list_events(60),
                  scan=market.last_scan(), shops=db.list_shops(), ok=request.query_params.get("ok"))


@app.post("/pilote")
async def autopilot_save(request: Request):
    form = await request.form()
    for k in db.DEFAULT_SETTINGS:
        if k in form:
            db.set_setting(k, form[k])
    if "ship_delay" in form:
        db.set_setting("ship_delay", form["ship_delay"])
    return RedirectResponse("/pilote?ok=Réglages enregistrés", status_code=303)


@app.post("/pilote/lancer/{job}")
def autopilot_run(job: str, bg: BackgroundTasks):
    jobs = {"sync": autopilot.run_sync, "messages": autopilot.run_inbox, "publier": autopilot.run_publish,
            "preparer": autopilot.run_prepare_night, "prix": autopilot.run_price_drops,
            "marche": autopilot.run_market_scan}
    if job not in jobs:
        raise HTTPException(404)

    def safe():
        try:
            jobs[job]()
        except Exception as e:
            db.log_event("error", f"{job} : {e}")
    bg.add_task(safe)
    return RedirectResponse(f"/pilote?ok=Tâche « {job} » lancée", status_code=303)


# ---------------- messages ----------------
@app.get("/messages")
def messages_page(request: Request):
    return render(request, "messages.html", convs=db.list_conversations(limit=100), error=JOB_ERRORS.pop(-1, None))


@app.post("/message/{cid}")
def message_action(cid: int, action: str = Form(...), text: str = Form("")):
    try:
        if action == "send":
            responder.send_pending(cid, text or None)
        else:
            db.upsert_conversation(cid, status="ignored")
    except Exception as e:
        JOB_ERRORS[-1] = str(e)
    return RedirectResponse("/messages", status_code=303)
