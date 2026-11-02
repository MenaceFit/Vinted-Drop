"""Boutiques : liste, fiche et réglages, comparaison, connexion Vinted, modèles d'annonce, messages."""
from fastapi import APIRouter, Form, HTTPException, Request

from .. import db, finance, listing, photos, publisher, responder, vinted_client
from ..image_providers import available
from .common import period, redirect, render

router = APIRouter()


def _niches():
    from .. import niches
    return niches.load()


@router.get("/boutiques")
def shops(request: Request):
    per = period(request)
    return render(request, "shops.html", per=per, shops=db.shop_breakdown(per["start"], per["end"]),
                  niches=_niches(), personas=db.list_personas())


@router.post("/boutiques")
def add_shop(name: str = Form(...), niche: str = Form(""), persona_id: str = Form(""), vinted_url: str = Form("")):
    name = name.strip()[:60]
    if not name:
        return redirect("/boutiques", err="Nom obligatoire")
    if db.find_shop(name):
        return redirect("/boutiques", err="Une boutique porte déjà ce nom")
    sid = db.add_shop(name, niche or None, int(persona_id) if persona_id else None, vinted_url or None)
    db.log_event("settings", f"Boutique « {name} » créée", shop_id=sid)
    return redirect(f"/boutique/{sid}", ok="Boutique créée : connecte maintenant son compte Vinted")


@router.get("/boutique/{sid}")
def shop_detail(request: Request, sid: int):
    shop = db.get_shop(sid)
    if not shop:
        raise HTTPException(404, "Boutique introuvable")
    per = period(request)
    s, e = per["start"], per["end"]
    return render(request, "shop.html", shop=shop, per=per, fin=finance.summary(s, e, sid),
                  series=db.series(s, e, sid), sales=db.list_sales(30, shop_id=sid, start=s, end=e),
                  events=db.list_events(30, shop_id=sid), top=db.top_products(s, e, 8, shop_id=sid),
                  unsold=db.unsold_products(14, 10, sid), personas=db.list_personas(), templates=db.list_templates(),
                  niches=_niches(), styles=listing.STYLES, providers=available(), sets=photos.SETS,
                  orders=db.list_orders(shop_id=sid, open_only=True, limit=20))


@router.post("/boutique/{sid}/reglages")
async def shop_settings(sid: int, request: Request):
    if not db.get_shop(sid):
        raise HTTPException(404)
    form = await request.form()
    fields = {}
    for k in ("name", "niche", "vinted_url", "listing_style", "photo_provider", "photo_set", "notes"):
        if k in form:
            fields[k] = (form[k] or "").strip() or None
    for k in ("publish_start", "publish_end", "daily_quota"):
        if form.get(k) not in (None, ""):
            fields[k] = max(0, min(24 if k != "daily_quota" else 200, int(form[k])))
    for k in ("monthly_goal", "min_profit"):
        if k in form:
            fields[k] = float(str(form[k]).replace(",", ".")) if form[k] not in ("", None) else None
    for k in ("persona_id", "template_id"):
        if k in form:
            fields[k] = int(form[k]) if form[k] else None
    if "active" in form:
        fields["active"] = 1 if form["active"] == "1" else 0
    if fields.get("name") is None:
        fields.pop("name", None)
    db.update_shop(sid, **fields)
    db.log_event("settings", "Réglages de la boutique modifiés", shop_id=sid)
    return redirect(f"/boutique/{sid}", ok="Réglages enregistrés")


@router.get("/boutiques/comparer")
def compare(request: Request):
    per = period(request)
    rows = finance.by_shop(per["start"], per["end"])
    best = {}
    if rows:
        best = {"profit": max(rows, key=lambda r: r["finance"]["net_profit"])["id"],
                "sales": max(rows, key=lambda r: r["sales"])["id"],
                "margin": max(rows, key=lambda r: r["margin_pct"])["id"],
                "performing": max(rows, key=lambda r: r["performing"])["id"]}
    return render(request, "shops_compare.html", per=per, rows=rows, best=best)


