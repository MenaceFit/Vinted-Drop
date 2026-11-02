"""Mode gratuit (ComfyUI sur le PC) et budget journalier, sans réseau."""
import io
import json

import pytest


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from vinted_drop import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "COMFYUI_URL", "http://pc:8188")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "PHOTO_PROVIDER", "local")
    (tmp_path / "media").mkdir()
    from vinted_drop import db
    db.init()


def png():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (64, 64), (1, 2, 3)).save(b, "PNG"); return b.getvalue()


def test_prune_removes_unused_image_slots():
    from vinted_drop import comfyui
    wf = comfyui.prune(comfyui.load_workflow(), n_images=1, use_lora=True)
    assert "11" in wf and "12" not in wf and "13" not in wf and "15" not in wf and "16" not in wf
    enc = wf["20"]["inputs"]
    assert "image1" in enc and "image2" not in enc and "image3" not in enc
    assert "40" in wf and "60" in wf  # le reste du workflow est intact


def test_prune_without_lora_rewires_model():
    from vinted_drop import comfyui
    wf = comfyui.prune(comfyui.load_workflow(), n_images=3, use_lora=False)
    assert "4" not in wf and wf["5"]["inputs"]["model"] == ["1", 0]


class Resp:
    def __init__(self, data=None, content=b"", code=200):
        self._d, self.content, self.status_code = data, content, code
        self.text = json.dumps(data) if data is not None else ""
    def json(self):
        return self._d
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_generate_calls_comfyui_api(monkeypatch, tmp_path):
    from vinted_drop import comfyui
    sent = {}
    ref = tmp_path / "ref.png"; ref.write_bytes(png())

    def post(url, json=None, files=None, data=None, timeout=None):
        if url.endswith("/upload/image"):
            return Resp({"name": "vd_ref.png", "subfolder": "", "type": "input"})
        sent["workflow"] = json["prompt"]
        return Resp({"prompt_id": "abc"})

    def get(url, params=None, timeout=None):
        if "/history/" in url:
            return Resp({"abc": {"status": {"status_str": "success"},
                                 "outputs": {"60": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}})
        return Resp(content=b"IMAGE")
    monkeypatch.setattr(comfyui.requests, "post", post)
    monkeypatch.setattr(comfyui.requests, "get", get)
    monkeypatch.setattr(comfyui.time, "sleep", lambda s: None)
    assert comfyui.generate("put the dress on her", [ref]) == b"IMAGE"
    wf = sent["workflow"]
    assert wf["11"]["inputs"]["image"] == "vd_ref.png"
    assert wf["20"]["inputs"]["prompt"] == "put the dress on her"
    assert isinstance(wf["40"]["inputs"]["seed"], int) and wf["40"]["inputs"]["steps"] == 4
    assert wf["30"]["inputs"]["width"] == 896 and wf["30"]["inputs"]["height"] == 1184
    assert "__" not in json.dumps(wf)  # tous les repères sont remplis


def test_local_is_free_and_preferred(monkeypatch, tmp_path):
    from vinted_drop import comfyui, db, image_providers
    monkeypatch.setattr(comfyui, "generate", lambda prompt, refs, aspect="3:4": png())
    res = image_providers.generate("x", [], tmp_path / "a.png", provider="auto")
    assert res["provider"] == "local" and res["cost"] == 0.0
    assert db.cost_stats()["eur"] == 0


def test_pc_off_falls_back_to_gemini_until_budget(monkeypatch, tmp_path):
    from vinted_drop import comfyui, db, image_providers
    def off(*a, **k):
        raise comfyui.ComfyError("ComfyUI injoignable")
    monkeypatch.setattr(comfyui, "generate", off)
    monkeypatch.setattr(image_providers, "gemini_generate", lambda prompt, refs, aspect="3:4": png())
    db.set_setting("daily_budget", "0.05")
    first = image_providers.generate("x", [], tmp_path / "a.png", provider="auto")
    assert first["provider"] == "gemini"
    with pytest.raises(image_providers.ImageError, match="budget du jour"):
        image_providers.generate("x", [], tmp_path / "b.png", provider="auto")


def test_status_when_pc_off(monkeypatch):
    from vinted_drop import comfyui
    def boom(*a, **k):
        raise comfyui.requests.ConnectionError()
    monkeypatch.setattr(comfyui.requests, "get", boom)
    st = comfyui.status()
    assert not st["ok"] and "injoignable" in st["error"]
