"""
plans.py -- the apps and plans a license key can grant. Shared by the license gate (inside the installed app)
and by the owner's tools (license_manager.py / KeyMaker.pyw).

The app keys and URL prefixes mirror the original app's own module list (main.py _PATH_MODULE_MAP).
"""

from __future__ import annotations

import json
from pathlib import Path

# (key, label shown to people, URL prefix) -- the built-in list, used only if apps.json is missing.
_FALLBACK_APPS = [
    ("nadi", "Nadi Astrology", "/nadi"),
    ("nadi_numero", "Num + Nadi", "/nadi-numero"),
    ("numerology", "Numerology", "/numerology"),
    ("horary", "Horary / Prashna", "/horary"),
    ("matchmaking", "Matchmaking", "/matchmaking"),
    ("parashari", "Parashari", "/parashari"),
    ("jaimini", "Jaimini", "/jaimini"),
    ("vedic_cards", "Vedic Cards", "/vedic-cards"),
    ("vastu", "Vastu", "/vastu"),
    ("hindu_calendar", "Panchang", "/hindu-calendar"),
    ("mobile_numerology", "Mobile Numerology", "/mobile-numerology"),
    ("name_correction", "Name Correction", "/name-correction"),
]


def _load_apps() -> list:
    """apps.json is written by every build from the pushed main app's own module list, so an app you add later is
    automatically known here: locked for any key that does not list it, included in 'all apps' keys."""
    try:
        data = json.loads((Path(__file__).resolve().parent / "apps.json").read_text(encoding="utf-8"))
        apps = [(d["key"], d["label"], d["prefix"]) for d in data]
        return apps or _FALLBACK_APPS
    except Exception:
        return _FALLBACK_APPS


APPS = _load_apps()
APP_KEYS = [k for k, _l, _p in APPS]
APP_LABEL = {k: label for k, label, _p in APPS}
APP_PREFIX = {k: prefix for k, _l, prefix in APPS}

# Longest prefix first so "/nadi-numero" is matched before "/nadi".
_PREFIX_TO_KEY = sorted(((p, k) for k, _l, p in APPS), key=lambda pair: -len(pair[0]))

PLANS = {
    "trial": "Trial",
    "purchased": "Purchased",
    "master": "Master",
    "admin": "Admin",
}
# Plans that always include every app (the app list cannot be narrowed for them). Master starts with every app
# ticked but the owner may untick some when making the key.
ALL_APPS_PLANS = {"admin"}
# Plans that include the Admin panel by default (a Trial / Purchased key can opt in with a tick-box).
ADMIN_PANEL_PLANS = {"master", "admin"}


def app_for_path(path: str) -> str | None:
    """Which app (key) a URL belongs to, or None for shared pages (home, login, saved charts, static files...)."""
    for prefix, key in _PREFIX_TO_KEY:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


def is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")
