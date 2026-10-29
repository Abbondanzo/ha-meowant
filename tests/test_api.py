"""Unit tests for custom_components/meowant/api.py's pure logic."""
from __future__ import annotations

import hashlib

import pytest

from custom_components.meowant.api import (
    MeowantApiError,
    MeowantAuthError,
    Tenant,
    account_type_candidates,
    hash_password,
    sign,
    unwrap,
)


def test_auth_error_is_api_error():
    # The coordinator and config flow rely on catching MeowantAuthError before
    # the broader MeowantApiError.
    assert issubclass(MeowantAuthError, MeowantApiError)


def test_sign_format_and_digest():
    header = sign("KEY", "dev123", timestamp=1792558858948, nonce="2HR6c1gLG5")
    digest, ts, nonce, version = header.split(" ")
    assert (ts, nonce, version) == ("1792558858948", "2HR6c1gLG5", "v2")
    assert digest == hashlib.md5(b"1792558858948KEY2HR6c1gLG5dev123").hexdigest()


def test_sign_default_timestamp_is_milliseconds():
    # The Android app sends a 13-digit millisecond timestamp, not seconds.
    assert len(sign("KEY", "dev").split(" ")[1]) == 13


def test_sign_random_nonce_is_ten_alphanumerics():
    nonce = sign("KEY", "dev").split(" ")[2]
    assert len(nonce) == 10 and nonce.isalnum()


def test_hash_password_is_md5_hex():
    assert hash_password("hunter2") == "2ab96390c7dbe3439de74d0c9b0b1767"


def test_account_type_candidates_single_attempt():
    assert account_type_candidates("a@b.com") == (1,)
    assert account_type_candidates("5551234567") == (0,)


def test_unwrap_lockout_is_rate_limit_not_auth():
    from custom_components.meowant.api import MeowantRateLimitError

    with pytest.raises(MeowantRateLimitError) as exc:
        unwrap(400, {"code": 2342, "message": "too many"})
    assert not isinstance(exc.value, MeowantAuthError)


def test_unwrap_returns_data():
    assert unwrap(200, {"code": 0, "data": {"x": 1}}) == {"x": 1}


def test_unwrap_passes_through_bare_response():
    # Login success has no {code, data} envelope.
    body = {"token": {"token": "t"}, "user": {"userId": 1}}
    assert unwrap(200, body) is body


@pytest.mark.parametrize("code", [2002, 2004])
def test_unwrap_auth_codes_raise_auth_error(code):
    with pytest.raises(MeowantAuthError):
        unwrap(400, {"code": code, "message": "nope"})


def test_unwrap_401_is_auth_error():
    with pytest.raises(MeowantAuthError) as exc:
        unwrap(401, None)
    assert exc.value.status == 401


def test_unwrap_other_code_is_api_error():
    with pytest.raises(MeowantApiError) as exc:
        unwrap(200, {"code": 1009, "message": "bad param"})
    assert not isinstance(exc.value, MeowantAuthError)
    assert exc.value.code == 1009


def test_tenant_missing_fields():
    assert Tenant("u", "", "k", "").missing_fields() == ["app_id", "product"]


class _FakeClient:
    """Stands in for MeowantApiClient._request with canned per-accountType errors."""

    def __init__(self, codes_by_type):
        self.codes_by_type = codes_by_type

    async def __call__(self, method, path, *, payload, token):
        code = self.codes_by_type[payload["accountType"]]
        unwrap(400, {"code": code, "message": f"err{code}"})


def test_login_email_makes_one_attempt():
    import asyncio

    from custom_components.meowant.api import MeowantApiClient

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"))
    api._request = _FakeClient({1: 2002, 0: 2004})
    with pytest.raises(MeowantAuthError) as exc:
        asyncio.run(api.login())
    assert exc.value.code == 2002
    assert [a[:2] for a in exc.value.attempts] == [(1, 2002)]


def test_login_payload_matches_app_and_parses_bare_response():
    import asyncio

    from custom_components.meowant.api import MeowantApiClient

    sent = []

    async def fake_request(method, path, *, payload, token):
        sent.append(payload)
        return unwrap(200, {"token": {"token": "tok", "expireAt": 123}, "user": {"userId": 714}})

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"))
    api._request = fake_request
    auth = asyncio.run(api.login())
    assert list(sent[0]) == ["account", "accountType", "areaCode", "password"]
    assert sent[0]["accountType"] == 1 and sent[0]["areaCode"] == "US"
    assert (auth.token, auth.expires_at_ms, auth.user_id) == ("tok", 123, "714")


@pytest.mark.parametrize("raw, expected", [(None, "US"), ("", "US"), (" us ", "US"), ("GB", "GB")])
def test_normalize_area_code(raw, expected):
    from custom_components.meowant.api import normalize_area_code

    assert normalize_area_code(raw) == expected


@pytest.mark.parametrize("raw", ["1", "+1", "USA", "U1"])
def test_normalize_area_code_rejects_non_iso(raw):
    from custom_components.meowant.api import normalize_area_code

    with pytest.raises(ValueError):
        normalize_area_code(raw)


def test_default_tenant_is_complete():
    assert Tenant.default().missing_fields() == []


def _recording_client():
    import time

    from custom_components.meowant.api import AuthSession, MeowantApiClient

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"))
    api._auth = AuthSession(token="t", expires_at_ms=int(time.time() * 1000) + 3_600_000, user_id="1", account_type=1)
    calls = []

    async def fake_request(method, path, *, payload, token, extra_headers=None):
        calls.append((method, path, payload, token))
        return {}

    api._request = fake_request
    return api, calls


