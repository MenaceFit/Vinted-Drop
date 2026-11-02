"""Tests du studio photo (aucun appel réseau : fournisseurs et contrôle qualité simulés)."""
import io
import json

import pytest


def png_bytes(color=(200, 30, 60), size=(1024, 1536)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from vinted_drop import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "o-key")
    monkeypatch.setattr(config, "PHOTO_PROVIDER", "gemini")
    (tmp_path / "media").mkdir()
    from vinted_drop import db
    db.init()
    yield


@pytest.fixture
def fake_ai(monkeypatch):
    """Gemini et OpenAI renvoient une image ; le contrôle qualité renvoie les notes de la file `scores`."""
    from vinted_drop import ai, image_providers
    calls = {"gemini": 0, "openai": 0, "prompts": [], "refs": []}
    scores = []

    def gem(prompt, refs, aspect="3:4"):
        calls["gemini"] += 1; calls["prompts"].append(prompt); calls["refs"].append(len(refs))
        return png_bytes()

    def oai(prompt, refs, aspect="3:4", quality=None):
        calls["openai"] += 1; calls["prompts"].append(prompt); calls["refs"].append(len(refs))
        return png_bytes((20, 20, 20))

    monkeypatch.setattr(image_providers, "gemini_generate", gem)
    monkeypatch.setattr(image_providers, "openai_generate", oai)
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {"fidelity": scores.pop(0) if scores else 9,
                                                           "realism": 8, "issues": [], "verdict": "ok"})
    calls["scores"] = scores
    return calls


def _product(niche="goth-classique", with_persona=True):
    from vinted_drop import config, db, photos
    persona_id = None
    if with_persona:
        persona_id = db.add_persona("Sophie", "a 25 year old woman, black hair")
        d = config.MEDIA_DIR / "personas"; d.mkdir(parents=True, exist_ok=True)
        for n in ("full", "face"):
            (d / f"p_{n}.jpg").write_bytes(png_bytes())
        db.update_persona(persona_id, reference_image="personas/p_full.jpg",
                          refs=["personas/p_full.jpg", "personas/p_face.jpg"], decor="a dark cosy bedroom")
    sid = db.add_shop("Sophie", niche, persona_id)
    pid = db.add_product(cost=12, shop_id=sid, niche=niche)
    img = photos.product_dir(pid) / "supplier.jpg"
    img.write_bytes(png_bytes((10, 10, 10), (800, 800)))
    db.update_product(pid, supplier_image=photos.rel(img))
    return pid


def test_find_image_in_both_gemini_formats():
    from vinted_drop.image_providers import find_image_b64
    interactions = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "ok"},
                                                                    {"type": "image", "data": "QUJD", "mime_type": "image/png"}]}]}
    legacy = {"candidates": [{"content": {"parts": [{"text": "hi"}, {"inlineData": {"mimeType": "image/png", "data": "WFla"}}]}}]}
    assert find_image_b64(interactions) == "QUJD"
    assert find_image_b64(legacy) == "WFla"
    assert find_image_b64({"candidates": []}) is None


def test_fallback_to_openai_and_cost_logged(fake_ai, monkeypatch, tmp_path):
    from vinted_drop import db, image_providers
    def broken(*a, **k):
        raise image_providers.ImageError("quota dépassé")
    monkeypatch.setattr(image_providers, "gemini_generate", broken)
    res = image_providers.generate("x", [], tmp_path / "o.png", provider="auto")
    assert res["provider"] == "openai" and res["fallback_from"] == "gemini"
    assert db.cost_stats()["images"] == 1


