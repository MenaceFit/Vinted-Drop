"""Chaque test tourne dans un dossier temporaire : base, médias, clé de chiffrement, sauvegardes."""
import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    from vinted_drop import config, db, security
    data = tmp_path / "data"
    (data / "media").mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "vinted_drop.db")
    monkeypatch.setattr(config, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(config, "SECRET_KEY", "")
    monkeypatch.setattr(security, "_fernet", None)
    # les fichiers JSON de niches restent ceux du dépôt
    real = config.ROOT / "data"
    for name in ("niches.json", "winning_products.json"):
        (data / name).write_text((real / name).read_text(encoding="utf-8"), encoding="utf-8")
    db._columns_cache.clear()
    yield
    db._columns_cache.clear()
