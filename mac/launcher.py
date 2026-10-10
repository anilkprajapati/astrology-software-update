"""
launcher.py -- the Astro Rishi desktop program for macOS (lives in AstroRishi.app/Contents/Resources).

1. Makes a writable working copy of the web app in ~/Library/Application Support/AstroRishi/runtime (the .app itself
   is read-only). Code is refreshed when the app bundle or a downloaded update is newer; the user's data (accounts,
   saved charts, settings, license, place database) is never overwritten.
2. Merges new place-database updates (small signed files from GitHub), opens its own window (WKWebView) with a
   loading screen, starts the web app (through the license gate) on 127.0.0.1 only, and checks for a newer version.
3. Stops the server when the window is closed.
Log: ~/Library/Application Support/AstroRishi/logs/server.log
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent                     # .../AstroRishi.app/Contents/Resources
DATA_DIR = Path.home() / "Library" / "Application Support" / "AstroRishi"
RUNTIME = DATA_DIR / "runtime"
APP_DIR = RUNTIME / "app"
LIC_DIR = RUNTIME / "licensing"
LOG_DIR = DATA_DIR / "logs"
STATE_FILE = DATA_DIR / "running.json"
WEBVIEW_DIR = DATA_DIR / "webview"
PREFERRED_PORT = 8765
TITLE = "Astro Rishi"

# Files that belong to the user: copied only when missing, never overwritten by a code refresh.
KEEP_IF_EXISTS = ("place_database/places.db", "native_ephemeris/data/")
KEEP_SUFFIX_IN = ("users_database/",)                        # users_database/*.json = accounts and settings


# ---------------------------------------------------------------- small helpers
def _osa(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def message(text: str, title: str = TITLE, buttons: tuple = ("OK",)) -> str:
    """A native dialog. Returns the label of the button pressed ('' if closed)."""
    btn = ", ".join(f'"{_osa(b)}"' for b in buttons)
    script = (f'display dialog "{_osa(text)}" with title "{_osa(title)}" buttons {{{btn}}} '
              f'default button "{_osa(buttons[-1])}"')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.split("button returned:")[-1].strip() if r.returncode == 0 else ""


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port() -> int:
    for p in range(PREFERRED_PORT, PREFERRED_PORT + 40):
        if port_free(p):
            return p
    raise RuntimeError("No free local port found.")


def answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__license", timeout=2):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:
        return False


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def version_of(root: Path) -> tuple:
    try:
        return tuple(int(x) for x in (root / "version.txt").read_text(encoding="utf-8").strip().split("."))
    except Exception:
        return (0,)


# ---------------------------------------------------------------- working copy of the app
def sync_tree(src: Path, dst: Path) -> None:
    """Copies src over dst, leaving the user's data alone (see KEEP_*)."""
    for path in src.rglob("*"):
        if path.is_dir() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(src).as_posix()
        target = dst / rel
        exists = target.exists()
        if exists and (rel in KEEP_IF_EXISTS[:1] or any(rel.startswith(k) for k in KEEP_IF_EXISTS[1:])):
            continue
        if exists and rel.endswith(".json") and any(rel.startswith(k) for k in KEEP_SUFFIX_IN):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def prepare_runtime(progress=None) -> None:
    """Brings the working copy up to the bundle's version (first run, or after the app was replaced by a newer one)."""
    if version_of(BUNDLE) <= version_of(RUNTIME) and APP_DIR.exists():
        return                                              # already current (or a newer update was applied)
    if progress:
        progress("Preparing Astro Rishi (first start takes a minute)...")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    sync_tree(BUNDLE / "app", APP_DIR)
    LIC_DIR.mkdir(parents=True, exist_ok=True)
    for f in (BUNDLE / "licensing").iterdir():
        if f.is_file():
            shutil.copy2(f, LIC_DIR / f.name)
    shutil.copy2(BUNDLE / "version.txt", RUNTIME / "version.txt")
    for rel in ("saved_charts_database/saved_charts", "saved_charts_database/saved_horary", "saved_charts_database/saved_matchmaking",
                "saved_charts_database/saved_numerology", "saved_charts_database/saved_vedic_cards", "saved_charts_database/user_dbs",
                "users_database/vastu_saved_plots", "users_database/dictionary_uploads", "users_database/manual_payment_uploads",
                "static/uploads", ".jinja_cache"):
        (APP_DIR / rel).mkdir(parents=True, exist_ok=True)


def python_exe() -> str:
    return str(BUNDLE / "python" / "bin" / "python3")


