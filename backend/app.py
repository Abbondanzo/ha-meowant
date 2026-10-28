"""Local login test page for the Meowant cloud API.

Thin FastAPI wrapper around MeowantApiClient -- the same client used by the
Home Assistant integration (custom_components/meowant/api.py). All auth/signing
logic lives there once; this file only adapts it to an HTTP endpoint the test
page can call. See _meowant_dev_import.py for why this needs an import shim.

Never returns tokens or passwords to the browser: everything sent back goes
through redact() first.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import ssl
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from _meowant_dev_import import load_meowant_api  # noqa: E402

_meowant_api = load_meowant_api()
MeowantApiClient = _meowant_api.MeowantApiClient
MeowantApiError = _meowant_api.MeowantApiError
MeowantAuthError = _meowant_api.MeowantAuthError
new_device_id = _meowant_api.new_device_id
normalize_area_code = _meowant_api.normalize_area_code
# Registered in sys.modules by load_meowant_api().
from custom_components.meowant.im import MeowantImSession  # noqa: E402
from custom_components.meowant.mqtt import MqttError  # noqa: E402

load_dotenv(REPO_ROOT / ".env", override=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("meowant_login_test")

FRONTEND_DIR = REPO_ROOT / "frontend"
DEVICE_ID_FILE = REPO_ROOT / ".dev_device_id"

SENSITIVE_KEYS = {
    "token", "refreshtoken", "accesstoken", "authorization", "password", "mqttpassword",
    "phone", "mobile", "email", "account", "appkey", "app_key", "sign", "secret",
    "mqttusername", "clientid",
}


def redact(value: Any) -> Any:
    """Recursively mask values under sensitive-looking keys, whatever the nesting."""
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if str(k).lower().replace("-", "") in SENSITIVE_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _device_id() -> str:
    """Stable fake install ID, persisted so every run looks like the same client."""
    if env := os.environ.get("MEOWANT_DEVICE_ID"):
        return env
    if DEVICE_ID_FILE.exists():
        return DEVICE_ID_FILE.read_text().strip()
    device_id = new_device_id()
    DEVICE_ID_FILE.write_text(device_id)
    return device_id


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


class LoginRequest(BaseModel):
    email: str = ""
    password: str = ""
    area_code: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _im is not None:
        await _im.close()
    if _session is not None:
        await _session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/api/defaults")
async def defaults() -> dict[str, Any]:
    """Prefill values for the form. Secrets are reported only as set/unset."""
    return {
        "email": _env("MEOWANT_EMAIL"),
        "password_set": bool(_env("MEOWANT_PASSWORD")),
        "area_code": _env("MEOWANT_AREA_CODE"),
    }


# One logged-in client for the whole dev server, reused by every endpoint.
# Logging in per request would burn attempts toward the server's lockout.
_session: aiohttp.ClientSession | None = None
_api: "MeowantApiClient | None" = None


def _new_session() -> aiohttp.ClientSession:
    # Optional: route through a local intercepting proxy (e.g. HTTP Toolkit on
    # http://127.0.0.1:8000) to compare our requests with the app's. Its CA
    # isn't trusted here, so TLS verification is off while this is set.
    if proxy := _env("MEOWANT_DEBUG_PROXY"):
        log.warning("Routing upstream requests through %s with TLS verification OFF", proxy)
        return aiohttp.ClientSession(proxy=proxy, connector=aiohttp.TCPConnector(ssl=False))
    return aiohttp.ClientSession()


def _require_api() -> "MeowantApiClient":
    if _api is None or _api.auth is None:
        raise HTTPException(status_code=401, detail="Not logged in. Log in first.")
    return _api


async def _section(coro) -> dict[str, Any]:
    """Run one upstream call; report its data or its error without failing the rest."""
    try:
        return {"ok": True, "data": redact(await coro)}
    except MeowantApiError as err:
        return {"ok": False, "error": str(err), "code": err.code}
    except ValueError as err:
        return {"ok": False, "error": str(err)}


@app.post("/api/login")
async def login(req: LoginRequest) -> dict[str, Any]:
    global _session, _api
    # Blank form fields fall back to .env. The tenant always comes from the
    # integration's constants (const.py).
    email = req.email.strip() or _env("MEOWANT_EMAIL")
    password = req.password or _env("MEOWANT_PASSWORD")
    area_code = req.area_code.strip() or _env("MEOWANT_AREA_CODE")
    if not email or not password:
        return {"ok": False, "stage": "input", "error": "Email and password are required."}
    try:
        area_code = normalize_area_code(area_code)
    except ValueError as err:
        return {"ok": False, "stage": "input", "error": str(err)}

    if _session is not None:
        await _session.close()
    _session = _new_session()
    api = MeowantApiClient(_session, email, password, device_id=_device_id(), area_code=area_code)
    _api = None
    try:
        auth = await api.login()
    except MeowantAuthError as err:
        return {
            "ok": False,
            "stage": "login",
            "auth_rejected": True,
            "error": str(err),
            "code": err.code,
            "attempts": [{"account_type": t, "code": c, "error": m} for t, c, m in err.attempts],
        }
    except MeowantApiError as err:
        return {"ok": False, "stage": "login", "error": str(err), "code": err.code, "status": err.status}

    _api = api
    log.info("Login OK (user_id=%s)", auth.user_id)
    return {"ok": True, **_session_info(api), **(await devices())}


def _session_info(api: "MeowantApiClient") -> dict[str, Any]:
    auth = api.auth
    return {
        "user_id": auth.user_id,
        "account_type": auth.account_type,
        "expires_at_ms": auth.expires_at_ms,
        "token_length": len(auth.token),
    }


@app.get("/api/session")
async def session() -> dict[str, Any]:
    """Whether the dev server already holds a logged-in client (survives page reloads)."""
    if _api is None or _api.auth is None:
        return {"logged_in": False}
    return {"logged_in": True, **_session_info(_api)}


@app.get("/api/devices")
async def devices() -> dict[str, Any]:
    try:
        return {"devices": redact(await _require_api().get_devices())}
    except MeowantApiError as err:
        return {"devices_error": str(err)}


@app.get("/api/devices/{serial}/state")
async def device_state(serial: str) -> dict[str, Any]:
    """Everything readable about one box, one section per upstream endpoint."""
    api = _require_api()
    return {
        "config": await _section(api.get_all_config(serial)),
        "waste_bin": await _section(api.get_box_use_times(serial)),
        "deodorant": await _section(api.get_deodorant_status(serial)),
        "online": await _section(api.get_online_status(serial)),
        "cat_visits": await _section(api.get_records(serial, 0)),
        "device_events": await _section(api.get_records(serial, 1)),
    }


@app.get("/api/cats")
async def cats() -> dict[str, Any]:
    return await _section(_require_api().get_cats())


class ActionRequest(BaseModel):
    enabled: bool | None = None
    function_type: int | None = None
    option_int: int | None = None
    config_id: int | None = None
    start: str | None = None  # "HH:MM"
    end: str | None = None  # "HH:MM"


def _hhmm(value: str | None, name: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in (value or "").split(":"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be HH:MM") from None
    return hour, minute


def _need(value: Any, name: str) -> Any:
    if value is None:
        raise HTTPException(status_code=400, detail=f"{name} is required")
    return value


@app.post("/api/devices/{serial}/actions/{action}")
async def device_action(serial: str, action: str, req: ActionRequest) -> dict[str, Any]:
    """Change a box setting. Each action maps to one method on MeowantApiClient."""
    api = _require_api()
    log.info("Action %s on %s: %s", action, serial, req.model_dump(exclude_none=True))
    if action == "reset_waste_bin":
        coro = api.reset_waste_bin(serial)
    elif action == "deodorant_replaced":
        coro = api.mark_deodorant_replaced(serial)
    elif action == "deodorant_alert":
        coro = api.set_deodorant_alert(serial, _need(req.enabled, "enabled"))
    elif action == "switch":
        coro = api.set_switch(
            serial,
            _need(req.function_type, "function_type"),
            _need(req.enabled, "enabled"),
            _need(req.option_int, "option_int"),
        )
    elif action == "time_period":
        coro = api.set_time_period(
            serial,
            config_id=_need(req.config_id, "config_id"),
            function_type=_need(req.function_type, "function_type"),
            enabled=_need(req.enabled, "enabled"),
            start=_hhmm(req.start, "start"),
            end=_hhmm(req.end, "end"),
        )
    else:
        raise HTTPException(status_code=404, detail=f"Unknown action {action!r}")
    return await _section(coro)


class RawRequest(BaseModel):
    method: str = "GET"
    path: str
    body: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


@app.post("/api/raw")
async def raw_request(req: RawRequest) -> dict[str, Any]:
    """Signed, authenticated request to any path on the tenant, for exploring endpoints."""
    api = _require_api()
    log.info("Raw %s %s", req.method.upper(), req.path)
    return await _section(api.authed_request(req.method, req.path, req.body, req.headers))


# --- IM / MQTT (live state and commands; see API.md) ---

_im: "MeowantImSession | None" = None
# Every decoded incoming message, newest last. Holds no credentials.
_im_messages: deque[dict[str, Any]] = deque(maxlen=300)
_im_ids = itertools.count(1)


def _record_im(message, serial: str | None) -> None:
    entry = {"id": next(_im_ids), "at_ms": int(time.time() * 1000), "serial": serial, **message.as_dict()}
    _im_messages.append(redact(entry))


def _im_status() -> dict[str, Any]:
    connected = _im is not None and _im.connected
    return {"connected": connected, "contacts": _im.contacts if connected else {}}


def _last_im_id() -> int:
    return _im_messages[-1]["id"] if _im_messages else 0


def _require_im() -> "MeowantImSession":
    if _im is None or not _im.connected:
        raise HTTPException(status_code=409, detail="IM not connected. Connect first.")
    return _im


async def _collect_im(after: int, serial: str, seconds: float, until_state: bool) -> list[dict[str, Any]]:
    """Messages that arrive within `seconds` (stopping early at a state reply from the box, if asked)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        new = [m for m in _im_messages if m["id"] > after]
        if until_state and any(m["serial"] == serial and "state" in m for m in new):
            return new
        await asyncio.sleep(0.1)
    return [m for m in _im_messages if m["id"] > after]


