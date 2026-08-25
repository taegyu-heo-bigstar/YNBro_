"""Sender 측 ADVERTISE → ACK → DETAIL → ACK 교환을 수행한다.

Perform the sender side of the ADVERTISE → ACK → DETAIL → ACK exchange.
"""

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


# SRS CON-004 / FR-SND-002에 따라 최초 ADVERTISE는 UDP broadcast여야 한다.
# The first ADVERTISE must be a UDP broadcast under SRS CON-004 / FR-SND-002.
# 따라서 공개 API가 unicast로 우회되지 않도록 IPv4 limited broadcast 주소를
# 내부 상수로 고정한다. Keeping this address private prevents a public unicast
# override from weakening the bootstrap contract.
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
    """Receiver 한 대와 UDP 부트스트랩 교환을 수행한다.

    Perform the UDP bootstrap exchange with one Receiver.

    전체 timeout 안에 두 ACK를 모두 확인하면 ``True``를 반환한다. 네트워크
    오류 또는 timeout은 예외로 전파하지 않고 ``False``로 처리한다.
    Return ``True`` only when both ACKs arrive within the shared timeout;
    network errors and timeouts produce ``False``.
    """

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

            # FR-SND-006: DETAIL ACK는 최초 ADVERTISE ACK와 같은 실제 peer에서
            # 와야 하며 DETAIL의 device/message ID와 일치해야 한다.
            # The DETAIL ACK must come from the selected peer and match the
            # DETAIL device/message identifiers.
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
    """socket을 열기 전에 ADVERTISE를 검증하고 인코딩한다.

    Validate and encode ADVERTISE before any network activity begins.
    """

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
    """DETAIL을 검증·인코딩하고 ADVERTISE와 다른 ID를 보장한다.

    Validate and encode DETAIL, ensuring its ID differs from ADVERTISE.
    """

    detail = make_detail(device_id, ip, rtsp_port, rtsp_path)
    # FR-SND-008: 한 교환의 ADVERTISE와 DETAIL은 서로 다른 ID를 사용한다.
    # One exchange must use distinct IDs for ADVERTISE and DETAIL.
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
    """ADVERTISE를 3초 간격으로 최대 10회 broadcast한다.

    Broadcast ADVERTISE every three seconds, up to ten attempts.

    각 대기 구간은 전체 deadline을 넘지 않는다. Each wait interval is capped
    by the exchange-wide deadline.
    """

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
    """deadline까지 현재 교환과 정확히 대응하는 ACK를 기다린다.

    Wait until the deadline for an ACK that exactly matches this exchange.

    손상된 패킷, 다른 메시지용 ACK, 선택되지 않은 peer의 응답은 무시한다.
    Malformed packets, unrelated ACKs, and responses from an unselected peer
    are ignored.
    """

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

        # DETAIL 단계에서는 최초 ACK의 실제 peer와 같은 발신자만 허용한다.
        # During DETAIL, accept only the peer selected by the first ACK.
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
