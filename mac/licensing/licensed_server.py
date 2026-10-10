"""
licensed_server.py -- the ASGI entry point the installed app runs:  uvicorn licensed_server:app

Imports the ORIGINAL app unchanged (``main.app``, from the app\\ folder that is on sys.path) and puts the
one-PC license check in front of it.
"""

import license_gate
from license_gate import LicenseGate

license_gate.install_runtime_patches()      # must run before the app is imported

from main import app as _app  # noqa: E402

app = LicenseGate(_app)

# Changes the owner pushed for THIS Machine ID (no new key needed) are fetched in the background from GitHub.
import entitlements  # noqa: E402


def _drop_cache() -> None:
    license_gate._cache["until"] = 0.0


entitlements.start_background(license_gate.machine_id(), _drop_cache)
