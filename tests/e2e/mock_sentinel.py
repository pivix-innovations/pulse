"""A stand-in Sentinel (docs/sentinel-contract.md) whose behaviour tests can switch at runtime."""
from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from tests.e2e.harness import free_port

DIRECTORY = {
    "ada": {"external_ref": "ada", "email": "ada@example.com", "phone": "+15550001111", "locale": "fr", "timezone": "Europe/Paris"},
    "bob": {"external_ref": "bob", "email": "bob@example.com"},
    "dana": {"external_ref": "dana", "email": "dana@example.com"},   # used by exactly one test each, so
    "erin": {"external_ref": "erin", "email": "erin@example.com"},   # nothing is cached or snapshotted for them
}


class MockSentinel:
    def __init__(self, token: str = "sentinel-token") -> None:
        self.token = token
        self.mode = "up"            # up | down | slow
        self.calls: list[dict] = []
        self.port = free_port()
        app = FastAPI()

        @app.post("/v1/recipients/resolve")
        async def resolve(request: Request, authorization: str | None = Header(None),
                          x_pulse_tenant_id: str | None = Header(None)):
            body = await request.json()
            self.calls.append({"auth": authorization, "tenant": x_pulse_tenant_id, "body": body})
            if self.mode == "down":
                return JSONResponse({"error": "boom"}, status_code=503)
            if self.mode == "slow":
                time.sleep(10)
            if authorization != f"Bearer {self.token}":
                return JSONResponse({"error": "no"}, status_code=401)
            user = body["payload"].get("user_id")
            if user in DIRECTORY:
                return {"recipients": [DIRECTORY[user]]}
            if user == "everyone":
                return {"recipients": [DIRECTORY["ada"], DIRECTORY["bob"]]}
            return JSONResponse({"recipients": []}, status_code=404)

        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> "MockSentinel":
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
