"""Produits, fiche produit et studio photo."""
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request

from .. import comfyui, config, db, finance, listing, notify, photos, pricing, publisher, studio, supplier, tasks
from ..image_providers import available
from .common import background, redirect, render

router = APIRouter()
PAGE_SIZE = 24


def _float(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(str(v).replace(",", ".").replace("€", "").strip())
    except ValueError:
        raise HTTPException(400, f"Nombre invalide : {v}")


def _import_image(pid, url):
    info = supplier.fetch_info(url) if url else {}
    if info.get("image_url"):
        img = supplier.download_image(info["image_url"], pid)
        if img:
            db.update_product(pid, supplier_image=photos.rel(img))
    if info.get("title"):
        p = db.get_product(pid)
        if not p.get("supplier_title"):
            db.update_product(pid, supplier_title=info["title"])
    return info


# ---------------- liste ----------------
@router.get("/produits")
def products(request: Request, statut: str | None = None, boutique: int | None = None, q: str | None = None,
             tri: str = "recent", page: int = 1, niche: str | None = None, vue: str = "grille"):
    page = max(1, page)
    total = db.count_products(status=statut or None, shop_id=boutique, q=q, niche=niche)
    items = db.list_products(status=statut or None, shop_id=boutique, q=q, sort=tri, niche=niche,
                             limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    running = {t["product_id"] for t in tasks.active() if t.get("product_id")}
    return render(request, "products.html", products=items, shops=db.list_shops(), statut=statut, boutique=boutique,
                  q=q or "", tri=tri, page=page, pages=max(1, -(-total // PAGE_SIZE)), total=total, niche=niche,
                  statuses=db.PRODUCT_STATUSES, running=running, vue=vue, niches=niches_list())


def niches_list():
    from .. import niches
    return niches.load()


@router.post("/produits")
def add_product(url: str = Form(""), cost: str = Form(...), shop_id: str = Form(""), size: str = Form(""),
                supplier_fees: str = Form(""), shipping_cost: str = Form("")):
    shop = db.get_shop(int(shop_id)) if shop_id else None
    pid = db.add_product(cost=_float(cost, 0), supplier_url=url or None,
                         supplier=supplier.supplier_name(url) if url else None,
                         shop_id=shop["id"] if shop else None, niche=shop["niche"] if shop else None,
                         size=size or None, supplier_fees=_float(supplier_fees, 0), shipping_cost=_float(shipping_cost, 0))
    if url:
        background("import", _import_image, pid, url, label="Import de l'image fournisseur", product_id=pid)
    db.log_event("import", f"Produit #{pid} ajouté", product_id=pid, shop_id=shop and shop["id"])
    return redirect(f"/produit/{pid}", ok="Produit ajouté")


@router.post("/produits/importer-gagnant")
def import_winner(url: str = Form(...), cost: str = Form(...), title: str = Form(""), niche: str = Form(""),
                  shop_id: str = Form("")):
    shop = db.get_shop(int(shop_id)) if shop_id else None
    pid = db.add_product(cost=_float(cost, 0), supplier_url=url, supplier=supplier.supplier_name(url),
                         supplier_title=title or None, niche=niche or (shop and shop["niche"]),
                         shop_id=shop["id"] if shop else None)
    background("import", _import_image, pid, url, label="Import de l'image fournisseur", product_id=pid)
    return redirect(f"/produit/{pid}", ok="Produit importé")


@router.post("/produits/lot")
async def bulk(request: Request):
    """Actions groupées : préparer, photos, fiche, archiver, attribuer une boutique."""
    form = await request.form()
    ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()][:100]
    action = form.get("action")
    if not ids:
        return redirect(request.headers.get("referer") or "/produits", err="Aucun produit sélectionné")
    n = 0
    for pid in ids:
        if action == "preparer":
            n += background("prepare", listing.prepare, pid, label=f"Préparation #{pid}", product_id=pid)[1]
        elif action == "photos":
            n += background("photos", photos.generate_product_photos, pid, label=f"Photos #{pid}", product_id=pid)[1]
        elif action == "fiche":
            n += background("listing", listing.generate_listing, pid, label=f"Fiche #{pid}", product_id=pid)[1]
        elif action == "archiver":
            db.update_product(pid, status="archived")
            n += 1
        elif action == "boutique" and form.get("shop_id"):
            shop = db.get_shop(int(form["shop_id"]))
            db.update_product(pid, shop_id=shop["id"], niche=shop.get("niche"))
            n += 1
    return redirect(request.headers.get("referer") or "/produits", ok=f"{n} produit(s) traités ({action})")


# ---------------- fiche produit ----------------
@router.get("/produit/{pid}")
def product(request: Request, pid: int):
    p = db.get_product(pid)
    if not p:
        raise HTTPException(404, "Produit introuvable")
    try:
        rec = pricing.recommend_for(pid)
    except Exception:
        rec = pricing.compute_prices(p["cost"] or 0)
    running = bool(tasks.active(product_id=pid))
    try:
        depop = listing.depop_listing(pid)
    except Exception:
        depop = None
    return render(request, "product.html", p=p, shops=db.list_shops(), rec=rec, sheet=finance.product_sheet(pid),
                  depop=depop, platforms=config.PLATFORMS,
                  running=running, statuses=db.PRODUCT_STATUSES, shots=photos.SHOTS, default_kinds=photos.default_kinds(p),
                  gallery=photos.summary(p), providers=available(), photo_cost=db.product_cost(pid),
                  settings=db.all_settings(), qa_min=config.PHOTO_QA_MIN, scenes=photos.SCENE_LABELS,
                  poses=photos.POSE_LABELS, generations=db.list_generations(pid, 30),
                  task_list=tasks.list_tasks(8, product_id=pid), styles=listing.STYLES,
                  sale=_sale_for(pid))


def _sale_for(pid):
    with db.connect() as c:
        r = c.execute("SELECT id FROM sales WHERE product_id=?", (pid,)).fetchone()
    return db.get_sale(r["id"]) if r else None


@router.post("/produit/{pid}/image")
async def upload_image(pid: int, request: Request):
    form = await request.form()
    f = form.get("image")
    if not f or not getattr(f, "filename", ""):
        return redirect(f"/produit/{pid}", err="Aucune image envoyée")
    ext = Path(f.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return redirect(f"/produit/{pid}", err="Format accepté : JPG, PNG, WEBP")
    data = await f.read()
    if len(data) > 15 * 1024 * 1024:
        return redirect(f"/produit/{pid}", err="Image trop lourde (15 Mo max)")
    out = photos.product_dir(pid) / f"supplier{ext}"
    out.write_bytes(data)
    db.update_product(pid, supplier_image=photos.rel(out), garment={})  # nouvelle image → nouvelle analyse
    return redirect(f"/produit/{pid}", ok="Image enregistrée")


@router.post("/produit/{pid}/modifier")
def edit_product(pid: int, title: str = Form(""), description: str = Form(""), price: str = Form(""),
                 min_price: str = Form(""), cost: str = Form(""), size: str = Form(""), shop_id: str = Form(""),
                 status_: str = Form("", alias="status"), vinted_url: str = Form(""), supplier_fees: str = Form(""),
                 shipping_cost: str = Form(""), other_fees: str = Form(""), brand: str = Form(""),
                 supplier_url: str = Form("")):
    if not db.get_product(pid):
        raise HTTPException(404)
    fields = {"title": title.strip()[:100] or None, "description": description or None, "size": size or None,
              "vinted_url": vinted_url or None, "shop_id": int(shop_id) if shop_id else None,
              "brand": brand or None, "supplier_url": supplier_url or None}
    for k, v in (("price", price), ("min_price", min_price), ("cost", cost), ("supplier_fees", supplier_fees),
                 ("shipping_cost", shipping_cost), ("other_fees", other_fees)):
        if v != "":
            fields[k] = _float(v)
    if "cost" in fields:
        fields["cost_unknown"] = 0
    if fields["shop_id"]:
        fields["niche"] = db.get_shop(fields["shop_id"])["niche"]
    if status_ in db.PRODUCT_STATUSES:
        fields["status"] = status_
    db.update_product(pid, **fields)
    return redirect(f"/produit/{pid}", ok="Fiche enregistrée")


@router.post("/produit/{pid}/prix-conseille")
def apply_recommended(pid: int):
    rec = pricing.recommend_for(pid)
    db.update_product(pid, price=rec["price"], min_price=rec["min_price"],
                      price_info={k: rec[k] for k in ("floor", "total_cost", "profit_at_price", "profit_at_min",
                                                      "margin_pct", "confidence", "confidence_label", "reasons",
                                                      "warnings", "min_profit")})
    return redirect(f"/produit/{pid}", ok=f"Prix appliqué : {rec['price']:.2f} € (mini {rec['min_price']:.2f} €)")


@router.post("/produit/{pid}/action/{action}")
def product_action(pid: int, action: str, sale_price: str = Form(""), buyer: str = Form(""),
                   platform: str = Form("vinted"),
                   style: str = Form("")):
    p = db.get_product(pid)
    if not p:
        raise HTTPException(404)
    if action == "photos":
        background("photos", photos.generate_product_photos, pid, label=f"Photos #{pid}", product_id=pid)
        return redirect(f"/produit/{pid}", ok="Génération des photos lancée")
    if action == "fiche":
        background("listing", listing.generate_listing, pid, "", style or None, label=f"Fiche #{pid}", product_id=pid)
        return redirect(f"/produit/{pid}", ok="Rédaction de la fiche lancée")
    if action == "preparer":
        def job():
            res = listing.prepare(pid)
            q = db.get_product(pid)
            notify.send(f"✅ Produit #{pid} prêt à publier : {q.get('title')}", category="photos",
                        buttons=[[("👀 Voir", f"view:{pid}"), ("✅ Publier", f"pub:{pid}")]])
            return res
        background("prepare", job, label=f"Préparation #{pid}", product_id=pid)
        return redirect(f"/produit/{pid}", ok="Préparation lancée (photos + fiche)")
    if action == "publie":
        db.update_product(pid, status="published")
        return redirect(f"/produit/{pid}", ok="Marqué en ligne")
    if action == "publier-vinted":
        try:
            res = publisher.publish(pid)
        except Exception as e:
            return redirect(f"/produit/{pid}", err=str(e))
        return redirect(f"/produit/{pid}", ok="Simulation : rien n'a été envoyé à Vinted" if res.get("simulated")
                        else "Publié sur Vinted")
    if action == "vendu":
        price = _float(sale_price) or p.get("price")
        if not price:
            return redirect(f"/produit/{pid}", err="Prix de vente manquant")
        try:
            sid = db.record_sale(pid, price, buyer or None, platform=platform if platform in config.PLATFORMS else "vinted")
        except (db.AlreadySold, ValueError) as e:
            return redirect(f"/produit/{pid}", err=str(e))
        notify.notify_sale(db.get_sale(sid))
        return redirect(f"/produit/{pid}", ok="Vente enregistrée : commande fournisseur créée")
    if action == "archiver":
        db.update_product(pid, status="archived")
        return redirect(f"/produit/{pid}", ok="Produit archivé")
    raise HTTPException(400, "Action inconnue")


# ---------------- studio photo ----------------
@router.post("/produit/{pid}/studio")
async def studio_generate(pid: int, request: Request):
    form = await request.form()
    kinds = [k for k in form.getlist("kinds") if k in photos.SHOTS] or None
    provider = form.get("provider") or None
    scene = form.get("scene") if form.get("scene") in photos.SCENES else "auto"
    pose = form.get("pose") if form.get("pose") in photos.POSES else "auto"
    _, created = background("photos", photos.generate_product_photos, pid, kinds, provider, scene, pose,
                            label=f"Photos #{pid}", product_id=pid)
    return redirect(f"/produit/{pid}#photos", ok="Génération lancée" if created else "Une génération est déjà en cours")


@router.post("/produit/{pid}/photo")
def photo_action(pid: int, path: str = Form(...), action: str = Form(...), provider: str = Form("")):
    p = db.get_product(pid)
    if not p or path not in (p.get("photos") or []):
        return redirect(f"/produit/{pid}#photos", err="Photo introuvable")
    if action == "cover":
        photos.set_cover(pid, path)
    elif action == "delete":
        photos.delete_photo(pid, path)
    elif action == "redo":
        background("photos", photos.regenerate_photo, pid, path, provider or None, label=f"Nouvel essai photo #{pid}",
                   product_id=pid, dedupe_key=f"redo:{pid}:{path}")
    return redirect(f"/produit/{pid}#photos")


@router.get("/studio")
def studio_page(request: Request):
    todo = [p for p in db.list_products(limit=300) if p["status"] in ("draft", "ready") and p.get("supplier_image")]
    try:
        budget = float(db.get_setting("daily_budget") or 0)
    except ValueError:
        budget = 0
    running = {t["product_id"] for t in tasks.active() if t.get("product_id")}
    return render(request, "studio.html", todo=todo, settings=db.all_settings(), costs=db.cost_stats(30),
                  local=comfyui.status(timeout=2), today_cost=db.cost_today(), budget=budget,
                  personas=db.list_personas(), providers=available(), sets=photos.SETS, shots=photos.SHOTS,
                  running=running, qa_min=config.PHOTO_QA_MIN, gen_stats=db.generation_stats(30),
                  history=db.list_generations(limit=30), scenes=photos.SCENE_LABELS, studio_bgs=studio.BACKGROUNDS,
                  models={"gemini": config.GEMINI_IMAGE_MODEL, "openai": config.OPENAI_IMAGE_MODEL,
                          "local": comfyui.settings()["unet"].split(".")[0]})


@router.post("/studio/reglages")
async def studio_settings(request: Request):
    form = await request.form()
    for k in ("photo_provider", "photo_set", "photo_clean", "photo_qa", "daily_budget", "budget_alert_pct",
              "photo_scene", "photo_disclosure", "studio_bg"):
        if k in form:
            db.set_setting(k, form[k])
    db.log_event("settings", "Réglages photo modifiés")
    return redirect("/studio", ok="Réglages photo enregistrés")


@router.post("/studio/lot")
async def studio_batch(request: Request):
    form = await request.form()
    ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()][:30]
    n = sum(background("photos", photos.generate_product_photos, pid, label=f"Photos #{pid}", product_id=pid)[1]
            for pid in ids)
    return redirect("/studio", ok=f"{n} génération(s) lancée(s)")


@router.post("/studio/test-local")
def studio_test_local():
    st = comfyui.status(timeout=5)
    if st["ok"]:
        return redirect("/studio", ok=f"ComfyUI connecté : {st['gpu']} ({st['vram_gb']} Go)")
    return redirect("/studio", err=st["error"])


@router.post("/modeles")
def add_persona(name: str = Form(...), description: str = Form(...), decor: str = Form(""), provider: str = Form(""),
                back: str = Form("/studio")):
    background("persona", photos.create_persona, name.strip()[:60], description.strip()[:600], decor or None,
               provider or None, label=f"Modèle photo « {name} »", dedupe_key=f"persona:{name}")
    return redirect(back if back.startswith("/") else "/studio", ok="Création du modèle lancée (≈ 1 min)")


@router.get("/api/produit/{pid}/prix")
def api_price(pid: int):
    return pricing.recommend_for(pid)

