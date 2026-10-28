"""Minimal asyncio MQTT 3.1.1 client: just what the IM channel needs.

CONNECT (clean session, username/password), one SUBSCRIBE, QoS 0 PUBLISH out,
QoS 0/1 PUBLISH in (PUBACK sent for QoS 1), keepalive pings and DISCONNECT.
Hand-rolled rather than depending on paho-mqtt, so the integration still needs
nothing beyond what Home Assistant core ships (see CLAUDE.md).

No homeassistant dependency, like api.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

# Packet types (high nibble of the fixed header).
CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
SUBSCRIBE = 8
SUBACK = 9
PINGREQ = 12
PINGRESP = 13
DISCONNECT = 14

_CONNACK_REASONS = {
    1: "unacceptable protocol version",
    2: "client identifier rejected",
    3: "server unavailable",
    4: "bad user name or password",
    5: "not authorized",
}


class MqttError(Exception):
    """Connecting failed or the connection was lost."""


class MqttAuthError(MqttError):
    """The broker rejected the credentials (CONNACK 4 or 5)."""


def _utf8(value: str) -> bytes:
    data = value.encode()
    if len(data) > 0xFFFF:
        raise ValueError("MQTT string too long")
    return len(data).to_bytes(2, "big") + data


def _remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        byte, length = length % 128, length // 128
        out.append(byte | 0x80 if length else byte)
        if not length:
            return bytes(out)


def packet(type_and_flags: int, body: bytes = b"") -> bytes:
    return bytes((type_and_flags,)) + _remaining_length(len(body)) + body


def connect_packet(client_id: str, username: str, password: str, keepalive: int) -> bytes:
    # Flags 0xC2: username + password + clean session.
    header = _utf8("MQTT") + bytes((4, 0xC2)) + keepalive.to_bytes(2, "big")
    return packet(CONNECT << 4, header + _utf8(client_id) + _utf8(username) + _utf8(password))


def subscribe_packet(packet_id: int, topic: str, qos: int) -> bytes:
    # SUBSCRIBE's fixed-header flags must be 0b0010.
    return packet(SUBSCRIBE << 4 | 2, packet_id.to_bytes(2, "big") + _utf8(topic) + bytes((qos,)))


def publish_packet(topic: str, payload: bytes) -> bytes:
    return packet(PUBLISH << 4, _utf8(topic) + payload)  # QoS 0: no packet id


def parse_publish(flags: int, body: bytes) -> tuple[str, int, int | None, bytes]:
    """Split a PUBLISH body into (topic, qos, packet id or None, payload)."""
    if len(body) < 2:
        raise MqttError("malformed PUBLISH")
    end = 2 + int.from_bytes(body[:2], "big")
    if end > len(body):
        raise MqttError("malformed PUBLISH topic")
    topic = body[2:end].decode("utf-8", errors="replace")
    qos = (flags >> 1) & 3
    packet_id = None
    if qos:
        if end + 2 > len(body):
            raise MqttError("malformed PUBLISH packet id")
        packet_id, end = int.from_bytes(body[end : end + 2], "big"), end + 2
    return topic, qos, packet_id, body[end:]


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, int, bytes]:
    """Read one packet: (type, flags, body)."""
    first = (await reader.readexactly(1))[0]
    length = shift = 0
    while True:
        byte = (await reader.readexactly(1))[0]
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
        if shift > 21:
            raise MqttError("malformed remaining length")
    body = await reader.readexactly(length) if length else b""
    return first >> 4, first & 0x0F, body


class MqttClient:
    """One MQTT connection. Not reusable: make a new client to reconnect."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        client_id: str,
        username: str,
        password: str,
        on_message: Callable[[str, bytes], None],
        ssl_context: ssl.SSLContext | None,
        keepalive: int = 20,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._on_message = on_message
        # None means plain TCP (tests only); the real broker is TLS.
        self._ssl = ssl_context
        self._keepalive = keepalive
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._subacks: dict[int, asyncio.Future[int]] = {}
        self._next_packet_id = 1
        self._last_rx = 0.0
        self._closed: asyncio.Future[None] | None = None

    @property
    def connected(self) -> bool:
        return self._closed is not None and not self._closed.done()

    async def connect(self, timeout: float = 15) -> None:
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(timeout):
                self._reader, self._writer = await asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=self._ssl,
                    server_hostname=self._host if self._ssl else None,
                )
                self._writer.write(connect_packet(self._client_id, self._username, self._password, self._keepalive))
                await self._writer.drain()
                kind, _, body = await read_packet(self._reader)
        except (OSError, asyncio.IncompleteReadError, TimeoutError) as err:
            await self._close_transport()
            raise MqttError(f"Couldn't connect to {self._host}:{self._port}: {err!r}") from err
        if kind != CONNACK or len(body) < 2:
            await self._close_transport()
            raise MqttError(f"Expected CONNACK, got packet type {kind}")
        if code := body[1]:
            await self._close_transport()
            reason = _CONNACK_REASONS.get(code, f"code {code}")
            raise (MqttAuthError if code in (4, 5) else MqttError)(f"Broker refused connection: {reason}")

        self._closed = loop.create_future()
        self._last_rx = time.monotonic()
        self._tasks = [
            asyncio.create_task(self._read_loop(), name="meowant-mqtt-read"),
            asyncio.create_task(self._ping_loop(), name="meowant-mqtt-ping"),
        ]

    async def subscribe(self, topic: str, qos: int = 1, timeout: float = 10) -> int:
        """Subscribe and return the granted QoS."""
        packet_id = self._packet_id()
        future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._subacks[packet_id] = future
        try:
            await self._send(subscribe_packet(packet_id, topic, qos))
            async with asyncio.timeout(timeout):
                granted = await asyncio.shield(future)
        except TimeoutError as err:
            raise MqttError("No SUBACK from broker") from err
        finally:
            self._subacks.pop(packet_id, None)
        if granted == 0x80:
            raise MqttError("Broker rejected the subscription")
        return granted

    async def publish(self, topic: str, payload: bytes) -> None:
        await self._send(publish_packet(topic, payload))

    async def wait_closed(self) -> None:
        """Return when the connection ends; raises MqttError if it was lost rather than closed."""
        if self._closed is None:
            return
        await asyncio.shield(self._closed)

    async def disconnect(self) -> None:
        if self.connected and self._writer is not None:
            with contextlib.suppress(OSError):
                self._writer.write(packet(DISCONNECT << 4))
                await self._writer.drain()
        await self._shutdown(None)

    # --- internals ---

    def _packet_id(self) -> int:
        packet_id = self._next_packet_id
        self._next_packet_id = packet_id % 0xFFFF + 1
        return packet_id

    async def _send(self, data: bytes) -> None:
        if not self.connected or self._writer is None:
            raise MqttError("Not connected")
        try:
            self._writer.write(data)
            await self._writer.drain()
        except OSError as err:
            await self._shutdown(MqttError(f"Send failed: {err!r}"))
            raise MqttError(f"Send failed: {err!r}") from err

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                kind, flags, body = await read_packet(self._reader)
                self._last_rx = time.monotonic()
                if kind == PUBLISH:
                    topic, qos, packet_id, payload = parse_publish(flags, body)
                    if qos == 1 and packet_id is not None:
                        await self._send(packet(PUBACK << 4, packet_id.to_bytes(2, "big")))
                    try:
                        self._on_message(topic, payload)
                    except Exception:  # a bad handler mustn't kill the connection
                        _LOGGER.exception("Error handling MQTT message")
                elif kind == SUBACK and len(body) >= 3:
                    future = self._subacks.get(int.from_bytes(body[:2], "big"))
                    if future and not future.done():
                        future.set_result(body[2])
                elif kind != PINGRESP:
                    _LOGGER.debug("Ignoring MQTT packet type %s", kind)
        except asyncio.CancelledError:
            raise
        except (OSError, asyncio.IncompleteReadError, MqttError) as err:
            await self._shutdown(MqttError(f"Connection lost: {err!r}"))

    async def _ping_loop(self) -> None:
        interval = self._keepalive / 2
        while True:
            await asyncio.sleep(interval)
            if time.monotonic() - self._last_rx > self._keepalive * 1.5:
                await self._shutdown(MqttError("Broker stopped responding"))
                return
            with contextlib.suppress(MqttError):
                await self._send(packet(PINGREQ << 4))

    async def _shutdown(self, error: MqttError | None) -> None:
        if self._closed is not None and not self._closed.done():
            if error is None:
                self._closed.set_result(None)
            else:
                self._closed.set_exception(error)
                # Mark retrieved, so an unawaited failure doesn't log "exception never retrieved".
                self._closed.exception()
        for future in self._subacks.values():
            if not future.done():
                future.set_exception(error or MqttError("Disconnected"))
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        await self._close_transport()

    async def _close_transport(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, asyncio.CancelledError, ssl.SSLError):
                await writer.wait_closed()
