import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import Device, User  # noqa: E402


@pytest.fixture
def app():
    app = create_app("config.TestConfig")
    with app.app_context():
        for name, uname, role in [("Admin User", "admin", "admin"),
                                  ("Net Admin", "netadmin", "network_admin"),
                                  ("Tech User", "tech", "technician")]:
            u = User(full_name=name, username=uname, email=f"{uname}@test.local", role=role)
            u.set_password("Passw0rd!")
            db.session.add(u)
        db.session.add(Device(name="Core Router", ip_address="10.0.0.1", device_type="Router",
                              location="Server room", api_key="k" * 48))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username="admin", password="Passw0rd!"):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


@pytest.fixture
def admin(client):
    login(client, "admin")
    return client


@pytest.fixture
def netadmin(client):
    login(client, "netadmin")
    return client


@pytest.fixture
def tech(client):
    login(client, "tech")
    return client


@pytest.fixture
def device(app):
    return Device.query.filter_by(ip_address="10.0.0.1").first()
