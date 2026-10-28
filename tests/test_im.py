"""Tests for custom_components/meowant/im.py: the IM session end to end against a fake broker."""
from __future__ import annotations

import asyncio

import pytest
from _broker import FakeBroker

from custom_components.meowant.api import MeowantApiError, Tenant
from custom_components.meowant.im import ImCredentials, MeowantImSession, contacts_by_serial
from custom_components.meowant.im_proto import (
    CMD_ALL_STATE,
    CMD_OPERATION,
    OP_CLEAN,
    OP_START,
    build_risp_frame,
    crc8,
    decode_im_message,
    encode_im_message,
)

IM_LOGIN = {
    "clientId": "client-1",
    "mqttUserName": "mqtt-user",
    "mqttPassword": "mqtt-secret",
    "pubTopic": "im/pub",
    "subTopic": "im/sub",
    "uid": "111",
}
FRIENDS = [{"userName": "SN1", "userId": 222}, {"userName": "someone", "userId": 333}, {"userId": 444}]


class FakeApi:
    tenant = Tenant("http://127.0.0.1", "i", "k", "p")
    auth = None

    def __init__(self, friends=FRIENDS):
        self.friends = friends
        self.friend_calls = 0

    async def get_im_login(self):
        return dict(IM_LOGIN)

    async def get_im_friends(self):
        self.friend_calls += 1
        return self.friends


def test_credentials_parse_and_repr_hides_secrets():
    creds = ImCredentials.parse(IM_LOGIN, None)
    assert (creds.pub_topic, creds.sub_topic, creds.uid) == ("im/pub", "im/sub", "111")
    text = repr(creds)
    assert "mqtt-secret" not in text and "mqtt-user" not in text and "client-1" not in text


def test_credentials_uid_falls_back_to_rest_user_id():
    data = {k: v for k, v in IM_LOGIN.items() if k != "uid"}
    assert ImCredentials.parse(data, "714").uid == "714"


def test_credentials_missing_fields_named_without_values():
    with pytest.raises(MeowantApiError) as exc:
        ImCredentials.parse({"mqttPassword": "mqtt-secret"}, None)
    assert "client_id" in str(exc.value) and "mqtt-secret" not in str(exc.value)


def test_contacts_by_serial():
    assert contacts_by_serial(FRIENDS) == {"SN1": "222", "someone": "333"}


def _state_reply(body: bytes, cmd: bytes = bytes.fromhex("fd07"), attr: int = 2) -> bytes:
    header = bytes.fromhex("0802") + b"" + cmd + (bytes((0x20, len(body))) if body else b"")
    header += bytes((0x28, attr, 0x38, 0x01))
    frame = bytes((0xDB, len(header))) + header + bytes((crc8(header),))
    if body:
        frame += body + bytes((crc8(body),))
    return encode_im_message(frame=frame, from_uid="222", to_uid="111", cid="c", gid="g", created_at_ms=1)


def test_session_sends_commands_and_reports_state():
    async def run():
        states, messages = [], []
        async with FakeBroker() as broker:
            session = MeowantImSession(
                FakeApi(),
                ssl_context=None,
                port=broker.port,
                on_state=lambda serial, state: states.append((serial, state)),
                on_message=lambda message, serial: messages.append(serial),
            )
            await session.connect()
            assert session.connected and session.contacts == {"SN1": "222", "someone": "333"}

            seq = await session.request_state("SN1")
            topic, payload = await asyncio.wait_for(broker.published.get(), 2)
            sent = decode_im_message(payload)
            assert topic == "im/pub"
            assert (sent.from_uid, sent.to_uid, sent.frame.cmd, sent.frame.seq) == ("111", "222", CMD_ALL_STATE, seq)

            await session.send_operation("SN1", OP_CLEAN, OP_START)
            _, payload = await asyncio.wait_for(broker.published.get(), 2)
            op = decode_im_message(payload).frame
            assert op.cmd == CMD_OPERATION and op.seq == seq + 1 and op.body.hex() == "08011001"

            # Reply from the box: cleaning, running. Then an empty (all-zero) reply: idle.
            await broker.push("im/sub", _state_reply(bytes.fromhex("08021001")))
            await broker.push("im/sub", _state_reply(b""))
            # Our own request echoed back by someone else's uid must not count as state.
            await broker.push(
                "im/sub",
                encode_im_message(
                    frame=build_risp_frame(CMD_ALL_STATE, 1), from_uid="999", to_uid="222", cid="c", gid="g",
                    created_at_ms=1,
                ),
            )
            # Unsolicited push (cmd 0x400, attr 6) after a pause.
            await broker.push("im/sub", _state_reply(bytes.fromhex("08021003183c"), cmd=bytes.fromhex("8008"), attr=6))
            for _ in range(100):
                if len(messages) == 4:
                    break
                await asyncio.sleep(0.01)
            assert [(s, st.status) for s, st in states] == [("SN1", "cleaning"), ("SN1", "idle"), ("SN1", "paused")]
            assert messages == ["SN1", "SN1", None, "SN1"]
            await session.close()
            assert not session.connected

    asyncio.run(run())


def test_unknown_serial_refreshes_contacts_once_then_errors():
    async def run():
        async with FakeBroker() as broker:
            api = FakeApi()
            session = MeowantImSession(api, ssl_context=None, port=broker.port)
            await session.connect()
            with pytest.raises(MeowantApiError):
                await session.request_state("NOPE")
            assert api.friend_calls == 2
            await session.close()

    asyncio.run(run())


def test_send_before_connect_errors():
    with pytest.raises(MeowantApiError):
        asyncio.run(MeowantImSession(FakeApi(), ssl_context=None).request_state("SN1"))
