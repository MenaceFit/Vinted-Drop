"""Page Photos gratuites : faux ComfyUI + faux Hugging Face en local (vraies requêtes HTTP, sans Internet)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

BLOB = bytes(range(256)) * 4096 * 3      # 3 Mo


class Fake(BaseHTTPRequestHandler):
    models = {"diffusion_models": [], "text_encoders": [], "vae": [], "loras": []}
    nodes_missing = set()
    posted = []
    cut_after = None                     # coupe le téléchargement après N octets (une fois)

    def log_message(self, *a):
        pass

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if p == "/system_stats":
            return self._json({"system": {"ram_total": 64e9, "comfyui_version": "0.9.1"},
                               "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 4070 : cudaMallocAsync", "type": "cuda",
                                            "vram_total": 12.9e9, "vram_free": 11e9}]})
        if p == "/queue":
            return self._json({"queue_running": [[1]], "queue_pending": [[2], [3]]})
        if p.startswith("/object_info/"):
            n = p.split("/")[-1]
            return self._json({} if n in Fake.nodes_missing else {n: {}})
        if p.startswith("/models/"):
            return self._json(Fake.models.get(p.split("/")[-1], []))
        if p.startswith("/hf/"):
            start = 0
            rng = self.headers.get("Range")
            data = BLOB
            if rng:
                start = int(rng.split("=")[1].rstrip("-"))
                self.send_response(206)
            else:
                self.send_response(200)
            chunk = data[start:]
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            if Fake.cut_after and not rng:
                self.wfile.write(chunk[:Fake.cut_after])
                Fake.cut_after = None
                self.wfile.flush()
                self.connection.close()
                return
            self.wfile.write(chunk)
            return
        self._json({}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Fake.posted.append((self.path, self.rfile.read(n)))
        self._json({})


@pytest.fixture
def comfy(monkeypatch, tmp_path):
    from vinted_drop import comfyui, config, db
    db.init()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    Fake.models = {"diffusion_models": [], "text_encoders": [], "vae": [], "loras": []}
    Fake.nodes_missing, Fake.posted, Fake.cut_after = set(), [], None
    # les fichiers des profils pointent vers le faux Hugging Face
    import copy
    presets = copy.deepcopy(comfyui.PRESETS)
    for pr in presets.values():
        for role, f in list(pr["files"].items()):
            pr["files"][role] = {**f, "url": f"{base}/hf/{f['name']}", "gb": 0.001}
    monkeypatch.setattr(comfyui, "PRESETS", presets)
    monkeypatch.setattr(config, "COMFYUI_URL", "")
    comfy_dir = tmp_path / "ComfyUI"
    (comfy_dir / "models").mkdir(parents=True)
    yield base, comfy_dir
    srv.shutdown()


def test_detect_and_status(comfy, monkeypatch):
    from vinted_drop import comfy_manager as cm, comfyui, db
    base, _ = comfy
    port = int(base.rsplit(":", 1)[1])
    monkeypatch.setattr(cm, "PORTS", (port,))
    assert cm.detect() == base and db.get_setting("comfy_url") == base
    st = comfyui.status()
    assert st["ok"] and st["gpu"] == "NVIDIA GeForce RTX 4070" and st["vram_gb"] == 12.9 and st["ram_gb"] == 64


def test_check_reports_missing_files_nodes_and_queue(comfy):
    from vinted_drop import comfy_manager as cm
    base, d = comfy
    cm.save_settings(url=base, folder=str(d), preset="standard")
    Fake.nodes_missing = {"FluxKontextMultiReferenceLatentMethod"}
    s = cm.check()
    assert s["status"]["ok"] and s["queue"] == {"running": 1, "pending": 2}
    assert len(s["files"]) == 4 and not any(f["present"] for f in s["files"])
    assert any("trop ancien" in p for p in s["problems"]) and not s["ready"]
    assert s["recommended"] == "standard"
    Fake.nodes_missing = set()
    Fake.models = {"diffusion_models": ["qwen_image_edit_2511_fp8mixed.safetensors"],
                   "text_encoders": ["qwen_2.5_vl_7b_fp8_scaled.safetensors"], "vae": ["qwen_image_vae.safetensors"],
                   "loras": ["sub/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"]}
    assert cm.check()["ready"]


def test_download_resumes_after_cut(comfy):
    from vinted_drop import comfy_manager as cm
    base, d = comfy
    cm.save_settings(url=base, folder=str(d), preset="standard")
    Fake.cut_after = 2_500_000
    with pytest.raises(Exception):
        cm.download_missing()
    part = d / "models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors.part"
    assert part.exists() and 0 < part.stat().st_size < len(BLOB)
    assert cm.progress()["status"] == "error"
    res = cm.download_missing()                      # reprise avec Range
    assert res["downloaded"] == 4
    for folder, name in [("diffusion_models", "qwen_image_edit_2511_fp8mixed.safetensors"),
                         ("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
                         ("vae", "qwen_image_vae.safetensors"),
                         ("loras", "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors")]:
        assert (d / "models" / folder / name).read_bytes() == BLOB
    assert cm.progress()["status"] == "done"
    assert all(f["present"] for f in cm.check()["files"])


def test_portable_layout_models_dir(tmp_path):
    from vinted_drop import comfy_manager as cm, db
    db.init()
    root = tmp_path / "ComfyUI_windows_portable"
    (root / "ComfyUI" / "models").mkdir(parents=True)
    (root / "python_embeded").mkdir()
    (root / "python_embeded" / "python.exe").write_text("")
    (root / "ComfyUI" / "main.py").write_text("")
    cm.save_settings(folder=str(root))
    assert cm.models_dir() == root / "ComfyUI" / "models"
    assert cm.comfy_root() == root / "ComfyUI"
    assert cm.comfy_python() == root / "python_embeded" / "python.exe"


def test_settings_validation(comfy):
    from vinted_drop import comfy_manager as cm
    with pytest.raises(cm.ManagerError):
        cm.save_settings(url="ftp://x")
    with pytest.raises(cm.ManagerError):
        cm.save_settings(folder="/dossier/qui/n/existe/pas")
    with pytest.raises(cm.ManagerError):
        cm.save_settings(preset="inconnu")


def test_presets_and_gguf_loader(comfy):
    from vinted_drop import comfy_manager as cm, comfyui, db
    base, d = comfy
    cm.save_settings(url=base, preset="leger")
    wf = comfyui.build("hello", ["a.png"])
    loaders = [n for n in wf.values() if "unet_name" in n["inputs"]]
    assert loaders[0]["class_type"] == "UnetLoaderGGUF" and loaders[0]["inputs"] == {"unet_name": "qwen-image-edit-2511-Q4_K_M.gguf"}
    assert "UnetLoaderGGUF" in cm.required_nodes()
    db.set_setting("comfy_preset", "standard")
    wf = comfyui.build("hello", ["a.png"])
    assert any(n["class_type"] == "FluxKontextMultiReferenceLatentMethod" for n in wf.values())
    assert wf["40"]["inputs"]["model"] == ["4", 0] and "__" not in json.dumps(wf)
    wf = comfyui.build("hello", ["a.png", "b.png", "c.png"])
    assert "13" in wf and wf["20"]["inputs"]["image3"] == ["16", 0]


def test_legacy_2509_env_is_kept(monkeypatch):
    from vinted_drop import comfyui, config, db
    db.init()
    monkeypatch.setattr(config, "COMFY_UNET", "qwen_image_edit_2509_fp8_e4m3fn.safetensors")
    monkeypatch.setattr(config, "COMFY_LORA", "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors")
    st = comfyui.settings()
    assert st["preset"] == "2509" and st["workflow"].endswith("qwen_image_edit_2509.json")
    db.set_setting("comfy_preset", "standard")                # choix dans le dashboard : il gagne
    assert comfyui.settings()["unet"] == "qwen_image_edit_2511_fp8mixed.safetensors"


def test_queue_actions_free_mode_and_page(comfy):
    from vinted_drop import comfy_manager as cm, db, image_providers
    from vinted_drop.dashboard import app as app_mod
    base, d = comfy
    cm.save_settings(url=base, folder=str(d))
    cm.interrupt(); cm.clear_queue(); cm.free_memory()
    assert [p for p, _ in Fake.posted] == ["/interrupt", "/queue", "/free"]
    assert image_providers.available()[0] == "local"
    cm.free_mode(qa_off=True)
    assert db.get_setting("photo_provider") == "local" and db.get_setting("daily_budget") == "0"
    assert db.get_setting("photo_qa") == "off"
    with TestClient(app_mod.app) as c:
        h = {"origin": "http://testserver"}
        r = c.get("/photos-gratuites")
        assert r.status_code == 200 and "RTX 4070" in r.text and "Télécharger les fichiers manquants" in r.text
        assert c.get("/api/comfyui").json()["status"]["ok"]
        r = c.post("/photos-gratuites/profil", data={"preset": "leger"}, headers=h, follow_redirects=False)
        assert r.status_code == 303 and db.get_setting("comfy_preset") == "leger"
        assert "ComfyUI-GGUF" in c.get("/photos-gratuites").text
        r = c.post("/photos-gratuites/file/vider", headers=h, follow_redirects=False)
        assert "ok=" in r.headers["location"]


def test_run_test_uses_drawn_reference(comfy, monkeypatch):
    from vinted_drop import comfy_manager as cm, comfyui, config
    base, _ = comfy
    cm.save_settings(url=base)
    seen = {}
    def gen(prompt, refs, aspect="3:4"):
        seen["refs"] = refs
        return b"PNG"
    monkeypatch.setattr(comfyui, "generate", gen)
    res = cm.run_test()
    assert seen["refs"][0].name == "comfy_test_ref.png" and (config.MEDIA_DIR / "comfy_test.png").read_bytes() == b"PNG"
    assert cm.last_test()["seconds"] == res["seconds"]
