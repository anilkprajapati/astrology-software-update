#!/bin/bash
# Double-click to open the Mac License Key Maker.
cd "$(dirname "$0")"
python3 -c "import cryptography, requests" 2>/dev/null || python3 -m pip install --user cryptography requests
python3 keymaker_mac.py