# ---------------- connexion Vinted ----------------
@router.get("/connexions")
def connections(request: Request):
    return render(request, "connections.html", shops=db.list_shops(), templates=db.list_templates())


@router.post("/boutique/{sid}/vinted")
def connect_vinted(sid: int, cookie: str = Form(...), domain: str = Form("vinted.fr"), user_agent: str = Form(""),
                   daily_quota: int = Form(20), back: str = Form("/connexions")):
    if not db.get_shop(sid):
        raise HTTPException(404)
    if domain not in vinted_client.DOMAINS:
        return redirect(back, err="Site Vinted inconnu")
    db.update_shop(sid, daily_quota=max(1, min(200, daily_quota)))
    try:
        user = vinted_client.connect_shop(sid, cookie, domain, user_agent or None)
    except Exception as e:
        db.log_event("vinted", f"Connexion refusée : {e}", shop_id=sid, level="error")
        return redirect(back, err=f"Connexion Vinted : {e}")
    return redirect(back, ok=f"Connecté à @{user.get('login')}")


@router.post("/boutique/{sid}/deconnecter")
def disconnect_vinted(sid: int, back: str = Form("/connexions")):
    db.update_shop(sid, vinted_cookies=None, vinted_status="disconnected")
    db.log_event("vinted", "Compte Vinted déconnecté (token supprimé)", shop_id=sid)
    return redirect(back, ok="Compte déconnecté, token supprimé")


@router.post("/boutique/{sid}/tester")
def test_vinted(sid: int, back: str = Form("/connexions")):
    try:
        user = vinted_client.VintedClient.for_shop(sid).me()
    except Exception as e:
        return redirect(back, err=f"Test échoué : {e}")
    return redirect(back, ok=f"Connexion OK : @{user.get('login')}")


@router.post("/boutique/{sid}/apprendre")
def learn_template(sid: int, item_url: str = Form(...), name: str = Form(""), back: str = Form("/connexions")):
    try:
        publisher.learn_template(sid, item_url, name or None)
    except Exception as e:
        return redirect(back, err=f"Apprentissage : {e}")
    return redirect(back, ok="Modèle d'annonce enregistré")


@router.post("/boutique/{sid}/modele-annonce")
def set_shop_template(sid: int, template_id: str = Form(""), back: str = Form("/connexions")):
    db.update_shop(sid, template_id=int(template_id) if template_id else None)
    return redirect(back, ok="Modèle d'annonce attribué")


@router.post("/boutique/{sid}/modele")
def set_shop_persona(sid: int, persona_id: str = Form(""), back: str = Form("")):
    db.update_shop(sid, persona_id=int(persona_id) if persona_id else None)
    return redirect(back or f"/boutique/{sid}", ok="Modèle photo attribué")


@router.post("/modeles-annonce")
def add_template_manual(name: str = Form(...), catalog_id: int = Form(...), brand_id: str = Form(""),
                        brand: str = Form(""), size_map: str = Form(""), package_size_id: int = Form(2)):
    sizes = {}
    for part in size_map.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if v.strip().isdigit():
                sizes[k.strip().upper()] = int(v)
    db.add_template(name.strip()[:60], catalog_id=catalog_id, brand_id=int(brand_id) if brand_id.isdigit() else None,
                    brand=brand or None, size_map=sizes, size_id=next(iter(sizes.values()), None),
                    package_size_id=package_size_id)
    return redirect("/connexions", ok="Modèle d'annonce créé")


# ---------------- messages ----------------
@router.get("/messages")
def messages_page(request: Request):
    return render(request, "messages.html", convs=db.list_conversations(limit=100))


@router.post("/message/{cid}")
def message_action(cid: int, action: str = Form(...), text: str = Form("")):
    try:
        if action == "send":
            responder.send_pending(cid, text.strip() or None)
            db.log_event("reply", "Réponse validée depuis le dashboard", source="dashboard")
        else:
            db.upsert_conversation(cid, status="ignored")
    except Exception as e:
        return redirect("/messages", err=str(e))
    return redirect("/messages", ok="Réponse envoyée" if action == "send" else "Message ignoré")
