"""A tiny in-process MQTT broker for tests: plain TCP on localhost, one client."""
from __future__ import annotations

import asyncio

from custom_components.meowant.mqtt import (
    CONNACK,
    CONNECT,
    PINGREQ,
    PINGRESP,
    PUBLISH,
    SUBACK,
    SUBSCRIBE,
    packet,
    read_packet,
)


class FakeBroker:
    def __init__(self, connack_code: int = 0) -> None:
        self.connack_code = connack_code
        self.received: list[tuple[int, int, bytes]] = []  # (type, flags, body)
        self.published: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.writer: asyncio.StreamWriter | None = None
        self.connected = asyncio.Event()
        self._server: asyncio.Server | None = None
        self.port = 0

    async def __aenter__(self) -> FakeBroker:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.writer is not None:
            self.writer.close()
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    def of_type(self, kind: int) -> list[tuple[int, bytes]]:
        return [(flags, body) for t, flags, body in self.received if t == kind]

    async def send(self, data: bytes) -> None:
        assert self.writer is not None
        self.writer.write(data)
        await self.writer.drain()

    async def push(self, topic: str, payload: bytes, *, qos: int = 1, packet_id: int = 7) -> None:
        topic_bytes = len(topic.encode()).to_bytes(2, "big") + topic.encode()
        pid = packet_id.to_bytes(2, "big") if qos else b""
        await self.send(packet(PUBLISH << 4 | qos << 1, topic_bytes + pid + payload))

    async def drop(self) -> None:
        assert self.writer is not None
        self.writer.close()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        try:
            while True:
                kind, flags, body = await read_packet(reader)
                self.received.append((kind, flags, body))
                if kind == CONNECT:
                    await self.send(packet(CONNACK << 4, bytes((0, self.connack_code))))
                    self.connected.set()
                elif kind == SUBSCRIBE:
                    await self.send(packet(SUBACK << 4, body[:2] + bytes((body[-1],))))
                elif kind == PINGREQ:
                    await self.send(packet(PINGRESP << 4))
                elif kind == PUBLISH:
                    end = 2 + int.from_bytes(body[:2], "big")
                    await self.published.put((body[2:end].decode(), body[end:]))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
