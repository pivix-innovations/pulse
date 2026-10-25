"""
End-to-end harness: the real API, arq workers and scheduler as separate
processes against a real Postgres and Redis (see docker-compose.yml).

Each Stack gets its own throwaway database and Redis DB index, runs the real
migrations, and talks to the API over HTTP, so what it exercises is what runs
in production, crashes included.
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import asyncpg
import httpx
from redis.asyncio import Redis

from config.loader import get_settings
from database.database import Database
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import IdentityService
from services.secrets import generate_key

ROOT = Path(__file__).resolve().parents[2]

WORKERS = {
    "email": ["-m", "arq", "workers.email_worker.WorkerSettings"],
    "push": ["-m", "arq", "workers.push_worker.WorkerSettings"],
    "sms": ["-m", "arq", "workers.sms_worker.WorkerSettings"],
    "scheduler": ["-m", "workers.scheduler_worker"],
}

# Fast reconciliation so the suite doesn't wait on production timings.
FAST_ENV = {
    "PULSE_RECONCILIATION__INTERVAL_SECONDS": "1",
    "PULSE_OUTBOX__POLL_INTERVAL_SECONDS": "0.2",
    "PULSE_RETRY__BASE_DELAY_SECONDS": "0.2",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn: Callable[[], Any], *, timeout: float = 30, interval: float = 0.25, what: str = "condition"):
    """Poll `fn` until it returns something truthy; fail with the last value."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"Timed out after {timeout}s waiting for {what} (last: {last!r})")


