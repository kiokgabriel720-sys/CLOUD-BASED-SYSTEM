@echo off
REM CloudNMS - one-click start for Windows
cd /d %~dp0
if not exist venv (
  python -m venv venv
  call venv\Scripts\activate
  pip install -r requirements.txt
  python seed.py 7
) else (
  call venv\Scripts\activate
)
echo.
echo  Open http://127.0.0.1:5000   Login: admin / Admin@2026
echo.
python wsgi.py
