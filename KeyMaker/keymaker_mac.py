"""
keymaker_mac.py -- Astro Rishi license key maker for the Mac version (OWNER ONLY -- never share this folder's keys/).

Independent of the Windows tools: it has its OWN signing key pair, created the first time it runs in keys/. Its public
half is copied to ../mac/licensing/license_public.pem -- the Mac app trusts only that key, so rebuild and push the Mac
project after the very first run.

    python3 keymaker_mac.py                    the window (paste the Machine ID, choose plan + apps, Generate key)
    python3 keymaker_mac.py issue --machine ABCDE-12345-FGHIJ-67890 --name "Ravi" --plan purchased --apps nadi,numerology --days 365
    python3 keymaker_mac.py machine-id         this Mac's Machine ID
    python3 keymaker_mac.py release AstroRishi-Mac-Update-<version>.zip     sign + publish a code update to GitHub
Needs:  pip3 install cryptography requests   (the window also needs tkinter: python.org's Python includes it)
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = HERE / "keys"
PRIVATE = KEYS / "license_private.pem"
PUBLIC = KEYS / "license_public.pem"
TOKEN = KEYS / "github_token.txt"
HISTORY = KEYS / "issued_keys.jsonl"
MAC_PROJECT_PUBLIC = HERE.parent / "mac" / "licensing" / "license_public.pem"
REPO = "anilkprajapati/astrology-software-update"

PLANS = {"trial": "Trial", "purchased": "Purchased", "master": "Master", "admin": "Admin"}
ALL_APPS_PLANS = {"admin"}
ADMIN_PANEL_PLANS = {"master", "admin"}
_FALLBACK = [("nadi", "Nadi Astrology"), ("nadi_numero", "Num + Nadi"), ("numerology", "Numerology"),
             ("horary", "Horary / Prashna"), ("matchmaking", "Matchmaking"), ("parashari", "Parashari"),
             ("jaimini", "Jaimini"), ("vedic_cards", "Vedic Cards"), ("vastu", "Vastu"), ("hindu_calendar", "Panchang"),
             ("mobile_numerology", "Mobile Numerology"), ("name_correction", "Name Correction")]


def load_apps() -> list[tuple[str, str]]:
    """The apps the Mac build found in your source (written to mac/licensing/apps.json by the build/sync)."""
    for f in (HERE / "apps.json", HERE.parent / "mac" / "licensing" / "apps.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data:
                return [(d["key"], d["label"]) for d in data]
        except Exception:
            continue
    return _FALLBACK


APPS = load_apps()
APP_KEYS = [k for k, _ in APPS]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def normalize_machine_id(text: str) -> str | None:
    digits = re.sub(r"[^0-9A-Fa-f]", "", text or "").upper()
    return "-".join(digits[i:i + 5] for i in range(0, 20, 5)) if len(digits) == 20 else None


def own_machine_id() -> str:
    guid = ""
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True).stdout
        m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
        guid = m.group(1) if m else ""
    except Exception:
        pass
    if not guid:
        import uuid
        guid = f"fallback-{uuid.getnode()}"
    digest = hashlib.sha256(f"AstroRishi|{guid}".encode()).hexdigest()[:20].upper()
    return "-".join(digest[i:i + 5] for i in range(0, 20, 5))


def keygen() -> bool:
    """Creates this tool's own key pair on first use. True if a new one was made."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if PRIVATE.exists():
        return False
    KEYS.mkdir(exist_ok=True)
    key = Ed25519PrivateKey.generate()
    PRIVATE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    PRIVATE.chmod(0o600)
    PUBLIC.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return True


def publish_public_key() -> None:
    """Puts this tool's public key where the Mac build picks it up."""
    if MAC_PROJECT_PUBLIC.parent.exists():
        shutil.copy2(PUBLIC, MAC_PROJECT_PUBLIC)


