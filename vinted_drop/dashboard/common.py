"""Outils partagés par toutes les pages du dashboard."""
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config, db, tasks

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _eur(v):
    try:
        return f"{float(v or 0):,.2f} €".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _num(v):
    try:
        return f"{float(v or 0):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _dt(v, fmt="%d/%m %H:%M"):
    if not v:
        return "—"
    try:
        return datetime.fromisoformat(str(v).replace("T", " ")[:19]).strftime(fmt)
    except ValueError:
        return str(v)


def _delta(cur, prev):
    """Variation en % entre deux périodes (None si pas comparable)."""
    try:
        cur, prev = float(cur or 0), float(prev or 0)
    except (TypeError, ValueError):
        return None
    if prev == 0:
        return None if cur == 0 else 100.0
    return round(100 * (cur - prev) / abs(prev), 1)


templates.env.filters["eur"] = _eur
templates.env.filters["num"] = _num
templates.env.filters["dt"] = _dt
templates.env.globals["delta"] = _delta
templates.env.globals["ORDER_LABELS"] = db.ORDER_LABELS
templates.env.globals["PERIODS"] = db.PERIODS


def nav_context():
    shops = db.list_shops()
    return {
        "nav_pending": len(db.list_conversations(status="pending")),
        "nav_orders": sum(v for k, v in db.order_counts().items() if k in ("to_buy", "problem")),
        "nav_autopilot": db.get_setting("autopilot"),
        "nav_emergency": db.get_setting("emergency_stop") == "on",
        "nav_simulation": db.get_setting("simulation") == "on",
        "nav_connected": sum(1 for x in shops if x.get("vinted_status") == "connected"),
        "nav_shops": len(shops),
        "nav_tasks": len(tasks.active()),
    }


def render(request: Request, name: str, **ctx):
    ctx.setdefault("ok", request.query_params.get("ok"))
    ctx.setdefault("error", request.query_params.get("err"))
    return templates.TemplateResponse(request, name, {"page": name.split(".")[0], **nav_context(), **ctx})


def redirect(url: str, ok: str | None = None, err: str | None = None):
    sep = "&" if "?" in url else "?"
    if ok:
        url += f"{sep}ok={quote(ok)}"
    elif err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url, status_code=303)


def period(request: Request, default="30d"):
    """Lit ?period=…&start=…&end=… et renvoie les bornes + libellé."""
    p = request.query_params.get("period") or default
    if p not in db.PERIODS and p != "custom":
        p = default
    start_q, end_q = request.query_params.get("start"), request.query_params.get("end")
    try:
        start, end = db.period_range(p, start_q, end_q)
    except ValueError:
        p, (start, end) = default, db.period_range(default)
    label = db.PERIODS.get(p) or f"Du {_dt(start, '%d/%m/%Y')} au {_dt(end, '%d/%m/%Y')}"
    return {"key": p, "start": start, "end": end, "label": label, "start_q": start_q or start[:10],
            "end_q": end_q or end[:10], "today": date.today().isoformat()}


def background(kind, fn, *args, label=None, product_id=None, shop_id=None, dedupe_key=None, **kwargs):
    """Lance une tâche de fond traçable (voir page Activité)."""
    return tasks.submit(kind, fn, *args, label=label, product_id=product_id, shop_id=shop_id,
                        dedupe_key=dedupe_key or (f"{kind}:{product_id}" if product_id else None), **kwargs)


def setup_checklist():
    """Étapes de configuration restantes (affichées sur l'accueil tant qu'elles ne sont pas faites)."""
    from ..image_providers import available
    shops = db.list_shops()
    steps = [
        ("Brancher ComfyUI (photos gratuites) ou une clé IA", bool(available() or config.OPENAI_API_KEY), "/photos-gratuites"),
        ("Créer une boutique", bool(shops), "/boutiques"),
        ("Connecter le compte Vinted (token)", any(s.get("vinted_status") == "connected" for s in shops), "/connexions"),
        ("Apprendre un modèle d'annonce", any(s.get("template_id") for s in shops), "/connexions"),
        ("Créer un modèle photo", bool(db.list_personas()), "/studio"),
        ("Connecter Telegram", bool(config.TELEGRAM_BOT_TOKEN and config.telegram_chats()), "/parametres"),
    ]
    return [{"label": l, "done": d, "url": u} for l, d, u in steps]
