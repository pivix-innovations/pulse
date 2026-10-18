"""Point a local/worker .env at Neon + managed Redis. Usage: python deploy/configure_env.py <redis_url>
Reads DATABASE_URL_UNPOOLED from .env (written by `neon deploy`); generates secrets once into undread/pulse-secrets.env."""
import pathlib, secrets, sys
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parent.parent
env_path, sec_path = ROOT / ".env", ROOT / "undread/pulse-secrets.env"
env = dotenv_values(env_path)
db, rd = urlsplit(env["DATABASE_URL_UNPOOLED"]), urlsplit(sys.argv[1])
if not sec_path.exists():
    sec_path.parent.mkdir(exist_ok=True)
    pw = secrets.token_urlsafe(18)
    sec_path.write_text("\n".join([
        f"PULSE_SECURITY__SECRETS_KEY={Fernet.generate_key().decode()}",
        f"PULSE_SECURITY__UNSUBSCRIBE_KEY={secrets.token_urlsafe(32)}",
        f"PULSE_BOOTSTRAP_EMAIL=admin@pivix.in", f"PULSE_BOOTSTRAP_PASSWORD={pw}", "PULSE_BOOTSTRAP_TENANT=pivix-dev", ""]))
    sec_path.chmod(0o600)
sec = dotenv_values(sec_path)
updates = {
    "PULSE_DATABASE__HOST": db.hostname, "PULSE_DATABASE__PORT": "5432", "PULSE_DATABASE__USERNAME": db.username,
    "PULSE_DATABASE__PASSWORD": db.password, "PULSE_DATABASE__DB": db.path.lstrip("/"), "PULSE_DATABASE__SSL": "true",
    "PULSE_REDIS__HOST": rd.hostname, "PULSE_REDIS__PORT": str(rd.port or 6379), "PULSE_REDIS__USERNAME": rd.username or "",
    "PULSE_REDIS__PASSWORD": rd.password or "", "PULSE_REDIS__SSL": "true" if rd.scheme == "rediss" else "false",
    "PULSE_SECURITY__SECRETS_KEY": sec["PULSE_SECURITY__SECRETS_KEY"],
    "PULSE_SECURITY__UNSUBSCRIBE_KEY": sec["PULSE_SECURITY__UNSUBSCRIBE_KEY"],
    "PULSE_API__PUBLIC_URL": "https://dev-pulse-api.pivix.in",
}
lines = [l for l in env_path.read_text().splitlines() if l.split("=")[0] not in updates]
env_path.write_text("\n".join(lines + [f'{k}="{v}"' for k, v in updates.items()]) + "\n")
print("updated", len(updates), "keys")