def issue(machine: str, name: str, days: int | None, plan: str = "master", apps: list[str] | None = None,
          admin_panel: bool | None = None) -> str:
    from cryptography.hazmat.primitives import serialization
    if keygen():
        publish_public_key()
    machine = normalize_machine_id(machine) or ""
    if not machine:
        raise SystemExit("A Machine ID looks like ABCDE-12345-FGHIJ-67890.")
    if plan not in PLANS:
        raise SystemExit("Plan must be one of: " + ", ".join(PLANS))
    if plan in ALL_APPS_PLANS:
        modules = None
    else:
        modules = [a for a in (apps or []) if a in APP_KEYS]
        if plan == "master" and apps is None:
            modules = list(APP_KEYS)
        if not modules:
            raise SystemExit("Tick at least one app for this license.")
        if plan == "master" and set(modules) == set(APP_KEYS):
            modules = None                     # Master with every app = "all apps", future apps included
    if admin_panel is None:
        admin_panel = plan in ADMIN_PANEL_PLANS
    payload = {"v": 2, "machine": machine, "name": name, "plan": plan, "modules": modules, "admin_panel": bool(admin_panel),
               "issued": dt.date.today().isoformat(),
               "expires": (dt.date.today() + dt.timedelta(days=days)).isoformat() if days else None}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    private = serialization.load_pem_private_key(PRIVATE.read_bytes(), password=None)
    key = f"ASTRO-{body}.{_b64(private.sign(body.encode('ascii')))}"
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"issued": f"{dt.datetime.now():%Y-%m-%d %H:%M}", "machine": machine, "name": name, "plan": plan,
                            "modules": modules, "admin_panel": bool(admin_panel), "expires": payload["expires"], "key": key}) + "\n")
    return key