def test_full_studio_run_with_persona_qa_and_finish(fake_ai):
    from PIL import Image
    from vinted_drop import config, db, photos
    db.set_setting("photo_set", "standard")
    pid = _product()
    fake_ai["scores"].extend([9])                  # 1er contrôle = image nettoyée (ok)
    result = photos.generate_product_photos(pid)
    p = db.get_product(pid)
    assert len(result) == 3
    kinds = [p["photo_meta"][x]["kind"] for x in p["photos"]]
    assert kinds[0] == "porte"                     # la photo portée sert de couverture
    assert set(kinds) == {"porte", "plat", "detail"}
    with Image.open(config.MEDIA_DIR / p["photos"][0]) as im:
        assert im.size == (1200, 1600) and im.format == "JPEG"
    worn = [pr for pr in fake_ai["prompts"] if "Mirror selfie" in pr][0]
    assert "PERSON" in worn and "dark cosy bedroom" in worn
    assert max(fake_ai["refs"]) == 3               # 2 références du modèle + le produit


def test_low_quality_photo_is_retried_with_other_provider(fake_ai):
    from vinted_drop import db, photos
    db.set_setting("photo_clean", "off")
    pid = _product()
    fake_ai["scores"].extend([4, 9])               # 1er essai mauvais, 2e bon
    photos.generate_product_photos(pid, kinds=["plat"])
    p = db.get_product(pid)
    meta = p["photo_meta"][p["photos"][0]]
    assert meta["tries"] == 2 and meta["fidelity"] == 9 and meta["provider"] == "openai"
    assert len(p["photos"]) == 1                   # le mauvais essai est supprimé


def test_kids_niche_has_no_person(fake_ai):
    from vinted_drop import db, photos
    pid = _product(niche="enfant-fetes", with_persona=False)
    photos.generate_product_photos(pid)
    p = db.get_product(pid)
    assert "porte" not in {m["kind"] for m in p["photo_meta"].values()}
    assert not any("PERSON" in pr for pr in fake_ai["prompts"])


def test_cover_delete_and_regenerate(fake_ai):
    from vinted_drop import db, photos
    db.set_setting("photo_clean", "off")
    pid = _product()
    photos.generate_product_photos(pid, kinds=["porte", "plat"])
    p = db.get_product(pid)
    plat = [x for x in p["photos"] if p["photo_meta"][x]["kind"] == "plat"][0]
    photos.set_cover(pid, plat)
    assert db.get_product(pid)["photos"][0] == plat
    new = photos.regenerate_photo(pid, plat, provider="openai")
    p = db.get_product(pid)
    assert p["photos"][0] == new and p["photo_meta"][new]["kind"] == "plat" and plat not in p["photos"]
    photos.delete_photo(pid, new)
    assert len(db.get_product(pid)["photos"]) == 1


def test_openai_text_model_fallback(monkeypatch):
    from vinted_drop import ai, config
    monkeypatch.setattr(ai, "_working_model", None)
    monkeypatch.setattr(config, "OPENAI_TEXT_MODEL", "old-model")
    monkeypatch.setattr(config, "OPENAI_TEXT_FALLBACKS", ["new-model"])
    used = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body
            self.text = json.dumps(body)
        def json(self):
            return self._b

    def post(url, headers=None, timeout=None, json=None):
        used.append(json["model"])
        if json["model"] == "old-model":
            return R(404, {"error": {"message": "The model `old-model` does not exist"}})
        return R(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(ai.requests, "post", post)
    assert ai.chat_json("s", "u") == {"ok": True}
    assert used == ["old-model", "new-model"]


def test_studio_pages(fake_ai):
    from fastapi.testclient import TestClient
    from vinted_drop import db, photos
    from vinted_drop.dashboard import app as app_mod
    db.set_setting("photo_clean", "off")
    pid = _product()
    photos.generate_product_photos(pid, kinds=["plat", "detail"])
    with TestClient(app_mod.app) as c:
        assert c.get("/studio").status_code == 200
        page = c.get(f"/produit/{pid}")
        assert page.status_code == 200 and "Studio photo" in page.text and "/10" in page.text
        path = db.get_product(pid)["photos"][1]
        r = c.post(f"/produit/{pid}/photo", data={"path": path, "action": "cover"}, follow_redirects=False)
        assert r.status_code == 303 and db.get_product(pid)["photos"][0] == path
        assert c.post("/studio/reglages", data={"photo_provider": "openai"}, follow_redirects=False).status_code == 303
    assert db.get_setting("photo_provider") == "openai"
