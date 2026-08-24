"""Wire messages and input validation for the YNB bootstrap protocol."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import uuid
from typing import Any, Final, Mapping


START_PORT: Final = 37_020
MAX_PACKET_SIZE: Final = 65_507
DEFAULT_TIMEOUT: Final = 30.0

ADVERTISE: Final = "ADVERTISE"
ACK: Final = "ACK"
DETAIL: Final = "DETAIL"

_MAC_PATTERN = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z")


class MessageError(ValueError):
    """A received value does not satisfy the wire-message contract."""


def validate_message_id(value: object) -> str:
    """Return a canonical lowercase UUID v4 message ID."""

    if not isinstance(value, str):
        raise ValueError("message_id must be a string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("message_id must be a UUID v4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("message_id must be a canonical lowercase UUID v4")
    return value


def validate_device_id(value: object) -> str:
    if not isinstance(value, str) or _MAC_PATTERN.fullmatch(value) is None:
        raise ValueError("device_id must use AA:BB:CC:DD:EE:FF form")
    return value


def validate_ipv4(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("ip must be an IPv4 address string")
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("ip must be a valid IPv4 address") from exc
    if str(parsed) != value:
        raise ValueError("ip must be in canonical IPv4 form")
    return value


def validate_port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65_535:
        raise ValueError("port must be an integer between 1 and 65535")
    return value


def validate_rtsp_path(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("rtsp_path must be a string beginning with /")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("rtsp_path must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("rtsp_path must be valid UTF-8 text") from exc
    return value


def validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a finite non-negative number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be a finite non-negative number")
    return timeout


def new_message_id() -> str:
    return str(uuid.uuid4())


def make_advertisement(
    device_id: object,
    *,
    message_id: object | None = None,
) -> dict[str, object]:
    return {
        "message_type": ADVERTISE,
        "message_id": validate_message_id(
            new_message_id() if message_id is None else message_id
        ),
        "device_id": validate_device_id(device_id),
    }


def make_ack(
    device_id: object,
    ack_for: object,
    *,
    message_id: object,
) -> dict[str, object]:
    if ack_for not in (ADVERTISE, DETAIL):
        raise ValueError("ack_for must be ADVERTISE or DETAIL")
    return {
        "message_type": ACK,
        "message_id": validate_message_id(message_id),
        "device_id": validate_device_id(device_id),
        "ack_for": ack_for,
    }


def make_detail(
    device_id: object,
    ip: object,
    rtsp_port: object,
    rtsp_path: object,
    *,
    message_id: object | None = None,
) -> dict[str, object]:
    return {
        "message_type": DETAIL,
        "message_id": validate_message_id(
            new_message_id() if message_id is None else message_id
        ),
        "device_id": validate_device_id(device_id),
        "ip": validate_ipv4(ip),
        "rtsp_port": validate_port(rtsp_port),
        "rtsp_path": validate_rtsp_path(rtsp_path),
    }


def encode_message(message: Mapping[str, object]) -> bytes:
    try:
        payload = json.dumps(
            dict(message),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("message must be UTF-8 JSON serializable") from exc
    if len(payload) > MAX_PACKET_SIZE:
        raise ValueError("message exceeds the UDP datagram payload limit")
    return payload


def decode_message(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise MessageError("wire data must be bytes")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        message = json.loads(data.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MessageError("wire data must be a UTF-8 JSON object") from exc
    if not isinstance(message, dict):
        raise MessageError("wire data must be a JSON object")
    return message


def parse_advertisement(message: Mapping[str, Any]) -> dict[str, object]:
    _require_exact_fields(message, {"message_type", "message_id", "device_id"})
    _require_type(message, ADVERTISE)
    try:
        return {
            "message_type": ADVERTISE,
            "message_id": validate_message_id(message["message_id"]),
            "device_id": validate_device_id(message["device_id"]),
        }
    except (KeyError, ValueError) as exc:
        raise MessageError("invalid ADVERTISE message") from exc


def parse_ack(message: Mapping[str, Any]) -> dict[str, object]:
    _require_exact_fields(
        message, {"message_type", "message_id", "device_id", "ack_for"}
    )
    _require_type(message, ACK)
    try:
        ack_for = message["ack_for"]
        if ack_for not in (ADVERTISE, DETAIL):
            raise ValueError("invalid ack_for")
        return {
            "message_type": ACK,
            "message_id": validate_message_id(message["message_id"]),
            "device_id": validate_device_id(message["device_id"]),
            "ack_for": ack_for,
        }
    except (KeyError, ValueError) as exc:
        raise MessageError("invalid ACK message") from exc


def parse_detail(message: Mapping[str, Any]) -> dict[str, object]:
    _require_exact_fields(
        message,
        {
            "message_type",
            "message_id",
            "device_id",
            "ip",
            "rtsp_port",
            "rtsp_path",
        },
    )
    _require_type(message, DETAIL)
    try:
        return {
            "message_type": DETAIL,
            "message_id": validate_message_id(message["message_id"]),
            "device_id": validate_device_id(message["device_id"]),
            "ip": validate_ipv4(message["ip"]),
            "rtsp_port": validate_port(message["rtsp_port"]),
            "rtsp_path": validate_rtsp_path(message["rtsp_path"]),
        }
    except (KeyError, ValueError) as exc:
        raise MessageError("invalid DETAIL message") from exc


def _require_exact_fields(message: Mapping[str, Any], expected: set[str]) -> None:
    if not isinstance(message, Mapping) or set(message) != expected:
        raise MessageError("message fields do not match the wire schema")


def _require_type(message: Mapping[str, Any], expected: str) -> None:
    if message.get("message_type") != expected:
        raise MessageError(f"message_type must be {expected}")
