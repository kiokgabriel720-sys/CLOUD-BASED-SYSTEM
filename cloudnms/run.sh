#!/usr/bin/env bash
# CloudNMS - one-click start for Linux / macOS
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  python3 -m venv venv && . venv/bin/activate
  pip install -r requirements.txt && python seed.py 7
else
  . venv/bin/activate
fi
echo; echo " Open http://127.0.0.1:5000   Login: admin / Admin@2026"; echo
python wsgi.py
