"""
build_mac.py -- builds AstroRishi.app, the .dmg and the code-only update zip for macOS. Runs on a Mac (GitHub Actions).

    python3 mac/build_mac.py --source <checkout of the private source repo> --places <places.db> --version 1.2026.1010.700

Nothing in the source repo is modified. Output (in ./out): AstroRishi-Mac-<arch>.dmg and AstroRishi-Mac-Update-<version>.zip
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "out"
WORK = ROOT / "build"

SKIP_DIRS = {"docs", "deploy", "scripts"}
SKIP_NAMES = {
    "run_server.bat", "run_preview.bat", "install_dependencies.bat", "install_dependencies.sh",
    "run_server.sh", "start_v2_local.sh", "start_v2_server.sh", "deploy_gcp.sh", "deploy_mac.sh",
    "promote_gcp.sh", "promote_mac.sh", "cutover_to_v2.sh", "HOSTING.md", "GCP_HOSTING.md", "README.md",
}
EXTRA_PIP = ["pywebview>=5.3", "cryptography>=42"]
DE440_URL = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp"
INFRA_SEGMENTS = {"admin", "consultation", "payment", "api", "saved", "pricing"}
SHARED_APP_KEYS = {"saved", "pricing"}


def say(msg: str) -> None:
    print(f"[build-mac] {msg}", flush=True)


def run(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(list(cmd), cwd=cwd, check=True, capture_output=True, text=True).stdout


def git_bytes(source: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=source, check=True, capture_output=True).stdout


# ---------------------------------------------------------------- app source
def stage_app(source: Path, app: Path, places: Path | None) -> None:
    say("copying the application source ...")
    data = git_bytes(source, "archive", "--format=tar", "HEAD")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar:
            if not m.isfile():
                continue
            parts = Path(m.name).parts
            if parts[0] in SKIP_DIRS or parts[-1] in SKIP_NAMES or parts[-1].endswith(".pyc"):
                continue
            dst = app / m.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as s, open(dst, "wb") as o:
                shutil.copyfileobj(s, o)
    if places and places.is_file():
        (app / "place_database").mkdir(parents=True, exist_ok=True)
        shutil.copy2(places, app / "place_database" / "places.db")
    else:
        say("WARNING: no places.db given -- the app will start without the city database")
    bsp = app / "native_ephemeris" / "data" / "de440s.bsp"
    bsp.parent.mkdir(parents=True, exist_ok=True)
    if not bsp.exists():
        say("downloading the JPL DE440s ephemeris ...")
        urllib.request.urlretrieve(DE440_URL, bsp)


def discover_apps(source: Path) -> list[dict]:
    """Same discovery as the Windows build: the app's own module map + routers (new apps stay locked for limited plans)."""
    main_src = git_bytes(source, "show", "HEAD:main.py").decode("utf-8")
    block = re.search(r"_PATH_MODULE_MAP\s*=\s*sorted\(\[(.*?)\]", main_src, re.S)
    pairs = re.findall(r'\(\s*"(/[a-z0-9\-]+)"\s*,\s*"([a-z0-9_]+)"\s*\)', block.group(1)) if block else []
    labels, order = {}, []
    try:
        ns: dict = {}
        exec(git_bytes(source, "show", "HEAD:core/permissions.py").decode("utf-8"), ns)
        tree = ns["PERMISSION_TREE"]
        labels = {k: v.get("label", k) for k, v in tree.items()}
        order = list(tree)
    except Exception as exc:                                                  # noqa: BLE001
        say(f"(could not read module labels: {exc})")
    by_key = {key: prefix for prefix, key in pairs if key not in SHARED_APP_KEYS}
    apps = [{"key": k, "label": labels.get(k, k.replace("_", " ").title()), "prefix": by_key[k]}
            for k in sorted(by_key, key=lambda k: (order.index(k) if k in order else 999, k))]
    known = {a["prefix"].strip("/") for a in apps} | {p.strip("/") for p, _k in pairs}
    for name in git_bytes(source, "ls-tree", "-r", "--name-only", "HEAD", "routers").decode().split():
        if not name.endswith(".py"):
            continue
        for prefix in re.findall(r'APIRouter\(\s*prefix="(/[^"]+)"', git_bytes(source, "show", f"HEAD:{name}").decode("utf-8")):
            seg = prefix.strip("/").split("/")[0]
            if seg and seg not in known and seg not in INFRA_SEGMENTS:
                apps.append({"key": seg.replace("-", "_"), "label": seg.replace("-", " ").title(), "prefix": "/" + seg})
                known.add(seg)
    return apps


# ---------------------------------------------------------------- python runtime
def stage_python(dest: Path, arch: str) -> None:
    say(f"downloading a relocatable Python for {arch} ...")
    req = urllib.request.Request("https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest",
                                 headers={"User-Agent": "astro-build", "Accept": "application/vnd.github+json",
                                          **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {})})
    release = json.loads(urllib.request.urlopen(req, timeout=60).read())
    triple = "aarch64-apple-darwin" if arch == "arm64" else "x86_64-apple-darwin"
    pattern = re.compile(rf"^cpython-3\.13\.\d+\+\d+-{triple}-install_only\.tar\.gz$")
    asset = next((a for a in release["assets"] if pattern.match(a["name"])), None)
    if not asset:
        raise SystemExit("No Python 3.13 build found for " + triple)
    tgz = WORK / asset["name"]
    urllib.request.urlretrieve(asset["browser_download_url"], tgz)
    with tarfile.open(tgz) as t:
        t.extractall(WORK / "py_extract")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(WORK / "py_extract" / "python"), str(dest))


