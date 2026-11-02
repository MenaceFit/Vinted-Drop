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


def _require_password():
    """Refuse d'exposer le dashboard sur le réseau sans mot de passe."""
    if config.DASHBOARD_HOST not in ("127.0.0.1", "localhost") and not config.DASHBOARD_PASSWORD:
        raise SystemExit("❌ DASHBOARD_HOST=" + config.DASHBOARD_HOST + " sans DASHBOARD_PASSWORD : ajoute un mot de passe "
                         "dans .env (le dashboard contient tes tokens Vinted et tes finances).")


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
    s.add_argument("--kinds", default="", help="porte,plat,detail (défaut : selon la niche)")
    s = sub.add_parser("listing"); s.add_argument("id", type=int); s.add_argument("--info", default="")
    s = sub.add_parser("prepare"); s.add_argument("id", type=int)
    s = sub.add_parser("sold"); s.add_argument("id", type=int); s.add_argument("price", type=float)
    s.add_argument("--buyer")
    s = sub.add_parser("price"); s.add_argument("cost", type=float)
    s = sub.add_parser("niches"); s.add_argument("--month", type=int)
    sub.add_parser("stats")
    sub.add_parser("dashboard")
    sub.add_parser("cycle", help="un passage du pilote automatique (synchro, messages, publication)")
    s = sub.add_parser("connect", help="connecte une boutique à Vinted avec le cookie")
    s.add_argument("shop"); s.add_argument("cookie"); s.add_argument("--domain", default="vinted.fr")
    s = sub.add_parser("publish", help="publie un produit sur Vinted"); s.add_argument("id", type=int)
    sub.add_parser("scan", help="analyse du marché Vinted pour toutes les niches")
    sub.add_parser("all", help="lance le dashboard ET le bot (pilote automatique inclus)")
    sub.add_parser("autopilot", help="pilote automatique seul, sans Telegram (boucle chaque minute)")
    s = sub.add_parser("backup", help="sauvegarde la base"); s.add_argument("--restore", help="nom du fichier à restaurer")
    sub.add_parser("check", help="vérifie la configuration (clés, sécurité, base)")
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
        for ph in photos.generate_product_photos(a.id, tuple(a.kinds.split(",")) if a.kinds else None):
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
        _require_password()
        print(f"✅ Dashboard : http://localhost:{config.DASHBOARD_PORT}")
        uvicorn.run("vinted_drop.dashboard.app:app", host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)
    elif a.cmd == "cycle":
        from . import autopilot
        print(json.dumps(autopilot.cycle(), ensure_ascii=False, indent=2, default=str))
    elif a.cmd == "connect":
        from . import vinted_client
        shop = db.find_shop(a.shop)
        if not shop:
            raise SystemExit("Boutique introuvable")
        user = vinted_client.connect_shop(shop["id"], a.cookie, a.domain)
        print(f"Connecté à @{user.get('login')}")
    elif a.cmd == "publish":
        from . import publisher
        print(publisher.publish(a.id))
    elif a.cmd == "scan":
        from . import autopilot
        for n in autopilot.run_market_scan() or []:
            print(n["score"], n["name"])
    elif a.cmd == "autopilot":
        import time as _t
        from . import autopilot, tasks
        tasks.recover()
        print("Pilote automatique lancé (Ctrl+C pour arrêter)")
        while True:
            ran = autopilot.tick()
            if ran:
                print(f"{db.now()} : {', '.join(ran)}")
            _t.sleep(60)
    elif a.cmd == "backup":
        from . import backup
        if a.restore:
            print(f"Restauré. État précédent : {backup.restore(a.restore)}")
        else:
            print(f"Sauvegarde : {backup.backup('manuelle')}")
    elif a.cmd == "check":
        from .image_providers import available
        rows = [("Base", str(config.DB_PATH)), ("Générateurs d'images", ", ".join(available()) or "aucun"),
                ("OpenAI (texte)", "oui" if config.OPENAI_API_KEY else "non"),
                ("Telegram", "oui" if config.TELEGRAM_BOT_TOKEN else "non"),
                ("Discussions autorisées", ", ".join(map(str, sorted(config.telegram_chats()))) or "aucune (bot fermé)"),
                ("Dashboard", f"{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT} · mot de passe {'oui' if config.DASHBOARD_PASSWORD else 'non'}"),
                ("Boutiques", str(len(db.list_shops()))),
                ("Boutiques connectées à Vinted", str(sum(1 for s in db.list_shops() if s.get('vinted_status') == 'connected')))]
        for k, v in rows:
            print(f"  {k:<30} {v}")
        if config.DASHBOARD_HOST != "127.0.0.1" and not config.DASHBOARD_PASSWORD:
            print("  ⚠️  Dashboard ouvert sur le réseau sans DASHBOARD_PASSWORD")
    elif a.cmd == "all":
        import asyncio
        import uvicorn
        _require_password()
        from .telegram_bot import run_with_dashboard
        server = uvicorn.Server(uvicorn.Config("vinted_drop.dashboard.app:app", host=config.DASHBOARD_HOST,
                                               port=config.DASHBOARD_PORT, log_level="warning"))
        url = f"http://localhost:{config.DASHBOARD_PORT}"
        from telegram.error import InvalidToken, NetworkError
        try:
            asyncio.run(run_with_dashboard(server, url, config.DASHBOARD_OPEN_BROWSER))
        except KeyboardInterrupt:
            pass
        except InvalidToken:
            raise SystemExit("❌ Token Telegram refusé : vérifie TELEGRAM_BOT_TOKEN dans .env "
                             "(si tu l'as révoqué chez @BotFather, colle le nouveau).")
        except NetworkError as e:
            raise SystemExit(f"❌ Impossible de joindre Telegram ({e}). Vérifie ta connexion Internet.")
    elif a.cmd == "bot":
        from telegram.error import InvalidToken
        from .telegram_bot import main as bot_main
        try:
            bot_main()
        except InvalidToken:
            raise SystemExit("❌ Token Telegram refusé : vérifie TELEGRAM_BOT_TOKEN dans .env")


if __name__ == "__main__":
    main()
