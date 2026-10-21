"""Client for the Meowant (UBTech AirPet) cloud API. Protocol reference: API.md.

Deliberately has no homeassistant dependency -- only aiohttp and const.py -- so
the dev backend and tests/ can import it without installing homeassistant. See
_meowant_dev_import.py.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import string
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    ACCOUNT_TYPE_EMAIL,
    ACCOUNT_TYPE_PHONE,
    API_LANGUAGE,
    DEFAULT_AREA_CODE,
    DEFAULT_APP_ID,
    DEFAULT_APP_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_PRODUCT,
    LOGIN_METHOD,
    TOKEN_REFRESH_MARGIN,
    USER_AGENT,
)

_NONCE_ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Upstream error codes (see API.md "Response envelope").
_CODE_BAD_PASSWORD = 2002
_CODE_NO_SUCH_USER = 2004
_CODE_TOO_MANY_ATTEMPTS = 2342  # account temporarily locked after repeated failures


class MeowantApiError(Exception):
    """Any failure talking to the Meowant API."""

    def __init__(self, message: str, *, status: int | None = None, code: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class MeowantRateLimitError(MeowantApiError):
    """Too many failed logins; the account is temporarily locked. Don't retry soon."""


class MeowantAuthError(MeowantApiError):
    """Credentials were rejected."""

    account_type: int | None = None
    # (accountType, code, message) for each login attempt, when raised by login().
    attempts: list[tuple[int | None, Any, str]] = []


@dataclass(frozen=True, slots=True)
class Tenant:
    """App-level identity of one branded tenant on the AirPet cloud."""

    base_url: str
    app_id: str
    app_key: str
    product: str

    @classmethod
    def default(cls) -> Tenant:
        return cls(DEFAULT_BASE_URL, DEFAULT_APP_ID, DEFAULT_APP_KEY, DEFAULT_PRODUCT)

    def missing_fields(self) -> list[str]:
        return [name for name in ("base_url", "app_id", "app_key", "product") if not getattr(self, name)]


@dataclass(frozen=True, slots=True)
class AuthSession:
    token: str
    expires_at_ms: int | None
    user_id: str | None
    account_type: int


def new_device_id() -> str:
    """A client install ID for x-ubt-deviceid. Persist it; don't regenerate per request."""
    return uuid.uuid4().hex


def sign(app_key: str, device_id: str, *, timestamp: int | None = None, nonce: str | None = None) -> str:
    """Build the x-ubt-sign header: "<md5(ts + app_key + nonce + device_id)> <ts> <nonce> v2".

    `ts` is Unix time in milliseconds, as the Android app sends it.
    """
    ts = str(int(time.time() * 1000) if timestamp is None else timestamp)
    if nonce is None:
        nonce = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(10))
    digest = hashlib.md5((ts + app_key + nonce + device_id).encode()).hexdigest()
    return f"{digest} {ts} {nonce} v2"


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()


def account_type_candidates(account: str) -> tuple[int, ...]:
    # One attempt per login: every failed attempt counts toward the server's
    # lockout (code 2342), so don't spray account types.
    return (ACCOUNT_TYPE_EMAIL,) if "@" in account else (ACCOUNT_TYPE_PHONE,)


def normalize_area_code(area_code: str | None) -> str:
    """Return a two-letter ISO country code (e.g. "US"), defaulting when blank.

    Raises ValueError for anything else, such as a phone prefix like "1".
    """
    code = (area_code or "").strip().upper() or DEFAULT_AREA_CODE
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"areaCode must be a two-letter country code like US, got {area_code!r}")
    return code


