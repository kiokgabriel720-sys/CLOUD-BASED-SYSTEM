"""Flask CLI commands used for setup, scheduled monitoring checks and demo data."""
import click

from . import db
from .models import User
from .monitoring import check_heartbeats


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    def create_admin(username, full_name, email, password):
        """Create the first System Administrator account."""
        user = User(username=username.lower(), full_name=full_name, email=email.lower(),
                    role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Administrator '{username}' created.")

    @app.cli.command("check-devices")
    def check_devices():
        """Scheduled task: mark silent devices offline and raise alerts."""
        offline = check_heartbeats()
        db.session.commit()
        click.echo(f"Heartbeat check complete - {len(offline)} device(s) newly offline.")

    @app.cli.command("seed-demo")
    @click.option("--days", default=7)
    def seed_demo(days):
        """Load demonstration users, devices and historical metrics."""
        from seed import seed
        seed(days)
        click.echo("Demo data loaded.")
