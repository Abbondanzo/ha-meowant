"""Encoding and decoding for the IM/MQTT command channel. Protocol reference:
API.md "Commands and live state: IM/MQTT".

Every MQTT payload is protobuf nested five deep (IM Message -> MessageContent
-> Any -> BytesValue -> CommandProto), wrapping a RISP frame whose body is
itself protobuf. There's no .proto file, so this hand-rolls the few wire
types involved.

Like api.py, this module has no homeassistant dependency.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from typing import Any

# --- RISP ---
RISP_START = 0xDB
RISP_DEV_AIRPET = 2
RISP_ATTR_REQUEST = 1
RISP_SEQ_MAX = 0x7FFF

CMD_OPERATION = 0x3FB  # the box acks with the same cmd and seq, empty body
CMD_ALL_STATE = 0x3FD  # request and reply use the same cmd
CMD_STATE_PUSH = 0x400  # sent by the box on every work-state change; all-state body
# Frames whose body is an all-state body.
STATE_CMDS = frozenset({CMD_ALL_STATE, CMD_STATE_PUSH})

COMMAND_PROTYPE_RISP = 2

# --- IM envelope ---
IM_PROTOCOL_VERSION = "1.0.1"
CONTENT_TYPE_CUSTOM = "custom"
TYPE_URL_BYTES_VALUE = "type.googleapis.com/google.protobuf.BytesValue"
TYPE_URL_RECEIPT = "type.googleapis.com/rosa.im.protobuf.ReceiptNotification"

# --- Operations (what we send). Op modes are NOT the reported work modes. ---
OP_CLEAN = 1
OP_LEVEL = 3
OP_RISE = 7
OP_DROP = 8

OP_START = 1
OP_PAUSE = 2
OP_RESUME = 3

# --- All-state reply (what the box reports) ---
WORK_MODE_IDLE = 0
WORK_MODE_RESETTING = 1
WORK_MODE_CLEANING = 2
WORK_MODE_LEVELLING = 3
WORK_MODE_REPLACING_LITTER = 4
WORK_MODE_RAKING_UP = 5

WORK_MODE_NAMES = {
    WORK_MODE_IDLE: "idle",
    WORK_MODE_RESETTING: "resetting",
    WORK_MODE_CLEANING: "cleaning",
    WORK_MODE_LEVELLING: "levelling",
    WORK_MODE_REPLACING_LITTER: "replacing_litter",
    WORK_MODE_RAKING_UP: "raking_up",
}

WORK_STATE_IDLE = 0
WORK_STATE_RUNNING = 1
WORK_STATE_BLOCKED = 2
WORK_STATE_PAUSED = 3

WORK_STATE_NAMES = {
    WORK_STATE_IDLE: "idle",
    WORK_STATE_RUNNING: "running",
    WORK_STATE_BLOCKED: "blocked",
    WORK_STATE_PAUSED: "paused",
}

# The op mode that pauses/resumes a given work mode.
WORK_MODE_TO_OP = {WORK_MODE_CLEANING: OP_CLEAN, WORK_MODE_LEVELLING: OP_LEVEL}

ALL_STATE_FIELDS = {
    1: "work_mode",
    2: "work_state",
    3: "cause",
    4: "toilet",
    5: "foreign_object",
    6: "weigh",
    7: "lid",
    8: "motor",
    9: "key",
    10: "object",
    11: "camera",
    12: "image_ctrl",
    13: "picture_ctrl",
    14: "update",
    15: "hall",
    16: "infrared",
    17: "box",
    18: "timestamp",
    19: "weigh_sensor",
    20: "led",
}

_WIRE_VARINT = 0
_WIRE_FIXED64 = 1
_WIRE_BYTES = 2
_WIRE_FIXED32 = 5


# --- protobuf wire format ---


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _varint_field(number: int, value: int) -> bytes:
    # proto3: zero values are omitted.
    return encode_varint(number << 3 | _WIRE_VARINT) + encode_varint(value) if value else b""


def _bytes_field(number: int, value: bytes | str) -> bytes:
    if isinstance(value, str):
        value = value.encode()
    return encode_varint(number << 3 | _WIRE_BYTES) + encode_varint(len(value)) + value if value else b""


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def decode_fields(data: bytes) -> dict[int, list[int | bytes]]:
    """Decode one protobuf message into {field number: [values]}, in wire order.

    Varints and fixed-width ints come back as int, length-delimited fields as bytes.
    """
    fields: dict[int, list[int | bytes]] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        value: int | bytes
        if wire == _WIRE_VARINT:
            value, pos = _decode_varint(data, pos)
        elif wire == _WIRE_BYTES:
            length, pos = _decode_varint(data, pos)
            if pos + length > len(data):
                raise ValueError("truncated length-delimited field")
            value, pos = data[pos : pos + length], pos + length
        elif wire in (_WIRE_FIXED64, _WIRE_FIXED32):
            size = 8 if wire == _WIRE_FIXED64 else 4
            if pos + size > len(data):
                raise ValueError("truncated fixed-width field")
            value, pos = int.from_bytes(data[pos : pos + size], "little"), pos + size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        fields.setdefault(number, []).append(value)
    return fields


def _int(fields: dict[int, list[int | bytes]], number: int) -> int | None:
    return next((v for v in fields.get(number, []) if isinstance(v, int)), None)


def _bytes(fields: dict[int, list[int | bytes]], number: int) -> bytes | None:
    return next((v for v in fields.get(number, []) if isinstance(v, bytes)), None)


def _str(fields: dict[int, list[int | bytes]], number: int) -> str | None:
    value = _bytes(fields, number)
    return value.decode("utf-8", errors="replace") if value is not None else None


# --- RISP frames ---


def crc8(data: bytes) -> int:
    """CRC-8, poly 0x07, init 0x00, final XOR 0x55."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc ^ 0x55


