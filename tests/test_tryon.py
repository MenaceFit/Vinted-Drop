"""Photos portées gratuites : cabine d'essayage, modèle créé gratuitement (FLUX), importé ou enrichi de poses."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw


def png(size=(600, 800), color=(120, 20, 40), bg=(250, 250, 250)):
    img = Image.new("RGB", size, bg)
    ImageDraw.Draw(img).rectangle((size[0] // 4, size[1] // 5, 3 * size[0] // 4, 4 * size[1] // 5), fill=color)
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


@pytest.fixture
def free(monkeypatch, tmp_path):
    """Hugging Face activé (simulé), aucun fournisseur payant : on enregistre les appels."""
    from vinted_drop import config, db, hf_space
    db.init()
    monkeypatch.setattr(config, "COMFYUI_URL", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    db.set_setting("hf_enabled", "on")
    db.set_setting("photo_provider", "gratuit")
    db.set_setting("photo_qa", "off")
    calls = {"edit": [], "t2i": []}
    def edit(prompt, refs, aspect="3:4", timeout=420):
        calls["edit"].append((prompt, [str(r) for r in refs]))
        return png(color=(30, 30, 200))
    def t2i(prompt, aspect="3:4", timeout=300):
        calls["t2i"].append(prompt)
        return png(color=(200, 180, 160))
    monkeypatch.setattr(hf_space, "generate", edit)
    monkeypatch.setattr(hf_space, "text_to_image", t2i)
    return calls


def _product(name="Veste militaire noire col montant", niche="veste-militaire-napoleon", persona=True):
    from vinted_drop import config, db, photos
    pid_persona = None
    if persona:
        pid_persona = db.add_persona("Léa", "a 25 year old woman")
        d = config.MEDIA_DIR / "personas"; d.mkdir(parents=True, exist_ok=True)
        (d / "lea_full.png").write_bytes(png(color=(10, 10, 10)))
        db.update_persona(pid_persona, reference_image="personas/lea_full.png", refs=["personas/lea_full.png"])
    sid = db.add_shop(f"Alt {name[:12]} {db.now()} {len(db.list_shops())}", niche, pid_persona)
    pid = db.add_product(cost=15, shop_id=sid, niche=niche)
    img = photos.product_dir(pid) / "supplier.png"
    img.write_bytes(png())
    db.update_product(pid, supplier_image=photos.rel(img), title=name)
    return pid


def test_category_detection():
    from vinted_drop import db, tryon
    db.init()
    assert tryon.category_for({"title": "Veste militaire noire", "niche": "veste-militaire-napoleon"}) == "top"
    assert tryon.category_for({"title": "Robe longue en dentelle", "niche": "goth-classique"}) == "full"
    assert tryon.category_for({"title": "Jean baggy Y2K", "niche": "jeans-baggy-y2k"}) == "bottom"
    assert tryon.category_for({"title": "Santiags brodées", "niche": "bottes-western"}) is None
    assert tryon.category_for({"title": "Ensemble Renaissance 3 pièces", "niche": "medieval"}) == "full"


def test_free_worn_photo_uses_cabin_with_persona_and_clean_garment(free):
    from vinted_drop import db, photos
    pid = _product()
    photos.generate_product_photos(pid, kinds=["porte"])
    prompt, refs = free["edit"][0]
    assert "Dress the person from picture 1" in prompt and "upper-body" in prompt
    assert refs[0].endswith("lea_full.png") and refs[1].endswith("garment_ref.jpg")    # modèle + vêtement détouré
    meta = list(db.get_product(pid)["photo_meta"].values())[0]
    assert meta["kind"] == "porte" and meta["provider"] == "hf" and meta["cost"] == 0
    assert db.cost_today() == 0


def test_cabin_skipped_for_shoes_and_custom_scene(free):
    from vinted_drop import photos
    pid = _product(name="Santiags brodées", niche="bottes-western")
    photos.generate_product_photos(pid, kinds=["porte"])
    assert "Dress the person" not in free["edit"][-1][0]
    pid = _product()
    photos.generate_product_photos(pid, kinds=["porte"], scene="rue")
    assert "Parisian street" in free["edit"][-1][0]


def test_persona_created_for_free_with_flux(free):
    from vinted_drop import photos
    m = photos.create_persona("Inès", "a 30 year old woman, curly hair", provider="gratuit")
    assert free["t2i"] and "Full body" in free["t2i"][0]
    assert m["reference_image"].endswith("_full.jpg")
    assert len(m["refs"]) == 2 and m["refs"][1].endswith("_face.jpg")      # portrait par Hugging Face (édition)


def test_add_pose_keeps_faces_last(free):
    from vinted_drop import db, photos
    pid = db.add_persona("Léa", "x")
    from vinted_drop import config
    d = config.MEDIA_DIR / "personas"; d.mkdir(parents=True, exist_ok=True)
    (d / "a_full.png").write_bytes(png()); (d / "a_face.png").write_bytes(png())
    db.update_persona(pid, reference_image="personas/a_full.png", refs=["personas/a_full.png", "personas/a_face.png"])
    m = photos.add_persona_pose(pid, "profil", provider="gratuit")
    assert "side view" in free["edit"][-1][0]
    assert m["refs"][0] == "personas/a_full.png" and "_pose" in m["refs"][1] and m["refs"][-1].endswith("_face.png")
    assert len(m["refs"]) == 3


def test_import_persona_requires_consent_and_valid_image(free):
    from vinted_drop import db, photos
    from vinted_drop.dashboard import app as app_mod
    with pytest.raises(ValueError):
        photos.import_persona("x", b"pas une image")
    with TestClient(app_mod.app) as c:
        h = {"origin": "http://testserver"}
        files = {"image": ("moi.png", png(), "image/png")}
        r = c.post("/modeles/importer", data={"name": "Moi"}, files=files, headers=h, follow_redirects=False)
        assert "err=" in r.headers["location"] and not db.list_personas()
        r = c.post("/modeles/importer", data={"name": "Moi", "consent": "on"}, files=files, headers=h,
                   follow_redirects=False)
        assert "ok=" in r.headers["location"]
        m = db.list_personas()[0]
        assert m["name"] == "Moi" and m["refs"] == [m["reference_image"]]


def test_tryon_test_and_page(free):
    from vinted_drop import db
    from vinted_drop.dashboard import app as app_mod
    from vinted_drop.dashboard import routes_comfy
    _product()
    res = routes_comfy.run_tryon_test()
    assert res["provider"] == "hf" and db.get_json_setting("_tryon_test")["persona"] == "Léa"
    with TestClient(app_mod.app) as c:
        h = {"origin": "http://testserver"}
        t = c.get("/photos-gratuites").text
        assert "cabine d'essayage" in t and "tryon_test.jpg" in t
        c.post("/photos-gratuites/essayage", headers=h, follow_redirects=False)
        assert db.get_setting("tryon") == "off"
        assert "Importer une vraie photo" in c.get("/studio").text
