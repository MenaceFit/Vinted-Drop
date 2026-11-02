"""Connexion à TON compte Vinted avec le token de ta session navigateur.

Vinted n'a pas d'API publique : ce module utilise les mêmes adresses que le site
quand tu es connecté. Elles peuvent changer sans prévenir ; toutes les adresses
sont regroupées dans ENDPOINTS pour être corrigées facilement.

Comment récupérer le token (voir aussi le dashboard → Boutiques → Connexion Vinted) :
  1. Ouvre vinted.fr dans Chrome, connecté au compte de la boutique.
  2. F12 → onglet Réseau (Network) → recharge la page → clique sur la première requête.
  3. Dans "En-têtes de requête", copie toute la valeur de "Cookie" (elle contient
     access_token_web et refresh_token_web) et colle-la dans le dashboard.
"""
import json
import re
import time
import uuid
from datetime import datetime

import requests

from . import db
from .security import decrypt, encrypt

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")

ENDPOINTS = {
    "me": "GET /users/current",
    "wardrobe": "GET /wardrobe/{user_id}/items",
    "user_items": "GET /users/{user_id}/items",
    "sold_orders": "GET /my_orders",
    "transaction": "GET /transactions/{tid}",
    "inbox": "GET /inbox",
    "conversation": "GET /conversations/{cid}",
    "reply": "POST /conversations/{cid}/replies",
    "offer": "POST /transactions/{tid}/offers",
    "accept_offer_request": "POST /transactions/{tid}/offer_requests/{rid}/accept",
    "upload_photo": "POST /photos",
    "create_item": "POST /item_upload/items",
    "get_own_item": "GET /item_upload/items/{item_id}",
    "update_item": "PUT /item_upload/items/{item_id}",
    "search": "GET /catalog/items",
}

DOMAINS = ("vinted.fr", "vinted.be", "vinted.es", "vinted.it", "vinted.de", "vinted.nl", "vinted.co.uk", "vinted.pl",
           "vinted.lu", "vinted.at", "vinted.pt")
MIN_INTERVAL = 2.5  # secondes minimum entre deux requêtes d'un même compte


class VintedError(RuntimeError):
    pass


class VintedAuthError(VintedError):
    """Token expiré ou invalide : il faut recoller le cookie dans le dashboard."""


def parse_cookie_header(raw: str) -> dict:
    raw = raw.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]
    if raw.startswith("{"):
        return json.loads(raw)
    if "=" not in raw:  # l'utilisateur a collé uniquement le access_token_web
        return {"access_token_web": raw}
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k.strip()] = v.strip()
    return out


