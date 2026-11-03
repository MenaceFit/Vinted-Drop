"""Dashboard web Vinted Drop 2.0 (FastAPI + Jinja, sans framework front).

Lancer : python -m vinted_drop.cli all   (dashboard + bot + pilote automatique)
Pages : accueil (centre de contrôle), produits, studio photo, ventes, commandes, finances, boutiques,
connexions Vinted, market intelligence, produits gagnants, opportunités, messages, activité,
pilote automatique, paramètres.
"""
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .. import config, db, finance, niches, tasks
from ..security import same_origin
from .common import period, render, setup_checklist

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
    tasks.recover()
    yield


app = FastAPI(title="Vinted Drop", dependencies=[Depends(auth)], lifespan=lifespan, docs_url=None, redoc_url=None)
config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def csrf_and_headers(request: Request, call_next):
    # CSRF : une action (POST) doit venir du dashboard lui-même
    if request.method in ("POST", "PUT", "DELETE") and not same_origin(request):
        return PlainTextResponse("Requête refusée (origine différente du dashboard).", status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    path = request.url.path
    if response.status_code == 200:
        if path.startswith("/static/") and "v" in request.query_params:
            # fichiers versionnés (?v=…) : le navigateur les garde, le numéro change à chaque mise à jour
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/media/"):
            response.headers["Cache-Control"] = "private, max-age=86400"
    return response


# compresse les pages et le JSON (ajouté après le middleware ci-dessus, donc exécuté en premier)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)


# ---------------- accueil ----------------
@app.get("/")
def home(request: Request):
    per = period(request)
    s, e = per["start"], per["end"]
    ps, pe = db.previous_range(s, e) if per["key"] != "all" else (s, e)
    cur, prev = finance.summary(s, e), finance.summary(ps, pe)
    series = db.series(s, e)
    prev_series = db.series(ps, pe)
    shops = db.shop_breakdown(s, e)
    return render(
        request, "home.html", per=per, cur=cur, prev=prev, series=json.dumps(series),
        prev_series=json.dumps(prev_series), series_list=series, shops=shops,
        niches_perf=db.niche_breakdown(s, e)[:8], top=db.top_products(s, e, 8),
        to_order=db.list_orders(status="to_buy", limit=6), events=db.list_events(10),
        pending_msgs=len(db.list_conversations(status="pending")), checklist=setup_checklist(),
        goal=float(db.get_setting("daily_goal") or 100), today=finance.summary(*db.period_range("today")),
        top_niches=niches.ranked(profitable_only=True)[:4],
        disconnected=[x for x in db.list_shops() if x.get("vinted_status") != "connected"])


@app.get("/api/stats")
def api_stats(request: Request):
    per = period(request)
    return JSONResponse({
        "today": db.today_stats(), "7d": db.stats(7), "30d": db.stats(30), "total": db.stats(),
        "period": finance.summary(per["start"], per["end"]), "series": db.series(per["start"], per["end"]),
        "shops": db.shop_breakdown(per["start"], per["end"]), "tasks": len(tasks.active())})


@app.get("/api/search")
def api_search(q: str = ""):
    return JSONResponse(db.search(q))


@app.get("/api/tasks")
def api_tasks(product_id: int | None = None):
    return JSONResponse({"active": tasks.active(product_id=product_id), "recent": tasks.list_tasks(10, product_id=product_id)})


@app.get("/health")
def health():
    return {"ok": True}


from . import routes_market, routes_products, routes_shops, routes_system  # noqa: E402

app.include_router(routes_products.router)
app.include_router(routes_shops.router)
app.include_router(routes_market.router)
app.include_router(routes_system.router)
