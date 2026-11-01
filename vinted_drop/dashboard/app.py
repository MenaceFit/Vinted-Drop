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

from .. import config, db, listing, niches, notify, photos, pricing, supplier

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
        top_niches=niches.ranked()[:4],
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
    elif action == "vendu":
        p = db.get_product(pid)
        price = float(sale_price.replace(",", ".")) if sale_price else p.get("price")
        if not price:
            raise HTTPException(400, "Prix de vente manquant")
        sid = db.record_sale(pid, price, buyer or None)
        notify.send(notify.sale_message(db.get_sale(sid)))
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
                  personas=db.list_personas(), niches=niches.load(), error=JOB_ERRORS.pop(0, None))


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