def read_history() -> list[dict]:
    rows = []
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def release(zip_path: str) -> None:
    """Signs a code update (context 'astro-update:') and publishes it as pre-release mac-v<version>."""
    import mimetypes
    import requests
    from cryptography.hazmat.primitives import serialization
    z = Path(zip_path)
    m = re.fullmatch(r"AstroRishi-Mac-Update-(\d+(?:\.\d+)+)\.zip", z.name)
    if not m:
        raise SystemExit("File name must look like AstroRishi-Mac-Update-1.2026.1010.700.zip")
    if not TOKEN.exists():
        raise SystemExit(f"Save your GitHub token (Contents: read+write on {REPO}) in {TOKEN} first.")
    version = m.group(1)
    private = serialization.load_pem_private_key(PRIVATE.read_bytes(), password=None)
    sig = z.with_name(z.name + ".sig")
    sig.write_text(base64.b64encode(private.sign(b"astro-update:" + hashlib.sha256(z.read_bytes()).digest())).decode(), encoding="ascii")
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
    release_info = r.json()
    for path in (z, sig):
        with open(path, "rb") as f:
            up = requests.post(release_info["upload_url"].split("{")[0], params={"name": path.name}, data=f, timeout=900,
                               headers={**headers, "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
        up.raise_for_status()
        print("uploaded", path.name)
    print("published", tag)


# ---------------------------------------------------------------- the window
def gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    validity = ["No expiry", "7 days", "14 days", "30 days", "90 days", "180 days", "1 year (365 days)",
                "2 years (730 days)", "Custom number of days..."]
    days_of = {"No expiry": None, "7 days": 7, "14 days": 14, "30 days": 30, "90 days": 90, "180 days": 180,
               "1 year (365 days)": 365, "2 years (730 days)": 730}
    plan_help = {
        "trial": "Trial - only the apps you tick, for a short time.",
        "purchased": "Purchased - exactly the apps the customer bought.",
        "master": "Master - every app by default and the Admin panel; untick any you do not want to include.",
        "admin": "Admin - every app and the Admin panel, for people you trust.",
    }
    default_validity = {"trial": "14 days", "purchased": "1 year (365 days)", "master": "1 year (365 days)", "admin": "No expiry"}

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title("Astro Rishi — Mac License Key Maker")
            self.geometry("860x900")
            self.machine, self.name = tk.StringVar(), tk.StringVar()
            self.plan, self.validity = tk.StringVar(value="Purchased"), tk.StringVar(value="1 year (365 days)")
            self.admin_panel = tk.BooleanVar(value=False)
            self.app_vars = {k: tk.BooleanVar(value=False) for k in APP_KEYS}
            self.status = tk.StringVar()
            self.history: list[dict] = []
            if keygen():
                publish_public_key()
                messagebox.showinfo("First start", "A new signing key was created for the Mac version.\n\nIts public half was copied to "
                                    "mac/licensing/license_public.pem. Commit + push the Mac project and rebuild the Mac app "
                                    "so the app trusts your keys.\n\nBack up the keys folder and never share it.")
            self._build()
            self.load_history()

        def _build(self) -> None:
            pad = {"padx": 16}
            ttk.Label(self, text="Mac License Key Maker", font=("Helvetica", 18, "bold")).pack(anchor="w", pady=(14, 0), **pad)
            ttk.Label(self, text="Paste the Machine ID the customer sent, choose the plan and apps, then generate the key.").pack(anchor="w", pady=(0, 8), **pad)
            form = ttk.Frame(self)
            form.pack(fill="x", **pad)
            form.columnconfigure(1, weight=1)
            ttk.Label(form, text="Machine ID").grid(row=0, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=self.machine, font=("Menlo", 12)).grid(row=0, column=1, sticky="ew", padx=8)
            ttk.Button(form, text="Paste", command=self.paste_id).grid(row=0, column=2)
            self.id_hint = ttk.Label(form, text="Looks like ABCDE-12345-FGHIJ-67890")
            self.id_hint.grid(row=1, column=1, sticky="w", padx=8)
            self.machine.trace_add("write", lambda *_: self.check_id())
            ttk.Label(form, text="Customer name").grid(row=2, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=self.name).grid(row=2, column=1, columnspan=2, sticky="ew", padx=8)
            ttk.Label(form, text="Plan").grid(row=3, column=0, sticky="w", pady=4)
            box = ttk.Combobox(form, textvariable=self.plan, values=list(PLANS.values()), state="readonly", width=24)
            box.grid(row=3, column=1, sticky="w", padx=8)
            box.bind("<<ComboboxSelected>>", lambda _e: self.plan_changed())
            self.plan_hint = ttk.Label(form, text="")
            self.plan_hint.grid(row=4, column=1, columnspan=2, sticky="w", padx=8)
            ttk.Label(form, text="Valid for").grid(row=5, column=0, sticky="w", pady=4)
            ttk.Combobox(form, textvariable=self.validity, values=validity, state="readonly", width=24).grid(row=5, column=1, sticky="w", padx=8)

            frame = ttk.LabelFrame(self, text="Apps this key unlocks")
            frame.pack(fill="x", pady=(10, 4), **pad)
            grid = ttk.Frame(frame)
            grid.pack(fill="x", padx=8, pady=(4, 2))
            self.checks = []
            for i, (key, label) in enumerate(APPS):
                cb = ttk.Checkbutton(grid, text=label, variable=self.app_vars[key])
                cb.grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=2)
                self.checks.append(cb)
            row = ttk.Frame(frame)
            row.pack(fill="x", padx=8, pady=(0, 6))
            self.all_btn = ttk.Button(row, text="Tick all", command=lambda: self.set_all(True))
            self.all_btn.pack(side="left")
            self.none_btn = ttk.Button(row, text="Clear", command=lambda: self.set_all(False))
            self.none_btn.pack(side="left", padx=6)
            self.admin_check = ttk.Checkbutton(row, text="Include the Admin panel", variable=self.admin_panel)
            self.admin_check.pack(side="right")

            ttk.Button(self, text="Generate key", command=self.generate).pack(anchor="w", pady=8, **pad)
            ttk.Label(self, text="License key (send this to the customer)").pack(anchor="w", **pad)
            self.key_box = tk.Text(self, height=4, wrap="char", font=("Menlo", 10), relief="solid", borderwidth=1)
            self.key_box.pack(fill="x", pady=(3, 6), **pad)
            buttons = ttk.Frame(self)
            buttons.pack(fill="x", **pad)
            ttk.Button(buttons, text="Copy key", command=lambda: self.copy(self.current_key())).pack(side="left")
            ttk.Button(buttons, text="Copy as a message", command=self.copy_message).pack(side="left", padx=8)
            ttk.Button(buttons, text="This Mac's Machine ID", command=self.show_own_id).pack(side="right")
            ttk.Label(self, textvariable=self.status, foreground="#2e7d32", wraplength=780).pack(anchor="w", pady=(6, 0), **pad)
            ttk.Separator(self).pack(fill="x", pady=8, **pad)
            ttk.Label(self, text="Keys you have issued (double-click one to show it again)").pack(anchor="w", **pad)
            cols = ("date", "machine", "name", "plan", "apps", "expires")
            self.tree = ttk.Treeview(self, columns=cols, show="headings", height=6)
            for col, title, width in (("date", "Issued", 110), ("machine", "Machine ID", 175), ("name", "Customer", 140),
                                      ("plan", "Plan", 80), ("apps", "Apps", 110), ("expires", "Expires", 90)):
                self.tree.heading(col, text=title)
                self.tree.column(col, width=width, anchor="w")
            self.tree.pack(fill="both", expand=True, pady=(3, 14), **pad)
            self.tree.bind("<Double-1>", self.show_selected)
            self.plan_changed()

        def plan_key(self) -> str:
            return next((k for k, v in PLANS.items() if v == self.plan.get()), "purchased")

        def plan_changed(self) -> None:
            plan = self.plan_key()
            self.plan_hint.configure(text=plan_help[plan])
            self.validity.set(default_validity[plan])
            if plan in ALL_APPS_PLANS or plan == "master":
                self.set_all(True)
            self.admin_panel.set(plan in ADMIN_PANEL_PLANS)
            state = "disabled" if plan in ALL_APPS_PLANS else "normal"
            for w in (*self.checks, self.all_btn, self.none_btn, self.admin_check):
                w.configure(state=state)

        def set_all(self, value: bool) -> None:
            for v in self.app_vars.values():
                v.set(value)

        def check_id(self) -> None:
            text = self.machine.get().strip()
            if not text:
                self.id_hint.configure(text="Looks like ABCDE-12345-FGHIJ-67890", foreground="")
            elif normalize_machine_id(text):
                self.id_hint.configure(text="valid Machine ID", foreground="#2e7d32")
            else:
                self.id_hint.configure(text="Not a valid Machine ID yet (20 letters/digits)", foreground="#b3261e")

        def paste_id(self) -> None:
            try:
                self.machine.set(self.clipboard_get().strip())
            except tk.TclError:
                pass

        def validity_days(self) -> int | None:
            choice = self.validity.get()
            if choice == validity[-1]:
                value = simpledialog.askinteger("Custom validity", "Number of days:", parent=self, minvalue=1, maxvalue=36500)
                if value is None:
                    raise ValueError("cancelled")
                return value
            return days_of[choice]

        def generate(self) -> None:
            machine = normalize_machine_id(self.machine.get())
            if not machine:
                messagebox.showwarning("Machine ID", "Paste the customer's Machine ID first (20 letters/digits).", parent=self)
                return
            name = self.name.get().strip()
            if not name:
                messagebox.showwarning("Customer name", "Enter the customer's name.", parent=self)
                return
            plan = self.plan_key()
            apps = [k for k, v in self.app_vars.items() if v.get()]
            if plan not in ALL_APPS_PLANS and not apps:
                messagebox.showwarning("Apps", "Tick at least one app for this key.", parent=self)
                return
            try:
                days = self.validity_days()
            except ValueError:
                return
            key = issue(machine, name, days, plan, apps, self.admin_panel.get())
            self.machine.set(machine)
            self.set_key(key)
            self.copy(key, quiet=True)
            expiry = f"valid until {dt.date.today() + dt.timedelta(days=days):%d %b %Y}" if days else "no expiry"
            self.status.set(f"{PLANS[plan]} key created for {name} ({expiry}) and copied to the clipboard.")
            self.load_history()

        def set_key(self, key: str) -> None:
            self.key_box.delete("1.0", "end")
            self.key_box.insert("1.0", key)

        def current_key(self) -> str:
            return self.key_box.get("1.0", "end").strip()

        def copy(self, text: str, quiet: bool = False) -> None:
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                if not quiet:
                    self.status.set("Copied to the clipboard.")

        def copy_message(self) -> None:
            key = self.current_key()
            if key:
                self.copy("Your Astro Rishi license key (for the Mac whose Machine ID you sent):\n\n" + key +
                          "\n\nOpen Astro Rishi, paste this key on the Activation screen and click Activate.")

        def load_history(self) -> None:
            self.tree.delete(*self.tree.get_children())
            self.history = list(reversed(read_history()))
            for i, row in enumerate(self.history):
                mods = row.get("modules")
                apps = "all apps" if mods is None else f"{len(mods)} app{'s' if len(mods) != 1 else ''}"
                self.tree.insert("", "end", iid=str(i), values=(row.get("issued", ""), row.get("machine", ""), row.get("name", ""),
                                                                PLANS.get(row.get("plan", "master"), "Master"), apps, row.get("expires") or "never"))

        def show_selected(self, _event=None) -> None:
            sel = self.tree.selection()
            if not sel:
                return
            row = self.history[int(sel[0])]
            self.machine.set(row.get("machine", ""))
            self.name.set(row.get("name", ""))
            self.set_key(row.get("key", ""))
            self.status.set("Shown from your list - use Copy key to send it again.")

        def show_own_id(self) -> None:
            mid = own_machine_id()
            self.copy(mid, quiet=True)
            messagebox.showinfo("This Mac's Machine ID", f"{mid}\n\n(copied to the clipboard)", parent=self)

    App().mainloop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("machine-id")
    p = sub.add_parser("issue")
    p.add_argument("--machine", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--days", type=int)
    p.add_argument("--plan", default="purchased", choices=list(PLANS))
    p.add_argument("--apps", default="")
    p.add_argument("--admin-panel", action="store_true")
    r = sub.add_parser("release")
    r.add_argument("zip")
    args = ap.parse_args()
    if args.cmd == "machine-id":
        print(own_machine_id())
    elif args.cmd == "issue":
        print(issue(args.machine, args.name, args.days, args.plan, [a for a in args.apps.split(",") if a] or None,
                    True if args.admin_panel else None))
    elif args.cmd == "release":
        release(args.zip)
    else:
        gui()


if __name__ == "__main__":
    main()
