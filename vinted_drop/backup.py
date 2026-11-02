"""Sauvegardes de la base SQLite (API de sauvegarde native : cohérente même pendant l'utilisation).

- backup()            : copie datée dans data/backups/, garde les BACKUP_KEEP plus récentes
- list_backups()      : liste des sauvegardes
- restore(name)       : vérifie l'intégrité, sauvegarde l'état actuel, puis restaure
Les cookies Vinted restent chiffrés dans les sauvegardes (ils le sont déjà dans la base).
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config


def backup_dir() -> Path:
    d = config.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup(reason="auto") -> Path | None:
    if not config.DB_PATH.exists():
        return None
    name = f"vinted_drop-{datetime.now():%Y%m%d-%H%M%S}-{reason}.db"
    out = backup_dir() / name
    src = sqlite3.connect(config.DB_PATH, timeout=30)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _rotate()
    return out


def _rotate():
    files = sorted(backup_dir().glob("vinted_drop-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[config.BACKUP_KEEP:]:
        old.unlink(missing_ok=True)


def list_backups():
    return [{"name": p.name, "size_kb": round(p.stat().st_size / 1024), "date": datetime.fromtimestamp(p.stat().st_mtime)}
            for p in sorted(backup_dir().glob("vinted_drop-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)]


def check(path: Path) -> bool:
    try:
        c = sqlite3.connect(path)
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        return ok and {"products", "sales", "shops"} <= tables
    except sqlite3.DatabaseError:
        return False


def restore(name: str) -> Path:
    src = backup_dir() / Path(name).name  # pas de chemin arbitraire
    if not src.exists():
        raise FileNotFoundError(f"Sauvegarde introuvable : {name}")
    if not check(src):
        raise ValueError("Sauvegarde corrompue : restauration annulée")
    safety = backup(reason="avant-restauration")
    s = sqlite3.connect(src)
    d = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return safety
