"""The IM (MQTT) session: live work state and commands for the account's boxes.

Ties together the REST calls that hand out MQTT credentials (api.py), the MQTT
transport (mqtt.py) and the payload encoding (im_proto.py). One session holds
one connection for every box on the account. Protocol reference: API.md
"Commands and live state: IM/MQTT".

No homeassistant dependency, like api.py.
"""
from __future__ import annotations

import logging
import secrets
import ssl
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .api import MeowantApiClient, MeowantApiError
from .const import MQTT_KEEPALIVE, MQTT_PORT
from .im_proto import (
    CMD_ALL_STATE,
    CMD_OPERATION,
    RISP_SEQ_MAX,
    STATE_CMDS,
    ImMessage,
    WorkState,
    build_risp_frame,
    decode_im_message,
    encode_im_message,
    encode_operation,
    new_cid,
)
from .mqtt import MqttClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImCredentials:
    """The parts of an im/login response we use. Secrets are kept out of repr()."""

    client_id: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    pub_topic: str
    sub_topic: str
    uid: str

    @classmethod
    def parse(cls, data: dict[str, Any], fallback_uid: str | None) -> ImCredentials:
        values = {
            "client_id": data.get("clientId"),
            "username": data.get("mqttUserName"),
            "password": data.get("mqttPassword"),
            "pub_topic": data.get("pubTopic"),
            "sub_topic": data.get("subTopic"),
            "uid": data.get("uid") or data.get("userId") or fallback_uid,
        }
        # Name missing fields only: the values are credentials.
        if missing := [name for name, value in values.items() if value in (None, "")]:
            raise MeowantApiError(f"IM login response is missing {', '.join(missing)}")
        return cls(**{name: str(value) for name, value in values.items()})


def contacts_by_serial(friends: list[dict[str, Any]]) -> dict[str, str]:
    """Map box serial -> IM uid from im/friends."""
    return {
        str(f["userName"]): str(f["userId"])
        for f in friends
        if isinstance(f, dict) and f.get("userName") and f.get("userId") is not None
    }


class MeowantImSession:
    """One MQTT connection to the IM channel. Make a new session to reconnect."""

    def __init__(
        self,
        api: MeowantApiClient,
        *,
        ssl_context: ssl.SSLContext | None,
        on_state: Callable[[str, WorkState], None] | None = None,
        on_message: Callable[[ImMessage, str | None], None] | None = None,
        port: int = MQTT_PORT,
    ) -> None:
        self._api = api
        self._ssl_context = ssl_context
        self._port = port
        self._on_state = on_state
        self._on_message = on_message
        self._mqtt: MqttClient | None = None
        self._credentials: ImCredentials | None = None
        self._contacts: dict[str, str] = {}  # serial -> uid
        self._serials: dict[str, str] = {}  # uid -> serial
        self._seq = 1 + secrets.randbelow(RISP_SEQ_MAX)
        self._gid_counter = secrets.randbelow(1 << 20)

    @property
    def connected(self) -> bool:
        return self._mqtt is not None and self._mqtt.connected

    @property
    def contacts(self) -> dict[str, str]:
        return dict(self._contacts)

    async def connect(self) -> None:
        """Fetch credentials and contacts over REST, then connect and subscribe."""
        auth = self._api.auth
        self._credentials = creds = ImCredentials.parse(
            await self._api.get_im_login(), auth.user_id if auth else None
        )
        await self.refresh_contacts()
        host = urllib.parse.urlparse(self._api.tenant.base_url).hostname
        if not host:
            raise MeowantApiError("Tenant base URL has no host")
        self._mqtt = MqttClient(
            host,
            self._port,
            client_id=creds.client_id,
            username=creds.username,
            password=creds.password,
            on_message=self._handle,
            ssl_context=self._ssl_context,
            keepalive=MQTT_KEEPALIVE,
        )
        await self._mqtt.connect()
        try:
            await self._mqtt.subscribe(creds.sub_topic, qos=1)
        except BaseException:
            await self._mqtt.disconnect()
            raise
        _LOGGER.debug("IM connected; boxes in contacts: %d", len(self._contacts))

    async def refresh_contacts(self) -> None:
        self._contacts = contacts_by_serial(await self._api.get_im_friends())
        self._serials = {uid: serial for serial, uid in self._contacts.items()}

    async def request_state(self, serial: str) -> int:
        """Ask a box for its all-state. The reply arrives through on_state. Returns the RISP seq.

        Rarely needed: the box also pushes its state on every change.
        """
        return await self._send(serial, CMD_ALL_STATE, b"")

    async def send_operation(self, serial: str, op_mode: int, op_state: int) -> int:
        """Start/pause/resume an operation (im_proto OP_*). Returns the RISP seq."""
        return await self._send(serial, CMD_OPERATION, encode_operation(op_mode, op_state))

    async def wait_closed(self) -> None:
        """Return when the connection closes; raises MqttError if it was lost."""
        if self._mqtt is not None:
            await self._mqtt.wait_closed()

    async def close(self) -> None:
        if self._mqtt is not None:
            await self._mqtt.disconnect()

    async def _send(self, serial: str, cmd: int, body: bytes) -> int:
        if self._mqtt is None or self._credentials is None:
            raise MeowantApiError("IM session isn't connected")
        if serial not in self._contacts:
            await self.refresh_contacts()  # e.g. a box added since connecting
        if (to_uid := self._contacts.get(serial)) is None:
            raise MeowantApiError(f"Box {serial} isn't in this account's IM contacts")
        seq = self._next_seq()
        now_ms = int(time.time() * 1000)
        payload = encode_im_message(
            frame=build_risp_frame(cmd, seq, body),
            from_uid=self._credentials.uid,
            to_uid=to_uid,
            cid=new_cid(now_ms),
            gid=self._next_gid(now_ms),
            created_at_ms=now_ms,
        )
        await self._mqtt.publish(self._credentials.pub_topic, payload)
        _LOGGER.debug("IM sent cmd %#x seq %d to %s (body %s)", cmd, seq, serial, body.hex())
        return seq

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = seq % RISP_SEQ_MAX + 1
        return seq

    def _next_gid(self, now_ms: int) -> str:
        self._gid_counter = (self._gid_counter + 1) & 0xFFFFF
        return str((now_ms << 20) + self._gid_counter)

    def _handle(self, topic: str, payload: bytes) -> None:
        try:
            message = decode_im_message(payload)
        except ValueError as err:
            _LOGGER.debug("Undecodable IM payload on %s (%s): %s", topic, err, payload.hex())
            return
        serial = self._serials.get(message.from_uid or "")
        _LOGGER.debug("IM received from %s: %s", serial or message.from_uid, message.as_dict())
        if self._on_message is not None:
            self._on_message(message, serial)
        frame = message.frame
        # State comes from the box, as a reply or an unsolicited push. Its body can
        # be empty: proto3 omits zero fields, so an all-zero state has no fields.
        if serial and frame is not None and frame.cmd in STATE_CMDS and self._on_state is not None:
            try:
                state = WorkState.parse(frame.body)
            except ValueError as err:
                _LOGGER.debug("Bad all-state body from %s (%s): %s", serial, err, frame.body.hex())
                return
            self._on_state(serial, state)
