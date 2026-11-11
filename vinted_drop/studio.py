"""Studio instantané : photos propres en quelques secondes, gratuites, sans carte graphique ni IA générative.

À partir de la vraie photo du fournisseur :
- détourage du vêtement (rembg si installé, sur le processeur ; sinon détourage des fonds unis) ;
- fond studio propre (gris, blanc, beige, rose, sauge ou noir), ombre douce, cadrage 3:4 centré ;
- photo « détail » : zoom net sur le tissu ;
- retouche légère (contraste, netteté).

Comme c'est la photo réelle du produit, aucune fidélité à contrôler : pas de risque de vêtement inventé.
Limite : pas de photo portée (il faut ComfyUI, Hugging Face ou une clé IA pour ça).
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

SIZE = (1200, 1600)
BACKGROUNDS = {
    "gris": ((238, 238, 236), (220, 220, 217), "Gris clair"),
    "blanc": ((250, 249, 246), (238, 236, 232), "Blanc cassé"),
    "beige": ((241, 233, 221), (226, 214, 198), "Beige lin"),
    "rose": ((246, 233, 232), (232, 213, 212), "Rose poudré"),
    "sauge": ((228, 234, 226), (208, 218, 205), "Vert sauge"),
    "noir": ((34, 33, 36), (14, 14, 16), "Noir (goth, soirée)"),
}
KINDS = {"plat", "cintre", "detail"}       # prises possibles sans IA (pas de photo portée)
REMBG_MODEL = "isnet-general-use"          # licence Apache 2.0, ~170 Mo téléchargés au premier usage


class StudioError(RuntimeError):
    pass


# ---------------- détourage ----------------
def rembg_available() -> bool:
    return importlib.util.find_spec("rembg") is not None


_session = None


def _rembg_mask(img: Image.Image) -> Image.Image | None:
    global _session
    try:
        from rembg import new_session, remove
        if _session is None:
            _session = new_session(REMBG_MODEL)
        return remove(img, session=_session, only_mask=True).convert("L")
    except Exception:
        return None


def _flood_mask(img: Image.Image, thresh=26) -> Image.Image | None:
    """Masque du vêtement quand le fond est uni : on « remplit » le fond depuis les bords."""
    small = img.convert("RGB").copy()
    small.thumbnail((480, 480))
    w, h = small.size
    marker = (255, 0, 254)
    work = small.copy()
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (0, h // 2), (w - 1, h // 2),
             (w // 2, h - 1)]
    for s in seeds:
        if work.getpixel(s) != marker:
            ImageDraw.floodfill(work, s, marker, thresh=thresh)
    bg = Image.new("RGB", work.size, marker)
    diff = ImageChops.difference(work, bg).convert("L")
    mask = diff.point(lambda v: 255 if v > 8 else 0)          # 255 = vêtement
    hist = mask.histogram()
    fg_ratio = hist[255] / (w * h)
    if not 0.08 <= fg_ratio <= 0.92:                           # fond non uni ou vêtement introuvable
        return None
    mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))   # bouche les petits trous
    mask = mask.resize(img.size, Image.LANCZOS).point(lambda v: 255 if v > 127 else 0)
    # resserre de 2 px : supprime le liseré de l'ancien fond, puis adoucit le bord
    return mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(1.2))


def cutout(img: Image.Image, method="auto") -> tuple[Image.Image | None, str]:
    """Renvoie (masque, méthode) ; masque None si le détourage n'est pas fiable."""
    if method in ("auto", "ia") and rembg_available():
        m = _rembg_mask(img)
        if m is not None and m.getbbox():
            return m, "ia"
    if method in ("auto", "basique"):
        m = _flood_mask(img)
        if m is not None:
            return m, "basique"
    return None, "aucun"


# ---------------- composition ----------------
def _background(key: str) -> Image.Image:
    top, bottom, _ = BACKGROUNDS.get(key, BACKGROUNDS["gris"])
    grad = Image.linear_gradient("L").resize(SIZE)
    a, b = Image.new("RGB", SIZE, top), Image.new("RGB", SIZE, bottom)
    bg = Image.composite(b, a, grad)
    # léger vignettage pour un rendu « studio »
    vign = Image.radial_gradient("L").resize(SIZE).point(lambda v: int(v * 0.10))
    return Image.composite(Image.new("RGB", SIZE, bottom), bg, vign)


