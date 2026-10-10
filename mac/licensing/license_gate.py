"""
license_gate.py -- one-PC licensing and plan permissions for the installed Astro Rishi.

Sits IN FRONT of the web app (a thin ASGI wrapper, see licensed_server.py); the app's own code is not touched.
A PC may use the app when it holds a LICENSE issued for this exact PC. (There is no password login in the shared program; the owner uses an Admin-plan key.)
A license is an Ed25519-signed token that says
which PLAN (Trial / Purchased / Master / Admin) and which APPS are included, and when it expires. Pages of an
app that is not included are blocked here -- not in the app's own settings -- so a customer cannot switch them
back on from the app's Admin page.

Honest limits: this keeps an honest user from passing the installer or install folder to other people or using
apps they did not buy. Someone with programming skills who edits the Python files in the install folder can
still remove it -- no purely offline scheme can fully prevent that.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

import plans
import platform_paths

HERE = Path(__file__).resolve().parent
DATA_DIR = platform_paths.data_dir(HERE)
LICENSE_FILE = DATA_DIR / "license.dat"
PUBLIC_KEY_FILE = HERE / "license_public.pem"

_PREFIX = "ASTRO"


def app_version() -> str:
    """The short version people see ("v0.1"). Set by the owner in display_version.txt (shipped next to this file)."""
    try:
        text = (HERE / "display_version.txt").read_text(encoding="utf-8").strip()
        return "v" + text.lstrip("vV") if text else ""
    except OSError:
        return ""


# ---------------------------------------------------------------- machine identity
def machine_id() -> str:
    """A stable id for THIS PC, shown to the user as XXXXX-XXXXX-XXXXX-XXXXX (20 hex digits)."""
    guid = ""
    try:
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=10).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            guid = m.group(1) if m else ""
        else:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                guid = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        pass
    if not guid:
        import uuid
        guid = f"fallback-{uuid.getnode()}"
    digest = hashlib.sha256(f"AstroRishi|{guid}".encode("utf-8")).hexdigest()[:20].upper()
    return "-".join(digest[i:i + 5] for i in range(0, 20, 5))


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- what a key entitles this PC to
@dataclass
class Entitlement:
    plan: str = "master"
    name: str = ""
    apps: set = field(default_factory=lambda: set(plans.APP_KEYS))
    admin_panel: bool = True
    expires: dt.date | None = None
    perm_deny: frozenset = frozenset()      # "module/tab" and "module/tab/element" the key switches OFF

    @property
    def plan_label(self) -> str:
        return plans.PLANS.get(self.plan, self.plan.title())

    def days_left(self) -> int | None:
        return None if self.expires is None else (self.expires - dt.date.today()).days


def _entitlement_from_payload(payload: dict) -> Entitlement:
    plan = payload.get("plan") or "master"           # keys made before plans existed keep full access
    mods = payload.get("modules")
    apps = set(plans.APP_KEYS) if (mods is None or plan in plans.ALL_APPS_PLANS) else {m for m in mods if m in plans.APP_KEYS}
    admin_panel = payload.get("admin_panel")
    if admin_panel is None:
        admin_panel = plan in plans.ADMIN_PANEL_PLANS
    expires = None
    if payload.get("expires"):
        try:
            expires = dt.date.fromisoformat(payload["expires"])
        except ValueError:
            pass
    deny = frozenset() if plan in plans.ALL_APPS_PLANS else frozenset(str(x) for x in (payload.get("perm_deny") or []))
    return Entitlement(plan=plan, name=payload.get("name") or "", apps=apps, admin_panel=bool(admin_panel),
                       expires=expires, perm_deny=deny)


# ---------------------------------------------------------------- license check
def verify_license_text(text: str, mid: str | None = None) -> tuple[bool, str, dict]:
    """(ok, message, payload). Never raises."""
    mid = mid or machine_id()
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        text = re.sub(r"\s+", "", text or "")
        if text.upper().startswith(_PREFIX + "-"):
            text = text[len(_PREFIX) + 1:]
        payload_b64, sig_b64 = text.split(".", 1)
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_FILE.read_bytes())
        try:
            public_key.verify(_b64d(sig_b64), payload_b64.encode("ascii"))
        except InvalidSignature:
            return False, "That license key is not valid.", {}
        payload = json.loads(_b64d(payload_b64))
    except Exception:
        return False, "That does not look like a license key.", {}
    if payload.get("machine") != mid:
        return False, "That license key was issued for a different computer.", payload
    expires = payload.get("expires")
    if expires:
        try:
            if dt.date.fromisoformat(expires) < dt.date.today():
                return False, f"That license expired on {expires}.", payload
        except ValueError:
            return False, "That license key is damaged.", payload
    return True, "ok", payload


_cache: dict = {"until": 0.0, "ent": None, "why": ""}


def entitlement() -> tuple[Entitlement | None, str]:
    """(entitlement or None, reason an installed license is not usable, '' if there is none). Cached briefly."""
    now = time.time()
    if now < _cache["until"]:
        return _cache["ent"], _cache["why"]
    mid = machine_id()
    ent, why = None, ""
    if ent is None and LICENSE_FILE.exists():
        good, msg, payload = verify_license_text(LICENSE_FILE.read_text(encoding="utf-8"), mid)
        if good:
            ent = _entitlement_from_payload(payload)
        else:
            why = msg
    _cache.update(until=now + 5, ent=ent, why=why)
    return ent, why


def licensed() -> tuple[bool, str]:
    ent, _why = entitlement()
    return (ent is not None), (ent.name if ent else "")


def _store(path: Path, text: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _cache["until"] = 0.0


# ---------------------------------------------------------------- pages
_CSS = """:root{--bg:#f4f1ec;--card:#fff;--ink:#23201c;--muted:#6f675d;--accent:#8a4b14;--line:#e1d9cd;--ok:#2e7d32;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#1c1a17;--card:#272420;--ink:#f1ece4;--muted:#a79d8f;--accent:#e0a35e;--line:#3a352f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 "Segoe UI",system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;padding:1.2rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1.6rem 1.8rem;max-width:580px;width:100%}
h1{margin:0 0 .2rem;font-size:1.4rem}p{margin:.4rem 0}.muted{color:var(--muted);font-size:.9rem}
.mid{font:600 1.35rem/1.3 Consolas,monospace;letter-spacing:.06em;background:var(--bg);border:1px dashed var(--line);border-radius:10px;padding:.7rem 1rem;margin:.5rem 0;display:flex;justify-content:space-between;gap:.6rem;align-items:center}
textarea,input{width:100%;padding:.6rem .7rem;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:5.2rem;font:.82rem Consolas,monospace}
button,a.btn{cursor:pointer;border:0;border-radius:8px;padding:.55rem 1.1rem;font:600 .95rem inherit;background:var(--accent);color:#fff;text-decoration:none;display:inline-block}
button.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent);padding:.3rem .8rem}
.msg{border-radius:8px;padding:.5rem .8rem;margin:.6rem 0;font-size:.92rem}.msg.bad{background:#b3261e22;color:var(--bad)}.msg.ok{background:#2e7d3222;color:var(--ok)}
hr{border:0;border-top:1px solid var(--line);margin:1.2rem 0}details summary{cursor:pointer;color:var(--muted);font-size:.9rem}
.apps{display:flex;flex-wrap:wrap;gap:.35rem;margin:.4rem 0}.apps span{background:var(--bg);border:1px solid var(--line);border-radius:999px;padding:.1rem .65rem;font-size:.85rem}
.apps span.off{opacity:.45;text-decoration:line-through}"""

_ACTIVATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>__CSS__</style></head><body><main class="card">
<h1>__HEADING__</h1>
__BODY__
</main></body></html>"""


def _mid_block() -> str:
    return ('<p class="muted" style="margin-top:.8rem">This computer\'s Machine ID</p>'
            f'<div class="mid"><span id="mid">{html.escape(machine_id())}</span>'
            '<button type="button" class="ghost" onclick="navigator.clipboard.writeText('
            "document.getElementById('mid').textContent);this.textContent='Copied'\">Copy</button></div>")


def _msg(message: str, kind: str) -> str:
    return f'<div class="msg {kind}">{html.escape(message)}</div>' if message else ""


def _key_form(button: str = "Activate") -> str:
    return ('<form method="post" action="/__license/activate" style="margin-top:.9rem">'
            '<label for="key" class="muted">License key</label>'
            '<textarea id="key" name="key" placeholder="Paste the license key here" required></textarea>'
            f'<p><button type="submit">{button}</button></p></form>')


def render_page(message: str = "", kind: str = "bad") -> bytes:
    """Activation screen for a PC with no usable license."""
    body = (
        '<p class="muted">This copy of the software is licensed for one computer. Send the Machine ID below to the '
        'person who supplied the software; they will give you a license key for this PC.</p>'
        + _mid_block() + _msg(message, kind) + _key_form()
        + f'<p class="muted" style="margin-top:1rem">Astro Rishi {html.escape(app_version())}</p>'
    )
    return _ACTIVATE.replace("__TITLE__", "Activate Astro Rishi").replace("__HEADING__", "Activate Astro Rishi") \
        .replace("__CSS__", _CSS).replace("__BODY__", body).encode("utf-8")


def _apps_chips(ent: Entitlement) -> str:
    chips = "".join(f'<span class="{"" if k in ent.apps else "off"}">{html.escape(label)}</span>'
                    for k, label, _p in plans.APPS)
    if ent.admin_panel:
        chips += "<span>Admin panel</span>"
    return f'<div class="apps">{chips}</div>'


def render_license_page(ent: Entitlement, message: str = "", kind: str = "ok") -> bytes:
    """Shown to a licensed PC: what the plan includes + a box for a new key (upgrade / renewal)."""
    days = ent.days_left()
    expiry = "no expiry" if ent.expires is None else f"{ent.expires:%d %b %Y} ({days} day{'s' if days != 1 else ''} left)"
    body = (
        _msg(message, kind)
        + f'<p class="muted">Plan</p><p><b>{html.escape(ent.plan_label)}</b>'
        + (f' &middot; {html.escape(ent.name)}' if ent.name else "") + f' &middot; {html.escape(expiry)}</p>'
        + '<p class="muted" style="margin-top:.8rem">Apps included (struck-through apps are not in your plan)</p>'
        + _apps_chips(ent) + _mid_block()
        + '<hr><details><summary>Enter a new license key (renewal or more apps)</summary>' + _key_form("Apply key")
        + '</details>'
        + f'<p class="muted" style="margin-top:1rem">Astro Rishi {html.escape(app_version())}</p>'
        + '<p style="margin-top:1rem"><a class="btn" href="/">Back to Astro Rishi</a></p>')
    return _ACTIVATE.replace("__TITLE__", "License").replace("__HEADING__", "Your license") \
        .replace("__CSS__", _CSS).replace("__BODY__", body).encode("utf-8")


def render_blocked(ent: Entitlement, what: str) -> bytes:
    body = (f'<p><b>{html.escape(what)}</b> is not included in your <b>{html.escape(ent.plan_label)}</b> plan.</p>'
            '<p class="muted">To add it, ask the person who supplied the software for an updated license key, '
            'then enter it on the License page.</p>' + _apps_chips(ent)
            + '<p style="margin-top:1rem"><a class="btn" href="/">Back</a> '
            '<a class="btn" style="background:transparent;color:var(--accent);border:1px solid var(--accent)" '
            'href="/__license">License</a></p>')
    return _ACTIVATE.replace("__TITLE__", "Not in your plan").replace("__HEADING__", "Not in your plan") \
        .replace("__CSS__", _CSS).replace("__BODY__", body).encode("utf-8")


def _injection(ent: Entitlement) -> bytes:
    """Added to every page: hides tiles/links of apps that are not in the plan and shows a small plan badge."""
    blocked = [p for k, _l, p in plans.APPS if k not in ent.apps]
    if not ent.admin_panel:
        blocked.append("/admin")
    days = ent.days_left()
    badge = ent.plan_label + (f" · {app_version()}" if app_version() else "")
    if days is not None and (ent.plan == "trial" or days <= 30):
        badge += f" · {max(days, 0)} day{'s' if days != 1 else ''} left"
    # No sign-in in the desktop program: the "admin v" account menu (Profile / Logout) and the new-sign-ups bell are hidden;
    # a plain Admin link takes their place when the key includes the Admin panel.
    admin_link = ""
    if ent.admin_panel:
        admin_link = ("<script>(function(){var a=document.querySelector('header .account');if(!a)return;"
                      "var l=document.createElement('a');l.href='/admin';l.textContent='Admin';l.className='btn secondary';"
                      "l.style.cssText='padding:0.3rem 0.8rem;margin-left:0.4rem;';a.appendChild(l);})();</script>")
    hide_login = ("<style>a[href=\"/login\"],a[href=\"/signup\"],a[href=\"/forgot-password\"],form[action=\"/logout\"],"
                  "#account-dropdown,#new-signups-dropdown{display:none!important}</style>" + admin_link)
    script = ""
    if blocked:
        script = ("<script>(function(){var b=" + json.dumps(blocked) + ";document.querySelectorAll('a[href]').forEach(function(a){"
                  "var h=a.getAttribute('href')||'';if(b.some(function(p){return h===p||h.indexOf(p+'/')===0||h.indexOf(p+'?')===0;})){"
                  "var t=a.closest('.tile')||a.closest('li')||a;t.style.display='none';}});})();</script>")
    pill = ('<a href="/__license" style="position:fixed;left:10px;bottom:8px;z-index:9998;font:600 12px/1 \'Segoe UI\',sans-serif;'
            'background:#8a4b14;color:#fff;border-radius:999px;padding:5px 11px;text-decoration:none;opacity:.85" '
            f'title="Your license">{html.escape(badge)}</a>')
    return (hide_login + pill + script).encode("utf-8")


# ---------------------------------------------------------------- feature permissions + automatic local sign-in
def feature_allowed(module: str, tab: str | None = None, element: str | None = None) -> bool:
    """False when the license key switched this tab / element OFF (the Key Maker's permission window)."""
    ent, _why = entitlement()
    if ent is None or not ent.perm_deny:
        return True
    if tab is None:
        return True
    if f"{module}/{tab}" in ent.perm_deny:
        return False
    return element is None or f"{module}/{tab}/{element}" not in ent.perm_deny


def install_runtime_patches() -> None:
    """Called by licensed_server.py BEFORE the original app is imported: the app's own has_tab / has_element / get_current_user
    are wrapped (the app's files are not touched). Anything the key switched off stays off -- even for the local admin account --
    and the signed-in account only has admin rights when the key includes the Admin panel."""
    import core.paths  # noqa: F401 -- puts the app's folders on sys.path
    import core.auth as auth
    import core.permissions as perms

    original_tab, original_element = perms.has_tab, perms.has_element
    perms.has_tab = lambda user, is_admin, module, tab: (
        feature_allowed(module, tab) and original_tab(user, is_admin, module, tab))
    perms.has_element = lambda user, is_admin, module, tab, element: (
        feature_allowed(module, tab, element) and original_element(user, is_admin, module, tab, element))

    original_user = auth.get_current_user

    def licensed_user(request):
        user = original_user(request)
        if user is None:
            return None
        ent, _why = entitlement()
        user = dict(user)
        user.update(is_admin=bool(ent and ent.admin_panel), is_master=True, approved=True, disabled=False,
                    paid_until=None, must_change_password=False)
        return user
    auth.get_current_user = licensed_user


_LOCAL_ACCOUNT = "admin"
_NO_LOGIN_PAGES = ("/login", "/signup", "/forgot-password", "/approval-status")


def _session_cookie(headers) -> str:
    for k, v in headers:
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "session_token":
                    return value
    return ""


def _ensure_local_session(scope) -> str | None:
    """No sign-in screen: a PC with a license is always signed in as its one local account. Returns a Set-Cookie value when a
    new session had to be created (the request's own Cookie header is patched too, so this very request is signed in)."""
    try:
        import users_db
        if users_db.verify_session(_session_cookie(scope.get("headers", []))):
            return None
        if users_db.get_user(_LOCAL_ACCOUNT) is None:
            users_db.create_user(_LOCAL_ACCOUNT, os.urandom(12).hex(), is_admin=True, must_change_password=False, approved=True)
        token = users_db.create_session(_LOCAL_ACCOUNT, is_admin=True, is_master=True, user_agent="desktop", ip="127.0.0.1")
    except Exception:
        return None
    cookie = f"session_token={token}"
    scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k != b"cookie"] + [(b"cookie", cookie.encode("latin-1"))]
    return f"{cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"


