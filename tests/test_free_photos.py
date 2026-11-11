"""2.4 : studio instantané (sans IA), Hugging Face gratuit, mode 100 % gratuit sans aucun fournisseur payant."""
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw


def garment(path, bg=(250, 250, 250), color=(120, 20, 40)):
    img = Image.new("RGB", (600, 800), bg)
    d = ImageDraw.Draw(img)
    d.polygon([(180, 140), (260, 110), (300, 150), (340, 110), (420, 140), (500, 260), (440, 300), (410, 250),
               (410, 690), (190, 690), (190, 250), (160, 300), (100, 260)], fill=color)
    img.save(path)
    return path


@pytest.fixture
def product(tmp_path, monkeypatch):
    from vinted_drop import config, db
    db.init()
    monkeypatch.setattr(config, "COMFYUI_URL", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    sid = db.add_shop("Alt", "corset-goth")
    garment(config.MEDIA_DIR / "sup.png")
    pid = db.add_product(cost=15, shop_id=sid, niche="corset-goth", supplier_image="sup.png")
    return pid


def test_studio_packshot_keeps_garment_colour(tmp_path):
    from vinted_drop import studio
    src = garment(tmp_path / "g.png")
    res = studio.packshot(src, tmp_path / "out.jpg", "gris")
    out = Image.open(res["path"])
    assert res["cutout"] in ("basique", "ia") and out.size == studio.SIZE
    r, g, b = out.getpixel((600, 900))
    assert abs(r - 120) < 12 and abs(g - 20) < 12 and abs(b - 40) < 12        # couleur fidèle
    assert out.getpixel((40, 40))[0] > 200                                     # fond studio clair
    noir = Image.open(studio.packshot(src, tmp_path / "n.jpg", "noir")["path"])
    assert noir.getpixel((40, 40))[0] < 60


def test_studio_busy_background_falls_back_to_framed_photo(tmp_path):
    import random
    from vinted_drop import studio
    img = Image.new("RGB", (400, 500))
    px = img.load()
    rnd = random.Random(1)
    for x in range(400):
        for y in range(500):
            px[x, y] = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
    img.save(tmp_path / "busy.png")
    res = studio.packshot(tmp_path / "busy.png", tmp_path / "o.jpg", method="basique")
    assert res["cutout"] == "aucun" and Image.open(res["path"]).size == studio.SIZE


def test_studio_detail_and_refuses_worn(tmp_path):
    from vinted_drop import studio
    src = garment(tmp_path / "g.png")
    assert Image.open(studio.render("detail", src, tmp_path / "d.jpg")["path"]).size == studio.SIZE
    with pytest.raises(studio.StudioError):
        studio.render("porte", src, tmp_path / "p.jpg")


def test_background_auto_for_goth_niche():
    from vinted_drop import niches, studio
    from vinted_drop import db
    db.init()
    assert studio.background_for(niches.get("corset-goth"), "auto") == "noir"
    assert studio.background_for(niches.get("trench"), "auto") == "gris"
    assert studio.background_for(None, "beige") == "beige"


def test_studio_mode_generates_without_any_ai(product, monkeypatch):
    from vinted_drop import ai, db, image_providers, photos
    def boom(*a, **k):
        raise AssertionError("aucune IA ne doit être appelée en studio instantané")
    monkeypatch.setattr(image_providers, "gemini_generate", boom)
    monkeypatch.setattr(ai, "chat_json", boom, raising=False)
    monkeypatch.setattr(photos, "analyze_garment", boom)
    db.set_setting("photo_provider", "studio")
    out = photos.generate_product_photos(product)              # jeu standard : portée retirée
    p = db.get_product(product)
    kinds = sorted(m["kind"] for m in p["photo_meta"].values())
    assert kinds == ["cintre", "detail", "plat"] and len(out) == 3
    assert all(m["provider"] == "studio" and m["cost"] == 0 for m in p["photo_meta"].values())
    assert db.cost_today() == 0


def test_free_mode_never_calls_paid_and_falls_back_to_studio(product, monkeypatch):
    from vinted_drop import comfy_manager as cm, comfyui, db, image_providers, photos
    paid = []
    monkeypatch.setattr(image_providers, "gemini_generate", lambda *a, **k: paid.append(1))
    db.set_setting("comfy_url", "http://pc:8188")
    def off(*a, **k):
        raise comfyui.ComfyError("PC éteint")
    monkeypatch.setattr(comfyui, "generate", off)
    monkeypatch.setattr(photos, "analyze_garment", lambda p: {})
    cm.free_mode()
    db.set_setting("daily_budget", "0")                         # 0 = illimité pour les payants : sans effet ici
    assert image_providers.order("gratuit") == ["local"]
    photos.generate_product_photos(product, kinds=["plat", "detail"])
    meta = db.get_product(product)["photo_meta"]
    assert not paid and {m["provider"] for m in meta.values()} == {"studio"}


def test_explicit_free_provider_has_no_paid_fallback(product, monkeypatch):
    from vinted_drop import db, image_providers
    db.set_setting("comfy_url", "http://pc:8188")
    assert image_providers.order("local") == ["local"]
    assert "gemini" in image_providers.order("auto")
    with pytest.raises(image_providers.ImageError):
        image_providers.order("hf")                             # pas activé


def test_old_free_mode_is_repaired_on_start():
    from vinted_drop import db
    db.init()
    db.set_setting("photo_provider", "local")
    db.set_setting("daily_budget", "0")
    db.init()
    assert db.get_setting("photo_provider") == "gratuit"
    db.set_setting("photo_provider", "local")
    db.set_setting("daily_budget", "2")
    db.init()
    assert db.get_setting("photo_provider") == "local"


class FakeJob:
    def __init__(self, result=None, error=None):
        self._r, self._e = result, error
    def result(self, timeout=None):
        if self._e:
            raise self._e
        return self._r


def test_hf_space_generate_and_quota(tmp_path, monkeypatch):
    from vinted_drop import db, hf_space, image_providers
    db.init()
    out_img = tmp_path / "res.png"
    Image.new("RGB", (10, 10), (1, 2, 3)).save(out_img)
    calls = []

    class FakeClient:
        def submit(self, *args, api_name=None):
            calls.append((args, api_name))
            return FakeJob(([{"image": str(out_img), "caption": None}], 123))
    monkeypatch.setattr(hf_space, "_client", lambda: FakeClient())
    hf_space.save(token_value="hf_abcdefghijklmnopqrstuvwxyz", enabled_value=True, steps_value=20)
    assert hf_space.token() == "hf_abcdefghijklmnopqrstuvwxyz"
    assert db.get_setting("hf_token") != hf_space.token()           # chiffré en base
    assert image_providers.available()[0] == "hf"
    ref = garment(tmp_path / "g.png")
    assert hf_space.generate("put it flat", [ref]) == out_img.read_bytes()
    args, api = calls[0]
    assert api == "/infer" and args[1] == "put it flat" and args[5] == 20 and args[8] is False   # pas de réécriture
    assert args[6:8] == (1184, 896)
    # quota épuisé : pause, et le bot passe au suivant
    monkeypatch.setattr(hf_space, "_client", lambda: type("C", (), {"submit": lambda self, *a, **k: FakeJob(
        error=RuntimeError("You have exceeded your free GPU quota (60s requested vs. 12s left)"))})())
    with pytest.raises(hf_space.HFError, match="Quota"):
        hf_space.generate("x", [ref])
    assert hf_space.quota_until()
    with pytest.raises(hf_space.HFError, match="épuisé"):
        hf_space.generate("x", [ref])
    with pytest.raises(hf_space.HFError):
        hf_space.save(token_value="pas-un-jeton")


def test_hf_result_shapes(tmp_path):
    from vinted_drop import hf_space
    f = tmp_path / "a.webp"
    f.write_bytes(b"x")
    assert hf_space._first_image(([str(f)], 1)) == f
    assert hf_space._first_image(([{"image": {"path": str(f)}}], 1)) == f
    with pytest.raises(hf_space.HFError):
        hf_space._first_image(([], 1))


def test_free_photos_page_and_actions(product):
    from vinted_drop import db
    from vinted_drop.dashboard import app as app_mod
    with TestClient(app_mod.app) as c:
        h = {"origin": "http://testserver"}
        r = c.get("/photos-gratuites")
        assert r.status_code == 200 and "Studio instantané" in r.text and "Hugging Face" in r.text
        r = c.post("/photos-gratuites/studio-test", headers=h, follow_redirects=False)
        assert "ok=" in r.headers["location"] and db.get_json_setting("_studio_test")["cutout"] in ("basique", "ia")
        assert "studio_test.jpg" in c.get("/photos-gratuites").text
        r = c.post("/photos-gratuites/hf", data={"token": "hf_abcdefghijklmnopqrstuvwxyz", "enabled": "on",
                                                 "steps": "24", "space": "Qwen/Qwen-Image-Edit-2511"},
                   headers=h, follow_redirects=False)
        assert db.get_setting("hf_enabled") == "on" and "hf_abcdefghij" not in c.get("/photos-gratuites").text
        r = c.post("/photos-gratuites/gratuit", headers=h, follow_redirects=False)
        assert db.get_setting("photo_provider") == "gratuit"
        r = c.post("/studio/reglages", data={"photo_provider": "studio", "studio_bg": "noir"}, headers=h,
                   follow_redirects=False)
        assert db.get_setting("studio_bg") == "noir" and "Studio instantané" in c.get("/studio").text
