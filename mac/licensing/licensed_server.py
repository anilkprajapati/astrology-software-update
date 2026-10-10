"""
licensed_server.py -- the ASGI entry point the installed app runs:  uvicorn licensed_server:app

Imports the ORIGINAL app unchanged (``main.app``, from the app\\ folder that is on sys.path) and puts the
one-PC license check in front of it.
"""

from license_gate import LicenseGate
from main import app as _app

app = LicenseGate(_app)
