"""YNB 부트스트랩 프로토콜의 메시지 형식과 입력 검증을 정의한다.

Define wire-message formats and input validation for the YNB bootstrap
protocol.
"""

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
    """수신 데이터가 wire-message 규약을 만족하지 않을 때 발생한다.

    Raised when received data does not satisfy the wire-message contract.
    """


def validate_message_id(value: object) -> str:
    """canonical lowercase UUID v4인 메시지 ID를 검증해 반환한다.

    Validate and return a canonical lowercase UUID v4 message ID.
    """

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
    """대문자 MAC 주소 형식의 장비 ID를 검증해 반환한다.

    Validate and return a device ID written as an uppercase MAC address.
    """

    if not isinstance(value, str) or _MAC_PATTERN.fullmatch(value) is None:
        raise ValueError("device_id must use AA:BB:CC:DD:EE:FF form")
    return value


def validate_ipv4(value: object) -> str:
    """축약되지 않은 표준 IPv4 주소 문자열을 검증해 반환한다.

    Validate and return an IPv4 string in canonical dotted-decimal form.
    """

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
    """bool을 제외한 유효한 TCP/UDP 포트 번호를 검증해 반환한다.

    Validate and return a TCP/UDP port number; booleans are not accepted.
    """

    if type(value) is not int or not 1 <= value <= 65_535:
        raise ValueError("port must be an integer between 1 and 65535")
    return value


def validate_rtsp_path(value: object) -> str:
    """슬래시로 시작하며 제어 문자가 없는 RTSP 경로를 검증한다.

    Validate an RTSP path that starts with a slash and has no control bytes.
    """

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
    """유한한 0 이상의 timeout 값을 초 단위 실수로 변환한다.

    Convert a finite, non-negative timeout to seconds as a float.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a finite non-negative number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be a finite non-negative number")
    return timeout


def new_message_id() -> str:
    """새 canonical lowercase UUID v4 문자열을 생성한다.

    Generate a new canonical lowercase UUID v4 string.
    """

    return str(uuid.uuid4())


def make_advertisement(
    device_id: object,
    *,
    message_id: object | None = None,
) -> dict[str, object]:
    """장비의 존재를 알리는 검증된 ADVERTISE 메시지를 만든다.

    Build a validated ADVERTISE message announcing a device.
    """

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
    """ADVERTISE 또는 DETAIL 수신을 확인하는 ACK 메시지를 만든다.

    Build an ACK for a received ADVERTISE or DETAIL message.

    ``message_id``는 새로 만들지 않고 확인 대상 메시지의 값을 사용한다.
    The ID is copied from the acknowledged message rather than generated anew.
    """

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
    """장비 식별자와 RTSP endpoint를 담은 DETAIL 메시지를 만든다.

    Build a DETAIL message containing a device ID and RTSP endpoint.
    """

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
    """메시지를 UTF-8 JSON UDP payload로 직렬화한다.

    Serialize a message as a compact UTF-8 JSON UDP payload.

    JSON으로 표현할 수 없거나 UDP payload 상한을 넘으면 ``ValueError``가
    발생한다. ``ValueError`` is raised for non-JSON values or oversized payloads.
    """

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
    """UTF-8 JSON payload를 객체로 해석하고 최상위 dict 여부를 확인한다.

    Decode a UTF-8 JSON payload and require a dictionary at the top level.
    """

    if not isinstance(data, bytes):
        raise MessageError("wire data must be bytes")

    def reject_constant(value: str) -> None:
        """JSON 표준에 없는 NaN과 Infinity를 명시적으로 거부한다.

        Reject NaN and Infinity, which are outside the JSON standard.
        """

        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        message = json.loads(data.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MessageError("wire data must be a UTF-8 JSON object") from exc
    if not isinstance(message, dict):
        raise MessageError("wire data must be a JSON object")
    return message


def parse_advertisement(message: Mapping[str, Any]) -> dict[str, object]:
    """ADVERTISE의 필드 집합과 각 필드 값을 검증한다.

    Validate the exact field set and values of an ADVERTISE message.
    """

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
    """ACK의 필드 집합, 대상 메시지 종류와 식별자를 검증한다.

    Validate an ACK's exact fields, target message type, and identifiers.
    """

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
    """DETAIL의 필드 집합과 RTSP endpoint 전체를 검증한다.

    Validate the exact fields and complete RTSP endpoint of a DETAIL message.
    """

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
    """누락·추가 필드가 없도록 wire schema를 엄격하게 적용한다.

    Enforce the wire schema strictly, rejecting missing or extra fields.
    """

    if not isinstance(message, Mapping) or set(message) != expected:
        raise MessageError("message fields do not match the wire schema")


def _require_type(message: Mapping[str, Any], expected: str) -> None:
    """message_type이 현재 parser가 처리하는 종류인지 확인한다.

    Ensure ``message_type`` matches the message handled by the parser.
    """

    if message.get("message_type") != expected:
        raise MessageError(f"message_type must be {expected}")
