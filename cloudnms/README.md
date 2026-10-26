# CloudNMS – Cloud-Based Network Management System

Flask web application for centrally monitoring network devices (routers, switches,
firewalls, servers, access points): availability, CPU, memory, latency, link utilisation
and packet loss, with automatic fault detection, alerts, reports, role-based access
control and an audit log.

## Project structure

```
cloudnms/
├── app/
│   ├── __init__.py      application factory, filters, error pages
│   ├── models.py        SQLAlchemy models (users, devices, metrics, alerts, reports, audit logs, settings)
│   ├── auth.py          login / logout / lockout / change password
│   ├── decorators.py    role_required() – RBAC
│   ├── main.py          dashboard, devices, alerts, reports, users, thresholds, audit log
│   ├── api.py           REST API: /api/v1/metrics (agents), chart data
│   ├── monitoring.py    validation, threshold evaluation, heartbeats, alerts, TCP probe
│   ├── reports.py       availability / performance / alert reports
│   ├── cli.py           flask create-admin | check-devices | seed-demo
│   ├── templates/       Jinja2 HTML templates
│   └── static/          CSS and Chart.js
├── agent/
│   ├── nms_agent.py     real monitoring agent (psutil)
│   └── simulator.py     lab simulator for several virtual devices
├── tests/               pytest suite (27 tests)
├── config.py  wsgi.py  seed.py  requirements.txt
```

## Run locally

**Windows:** double-click `run.bat`  ·  **Linux/macOS:** `./run.sh`
(first run creates a virtual environment, installs requirements and loads demo data).

Or manually:

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python seed.py 7                  # optional: demo users, 8 devices, 7 days of metrics
python wsgi.py                    # http://127.0.0.1:5000
```

## Login

| Role | Username | Password |
|---|---|---|
| System Administrator | `admin` | `Admin@2026` |
| System Administrator (demo data) | `gkunterei` | `Admin@2026` |
| Network Administrator (demo data) | `jwanjiku` | `Netadmin@2026` |
| IT Technician (demo data) | `bomondi` | `Tech@2026` |

The `admin` account is created automatically the first time the app starts with an empty
database, even without demo data. The login page shows these credentials until the
admin password is changed (Profile → Change password). To use different first-run
credentials, set `NMS_ADMIN_USERNAME` and `NMS_ADMIN_PASSWORD` before the first start.
Five wrong passwords lock an account for 15 minutes.

## Send metrics

Each device page shows its agent API key.

```bash
python agent/nms_agent.py --server http://127.0.0.1:5000 --key <API key> --interval 60
python agent/simulator.py --config agent/lab_devices.json --interval 30 --fault "Core Router:cpu"
```

## Tests

```bash
python -m pytest -v --cov=app tests
```

## Deploy on PythonAnywhere

1. `git clone <your repo>` in a PythonAnywhere Bash console.
2. `mkvirtualenv --python=/usr/bin/python3.11 cloudnms-env && pip install -r requirements.txt`
3. Web tab → Add a new web app → Manual configuration → Python 3.11; set the virtualenv.
4. Edit the WSGI file:
   ```python
   import os, sys
   path = "/home/<username>/cloudnms"
   if path not in sys.path:
       sys.path.insert(0, path)
   os.environ["NMS_SECRET_KEY"] = "<long random string>"
   os.environ["NMS_SECURE_COOKIES"] = "1"
   from wsgi import app as application
   ```
5. Static files: URL `/static/` → `/home/<username>/cloudnms/app/static`
6. Reload the app – the `admin / Admin@2026` account is created automatically (or run `python seed.py` for the demo). Log in and change the password.
7. Enable "Force HTTPS", reload the web app.
8. Tasks tab: schedule `cd ~/cloudnms && ~/.virtualenvs/cloudnms-env/bin/flask --app wsgi check-devices`