def start_server(port: int) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / "server.log", "ab")
    env = dict(os.environ)
    env.update({
        "COOKIE_SECURE": "false", "TEMPLATE_AUTO_RELOAD": "false", "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(APP_DIR), str(LIC_DIR)]),
    })
    return subprocess.Popen(
        [python_exe(), "-m", "uvicorn", "licensed_server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(APP_DIR), env=env, stdout=log, stderr=log)


def stop(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------- the loading screen and page helper
SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#f4f1ec;color:#23201c;font:16px -apple-system,system-ui,sans-serif}
@media (prefers-color-scheme:dark){html,body{background:#1c1a17;color:#f1ece4}}
body{display:grid;place-items:center;text-align:center}
.om{font-size:64px;line-height:1;color:#8a4b14}.t{font-size:22px;font-weight:600;margin:.4rem 0 .2rem}
#msg{opacity:.75;min-height:1.5em}.bar{width:260px;height:6px;background:#8a4b1433;border-radius:3px;margin:14px auto 0;overflow:hidden}
.bar i{display:block;height:100%;width:30%;background:#8a4b14;border-radius:3px;animation:s 1.2s ease-in-out infinite}
@keyframes s{0%{margin-left:-30%}100%{margin-left:100%}}
</style></head><body><div><div class="om">&#x1F549;</div><div class="t">Astro Rishi</div>
<div id="msg">Starting...</div><div class="bar"><i></i></div></div>
<script>function setMsg(t){document.getElementById('msg').textContent=t;}</script></body></html>"""

PAGE_SHIM = r"""
(function () {
  if (window.__astroShim) return;
  window.__astroShim = true;
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || f.tagName !== 'FORM' || (f.target || '').toLowerCase() !== '_blank') return;
    e.preventDefault();
    var method = (f.method || 'get').toUpperCase();
    var data = new FormData(f);
    if (e.submitter && e.submitter.name) data.append(e.submitter.name, e.submitter.value);
    var url = f.action, init = {method: method, credentials: 'same-origin'};
    if (method === 'GET') { url += (url.indexOf('?') < 0 ? '?' : '&') + new URLSearchParams(data).toString(); }
    else { init.body = (f.enctype === 'multipart/form-data') ? data : new URLSearchParams(data); }
    fetch(url, init).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { alert(t || ('Could not create the file (' + r.status + ').')); });
      var disp = r.headers.get('content-disposition') || '';
      var m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(disp);
      var type = r.headers.get('content-type') || '';
      var name = m ? decodeURIComponent(m[1]) : (type.indexOf('pdf') >= 0 ? 'report.pdf' : 'download');
      if (type.indexOf('html') >= 0) {
        return r.text().then(function (t) { document.open(); document.write(t); document.close(); });
      }
      return r.blob().then(function (b) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(b); a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
      });
    }).catch(function (err) { alert('Could not create the file: ' + err); });
  }, true);
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[target="_blank"]');
    if (a && a.href && a.origin === location.origin) { e.preventDefault(); location.href = a.href; }
  }, true);
})();
"""


def _safe_eval(window, script: str) -> None:
    try:
        window.evaluate_js(script)
    except Exception:
        pass


# ---------------------------------------------------------------- updates (code only; new Python packages need a new .dmg)
def offer_update(info: dict, window) -> bool:
    """Asks, downloads (signature-checked) and applies a code update. True = applied, the program must restart."""
    import updater
    notes = (info.get("notes") or "").strip()[:500]
    question = (f"Astro Rishi {info['version']} is available (you have {updater.installed_version()}).\n\n"
                + (notes + "\n\n" if notes else "") + "Install it now? The program restarts by itself.")
    if message(question, "Astro Rishi update", ("Later", "Install now")) != "Install now":
        return False
    _safe_eval(window, "setMsg('Downloading the update...')")
    path = updater.download(info, lambda d, t: _safe_eval(
        window, f"setMsg('Downloading the update... {d / 1048576:.0f} MB')"))
    if not path:
        message("The update could not be downloaded or did not pass the security check.\n\nAstro Rishi will start normally.")
        return False
    _safe_eval(window, "setMsg('Installing the update...')")
    work = DATA_DIR / "update_work"
    shutil.rmtree(work, ignore_errors=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(work)
    sync_tree(work / "app", APP_DIR)
    for f in (work / "licensing").iterdir():
        if f.is_file():
            shutil.copy2(f, LIC_DIR / f.name)
    shutil.copy2(work / "version.txt", RUNTIME / "version.txt")
    shutil.rmtree(work, ignore_errors=True)
    path.unlink(missing_ok=True)
    return True


def run_in_window(existing_port: int | None) -> None:
    import webview
    WEBVIEW_DIR.mkdir(parents=True, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(TITLE, html=SPLASH, width=1366, height=860, min_size=(900, 600), text_select=True, maximized=True)
    window.events.loaded += lambda: _safe_eval(window, PAGE_SHIM)
    box: dict = {"proc": None, "restart": False}

    def boot() -> None:
        try:
            if existing_port:
                window.load_url(f"http://127.0.0.1:{existing_port}/")
                return
            prepare_runtime(lambda t: _safe_eval(window, f"setMsg({json.dumps(t)})"))
            sys.path.insert(0, str(LIC_DIR))
            try:
                import placedata
                placedata.run(lambda t: _safe_eval(window, f"setMsg({json.dumps(t)})"))
            except Exception:
                pass
            port = pick_port()
            box["proc"] = start_server(port)
            STATE_FILE.write_text(json.dumps({"port": port, "pid": box["proc"].pid}), encoding="utf-8")
            update = {}
            try:
                import updater
                t = threading.Thread(target=lambda: update.setdefault("info", updater.find_update()), daemon=True)
                t.start()
            except Exception:
                t = None
            deadline = time.time() + 180
            while time.time() < deadline and box["proc"].poll() is None and not answers(port):
                time.sleep(0.4)
            if box["proc"].poll() is not None or not answers(port):
                message("Astro Rishi could not start.\n\nDetails are in:\n" + str(LOG_DIR / "server.log"))
                window.destroy()
                return
            if t:
                t.join(6)
            if update.get("info") and offer_update(update["info"], window):
                box["restart"] = True
                window.destroy()
                return
            window.load_url(f"http://127.0.0.1:{port}/")
        except Exception as exc:                            # noqa: BLE001
            message(f"Astro Rishi could not start:\n\n{exc}")
            window.destroy()

    webview.start(boot, storage_path=str(WEBVIEW_DIR), private_mode=False)
    stop(box["proc"])
    if box["proc"]:
        try:
            STATE_FILE.unlink()
        except OSError:
            pass
    if box["restart"]:
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = read_state()
    existing = state.get("port") if state.get("port") and answers(state["port"]) else None
    run_in_window(existing)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:                                # noqa: BLE001
        message(f"Astro Rishi hit an error:\n\n{exc}")