def unwrap(status: int, body: Any) -> Any:
    """Return the response payload, or raise on an HTTP or envelope error.

    Errors (and some successes) come wrapped as {"code", "message", "data"}; the
    login success response is bare, with no envelope at all.
    """
    enveloped = isinstance(body, dict) and "code" in body
    code = body.get("code") if enveloped else None
    message = (body.get("message") or body.get("msg")) if isinstance(body, dict) else None
    if status == 401:
        raise MeowantAuthError(f"HTTP 401: {message}", status=status, code=code)
    if status >= 400 or code not in (None, 0):
        if code == _CODE_TOO_MANY_ATTEMPTS:
            raise MeowantRateLimitError(f"code={code} message={message}", status=status, code=code)
        if code in (_CODE_BAD_PASSWORD, _CODE_NO_SUCH_USER):
            raise MeowantAuthError(f"code={code} message={message}", status=status, code=code)
        raise MeowantApiError(f"HTTP {status} code={code} message={message}", status=status, code=code)
    return body.get("data") if enveloped else body


class MeowantApiClient:
    """Thin async client for the Meowant cloud API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        device_id: str,
        tenant: Tenant | None = None,
        area_code: str | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._device_id = device_id
        self._tenant = tenant or Tenant.default()
        # The server looks accounts up per region: a wrong areaCode gives 2004
        # (and counts toward the lockout), so reject non-ISO values up front.
        self._area_code = normalize_area_code(area_code)
        self._auth: AuthSession | None = None

    @property
    def auth(self) -> AuthSession | None:
        return self._auth

    async def login(self) -> AuthSession:
        """Authenticate. Raises MeowantAuthError on bad credentials."""
        if missing := self._tenant.missing_fields():
            raise MeowantApiError(f"Meowant tenant not configured: missing {', '.join(missing)}")

        errors: list[MeowantAuthError] = []
        for account_type in account_type_candidates(self._email):
            # Field order and types as the Android app sends them.
            payload = {
                "account": self._email,
                "accountType": account_type,
                "areaCode": self._area_code,
                "password": hash_password(self._password),
            }
            try:
                data = await self._request(LOGIN_METHOD, "/user-service-rest/v2/user/login", payload=payload, token=None)
            except MeowantAuthError as err:
                err.account_type = account_type
                errors.append(err)
                continue
            token = (data or {}).get("token") if isinstance(data, dict) else None
            if not isinstance(token, dict) or not token.get("token"):
                raise MeowantApiError("Login response had no token")
            user = data.get("user") if isinstance(data.get("user"), dict) else {}
            self._auth = AuthSession(
                token=token["token"],
                expires_at_ms=token.get("expireAt"),
                user_id=str(user["userId"]) if user.get("userId") is not None else None,
                account_type=account_type,
            )
            return self._auth
        # "Wrong password" from one account type beats "no such user" from another.
        best = next((e for e in errors if e.code == _CODE_BAD_PASSWORD), errors[-1])
        best.attempts = [(e.account_type, e.code, str(e)) for e in errors]
        raise best

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return every litter box on the account (each keyed by `serialNumber`)."""
        data = await self._authed("GET", "/user-service-rest/v2/robot/common/device/list")
        if not isinstance(data, list):
            raise MeowantApiError("Device list response was not a list")
        return data

    # --- Per-box reads (see API.md "Read endpoints"; response shapes unverified) ---

    async def get_all_config(self, serial: str) -> Any:
        return await self._authed("GET", f"/catbox-server/box/config/allConfig?serialNumber={_q(serial)}")

    async def get_box_use_times(self, serial: str) -> Any:
        return await self._authed("GET", f"/catbox-server/box/config/box-use-times/?serialNumber={_q(serial)}")

    async def get_deodorant_status(self, serial: str) -> Any:
        return await self._authed("GET", f"/catbox-server/app/deodorant-block/status?serialNumber={_q(serial)}")

    async def get_online_status(self, serial: str) -> Any:
        return await self._authed("GET", f"/v1/ubtechinc-im-manager/im/online/device/?sn={_q(serial)}")

    async def get_cats(self) -> Any:
        return await self._authed("GET", "/catbox-server/web/cat/info")

    async def get_records(self, serial: str, message_type: int, size: int = 5) -> Any:
        """Latest event records: message_type 0 = cat visits, 1 = device events."""
        payload = {"eventTime": "0", "serialNumber": serial, "size": size, "messageType": message_type}
        return await self._authed("POST", "/catbox-server/web/box/record/new", payload)

    # --- Per-box writes (see API.md "Write endpoints") ---

    async def reset_waste_bin(self, serial: str) -> Any:
        payload = {"boxUseTimes": 0, "serialNumber": serial}
        return await self._authed("PUT", "/catbox-server/box/config/box-use-times/reset", payload)

    async def set_deodorant_alert(self, serial: str, enabled: bool) -> Any:
        payload = {"serialNumber": serial, "alertSwitch": int(enabled)}
        return await self._authed("POST", "/catbox-server/app/deodorant-block/switch", payload)

    async def mark_deodorant_replaced(self, serial: str) -> Any:
        return await self._authed("POST", "/catbox-server/app/deodorant-block/replaced", {"serialNumber": serial})

    async def set_switch(self, serial: str, function_type: int, enabled: bool, option_int: int) -> Any:
        """Toggle a box feature. function_type 2 = auto clean (option_int = delay in seconds)."""
        payload = {
            "serialNumber": serial,
            "functionType": function_type,
            "controlSwitch": int(enabled),
            "optionInt": option_int,
        }
        return await self._authed("PUT", "/catbox-server/box/config/switch/update", payload)

    async def set_time_period(
        self,
        serial: str,
        *,
        config_id: int,
        function_type: int,
        enabled: bool,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> Any:
        """Set a start/end window feature such as do-not-disturb. Times are (hour, minute)."""
        payload = {
            "serialNumber": serial,
            "functionType": function_type,
            "openSwitch": int(enabled),
            "configId": config_id,
            "startTimeHour": start[0],
            "startTimeMinute": start[1],
            "endTimeHour": end[0],
            "endTimeMinute": end[1],
        }
        return await self._authed("PUT", "/catbox-server/box/config/timePeriod/update", payload)

    async def authed_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Signed, authenticated request to any path on the tenant. For dev tooling."""
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        return await self._authed(method.upper(), path, payload, headers)

    async def _authed(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        if not self._token_usable():
            await self.login()
        assert self._auth is not None
        try:
            return await self._request(
                method, path, payload=payload, token=self._auth.token, extra_headers=extra_headers
            )
        except MeowantAuthError as err:
            if err.status != 401:
                raise
            await self.login()
            return await self._request(
                method, path, payload=payload, token=self._auth.token, extra_headers=extra_headers
            )

    def _token_usable(self) -> bool:
        if self._auth is None:
            return False
        if self._auth.expires_at_ms is None:
            return True
        margin_ms = TOKEN_REFRESH_MARGIN.total_seconds() * 1000
        return self._auth.expires_at_ms - time.time() * 1000 > margin_ms

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None,
        token: str | None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        # Mirrors the Android app's login request (see API.md).
        headers = {
            "content-type": "application/json; charset=UTF-8",
            "user-agent": USER_AGENT,
            "x-ubt-appid": self._tenant.app_id,
            "x-ubt-deviceid": self._device_id,
            "x-ubt-sign": sign(self._tenant.app_key, self._device_id),
        }
        if token is not None:
            headers["authorization"] = token
            headers["product"] = self._tenant.product
            headers["x-ubt-language"] = API_LANGUAGE
        # Extra headers (dev tooling) can add to, but never replace, auth/signing headers.
        for name, value in (extra_headers or {}).items():
            headers.setdefault(name.lower(), value)

        url = self._tenant.base_url.rstrip("/") + path
        try:
            # Compact JSON, no Accept header, gzip only: byte-for-byte what okhttp sends.
            body_bytes = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
            headers["accept-encoding"] = "gzip"
            async with self._session.request(
                method,
                url,
                data=body_bytes,
                headers=headers,
                skip_auto_headers=("Accept",),
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
                return unwrap(resp.status, body)
        except aiohttp.ClientError as err:
            raise MeowantApiError(f"Request to {path} failed: {err}") from err
        except TimeoutError as err:
            raise MeowantApiError(f"Request to {path} timed out") from err
