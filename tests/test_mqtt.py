"""Tests for custom_components/meowant/mqtt.py against an in-process fake broker."""
from __future__ import annotations

import asyncio

import pytest
from _broker import FakeBroker

from custom_components.meowant.mqtt import (
    PUBACK,
    SUBSCRIBE,
    MqttAuthError,
    MqttClient,
    MqttError,
    connect_packet,
)


def _client(port: int, received: list, **kwargs) -> MqttClient:
    return MqttClient(
        "127.0.0.1",
        port,
        client_id="cid",
        username="user",
        password="pass",
        on_message=lambda topic, payload: received.append((topic, payload)),
        ssl_context=None,
        **kwargs,
    )


def test_connect_packet_layout():
    data = connect_packet("cid", "user", "pass", 20)
    # type 1, remaining length, "MQTT", level 4, flags user+pass+clean, keepalive 20
    assert data[:12] == bytes.fromhex("10") + bytes((len(data) - 2,)) + b"\x00\x04MQTT\x04\xc2\x00\x14"
    assert data.endswith(b"\x00\x03cid\x00\x04user\x00\x04pass")


def test_connect_subscribe_publish_receive():
    async def run():
        received = []
        async with FakeBroker() as broker:
            client = _client(broker.port, received)
            await client.connect()
            assert client.connected
            assert await client.subscribe("sub/topic", qos=1) == 1
            flags, body = broker.of_type(SUBSCRIBE)[0]
            assert flags == 2 and body.endswith(b"\x00\x09sub/topic\x01")

            await client.publish("pub/topic", b"\x01\x02")
            assert await asyncio.wait_for(broker.published.get(), 2) == ("pub/topic", b"\x01\x02")

            await broker.push("sub/topic", b"hello", qos=1, packet_id=0x1234)
            for _ in range(50):
                if received and broker.of_type(PUBACK):
                    break
                await asyncio.sleep(0.01)
            assert received == [("sub/topic", b"hello")]
            assert broker.of_type(PUBACK) == [(0, b"\x12\x34")]
            await client.disconnect()
            assert not client.connected
            await client.wait_closed()  # clean close: no error

    asyncio.run(run())


@pytest.mark.parametrize("code, error", [(4, MqttAuthError), (5, MqttAuthError), (3, MqttError)])
def test_connack_refusal(code, error):
    async def run():
        async with FakeBroker(connack_code=code) as broker:
            with pytest.raises(error):
                await _client(broker.port, []).connect()

    asyncio.run(run())


def test_lost_connection_surfaces_in_wait_closed():
    async def run():
        async with FakeBroker() as broker:
            client = _client(broker.port, [])
            await client.connect()
            await broker.drop()
            with pytest.raises(MqttError):
                await asyncio.wait_for(client.wait_closed(), 2)
            assert not client.connected
            with pytest.raises(MqttError):
                await client.publish("t", b"x")

    asyncio.run(run())


def test_connect_refused_port():
    async def run():
        async with FakeBroker() as broker:
            port = broker.port
        with pytest.raises(MqttError):
            await _client(port, []).connect(timeout=2)

    asyncio.run(run())


def test_handler_exception_doesnt_kill_connection():
    async def run():
        async with FakeBroker() as broker:
            def boom(topic, payload):
                raise RuntimeError("handler bug")

            client = MqttClient(
                "127.0.0.1", broker.port, client_id="c", username="u", password="p", on_message=boom, ssl_context=None
            )
            await client.connect()
            await broker.push("t", b"x", qos=0)
            await asyncio.sleep(0.05)
            assert client.connected
            await client.disconnect()

    asyncio.run(run())
