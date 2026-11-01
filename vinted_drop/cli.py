"""Ligne de commande.

  python -m vinted_drop.cli init
  python -m vinted_drop.cli persona "Sophie" "a 25 year old woman, pale skin, long black hair..."
  python -m vinted_drop.cli shop "Sophie" --niche goth-classique --persona 1
  python -m vinted_drop.cli add <lien> <prix_achat> --shop Sophie --size M
  python -m vinted_drop.cli prepare <id>         # photos + fiche
  python -m vinted_drop.cli sold <id> <prix>
  python -m vinted_drop.cli niches
  python -m vinted_drop.cli price 14.90
  python -m vinted_drop.cli dashboard
  python -m vinted_drop.cli bot
"""
import argparse
import json

from . import config, db, listing, niches, notify, photos, pricing, supplier


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vinted_drop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    s = sub.add_parser("persona"); s.add_argument("name"); s.add_argument("description")
    s = sub.add_parser("shop"); s.add_argument("name"); s.add_argument("--niche"); s.add_argument("--persona", type=int)
    s.add_argument("--url")
    s = sub.add_parser("add"); s.add_argument("url"); s.add_argument("cost", type=float)
    s.add_argument("--shop"); s.add_argument("--size"); s.add_argument("--image", help="image locale du produit")
    s = sub.add_parser("photos"); s.add_argument("id", type=int)
    s.add_argument("--kinds", default="porte,plat,detail")
    s = sub.add_parser("listing"); s.add_argument("id", type=int); s.add_argument("--info", default="")
    s = sub.add_parser("prepare"); s.add_argument("id", type=int)
    s = sub.add_parser("sold"); s.add_argument("id", type=int); s.add_argument("price", type=float)
    s.add_argument("--buyer")
    s = sub.add_parser("price"); s.add_argument("cost", type=float)
    s = sub.add_parser("niches"); s.add_argument("--month", type=int)
    sub.add_parser("stats")
    sub.add_parser("dashboard")
    sub.add_parser("bot")
    a = ap.parse_args(argv)
    db.init()

    if a.cmd == "init":
        print(f"Base prête : {config.DB_PATH}")
    elif a.cmd == "persona":
        p = photos.create_persona(a.name, a.description)
        print(f"Modèle #{p['id']} créé : {config.MEDIA_DIR / p['reference_image']}")
    elif a.cmd == "shop":
        print(f"Boutique #{db.add_shop(a.name, a.niche, a.persona, a.url)} créée")
    elif a.cmd == "add":
        shop = db.find_shop(a.shop) if a.shop else None
        info = supplier.fetch_info(a.url)
        pid = db.add_product(cost=a.cost, supplier_url=a.url, supplier=info["supplier"],
                             supplier_title=info["title"], shop_id=shop and shop["id"],
                             niche=shop and shop["niche"], size=a.size)
        img = None
        if a.image:
            img = photos.product_dir(pid) / ("supplier" + (a.image[a.image.rfind("."):] or ".jpg"))
            img.write_bytes(open(a.image, "rb").read())
        elif info["image_url"]:
            img = supplier.download_image(info["image_url"], pid)
        if img:
            db.update_product(pid, supplier_image=photos.rel(img))
        print(f"Produit #{pid} ajouté" + ("" if img else " (sans image : utilise --image)"))
    elif a.cmd == "photos":
        for ph in photos.generate_product_photos(a.id, tuple(a.kinds.split(","))):
            print(config.MEDIA_DIR / ph)
    elif a.cmd == "listing":
        print(json.dumps(listing.generate_listing(a.id, a.info), ensure_ascii=False, indent=2))
    elif a.cmd == "prepare":
        print(json.dumps(listing.prepare(a.id), ensure_ascii=False, indent=2))
    elif a.cmd == "sold":
        sid = db.record_sale(a.id, a.price, a.buyer)
        sale = db.get_sale(sid)
        notify.send(notify.sale_message(sale))
        print(f"Vente #{sid} : bénéfice {sale['profit']:.2f} € — commander : {sale.get('supplier_url')}")
    elif a.cmd == "price":
        print(json.dumps(pricing.compute_prices(a.cost), indent=2))
    elif a.cmd == "niches":
        for n in niches.ranked(a.month):
            print(f"{'🔥' if n['in_season'] else '⏳'} {n['score']:>4}/10  {n['name']}  "
                  f"(achat {n['buy_price']} → vente {n['sell_price']})")
    elif a.cmd == "stats":
        print(json.dumps({"aujourdhui": db.today_stats(), "7j": db.stats(7), "30j": db.stats(30),
                          "total": db.stats()}, ensure_ascii=False, indent=2))
    elif a.cmd == "dashboard":
        import uvicorn
        uvicorn.run("vinted_drop.dashboard.app:app", host="0.0.0.0", port=config.DASHBOARD_PORT)
    elif a.cmd == "bot":
        from .telegram_bot import main as bot_main
        bot_main()


if __name__ == "__main__":
    main()
