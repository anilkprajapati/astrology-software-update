"""
mac_release.py -- run this ON YOUR MAC to build and publish a Mac update. License keys are still made on Windows (Key Maker).

    python3 mac/mac_release.py setup                       once: creates the Mac UPDATE signing key (not a license key)
    python3 mac/mac_release.py update --source ../v2_astrology        build the code-only update, sign it, publish it
    python3 mac/mac_release.py sign-publish <update.zip>   sign + publish a zip you already built
Needs:  pip3 install cryptography requests
The update key (mac/keys/update_private.pem) never leaves this Mac and is git-ignored. Its public half is
mac/licensing/update_public.pem -- commit and push that file (the installed apps then trust updates signed here).
Save a GitHub token (Contents: read + write on the release repo) in mac/keys/github_token.txt once.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import mimetypes
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KEYS = HERE / "keys"
PRIVATE = KEYS / "update_private.pem"
PUBLIC = HERE / "licensing" / "update_public.pem"
TOKEN = KEYS / "github_token.txt"
REPO = "anilkprajapati/astrology-software-update"


def setup() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if PRIVATE.exists():
        print("The Mac update key already exists:", PRIVATE)
        return
    KEYS.mkdir(exist_ok=True)
    key = Ed25519PrivateKey.generate()
    PRIVATE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    PRIVATE.chmod(0o600)
    PUBLIC.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"Created the Mac update key.\n  private (keep it safe, back it up): {PRIVATE}\n  public  (commit + push this):       {PUBLIC}")
    print("Next: git add mac/licensing/update_public.pem && git commit -m 'Mac update key' && git push")


def sign_and_publish(zip_path: Path) -> None:
    import requests
    from cryptography.hazmat.primitives import serialization
    m = re.fullmatch(r"AstroRishi-Mac-Update-(\d+(?:\.\d+)+)\.zip", zip_path.name)
    if not m:
        raise SystemExit("File name must look like AstroRishi-Mac-Update-1.2026.1010.705.zip")
    if not PRIVATE.exists():
        raise SystemExit("No update key yet. Run:  python3 mac/mac_release.py setup")
    if not TOKEN.exists():
        raise SystemExit(f"Save your GitHub token (Contents: read + write on {REPO}) in {TOKEN} first.")
    version = m.group(1)
    private = serialization.load_pem_private_key(PRIVATE.read_bytes(), password=None)
    sig = zip_path.with_name(zip_path.name + ".sig")
    sig.write_text(base64.b64encode(private.sign(b"astro-update:" + hashlib.sha256(zip_path.read_bytes()).digest())).decode(), encoding="ascii")
    headers = {"Authorization": f"Bearer {TOKEN.read_text().strip()}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AstroRishi-mac-publisher"}
    api = f"https://api.github.com/repos/{REPO}"
    tag = f"mac-v{version}"
    r = requests.post(f"{api}/releases", headers=headers, timeout=60, json={
        "tag_name": tag, "name": f"Astro Rishi for Mac {version}", "body": "Improvements and fixes.", "prerelease": True,
        "make_latest": "false"})
    if r.status_code == 422:
        r = requests.get(f"{api}/releases/tags/{tag}", headers=headers, timeout=60)
    r.raise_for_status()
    upload_url = r.json()["upload_url"].split("{")[0]
    for path in (zip_path, sig):
        with open(path, "rb") as f:
            up = requests.post(upload_url, params={"name": path.name}, data=f, timeout=900,
                               headers={**headers, "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
        up.raise_for_status()
        print("uploaded", path.name)
    print("published", tag, "-- installed Macs will offer it the next time they start")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setup")
    u = sub.add_parser("update")
    u.add_argument("--source", required=True, help="your clone of the private source repo (git pull it first)")
    u.add_argument("--version", help="default: automatic")
    sp = sub.add_parser("sign-publish")
    sp.add_argument("zip")
    args = ap.parse_args()
    if args.cmd == "setup":
        setup()
    elif args.cmd == "update":
        sys.path.insert(0, str(HERE))
        import build_mac
        source = Path(args.source).resolve()
        ns = argparse.Namespace(source=str(source), version=args.version or build_mac.auto_version(source), repo=REPO, places=None, arch=None)
        sign_and_publish(build_mac.build_update_only(ns))
    elif args.cmd == "sign-publish":
        sign_and_publish(Path(args.zip))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