def build_risp_frame(cmd: int, seq: int, body: bytes = b"") -> bytes:
    header = b"".join(
        (
            _varint_field(1, RISP_DEV_AIRPET),
            _varint_field(2, cmd),
            _varint_field(4, len(body)),
            _varint_field(5, RISP_ATTR_REQUEST),
            _varint_field(7, seq),
        )
    )
    frame = bytes((RISP_START, len(header))) + header + bytes((crc8(header),))
    if body:
        frame += body + bytes((crc8(body),))
    return frame


@dataclass(frozen=True, slots=True)
class RispFrame:
    dev: int | None
    cmd: int | None
    attr: int | None
    seq: int | None
    body: bytes
    # False if either checksum didn't match. Parsed anyway: the CRC model is
    # only confirmed for frames we send, so don't drop replies over it.
    crc_ok: bool


def parse_risp_frame(frame: bytes) -> RispFrame:
    if len(frame) < 3 or frame[0] != RISP_START:
        raise ValueError("not a RISP frame")
    header_end = 2 + frame[1]
    if header_end >= len(frame):
        raise ValueError("truncated RISP header")
    header = frame[2:header_end]
    fields = decode_fields(header)
    crc_ok = frame[header_end] == crc8(header)
    body_len = _int(fields, 4) or 0
    body = frame[header_end + 1 : header_end + 1 + body_len]
    if len(body) != body_len:
        raise ValueError("truncated RISP body")
    if body_len:
        body_crc = frame[header_end + 1 + body_len : header_end + 2 + body_len]
        crc_ok = crc_ok and body_crc == bytes((crc8(body),))
    return RispFrame(
        dev=_int(fields, 1), cmd=_int(fields, 2), attr=_int(fields, 5), seq=_int(fields, 7), body=body, crc_ok=crc_ok
    )


def encode_operation(op_mode: int, op_state: int) -> bytes:
    return _varint_field(1, op_mode) + _varint_field(2, op_state)


# --- IM messages ---


def new_cid(now_ms: int) -> str:
    """Client message ID: "<ms>+<6-digit random>"."""
    return f"{now_ms}+{100000 + secrets.randbelow(900000)}"


def encode_im_message(*, frame: bytes, from_uid: str, to_uid: str, cid: str, gid: str, created_at_ms: int) -> bytes:
    command = _varint_field(1, COMMAND_PROTYPE_RISP) + _bytes_field(2, frame)
    any_value = _bytes_field(1, TYPE_URL_BYTES_VALUE) + _bytes_field(2, _bytes_field(1, command))
    content = _bytes_field(1, CONTENT_TYPE_CUSTOM) + _bytes_field(2, any_value)
    return b"".join(
        (
            _bytes_field(1, cid),
            _bytes_field(2, gid),
            _varint_field(3, created_at_ms),
            _bytes_field(4, from_uid),
            _bytes_field(5, to_uid),
            _bytes_field(7, content),
            _bytes_field(8, IM_PROTOCOL_VERSION),
        )
    )


