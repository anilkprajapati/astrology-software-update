"""
placedata.py -- keeps the place database (city search) of an installed Astro Rishi up to date WITHOUT replacing it.

The owner publishes small "place data updates" (GitHub releases tagged places-1, places-2, ... -- marked as
pre-releases so they never count as an app update). Each one lists the places that are new or changed since the
previous one, plus any that were removed. This module downloads the ones this PC is missing, checks the owner's
signature (a different signing context from app updates), and MERGES them into places.db:

  * a place in the update replaces the same place (country + state + city) on this PC,
  * every other place already on this PC -- including ones its own searches added -- is left alone.

It runs from the launcher BEFORE the server starts, so the database is not in use. The version of the data is
kept inside places.db itself (table place_data_meta), so a fresh full install always knows what it contains.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import platform_paths

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                   # install folder
DB = ROOT / "app" / "place_database" / "places.db"
CONFIG = HERE / "update_config.json"                 # {"repo": "owner/name", "api_base": optional override for tests}
PUBLIC_KEY_FILE = HERE / "license_public.pem"
DATA_DIR = platform_paths.data_dir(ROOT)
DOWNLOAD_DIR = DATA_DIR / "updates"
LAST_CHECK = DATA_DIR / "place_last_check.txt"
_SIGN_PREFIX = b"astro-place:"
COLUMNS = ["country", "state", "city", "lat", "lon", "population", "source", "display_name", "pincode",
           "updated_date", "updated_time", "coord_verified_date", "coord_verified_provider"]

_UPSERT = (
    f"INSERT INTO places ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))}) "
    "ON CONFLICT(country, state, city) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in COLUMNS[3:])
)


def local_version(db: Path = DB) -> int:
    """The place-data version this database contains (0 = the original data it shipped with)."""
    try:
        conn = sqlite3.connect(str(db), timeout=30)
        try:
            row = conn.execute("SELECT value FROM place_data_meta WHERE key='version'").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except (sqlite3.Error, ValueError):
        return 0


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AstroRishi-placedata", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_updates(have: int, timeout: float = 6.0) -> list[dict]:
    """Published place updates newer than `have`, as an unbroken chain have+1, have+2, ... (never raises)."""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        repo = cfg.get("repo")
        if not repo:
            return []
        base = cfg.get("api_base") or "https://api.github.com"
        releases = json.loads(_get(f"{base}/repos/{repo}/releases?per_page=100", timeout))
        found = {}
        for rel in releases:
            m = re.fullmatch(r"places-(\d+)", str(rel.get("tag_name", "")))
            if not m:
                continue
            n = int(m.group(1))
            assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
            name = f"places-{n}.json.gz"
            if name in assets and name + ".sig" in assets:
                found[n] = {"n": n, "name": name, "url": assets[name], "sig_url": assets[name + ".sig"]}
        chain, want = [], have + 1
        while want in found:
            chain.append(found[want])
            want += 1
        return chain
    except Exception:
        return []


def verify_file(path: Path, signature_b64: str) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        digest = hashlib.sha256(path.read_bytes()).digest()
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_FILE.read_bytes())
        try:
            public_key.verify(base64.b64decode(signature_b64.strip()), _SIGN_PREFIX + digest)
            return True
        except InvalidSignature:
            return False
    except Exception:
        return False


def download(info: dict) -> Path | None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / info["name"]
    try:
        target.write_bytes(_get(info["url"], 120))
        signature = _get(info["sig_url"], 30).decode("ascii")
    except Exception:
        target.unlink(missing_ok=True)
        return None
    if not verify_file(target, signature):
        target.unlink(missing_ok=True)
        return None
    return target


def apply_file(path: Path, db: Path = DB) -> int:
    """Merges one verified update file into the database in a single transaction. Returns its version number."""
    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    conn = sqlite3.connect(str(db), timeout=60)
    try:
        with conn:                                    # all-or-nothing
            conn.execute("CREATE TABLE IF NOT EXISTS place_data_meta (key TEXT PRIMARY KEY, value TEXT)")
            for row in payload.get("upsert", []):
                conn.execute(_UPSERT, row)
            for country, state, city in payload.get("delete", []):
                conn.execute("DELETE FROM places WHERE country=? AND state=? AND city=?", (country, state, city))
            conn.execute("INSERT INTO place_data_meta (key, value) VALUES ('version', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(payload["version"]),))
        return int(payload["version"])
    finally:
        conn.close()


def run(progress=None, min_hours: float = 12.0) -> int:
    """Download + apply any missing place updates. Returns how many were applied. Never raises.
    Checks the network at most every `min_hours` hours so start-up stays quick."""
    try:
        if not DB.exists():
            return 0
        if min_hours and LAST_CHECK.exists() and time.time() - LAST_CHECK.stat().st_mtime < min_hours * 3600:
            return 0
        have = local_version()
        updates = find_updates(have)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LAST_CHECK.write_text(str(time.time()))
        applied = 0
        for info in updates:
            if progress:
                progress(f"Updating place data ({info['n']})...")
            path = download(info)
            if not path:
                break                                    # not downloaded / not signed by the owner -> stop, try again later
            apply_file(path)
            path.unlink(missing_ok=True)
            applied += 1
        if applied:
            LAST_CHECK.unlink(missing_ok=True)           # check again next start, there may be more
        return applied
    except Exception:
        return 0
