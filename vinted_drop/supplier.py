"""Récupération des infos d'un produit fournisseur (Shein, Temu, AliExpress...).

Les sites chinois bloquent souvent les robots : on essaie de lire les balises
Open Graph (titre, image, prix). Si ça échoue, envoie simplement la photo du
produit au bot Telegram avec le prix d'achat en légende.
"""
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def supplier_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for name in ("shein", "temu", "aliexpress", "amazon", "romwe", "cider"):
        if name in host:
            return name.capitalize()
    return host or "Autre"


def _meta(html: str, prop: str):
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
                  html, re.I) or re.search(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']', html, re.I)
    return m.group(1).strip() if m else None


def fetch_info(url: str) -> dict:
    info = {"supplier": supplier_name(url), "title": None, "image_url": None, "price": None}
    try:
        html = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"},
                            timeout=20).text
    except requests.RequestException:
        return info
    info["title"] = _meta(html, "og:title")
    info["image_url"] = _meta(html, "og:image")
    price = _meta(html, "product:price:amount") or _meta(html, "og:price:amount")
    if price:
        try:
            info["price"] = float(price.replace(",", "."))
        except ValueError:
            pass
    if info["image_url"] and info["image_url"].startswith("//"):
        info["image_url"] = "https:" + info["image_url"]
    return info


def download_image(image_url: str, product_id: int) -> Path | None:
    try:
        r = requests.get(image_url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    ext = ".png" if "png" in r.headers.get("content-type", "") else ".jpg"
    out = config.MEDIA_DIR / f"product_{product_id}" / f"supplier{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    return out