def test_set_switch_payload():
    import asyncio

    api, calls = _recording_client()
    asyncio.run(api.set_switch("SN1", 2, True, 480))
    assert calls == [(
        "PUT",
        "/catbox-server/box/config/switch/update",
        {"serialNumber": "SN1", "functionType": 2, "controlSwitch": 1, "optionInt": 480},
        "t",
    )]


def test_reads_quote_serial_and_records_payload():
    import asyncio

    api, calls = _recording_client()
    asyncio.run(api.get_all_config("A B/1"))
    asyncio.run(api.get_records("SN1", 0, size=3))
    assert calls[0][1] == "/catbox-server/box/config/allConfig?serialNumber=A%20B%2F1"
    assert calls[1][:3] == (
        "POST",
        "/catbox-server/web/box/record/new",
        {"eventTime": "0", "serialNumber": "SN1", "size": 3, "messageType": 0},
    )


def test_authed_request_requires_absolute_path():
    import asyncio

    api, _ = _recording_client()
    with pytest.raises(ValueError):
        asyncio.run(api.authed_request("GET", "catbox-server/x"))


def test_authed_requests_send_language_header_but_login_does_not():
    import asyncio

    from custom_components.meowant.api import MeowantApiClient

    seen = []

    class FakeResp:
        status = 200

        async def json(self, content_type=None):
            return {"code": 0, "data": []}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def request(self, method, url, *, headers, **kwargs):
            seen.append(headers)
            return FakeResp()

    api = MeowantApiClient(FakeSession(), "a@b.com", "pw", device_id="d", tenant=Tenant("http://x", "i", "k", "p"))
    asyncio.run(api._request("PUT", "/login", payload={}, token=None))
    asyncio.run(api._request("GET", "/cats", payload=None, token="t"))
    assert "x-ubt-language" not in seen[0]
    assert seen[1]["x-ubt-language"] == "en"


def test_concurrent_calls_with_expired_token_log_in_once():
    # The coordinator and the IM connection share one client. Logins count
    # toward the server's lockout, so two callers must not both log in.
    import asyncio

    from custom_components.meowant.api import AuthSession, MeowantApiClient

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"))
    api._auth = AuthSession(token="old", expires_at_ms=1, user_id="1", account_type=1)
    logins = []

    async def fake_request(method, path, *, payload, token, extra_headers=None):
        if path.endswith("/user/login"):
            logins.append(1)
            await asyncio.sleep(0.01)
            return {"token": {"token": "new", "expireAt": 2**53}, "user": {"userId": 1}}
        assert token == "new"
        return []

    api._request = fake_request

    async def run():
        await asyncio.gather(api.get_im_friends(), api.get_devices(), api.get_cats())

    asyncio.run(run())
    assert len(logins) == 1


def test_401_relogs_in_once_across_concurrent_callers():
    import asyncio
    import time

    from custom_components.meowant.api import AuthSession, MeowantApiClient

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"))
    api._auth = AuthSession(token="old", expires_at_ms=int(time.time() * 1000) + 3_600_000, user_id="1", account_type=1)
    logins = []

    async def fake_request(method, path, *, payload, token, extra_headers=None):
        if path.endswith("/user/login"):
            logins.append(1)
            return {"token": {"token": "new", "expireAt": 2**53}, "user": {"userId": 1}}
        if token == "old":
            await asyncio.sleep(0.01)
            raise MeowantAuthError("HTTP 401", status=401)
        return []

    api._request = fake_request

    async def run():
        await asyncio.gather(api.get_im_friends(), api.get_devices())

    asyncio.run(run())
    assert len(logins) == 1


def test_auth_session_round_trip_and_garbage():
    from custom_components.meowant.api import AuthSession

    auth = AuthSession(token="t", expires_at_ms=123, user_id="7", account_type=1)
    assert AuthSession.from_dict(auth.as_dict()) == auth
    for bad in (None, {}, {"token": ""}, {"token": "t"}, {"token": "t", "account_type": "x"}, "t"):
        assert AuthSession.from_dict(bad) is None


def test_saved_session_is_reused_without_login():
    # A restart or setup retry with a saved, unexpired token must not log in.
    import asyncio
    import time

    from custom_components.meowant.api import AuthSession, MeowantApiClient

    saved = AuthSession(token="saved", expires_at_ms=int(time.time() * 1000) + 3_600_000, user_id="1", account_type=1)
    paths = []

    async def fake_request(method, path, *, payload, token, extra_headers=None):
        paths.append((path, token))
        return []

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"), auth=saved)
    api._request = fake_request
    asyncio.run(api.get_devices())
    assert paths == [("/user-service-rest/v2/robot/common/device/list", "saved")]


def test_on_login_receives_new_session():
    import asyncio

    from custom_components.meowant.api import MeowantApiClient

    saved = []

    async def fake_request(method, path, *, payload, token, extra_headers=None):
        return {"token": {"token": "new", "expireAt": 99}, "user": {"userId": 5}}

    api = MeowantApiClient(None, "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"), on_login=saved.append)
    api._request = fake_request
    asyncio.run(api.login())
    assert [(a.token, a.expires_at_ms, a.user_id) for a in saved] == [("new", 99, "5")]


def test_unreachable_server_raises_connection_error():
    import asyncio

    import aiohttp

    from custom_components.meowant.api import MeowantApiClient, MeowantConnectionError

    class DeadSession:
        def request(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("connect timed out")

    api = MeowantApiClient(DeadSession(), "a@b.com", "pw", device_id="d", tenant=Tenant("u", "i", "k", "p"))
    with pytest.raises(MeowantConnectionError):
        asyncio.run(api.login())
