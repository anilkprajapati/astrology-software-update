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
