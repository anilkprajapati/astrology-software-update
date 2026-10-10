"""
entitlements.py -- change what ONE customer may use WITHOUT sending them a new key.

The owner (Key Maker -> "Push change") publishes a small signed file for a customer's Machine ID to the GitHub release
repo (release tag "entitlements", asset ent-<MACHINE-ID>.json + .sig). Every running copy looks for the file named after its
OWN Machine ID -- at start-up and every few hours -- checks the owner's signature and keeps it in its data folder. license_gate.py
then applies it on top of the license key. A file only counts when its serial number is higher than the last one accepted
(an old signed file cannot be replayed to undo a newer change), it was made for this very Machine ID, and it can never
create access by itself: it only changes a copy that already holds a valid license key.

Fields a file may carry (a missing field = leave the key's value alone):
    modules      list of app keys, or null = every app          admin_panel  true / false
    perm_deny    list of "module/tab" and "module/tab/element" switched off
    expires      "YYYY-MM-DD" or null (no expiry)               disabled     true = switch this license off
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

import platform_paths

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "update_config.json"                 # {"repo": "owner/name", "api_base": optional override for tests}
PUBLIC_KEY_FILE = HERE / "license_public.pem"
DATA_DIR = platform_paths.data_dir(ROOT)
STORE = DATA_DIR / "entitlement.json"
TAG = "entitlements"
_SIGN_PREFIX = b"astro-entitlement:"
REFRESH_HOURS = 6.0

_cache: dict = {"mtime": None, "value": None}


def asset_name(machine: str) -> str:
    return f"ent-{machine}.json"


def _get(url: str, timeout: float, raw: bool = False) -> bytes:
    # raw=True: the release-asset API endpoint (always the CURRENT file; the public download URL can be served from a cache)
    req = urllib.request.Request(url, headers={"User-Agent": "AstroRishi-entitlements",
                                               "Accept": "application/octet-stream" if raw else "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def verify(raw: bytes, signature_b64: str) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_FILE.read_bytes())
        try:
            public_key.verify(base64.b64decode(signature_b64.strip()), _SIGN_PREFIX + hashlib.sha256(raw).digest())
            return True
        except InvalidSignature:
            return False
    except Exception:
        return False


def load() -> dict | None:
    """The last accepted override for this PC (or None). Cheap: re-read only when the file changed."""
    try:
        mtime = STORE.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mtime:
        try:
            _cache.update(mtime=mtime, value=json.loads(STORE.read_text(encoding="utf-8")))
        except Exception:
            _cache.update(mtime=mtime, value=None)
    return _cache["value"]


def refresh(machine: str, timeout: float = 8.0) -> bool:
    """Looks for a newer signed override for `machine`. True when a new one was accepted. Never raises."""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        repo = cfg.get("repo")
        if not repo:
            return False
        base = cfg.get("api_base") or "https://api.github.com"
        try:
            release = json.loads(_get(f"{base}/repos/{repo}/releases/tags/{TAG}", timeout))
        except Exception:
            return False                                    # no such release yet / offline
        assets = {a["name"]: a["url"] for a in release.get("assets", [])}
        name = asset_name(machine)
        if name not in assets or name + ".sig" not in assets:
            return False
        raw = _get(assets[name], timeout, raw=True)
        if not verify(raw, _get(assets[name + ".sig"], timeout, raw=True).decode("ascii")):
            return False
        data = json.loads(raw.decode("utf-8"))
        if data.get("machine") != machine:
            return False
        current = load()
        if current and int(data.get("serial", 0)) <= int(current.get("serial", 0)):
            return False                                    # not newer -- ignore (replay protection)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data), encoding="utf-8")
        return True
    except Exception:
        return False


def start_background(machine: str, on_change=None) -> None:
    """Checks shortly after start-up, then every REFRESH_HOURS, on a daemon thread."""
    def loop() -> None:
        time.sleep(4)
        while True:
            try:
                if refresh(machine) and on_change:
                    on_change()
            except Exception:
                pass
            time.sleep(REFRESH_HOURS * 3600)
    threading.Thread(target=loop, daemon=True, name="entitlement-refresh").start()
