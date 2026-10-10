"""platform_paths.py -- where Astro Rishi keeps this user's license and downloads, per operating system."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir(fallback: Path) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AstroRishi"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", str(fallback))) / "AstroRishi"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "AstroRishi"
