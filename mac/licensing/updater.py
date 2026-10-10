"""
updater.py -- finds, downloads and verifies a newer Astro Rishi, used by the launcher.

Updates are published as GitHub Releases of a small release-only repository (the repo name is in
update_config.json, written at build time). Each release carries
    AstroRishi-Update-<version>.exe       the small update installer
    AstroRishi-Update-<version>.exe.sig   an Ed25519 signature of its SHA-256 made with the OWNER's private key
and the app refuses to run anything whose signature does not verify against the public key it shipped with --
so nobody else can push a fake update to users, even if they control the download location.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import platform_paths

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                   # install folder
CONFIG = HERE / "update_config.json"                 # {"repo": "owner/name", "api_base": optional override for tests}
PUBLIC_KEY_FILE = HERE / "license_public.pem"
DATA_DIR = platform_paths.data_dir(ROOT)
DOWNLOAD_DIR = DATA_DIR / "updates"
_SIGN_PREFIX = b"astro-update:"


def installed_version() -> str:
    try:
        return (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def _parts(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version))


def is_newer(candidate: str, current: str) -> bool:
    return _parts(candidate) > _parts(current)


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AstroRishi-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_update(timeout: float = 6.0) -> dict | None:
    """{"version", "notes", "exe_url", "sig_url", "name"} when a newer release exists, else None. Never raises."""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        repo = cfg.get("repo")
        if not repo:
            return None
        base = cfg.get("api_base") or "https://api.github.com"
        if sys.platform == "darwin":
            # Mac updates are pre-releases tagged mac-v<version> holding AstroRishi-Mac-Update-<version>.zip (+ .sig)
            releases = json.loads(_get(f"{base}/repos/{repo}/releases?per_page=30", timeout))
            tagged = [r for r in releases if str(r.get("tag_name", "")).startswith("mac-v")]
            if not tagged:
                return None
            release = max(tagged, key=lambda r: _parts(str(r["tag_name"])[5:]))
            version = str(release["tag_name"])[5:]
            wanted = f"AstroRishi-Mac-Update-{version}.zip"
        else:
            release = json.loads(_get(f"{base}/repos/{repo}/releases/latest", timeout))
            version = str(release.get("tag_name", "")).lstrip("vV")
            wanted = f"AstroRishi-Update-{version}.exe"
        if not version or not is_newer(version, installed_version()):
            return None
        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        if wanted not in assets or wanted + ".sig" not in assets:
            return None
        return {"version": version, "notes": (release.get("body") or "").strip(), "name": wanted,
                "exe_url": assets[wanted], "sig_url": assets[wanted + ".sig"]}
    except Exception:
        return None


def verify_file(path: Path, signature_b64: str) -> bool:
    """True when the owner's signature checks out against ANY key this program trusts: the license key it shipped with, or
    (if present) update_public.pem -- the separate update-signing key a Mac release machine can hold."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        digest = hashlib.sha256(path.read_bytes()).digest()
        signature = base64.b64decode(signature_b64.strip())
        for key_file in (HERE / "update_public.pem", PUBLIC_KEY_FILE):
            if not key_file.exists():
                continue
            try:
                serialization.load_pem_public_key(key_file.read_bytes()).verify(signature, _SIGN_PREFIX + digest)
                return True
            except InvalidSignature:
                continue
        return False
    except Exception:
        return False


def download(info: dict, progress=None) -> Path | None:
    """Downloads and verifies the update installer. Returns its path, or None if anything is wrong."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / info["name"]
    try:
        req = urllib.request.Request(info["exe_url"], headers={"User-Agent": "AstroRishi-updater"})
        with urllib.request.urlopen(req, timeout=30) as r, open(target, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        signature = _get(info["sig_url"], 20).decode("ascii")
    except Exception:
        target.unlink(missing_ok=True)
        return None
    if not verify_file(target, signature):
        target.unlink(missing_ok=True)
        return None
    return target