class VintedClient:
    def __init__(self, shop: dict):
        self.shop = shop
        self.domain = shop.get("vinted_domain") or "vinted.fr"
        self.base = f"https://www.{self.domain}"
        self.api = f"{self.base}/api/v2"
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": shop.get("vinted_user_agent") or DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Referer": self.base + "/",
            "Origin": self.base,
        })
        cookies = decrypt(shop.get("vinted_cookies"))
        if not cookies:
            raise VintedAuthError(f"La boutique {shop.get('name')} n'est pas connectée à Vinted")
        for k, v in json.loads(cookies).items():
            self.s.cookies.set(k, v, domain="." + self.domain)
        self.csrf = None
        self._last = 0.0
        self.user_id = shop.get("vinted_user_id")

    @classmethod
    def for_shop(cls, shop_id: int):
        return cls(db.get_shop(shop_id))

    # ---------- bas niveau ----------
    def _wait(self):
        delta = time.monotonic() - self._last
        if delta < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - delta)
        self._last = time.monotonic()

    def _save_cookies(self):
        if self.shop.get("id"):
            jar = {c.name: c.value for c in self.s.cookies}
            db.update_shop(self.shop["id"], vinted_cookies=encrypt(json.dumps(jar)))

    def refresh_session(self):
        """Recharge la page d'accueil : Vinted renvoie un nouveau access_token si le refresh_token est valide.
        Récupère aussi le jeton CSRF nécessaire pour publier / répondre."""
        self._wait()
        r = self.s.get(self.base + "/", headers={"Accept": "text/html"}, timeout=30)
        m = (re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
             or re.search(r'CSRF_TOKEN\\?"\s*:\s*\\?"([^"\\]+)', r.text))
        if m:
            self.csrf = m.group(1)
        self._save_cookies()
        return r.status_code

    @property
    def simulated(self) -> bool:
        return db.get_setting("simulation") == "on"

    def call(self, name, path_args=None, retry=True, **kw):
        method, path = ENDPOINTS[name].split(" ", 1)
        if method != "GET" and self.simulated:
            # mode simulation : aucune écriture sur le compte réel
            db.log_event("vinted", f"[simulation] {name} non envoyé à Vinted", shop_id=self.shop.get("id"),
                         source="simulation")
            return {"simulated": True, "item": {"id": None}, "id": None}
        url = self.api + path.format(**(path_args or {}))
        headers = kw.pop("headers", {})
        if method != "GET":
            if not self.csrf:
                self.refresh_session()
            if self.csrf:
                headers["X-CSRF-Token"] = self.csrf
        self._wait()
        r = self.s.request(method, url, headers=headers, timeout=60, **kw)
        if r.status_code in (401, 403) and retry:
            self.refresh_session()
            return self.call(name, path_args, retry=False, headers=headers, **kw)
        if r.status_code == 401:
            self._mark("expired")
            raise VintedAuthError("Token Vinted expiré : recolle le cookie dans le dashboard (Boutiques)")
        if r.status_code == 403:
            raise VintedError("Vinted a refusé la requête (403). Ouvre vinted.fr dans ton navigateur, "
                              "puis recolle le cookie. Si ça persiste, Vinted bloque l'accès depuis ce serveur.")
        if r.status_code == 429:
            raise VintedError("Trop de requêtes (429) : Vinted demande de ralentir. Réessai au prochain cycle.")
        if r.status_code >= 400:
            raise VintedError(f"Vinted {r.status_code} sur {name} : {r.text[:300]}")
        self._save_cookies()
        try:
            return r.json()
        except ValueError:
            raise VintedError(f"Réponse inattendue de Vinted sur {name}")

    def _mark(self, status):
        if self.shop.get("id"):
            db.update_shop(self.shop["id"], vinted_status=status,
                           vinted_checked_at=datetime.now().isoformat(timespec="seconds"))

    # ---------- compte ----------
    def me(self) -> dict:
        user = self.call("me").get("user") or {}
        if not user.get("id"):
            raise VintedAuthError("Impossible de lire le compte : cookie invalide")
        self.user_id = user["id"]
        if self.shop.get("id"):
            db.update_shop(self.shop["id"], vinted_user_id=user["id"], vinted_login=user.get("login"),
                           vinted_status="connected", vinted_checked_at=datetime.now().isoformat(timespec="seconds"))
        return user

    def my_items(self, max_pages=10) -> list[dict]:
        if not self.user_id:
            self.me()
        items = []
        for page in range(1, max_pages + 1):
            params = {"page": page, "per_page": 96, "order": "relevance"}
            try:
                data = self.call("wardrobe", {"user_id": self.user_id}, params=params)
            except VintedError:
                data = self.call("user_items", {"user_id": self.user_id}, params=params)
            batch = data.get("items") or []
            items += batch
            total = (data.get("pagination") or {}).get("total_pages", 1)
            if page >= total or not batch:
                break
        return items

    def sold_orders(self, pages=2) -> list[dict]:
        out = []
        for page in range(1, pages + 1):
            data = self.call("sold_orders", params={"type": "sold", "status": "all", "page": page, "per_page": 20})
            batch = data.get("my_orders") or data.get("orders") or []
            out += batch
            if len(batch) < 20:
                break
        return out

    def transaction(self, tid) -> dict:
        return self.call("transaction", {"tid": tid}).get("transaction") or {}

    # ---------- messages ----------
    def inbox(self, per_page=20) -> list[dict]:
        return self.call("inbox", params={"page": 1, "per_page": per_page}).get("conversations") or []

    def conversation(self, cid) -> dict:
        return self.call("conversation", {"cid": cid}).get("conversation") or {}

    def reply(self, cid, text):
        return self.call("reply", {"cid": cid}, json={"reply": {
            "body": text, "photo_temp_uuids": None, "is_personal_data_sharing_check_skipped": False}})

    def counter_offer(self, tid, price: float):
        return self.call("offer", {"tid": tid}, json={"offer": {"price": f"{price:.2f}", "currency": "EUR"}})

    def accept_offer_request(self, tid, request_id):
        return self.call("accept_offer_request", {"tid": tid, "rid": request_id}, json={})

    # ---------- annonces ----------
    def upload_photo(self, path, temp_uuid) -> int:
        with open(path, "rb") as f:
            data = self.call("upload_photo", files={"photo[file]": (path.name, f, "image/png")},
                             data={"photo[type]": "item", "photo[temp_uuid]": temp_uuid})
        return data.get("id") or (data.get("photo") or {}).get("id")

    def create_item(self, fields: dict, photo_paths) -> dict:
        temp = str(uuid.uuid4())
        photo_ids = [self.upload_photo(p, temp) for p in photo_paths]
        item = {
            "id": None, "currency": "EUR", "temp_uuid": temp, "isbn": None, "is_unisex": False,
            "video_game_rating_id": None, "shipment_prices": {"domestic": None, "international": None},
            "measurement_length": None, "measurement_width": None, "manufacturer": None,
            "manufacturer_labelling": None, "item_attributes": [], "color_ids": [],
            "assigned_photos": [{"id": pid, "orientation": 0} for pid in photo_ids if pid],
            **fields,
        }
        data = self.call("create_item", json={"item": item, "feedback_id": None, "push_up": False,
                                               "parcel": None, "upload_session_id": temp})
        return data.get("item") or data

    def get_own_item(self, item_id) -> dict:
        return self.call("get_own_item", {"item_id": item_id}).get("item") or {}

    def update_price(self, item_id, new_price: float):
        item = self.get_own_item(item_id)
        if not item:
            raise VintedError(f"Annonce {item_id} introuvable")
        item["price"] = round(new_price, 2)
        item["assigned_photos"] = [{"id": p["id"], "orientation": p.get("orientation", 0)}
                                   for p in item.get("photos", [])] or item.get("assigned_photos", [])
        return self.call("update_item", {"item_id": item_id}, json={"item": item, "feedback_id": None,
                                                                     "push_up": False, "parcel": None})

    # ---------- marché ----------
    def search(self, text, per_page=96, order="relevance") -> dict:
        return self.call("search", params={"search_text": text, "per_page": per_page, "order": order, "page": 1})


def price_of(obj) -> float | None:
    """Les prix Vinted arrivent sous forme "12.0", 12 ou {"amount": "12.0"}."""
    p = obj.get("price") if isinstance(obj, dict) else obj
    if isinstance(p, dict):
        p = p.get("amount")
    try:
        return float(p)
    except (TypeError, ValueError):
        return None


def connect_shop(shop_id: int, raw_cookie: str, domain="vinted.fr", user_agent=None) -> dict:
    """Enregistre le cookie collé dans le dashboard et vérifie qu'il fonctionne."""
    cookies = parse_cookie_header(raw_cookie)
    if "access_token_web" not in cookies:
        raise VintedAuthError("Le cookie doit contenir access_token_web (copie toute la valeur Cookie)")
    db.update_shop(shop_id, vinted_cookies=encrypt(json.dumps(cookies)), vinted_domain=domain,
                   vinted_user_agent=user_agent or None, vinted_status="checking")
    client = VintedClient.for_shop(shop_id)
    try:
        user = client.me()
    except VintedError:
        db.update_shop(shop_id, vinted_status="error")
        raise
    db.log_event("vinted", f"Compte Vinted @{user.get('login')} connecté", shop_id=shop_id)
    return user
