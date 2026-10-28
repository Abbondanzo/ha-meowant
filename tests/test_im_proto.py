"""Unit tests for custom_components/meowant/im_proto.py (IM/MQTT payload encoding)."""
from __future__ import annotations

import pytest

from custom_components.meowant.im_proto import (
    CMD_ALL_STATE,
    CMD_OPERATION,
    OP_CLEAN,
    OP_LEVEL,
    OP_PAUSE,
    OP_RESUME,
    OP_START,
    TYPE_URL_RECEIPT,
    WorkState,
    _bytes_field,
    build_risp_frame,
    crc8,
    decode_fields,
    decode_im_message,
    encode_im_message,
    encode_operation,
    encode_varint,
    new_cid,
    parse_risp_frame,
)

# Reference frames from API.md "RISP frame" (seq 1).
STATE_REQUEST = bytes.fromhex("db09080210fd0728013801f6")
START_CLEAN = bytes.fromhex("db0b080210fb072004280138018b08011001de")


def test_varint():
    assert encode_varint(0) == b"\x00"
    assert encode_varint(1) == b"\x01"
    assert encode_varint(300) == b"\xac\x02"
    assert decode_fields(b"\x08" + encode_varint(2**40)) == {1: [2**40]}


def test_reference_frames():
    assert build_risp_frame(CMD_ALL_STATE, 1) == STATE_REQUEST
    assert build_risp_frame(CMD_OPERATION, 1, encode_operation(OP_CLEAN, OP_START)) == START_CLEAN


@pytest.mark.parametrize(
    "op_mode, op_state, hex_body",
    [(OP_CLEAN, OP_START, "08011001"), (OP_CLEAN, OP_PAUSE, "08011002"), (OP_CLEAN, OP_RESUME, "08011003"),
     (OP_LEVEL, OP_START, "08031001")],
)
def test_operation_bodies(op_mode, op_state, hex_body):
    assert encode_operation(op_mode, op_state).hex() == hex_body


def test_crc8_matches_reference_frames():
    # Header CRC sits right after the header; body CRC is the last byte.
    assert crc8(START_CLEAN[2:13]) == START_CLEAN[13]
    assert crc8(bytes.fromhex("08011001")) == START_CLEAN[-1]


def test_parse_risp_frame():
    frame = parse_risp_frame(START_CLEAN)
    assert (frame.dev, frame.cmd, frame.attr, frame.seq) == (2, CMD_OPERATION, 1, 1)
    assert frame.body == bytes.fromhex("08011001") and frame.crc_ok
    empty = parse_risp_frame(STATE_REQUEST)
    assert empty.cmd == CMD_ALL_STATE and empty.body == b"" and empty.crc_ok


def test_parse_risp_frame_flags_bad_crc_but_still_parses():
    corrupted = START_CLEAN[:-1] + b"\x00"
    frame = parse_risp_frame(corrupted)
    assert not frame.crc_ok and frame.body == bytes.fromhex("08011001")


@pytest.mark.parametrize("bad", [b"", b"\x00\x01\x02", bytes.fromhex("db09080210fd07")])
def test_parse_risp_frame_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_risp_frame(bad)


def test_seq_wraps_into_two_byte_varint():
    frame = parse_risp_frame(build_risp_frame(CMD_ALL_STATE, 0x7FFF))
    assert frame.seq == 0x7FFF and frame.crc_ok


def test_im_message_round_trip():
    payload = encode_im_message(
        frame=START_CLEAN, from_uid="111", to_uid="222", cid="1+123456", gid="99", created_at_ms=1792550769000
    )
    message = decode_im_message(payload)
    assert (message.cid, message.gid, message.created_at_ms) == ("1+123456", "99", 1792550769000)
    assert (message.from_uid, message.to_uid, message.content_type) == ("111", "222", "custom")
    assert message.type_url == "type.googleapis.com/google.protobuf.BytesValue"
    assert message.frame is not None and message.frame.cmd == CMD_OPERATION and message.error is None


def test_im_message_layout():
    # Field 7 is MessageContent, and the protocol version trails as field 8.
    fields = decode_fields(
        encode_im_message(frame=STATE_REQUEST, from_uid="1", to_uid="2", cid="c", gid="g", created_at_ms=5)
    )
    assert fields[8] == [b"1.0.1"]
    assert sorted(fields) == [1, 2, 3, 4, 5, 7, 8]


def _box_reply(body_hex: str, attr: int = 2) -> bytes:
    """An all-state reply as the box would send it (attr 2 = response, assumed)."""
    body = bytes.fromhex(body_hex)
    header = bytes.fromhex("080210fd07") + (bytes((0x20, len(body))) if body else b"") + bytes((0x28, attr, 0x38, 0x05))
    frame = bytes((0xDB, len(header))) + header + bytes((crc8(header),))
    if body:
        frame += body + bytes((crc8(body),))
    return encode_im_message(frame=frame, from_uid="222", to_uid="111", cid="c", gid="g", created_at_ms=1)