class Stack:
    def __init__(self, name: str, redis_db: int, env: dict[str, str] | None = None) -> None:
        self.name = name
        self.db_name = f"pulse_e2e_{name}"
        self.redis_db = redis_db
        self.settings = get_settings()
        self.env = {
            **os.environ,
            **FAST_ENV,
            "PULSE_DATABASE__DB": self.db_name,
            "PULSE_REDIS__DB": str(redis_db),
            "PULSE_SECURITY__SECRETS_KEY": generate_key(),
            "PYTHONUNBUFFERED": "1",
            **(env or {}),
        }
        self.procs: dict[str, subprocess.Popen] = {}
        self._offsets: dict[str, int] = {}  # log size at spawn, so restarts are recognised
        self.logs = Path(os.environ.get("PULSE_E2E_LOGS", "/tmp")) / f"pulse-e2e-{name}"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.api_key = ""
        self.tenant_id = ""
        self.client: httpx.Client | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> "Stack":
        asyncio.run(self._reset_infra())
        self._run(["-m", "alembic", "upgrade", "head"], check=True)
        self.api_key, self.tenant_id = asyncio.run(self._seed())
        self.spawn("api", ["-m", "uvicorn", "main:app", "--port", str(self.port), "--log-level", "warning"])
        wait_for(self._api_up, timeout=30, what="API to come up")
        for name in WORKERS:
            self.spawn(name, WORKERS[name])
        wait_for(lambda: all(self._worker_ready(n) for n in WORKERS), timeout=30, what="workers and scheduler")
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}/api/v1",
            headers={"X-API-Key": self.api_key},
            timeout=15,
        )
        return self

    def stop(self) -> None:
        if self.client:
            self.client.close()
        for name in list(self.procs):
            self.kill(name, signal.SIGTERM)

    def _run(self, args: list[str], check: bool = False) -> subprocess.CompletedProcess:
        out = subprocess.run(
            [sys.executable, *args], cwd=ROOT, env=self.env, capture_output=True, text=True
        )
        if check and out.returncode:
            raise RuntimeError(f"{' '.join(args)} failed:\n{out.stdout}\n{out.stderr}")
        return out

    async def _reset_infra(self) -> None:
        db = self.settings.database
        admin = await asyncpg.connect(
            host=db.host, port=db.port, user=db.username, password=db.password, database="postgres"
        )
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{self.db_name}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{self.db_name}"')
        finally:
            await admin.close()
        r = self.settings.redis
        redis = Redis(host=r.host, port=r.port, db=self.redis_db, password=r.password)
        try:
            await redis.flushdb()  # this index belongs to the e2e suite
        finally:
            await redis.aclose()

    async def _seed(self) -> tuple[str, str]:
        from domain.entities.identity import Tenant
        from domain.value_objects.ids import TenantId

        database = Database(self.settings.database.model_copy(update={"db": self.db_name}))
        await database.initialize()
        try:
            def factory() -> SqlAlchemyUnitOfWork:
                return SqlAlchemyUnitOfWork(database.session_factory)

            tenant = Tenant(id=TenantId.new(), name="E2E", slug=f"e2e-{self.name}")
            async with factory() as uow:
                await uow.tenants.add(tenant)
                await uow.commit()
            key, _ = await IdentityService(factory).create_api_key(tenant.id, "e2e")
            return key, str(tenant.id)
        finally:
            await database.shutdown()

    def admin_client(self) -> httpx.Client:
        """A console session (tenant admin) on the real API, the way key management is done."""
        asyncio.run(self._add_admin())
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}/api/v1", timeout=15,
            headers={"X-Tenant-Id": self.tenant_id},
        )
        r = client.post("/auth/login", json={"email": "admin@e2e.test", "password": "e2e-admin-password"})
        assert r.status_code == 200, r.text
        return client

    async def _add_admin(self) -> None:
        from domain.entities.identity import Membership, User
        from domain.enums.identity import TenantRole
        from domain.value_objects.ids import MembershipId, TenantId, UserId
        from services.identity_service import hash_password

        database = Database(self.settings.database.model_copy(update={"db": self.db_name}))
        await database.initialize()
        try:
            async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
                if await uow.users.get_by_email("admin@e2e.test") is None:
                    user = User(id=UserId.new(), email="admin@e2e.test", password_hash=hash_password("e2e-admin-password"))
                    await uow.users.add(user)
                    await uow.commit()                 # the membership's foreign key needs the user row first
                    await uow.memberships.upsert(Membership(
                        id=MembershipId.new(), tenant_id=TenantId.from_str(self.tenant_id),
                        user_id=user.id, role=TenantRole.ADMIN))
                    await uow.commit()
        finally:
            await database.shutdown()

    def add_tenant(self, name: str) -> tuple[str, str]:
        """Another tenant with its own API key, for isolation tests. Returns (api_key, tenant_id)."""
        return asyncio.run(self._add_tenant(name))

    async def _add_tenant(self, name: str) -> tuple[str, str]:
        from domain.entities.identity import Tenant
        from domain.value_objects.ids import TenantId

        database = Database(self.settings.database.model_copy(update={"db": self.db_name}))
        await database.initialize()
        try:
            def factory() -> SqlAlchemyUnitOfWork:
                return SqlAlchemyUnitOfWork(database.session_factory)

            tenant = Tenant(id=TenantId.new(), name=name, slug=f"{name}-{self.name}")
            async with factory() as uow:
                await uow.tenants.add(tenant)
                await uow.commit()
            key, _ = await IdentityService(factory).create_api_key(tenant.id, name)
            return key, str(tenant.id)
        finally:
            await database.shutdown()

    # ── processes ──────────────────────────────────────────────────────

    def spawn(self, name: str, args: list[str], env: dict[str, str] | None = None) -> None:
        path = self.logs / f"{name}.log"
        self._offsets[name] = len(self.log(name))
        log = open(path, "ab")
        self.procs[name] = subprocess.Popen(
            [sys.executable, *args], cwd=ROOT, env={**self.env, **(env or {})},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def kill(self, name: str, sig: int = signal.SIGKILL) -> None:
        proc = self.procs.pop(name, None)
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)

    def restart_worker(self, name: str) -> None:
        self.spawn(name, WORKERS[name])
        wait_for(lambda: self._worker_ready(name), timeout=30, what=f"{name} worker restart")

    def log(self, name: str) -> str:
        path = self.logs / f"{name}.log"
        return path.read_text(errors="replace") if path.exists() else ""

    def _api_up(self) -> bool:
        try:
            return httpx.get(f"http://127.0.0.1:{self.port}/openapi.json", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def _worker_ready(self, name: str) -> bool:
        marker = "Reconciliation sweep started" if name == "scheduler" else "Starting worker"
        return marker in self.log(name)[self._offsets.get(name, 0):]

    # ── API / DB helpers ───────────────────────────────────────────────

    def api(self, method: str, path: str, **kw) -> httpx.Response:
        assert self.client is not None
        return self.client.request(method, path, **kw)

    def sql(self, query: str, *args) -> list[asyncpg.Record]:
        async def run():
            db = self.settings.database
            conn = await asyncpg.connect(
                host=db.host, port=db.port, user=db.username, password=db.password, database=self.db_name
            )
            try:
                return await conn.fetch(query, *args)
            finally:
                await conn.close()

        return asyncio.run(run())

    def redis_keys(self, pattern: str) -> list[str]:
        async def run():
            r = self.settings.redis
            redis = Redis(host=r.host, port=r.port, db=self.redis_db, password=r.password)
            try:
                return [k.decode() async for k in redis.scan_iter(pattern)]
            finally:
                await redis.aclose()

        return asyncio.run(run())

    def sse_connections(self) -> int:
        """Total live SSE connections across recipients, from the registry's counters."""
        async def run():
            r = self.settings.redis
            redis = Redis(host=r.host, port=r.port, db=self.redis_db, password=r.password)
            try:
                total = 0
                async for key in redis.scan_iter("pulse:sse:conn:*"):
                    total += int(await redis.get(key) or 0)
                return total
            finally:
                await redis.aclose()

        return asyncio.run(run())

    def notification(self, notification_id: str) -> dict:
        r = self.api("GET", f"/notifications/{notification_id}")
        r.raise_for_status()
        return r.json()

    def dump_logs(self) -> str:
        return "\n".join(
            f"--- {n} ---\n{self.log(n)[-1500:]}" for n in ("api", "email", "push", "sms", "scheduler")
        )


class SseClient:
    """A live SSE connection to the API process, read on a background thread."""

    def __init__(self, stack: "Stack", recipient: str, *, last_event_id: str | None = None) -> None:
        import queue
        import threading

        self.events: "queue.Queue[dict]" = queue.Queue()
        self._stop = threading.Event()
        headers = {"X-API-Key": stack.api_key}
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        url = f"http://127.0.0.1:{stack.port}/api/v1/sse/stream"
        self._params = {"recipient": recipient}

        def run() -> None:
            import json

            try:
                with self._client as client:
                    with client.stream("GET", url, params=self._params, headers=headers) as res:
                        self.status = res.status_code
                        fields: dict = {}
                        for line in res.iter_lines():
                            if self._stop.is_set():
                                return
                            if line == "":
                                if "data" in fields:
                                    self.events.put({**fields, "data": json.loads(fields["data"])})
                                fields = {}
                            elif line.startswith(":"):
                                self.events.put({"comment": line[1:].strip()})
                            else:
                                key, _, value = line.partition(": ")
                                fields[key] = value
            except (httpx.HTTPError, OSError):
                pass  # closed by close()

        self.status = None
        self._client = httpx.Client(timeout=httpx.Timeout(10, read=None))
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def next_notification(self, timeout: float = 15) -> dict:
        import queue

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if event.get("event") == "notification":
                return event
        raise AssertionError("no notification arrived on the SSE stream")

    def close(self) -> None:
        """Drop the connection for real, as a browser tab closing would."""
        self._stop.set()
        try:
            self._client.close()
        except Exception:
            pass
        self._thread.join(timeout=5)
