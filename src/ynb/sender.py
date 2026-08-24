"""Sender-side ADVERTISE -> ACK -> DETAIL -> ACK exchange."""

from __future__ import annotations

import socket
import time
from typing import Any, Final

from ._protocol import (
    ADVERTISE,
    DEFAULT_TIMEOUT,
    DETAIL,
    MAX_PACKET_SIZE,
    MessageError,
    START_PORT,
    decode_message,
    encode_message,
    make_advertisement,
    make_detail,
    parse_ack,
    validate_port,
    validate_timeout,
)


# SRS CON-004 / FR-SND-002 require the first ADVERTISE to use UDP broadcast.
# This implementation uses the IPv4 limited-broadcast address and intentionally
# exposes no destination override that could turn the bootstrap into unicast.
_BROADCAST_ADDRESS = "255.255.255.255"
_ADVERTISEMENT_INTERVAL: Final = 3.0
_ADVERTISEMENT_ATTEMPTS: Final = 10


def advertise(
    device_id: str,
    ip: str,
    rtsp_port: int,
    rtsp_path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    start_port: int = START_PORT,
) -> bool:
    """Perform the SRS UDP broadcast bootstrap exchange for one Receiver."""

    timeout_value = validate_timeout(timeout)
    destination = (_BROADCAST_ADDRESS, validate_port(start_port))
    advertisement, advertisement_payload = _prepare_advertisement(device_id)
    detail, detail_payload = _prepare_detail(
        device_id,
        ip,
        rtsp_port,
        rtsp_path,
        advertisement_id=str(advertisement["message_id"]),
    )
    deadline = time.monotonic() + timeout_value

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp_socket.bind(("0.0.0.0", 0))
            receiver_peer = _broadcast_until_ack(
                udp_socket,
                advertisement_payload,
                destination,
                deadline=deadline,
                device_id=device_id,
                message_id=str(advertisement["message_id"]),
            )
            if receiver_peer is None:
                return False

            udp_socket.sendto(detail_payload, receiver_peer)

            # CODEX-GENERATED (Codex를 통해 생성된 코드): FR-SND-006 구현.
            # DETAIL ACK는 첫 ACK를 보낸 동일 peer에서 와야 하며 DETAIL의
            # device/message ID와 일치해야 한다.
            detail_ack_peer = _wait_for_matching_ack(
                udp_socket,
                deadline=deadline,
                device_id=device_id,
                ack_for=DETAIL,
                message_id=str(detail["message_id"]),
                expected_peer=receiver_peer,
            )
            return detail_ack_peer is not None
    except (OSError, OverflowError):
        return False


def _prepare_advertisement(
    device_id: str,
) -> tuple[dict[str, object], bytes]:
    """Validate and encode ADVERTISE before network activity."""

    advertisement = make_advertisement(device_id)
    return advertisement, encode_message(advertisement)


def _prepare_detail(
    device_id: str,
    ip: str,
    rtsp_port: int,
    rtsp_path: str,
    *,
    advertisement_id: str,
) -> tuple[dict[str, object], bytes]:
    """Validate DETAIL and ensure it has a new ID before network activity."""

    detail = make_detail(device_id, ip, rtsp_port, rtsp_path)
    # FR-SND-008: one exchange must use different IDs for its two messages.
    while detail["message_id"] == advertisement_id:
        detail = make_detail(device_id, ip, rtsp_port, rtsp_path)
    return detail, encode_message(detail)


def _broadcast_until_ack(
    udp_socket: socket.socket,
    advertisement_payload: bytes,
    destination: tuple[str, int],
    *,
    deadline: float,
    device_id: str,
    message_id: str,
) -> tuple[str, int] | None:
    """Broadcast one ADVERTISE every three seconds, at most ten times."""

    for _attempt in range(_ADVERTISEMENT_ATTEMPTS):
        udp_socket.sendto(advertisement_payload, destination)
        attempt_deadline = min(
            deadline,
            time.monotonic() + _ADVERTISEMENT_INTERVAL,
        )
        receiver_peer = _wait_for_matching_ack(
            udp_socket,
            deadline=attempt_deadline,
            device_id=device_id,
            ack_for=ADVERTISE,
            message_id=message_id,
        )
        if receiver_peer is not None:
            return receiver_peer
        if time.monotonic() >= deadline:
            return None
    return None


def _wait_for_matching_ack(
    udp_socket: socket.socket,
    *,
    deadline: float,
    device_id: str,
    ack_for: str,
    message_id: str,
    expected_peer: tuple[str, int] | None = None,
) -> tuple[str, int] | None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            udp_socket.settimeout(remaining)
            payload, peer = udp_socket.recvfrom(MAX_PACKET_SIZE)
        except (socket.timeout, TimeoutError):
            return None
        except (OSError, OverflowError):
            return None

        # CODEX-GENERATED (Codex를 통해 생성된 코드): DETAIL ACK 단계에서는
        # 최초 ADVERTISE ACK의 실제 peer와 정확히 같은 발신자만 허용한다.
        if expected_peer is not None and peer != expected_peer:
            continue
        try:
            ack: dict[str, Any] = parse_ack(decode_message(payload))
        except MessageError:
            continue
        if (
            ack["device_id"] == device_id
            and ack["ack_for"] == ack_for
            and ack["message_id"] == message_id
        ):
            return peer