def _enhance(img: Image.Image) -> Image.Image:
    """Retouche légère qui ne change pas la couleur du vêtement (fidélité avant tout)."""
    img = ImageEnhance.Contrast(img).enhance(1.03)
    return ImageEnhance.Sharpness(img).enhance(1.2)


def packshot(src: Path, out: Path, background="gris", method="auto", fill=0.82) -> dict:
    """Vêtement détouré, centré sur un fond studio avec ombre douce. Sans détourage fiable : photo
    d'origine recadrée proprement sur le fond."""
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    mask, how = cutout(img, method)
    canvas = _background(background)
    if mask is not None:
        box = mask.point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, *img.size)
        item, m = img.crop(box), mask.crop(box)
    else:
        item, m = img, Image.new("L", img.size, 255)
    scale = min(SIZE[0] * fill / item.width, SIZE[1] * fill / item.height)
    nw, nh = max(1, int(item.width * scale)), max(1, int(item.height * scale))
    item, m = _enhance(item.resize((nw, nh), Image.LANCZOS)), m.resize((nw, nh), Image.LANCZOS)
    x, y = (SIZE[0] - nw) // 2, (SIZE[1] - nh) // 2
    if mask is not None:                                       # ombre douce sous le vêtement
        dark = background == "noir"
        shadow = m.point(lambda v: int(v * (0.55 if dark else 0.30))).filter(ImageFilter.GaussianBlur(22))
        canvas.paste(Image.new("RGB", (nw, nh), (0, 0, 0)), (x + 14, y + 22), shadow)
    else:                                                      # photo entière : coins arrondis + ombre
        r = Image.new("L", (nw, nh), 0)
        ImageDraw.Draw(r).rounded_rectangle((0, 0, nw - 1, nh - 1), radius=28, fill=255)
        m = r
        canvas.paste(Image.new("RGB", (nw, nh), (0, 0, 0)), (x + 10, y + 16),
                     r.point(lambda v: int(v * 0.25)).filter(ImageFilter.GaussianBlur(18)))
    canvas.paste(item, (x, y), m)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "JPEG", quality=92, optimize=True)
    return {"path": out, "cutout": how}


def detail(src: Path, out: Path, zoom=0.42) -> dict:
    """Gros plan net sur le centre du vêtement (tissu, coutures, motif)."""
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    mask, how = cutout(img)
    box = (mask.point(lambda v: 255 if v > 40 else 0).getbbox() if mask is not None else None) or (0, 0, *img.size)
    bw, bh = box[2] - box[0], box[3] - box[1]
    cw = max(64, int(bw * zoom))
    ch = int(cw * SIZE[1] / SIZE[0])
    cx, cy = box[0] + bw // 2, box[1] + int(bh * 0.42)        # un peu au-dessus du centre : col, boutons
    left = min(max(0, cx - cw // 2), max(0, img.width - cw))
    top = min(max(0, cy - ch // 2), max(0, img.height - ch))
    crop = img.crop((left, top, min(img.width, left + cw), min(img.height, top + ch)))
    crop = ImageOps.fit(crop, SIZE, Image.LANCZOS)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out, "JPEG", quality=92, optimize=True)
    return {"path": out, "cutout": how}


def render(kind: str, src: Path, out: Path, background="gris") -> dict:
    if kind not in KINDS:
        raise StudioError("Le studio instantané ne fait pas de photo portée : il faut ComfyUI, Hugging Face "
                          "ou une clé IA pour ça.")
    if kind == "detail":
        return detail(src, out)
    if kind == "cintre":                                        # 2e fond pour varier les photos
        alt = "blanc" if background != "blanc" else "beige"
        return packshot(src, out, alt, fill=0.86)
    return packshot(src, out, background)


def background_for(niche: dict | None, setting: str | None) -> str:
    """Fond choisi, ou automatique : noir pour les niches alternatives (goth…), gris clair sinon."""
    if setting and setting in BACKGROUNDS:
        return setting
    return "noir" if (niche or {}).get("alt") and any(w in (niche or {}).get("id", "")
                                                       for w in ("goth", "corset", "whimsigoth", "napoleon")) else "gris"


def install_rembg() -> str:
    """Installe le détourage IA (rembg, processeur) dans l'environnement du bot."""
    res = subprocess.run([sys.executable, "-m", "pip", "install", "rembg[cpu]>=2.0.60"],
                         capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        raise StudioError("Installation impossible : " + (res.stderr or res.stdout)[-300:])
    importlib.invalidate_caches()
    return "Détourage IA installé. Le modèle (~170 Mo) se télécharge à la première photo."