# ---------------------------------------------------------------- ASGI wrapper


class LicenseGate:
    """ASGI middleware: unlicensed PCs only reach the activation page; licensed PCs reach the apps their plan includes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "/")
        ent, why = entitlement()
        if path.startswith("/__license"):
            await self._license_routes(scope, receive, send, path, ent, why)
            return
        if ent is None:
            if path.startswith(("/static/", "/favicon", "/apple-touch-icon")):
                await self.app(scope, receive, send)
            elif scope["method"] == "GET":
                await self._send(send, 303, b"", [(b"location", b"/__license")])
            else:
                await self._send(send, 403, b"This copy is not activated.", [(b"content-type", b"text/plain")])
            return

        if path in _NO_LOGIN_PAGES or path.startswith(("/signup/", "/forgot-password/")) or path == "/logout":
            await self._send(send, 303, b"", [(b"location", b"/")])
            return
        key = plans.app_for_path(path)
        if key and key not in ent.apps:
            await self._blocked(scope, send, ent, plans.APP_LABEL.get(key, key))
            return
        if plans.is_admin_path(path) and not ent.admin_panel:
            await self._blocked(scope, send, ent, "The Admin panel")
            return
        scope = dict(scope)
        set_cookie = _ensure_local_session(scope)
        if set_cookie:
            inner_send = send

            async def send(message, _inner=inner_send):                       # noqa: F811
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = list(message.get("headers", [])) + [(b"set-cookie", set_cookie.encode("latin-1"))]
                await _inner(message)
        await self._run_app(scope, receive, send, ent)

    # -- pass a request to the app, adding the plan badge / hiding blocked links on HTML pages
    async def _run_app(self, scope, receive, send, ent):
        wants_html = scope["method"] == "GET" and any(
            k == b"accept" and b"text/html" in v for k, v in scope.get("headers", []))
        if not wants_html:
            await self.app(scope, receive, send)
            return
        # Ask the app for an uncompressed page so the badge can be added to the HTML.
        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k != b"accept-encoding"]
        state = {"html": False, "start": None, "body": b""}

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                ctype = headers.get(b"content-type", b"")
                if message["status"] == 200 and b"text/html" in ctype and b"content-encoding" not in headers:
                    state["html"], state["start"] = True, message
                    return
                await send(message)
            elif message["type"] == "http.response.body" and state["html"]:
                state["body"] += message.get("body", b"")
                if message.get("more_body"):
                    return
                body = state["body"]
                add = _injection(ent)
                idx = body.lower().rfind(b"</body>")
                body = body[:idx] + add + body[idx:] if idx >= 0 else body + add
                start = dict(state["start"])
                start["headers"] = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"] \
                    + [(b"content-length", str(len(body)).encode())]
                await send(start)
                await send({"type": "http.response.body", "body": body})
            else:
                await send(message)

        await self.app(scope, receive, wrapped)

    async def _blocked(self, scope, send, ent, what):
        dest = dict(scope.get("headers", [])).get(b"sec-fetch-dest", b"document")
        if dest in (b"document", b"iframe", b"frame") and scope["method"] == "GET":
            await self._html(send, render_blocked(ent, what), 403)
        else:
            snippet = f'<div class="card"><p class="error">{html.escape(what)} is not included in your plan.</p></div>'
            await self._html(send, snippet.encode("utf-8"), 403)

    async def _license_routes(self, scope, receive, send, path, ent, why):
        if path == "/__license/activate" and scope["method"] == "POST":
            form = await self._form(receive)
            good, msg, _payload = verify_license_text((form.get("key") or [""])[0])
            if good:
                _store(LICENSE_FILE, re.sub(r"\s+", "", form["key"][0]))
                new_ent, _ = entitlement()
                await self._html(send, render_license_page(new_ent, "License applied. Your plan is active.", "ok"))
            else:
                page = render_license_page(ent, msg, "bad") if ent else render_page(msg)
                await self._html(send, page, 400)
            return
        if ent is not None:
            await self._html(send, render_license_page(ent, "", "ok"))
            return
        await self._html(send, render_page(why or "", "bad") if why else render_page())

    @staticmethod
    async def _form(receive) -> dict:
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        return parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)

    @staticmethod
    async def _send(send, status: int, body: bytes, headers: list) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": headers + [(b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def _html(self, send, body: bytes, status: int = 200) -> None:
        await self._send(send, status, body, [(b"content-type", b"text/html; charset=utf-8")])