def install_packages(py: Path, source: Path) -> None:
    exe = str(py / "bin" / "python3")
    say("installing the application's Python packages ...")
    subprocess.run([exe, "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check",
                    "-r", str(source / "requirements.txt"), *EXTRA_PIP], check=True)


# ---------------------------------------------------------------- bundle pieces
def make_icns(source: Path, dest: Path) -> None:
    from PIL import Image
    candidates = [source / "static" / "apple-touch-icon.png", source / "static" / "favicon.ico"]
    src = next((c for c in candidates if c.exists()), None)
    if not src:
        return
    img = Image.open(src).convert("RGBA")
    iconset = WORK / "AstroRishi.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for size in (16, 32, 64, 128, 256, 512):
        img.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
        img.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)


INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Astro Rishi</string>
<key>CFBundleDisplayName</key><string>Astro Rishi</string>
<key>CFBundleIdentifier</key><string>com.astrorishi.desktop</string>
<key>CFBundleExecutable</key><string>AstroRishi</string>
<key>CFBundleIconFile</key><string>AstroRishi</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>{version}</string>
<key>CFBundleVersion</key><string>{version}</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
"""

STARTER = """#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$DIR/python/bin/python3" "$DIR/launcher.py" "$@"
"""

README_DMG = """Install: drag "Astro Rishi" into the Applications folder.

First start: macOS may say the app "cannot be opened because the developer cannot be verified".
Open the Applications folder, RIGHT-CLICK (or Control-click) "Astro Rishi", choose Open, then Open again.
You only need to do this once. If macOS still refuses, open Terminal and run:
    xattr -cr "/Applications/Astro Rishi.app"

On the first start the program shows a Machine ID. Send it to the person who supplied the software;
they will give you a license key for this Mac. After you enter it the program starts without asking again.
"""


def build(args) -> None:
    source = Path(args.source).resolve()
    arch = args.arch or ("arm64" if platform.machine() == "arm64" else "x86_64")
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    OUT.mkdir(exist_ok=True)
    bundle = WORK / "dmg" / "Astro Rishi.app"
    res = bundle / "Contents" / "Resources"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    res.mkdir(parents=True)

    stage_app(source, res / "app", Path(args.places) if args.places else None)
    stage_python(res / "python", arch)
    install_packages(res / "python", source)

    lic = res / "licensing"
    lic.mkdir()
    for f in (ROOT / "mac" / "licensing").iterdir():
        if f.is_file() and f.name not in ("apps.json", "update_config.json"):
            shutil.copy2(f, lic / f.name)
    apps = discover_apps(source)
    (lic / "apps.json").write_text(json.dumps(apps, indent=2), encoding="utf-8")
    (lic / "update_config.json").write_text(json.dumps({"repo": args.repo}), encoding="utf-8")
    say("apps found: " + ", ".join(a["key"] for a in apps))

    shutil.copy2(HERE / "launcher.py", res / "launcher.py")
    (res / "version.txt").write_text(args.version, encoding="utf-8")
    starter = bundle / "Contents" / "MacOS" / "AstroRishi"
    starter.write_text(STARTER, encoding="utf-8")
    starter.chmod(0o755)
    (bundle / "Contents" / "Info.plist").write_text(INFO_PLIST.format(version=args.version), encoding="utf-8")
    try:
        subprocess.run([str(res / "python" / "bin" / "python3"), "-c", "import PIL"], check=True, capture_output=True)
        make_icns_with = str(res / "python" / "bin" / "python3")
    except Exception:
        make_icns_with = ""
    if make_icns_with:                                  # Pillow is a dependency of the app, so it is in the bundled Python
        script = ("import sys; sys.path.insert(0, %r); import build_mac; from pathlib import Path; "
                  "build_mac.make_icns(Path(%r), Path(%r))") % (str(HERE), str(source), str(res / "AstroRishi.icns"))
        subprocess.run([make_icns_with, "-c", script], check=False)

    say("signing (ad-hoc) ...")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=False)

    dmg_dir = WORK / "dmg"
    (dmg_dir / "Applications").symlink_to("/Applications")
    (dmg_dir / "READ ME FIRST.txt").write_text(README_DMG, encoding="utf-8")
    dmg = OUT / f"AstroRishi-Mac-{arch}.dmg"
    dmg.unlink(missing_ok=True)
    say("creating the disk image ...")
    subprocess.run(["hdiutil", "create", "-volname", "Astro Rishi", "-srcfolder", str(dmg_dir), "-ov", "-format", "UDZO", str(dmg)], check=True)

    # code-only update (no Python, no big data files): applied by the program into its working copy
    upd_root = WORK / "update"
    shutil.copytree(res / "app", upd_root / "app", ignore=shutil.ignore_patterns("places.db", "de440s.bsp", "__pycache__"))
    shutil.copytree(lic, upd_root / "licensing")
    (upd_root / "version.txt").write_text(args.version, encoding="utf-8")
    zip_base = OUT / f"AstroRishi-Mac-Update-{args.version}"
    shutil.make_archive(str(zip_base), "zip", upd_root)
    say(f"done: {dmg.name} ({dmg.stat().st_size / 1e6:.0f} MB) and {zip_base.name}.zip")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--places", help="places.db to ship")
    ap.add_argument("--version", required=True)
    ap.add_argument("--repo", default="anilkprajapati/astrology-software-update")
    ap.add_argument("--arch", choices=["arm64", "x86_64"])
    build(ap.parse_args())