def test_decode_all_state_reply():
    message = decode_im_message(_box_reply("080210011803"))
    state = WorkState.parse(message.frame.body)
    assert (state.work_mode, state.work_state, state.cause, state.status) == (2, 1, 3, "cleaning")
    assert message.as_dict()["state"]["work_mode_name"] == "cleaning"


def test_empty_all_state_reply_means_idle():
    # proto3 omits zeros: an idle box with every field 0 sends an empty body.
    message = decode_im_message(_box_reply(""))
    assert message.frame.body == b""
    assert message.as_dict()["state"]["status"] == "idle"


def test_own_state_request_isnt_shown_as_state():
    payload = encode_im_message(frame=STATE_REQUEST, from_uid="1", to_uid="2", cid="c", gid="g", created_at_ms=1)
    assert "state" not in decode_im_message(payload).as_dict()


@pytest.mark.parametrize(
    "mode, state, status",
    [(0, 0, "idle"), (0, 1, "idle"), (2, 1, "cleaning"), (3, 1, "levelling"), (2, 3, "paused"), (3, 2, "blocked"),
     (9, 1, None)],
)
def test_work_state_status(mode, state, status):
    # (0, 1): SC-01 quirk, mode 0 + running is really idle.
    body = (b"\x08" + bytes((mode,)) if mode else b"") + (b"\x10" + bytes((state,)) if state else b"")
    assert WorkState.parse(body).status == status


def test_work_state_names_known_and_unknown_fields():
    state = WorkState.parse(bytes.fromhex("0802") + bytes.fromhex("9001" + "2a") + bytes.fromhex("a80107"))
    assert state.fields == {"work_mode": 2, "timestamp": 42, "field_21": 7}


def test_decode_receipt():
    receipt = _bytes_field(1, "receipt/unreachable") + _bytes_field(2, "offline")
    content = _bytes_field(1, "custom") + _bytes_field(2, _bytes_field(1, TYPE_URL_RECEIPT) + _bytes_field(2, receipt))
    message = decode_im_message(_bytes_field(1, "c") + _bytes_field(7, content))
    assert (message.receipt_status, message.receipt_cause) == ("receipt/unreachable", "offline")
    assert message.as_dict()["receipt"] == {"status": "receipt/unreachable", "cause": "offline"}


def test_decode_tolerates_bad_inner_layer():
    # Outer message fine, content garbage: report it rather than raise.
    message = decode_im_message(_bytes_field(1, "c") + _bytes_field(7, b"\x0a\xff"))
    assert message.cid == "c" and message.error


# Real frames from a Meowant SC-01 (2026-10-28), rebuilt around the observed bodies.
def _box_frame(cmd: int, attr: int, seq: int, body: bytes) -> bytes:
    from custom_components.meowant.im_proto import _varint_field

    header = _varint_field(1, 2) + _varint_field(2, cmd) + _varint_field(4, len(body)) + _varint_field(5, attr)
    header += _varint_field(7, seq)
    frame = bytes((0xDB, len(header))) + header + bytes((crc8(header),))
    return frame + (body + bytes((crc8(body),)) if body else b"")


@pytest.mark.parametrize(
    "body_hex, status, mode, state, cause",
    [
        ("0802100118329001b2f785d706", "cleaning", 2, 1, 50),  # pushed after "start clean"
        ("08021003183c9001c2f785d706", "paused", 2, 3, 60),  # pushed after "pause clean"
        ("1001185a9001b1f885d706", "idle", 0, 1, 90),  # pushed when the clean finished (mode-0 quirk)
    ],
)
def test_state_push_is_decoded_as_state(body_hex, status, mode, state, cause):
    frame = _box_frame(0x400, 6, 1496, bytes.fromhex(body_hex))
    payload = encode_im_message(frame=frame, from_uid="222", to_uid="111", cid="c", gid="g", created_at_ms=1)
    decoded = decode_im_message(payload).as_dict()
    assert decoded["risp"]["cmd"] == "0x400" and decoded["risp"]["crc_ok"]
    assert (decoded["state"]["status"], decoded["state"]["work_mode"]) == (status, mode)
    assert (decoded["state"]["work_state"], decoded["state"]["cause"]) == (state, cause)


def test_operation_ack_is_not_state():
    frame = _box_frame(CMD_OPERATION, 2, 29440, b"")
    payload = encode_im_message(frame=frame, from_uid="222", to_uid="111", cid="c", gid="g", created_at_ms=1)
    assert "state" not in decode_im_message(payload).as_dict()


def test_new_cid_format():
    ms, suffix = new_cid(1792550769000).split("+")
    assert ms == "1792550769000" and len(suffix) == 6 and suffix.isdigit()