@dataclass(frozen=True, slots=True)
class ImMessage:
    """A decoded IM message. Inner layers are None when absent or not understood."""

    cid: str | None
    gid: str | None
    created_at_ms: int | None
    from_uid: str | None
    to_uid: str | None
    type: int | None
    content_type: str | None = None
    type_url: str | None = None
    frame: RispFrame | None = None
    receipt_status: str | None = None
    receipt_cause: str | None = None
    # Why an inner layer couldn't be decoded, if one couldn't.
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly view, for logs and the dev console."""
        out: dict[str, Any] = {
            "cid": self.cid,
            "gid": self.gid,
            "created_at_ms": self.created_at_ms,
            "from_uid": self.from_uid,
            "to_uid": self.to_uid,
            "type": self.type,
            "content_type": self.content_type,
            "type_url": self.type_url,
        }
        if self.receipt_status is not None or self.receipt_cause is not None:
            out["receipt"] = {"status": self.receipt_status, "cause": self.receipt_cause}
        if self.frame is not None:
            out["risp"] = {
                "dev": self.frame.dev,
                "cmd": hex(self.frame.cmd) if self.frame.cmd is not None else None,
                "attr": self.frame.attr,
                "seq": self.frame.seq,
                "body_hex": self.frame.body.hex(),
                "crc_ok": self.frame.crc_ok,
            }
            # A reply or push, not our own request echoed back. Its body may be empty when all fields are 0.
            if self.frame.cmd in STATE_CMDS and (self.frame.body or self.frame.attr != RISP_ATTR_REQUEST):
                out["state"] = WorkState.parse(self.frame.body).as_dict()
        if self.error:
            out["error"] = self.error
        return out


def decode_im_message(payload: bytes) -> ImMessage:
    """Decode an incoming MQTT payload. Raises ValueError only if the outer message is unreadable."""
    fields = decode_fields(payload)
    message = ImMessage(
        cid=_str(fields, 1),
        gid=_str(fields, 2),
        created_at_ms=_int(fields, 3),
        from_uid=_str(fields, 4),
        to_uid=_str(fields, 5),
        type=_int(fields, 6),
    )
    content = _bytes(fields, 7)
    if content is None:
        return message
    try:
        return _decode_content(message, content)
    except ValueError as err:
        return replace(message, error=str(err))


def _decode_content(message: ImMessage, content: bytes) -> ImMessage:
    content_fields = decode_fields(content)
    message = replace(message, content_type=_str(content_fields, 1))
    any_fields = decode_fields(_bytes(content_fields, 2) or b"")
    type_url = _str(any_fields, 1)
    value = _bytes(any_fields, 2) or b""
    message = replace(message, type_url=type_url)
    if type_url == TYPE_URL_RECEIPT:
        receipt = decode_fields(value)
        return replace(message, receipt_status=_str(receipt, 1), receipt_cause=_str(receipt, 2))
    if type_url != TYPE_URL_BYTES_VALUE:
        return message
    command = decode_fields(_bytes(decode_fields(value), 1) or b"")
    protype, frame = _int(command, 1), _bytes(command, 2)
    if protype != COMMAND_PROTYPE_RISP or frame is None:
        return message
    return replace(message, frame=parse_risp_frame(frame))


# --- Work state ---


@dataclass(frozen=True, slots=True)
class WorkState:
    """One all-state reply. Most sub-state fields aren't decoded yet (see API.md)."""

    work_mode: int
    work_state: int
    cause: int
    fields: dict[str, int] = field(default_factory=dict)

    @classmethod
    def parse(cls, body: bytes) -> WorkState:
        raw = decode_fields(body)
        named = {
            ALL_STATE_FIELDS.get(number, f"field_{number}"): values[0]
            for number, values in sorted(raw.items())
            if isinstance(values[0], int)
        }
        # proto3 omits zeros, so a missing field means 0.
        return cls(
            work_mode=named.get("work_mode", 0),
            work_state=named.get("work_state", 0),
            cause=named.get("cause", 0),
            fields=named,
        )

    @property
    def is_idle(self) -> bool:
        # Quirk: SC-01 can report mode 0 with state "running" while idle. Mode wins.
        return self.work_mode == WORK_MODE_IDLE

    @property
    def status(self) -> str | None:
        """One summary value: idle, paused, blocked, or what it's doing. None if unrecognized."""
        if self.is_idle:
            return "idle"
        if self.work_state == WORK_STATE_PAUSED:
            return "paused"
        if self.work_state == WORK_STATE_BLOCKED:
            return "blocked"
        return WORK_MODE_NAMES.get(self.work_mode)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "work_mode": self.work_mode,
            "work_mode_name": WORK_MODE_NAMES.get(self.work_mode),
            "work_state": self.work_state,
            "work_state_name": WORK_STATE_NAMES.get(self.work_state),
            "cause": self.cause,
            "fields": dict(self.fields),
        }