@app.post("/api/im/connect")
async def im_connect() -> dict[str, Any]:
    """Fetch MQTT credentials (im/login) and connect. Replaces any existing connection."""
    global _im
    api = _require_api()
    if _im is not None:
        await _im.close()
    # MQTT doesn't go through MEOWANT_DEBUG_PROXY; it connects directly with normal TLS verification.
    _im = MeowantImSession(api, ssl_context=ssl.create_default_context(), on_message=_record_im)
    try:
        await _im.connect()
    except (MeowantApiError, MqttError) as err:
        _im = None
        return {"ok": False, "error": str(err)}
    log.info("IM connected; contacts: %d", len(_im.contacts))
    return {"ok": True, **_im_status()}


@app.post("/api/im/disconnect")
async def im_disconnect() -> dict[str, Any]:
    global _im
    if _im is not None:
        await _im.close()
        _im = None
    return {"ok": True, **_im_status()}


@app.get("/api/im/messages")
async def im_messages(after: int = 0) -> dict[str, Any]:
    return {**_im_status(), "messages": [m for m in _im_messages if m["id"] > after]}


@app.post("/api/devices/{serial}/im/state")
async def im_request_state(serial: str) -> dict[str, Any]:
    """Send an all-state request and wait (up to 5 s) for the box's reply."""
    session = _require_im()
    after = _last_im_id()
    try:
        seq = await session.request_state(serial)
    except (MeowantApiError, MqttError) as err:
        return {"ok": False, "error": str(err)}
    messages = await _collect_im(after, serial, 5, until_state=True)
    state = next((m["state"] for m in reversed(messages) if m["serial"] == serial and "state" in m), None)
    return {"ok": state is not None, "seq": seq, "state": state, "messages": messages,
            **({} if state else {"error": "No state reply within 5 s"})}


class OperationRequest(BaseModel):
    op_mode: int
    op_state: int


@app.post("/api/devices/{serial}/im/operation")
async def im_operation(serial: str, req: OperationRequest) -> dict[str, Any]:
    """Send an operation (op mode + op state; see API.md) and collect replies for 3 s."""
    session = _require_im()
    log.info("IM operation on %s: mode=%d state=%d", serial, req.op_mode, req.op_state)
    after = _last_im_id()
    try:
        seq = await session.send_operation(serial, req.op_mode, req.op_state)
    except (MeowantApiError, MqttError) as err:
        return {"ok": False, "error": str(err)}
    return {"ok": True, "seq": seq, "messages": await _collect_im(after, serial, 3, until_state=False)}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
