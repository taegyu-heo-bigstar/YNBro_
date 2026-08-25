"""Receiver 측 UDP 발견 교환과 RTSP 연결 확인을 수행한다.

Perform receiver-side UDP discovery and RTSP endpoint probing.
"""

from __future__ import annotations

import socket
import time
from typing import Any

from . import connecter
from ._protocol import (
    ADVERTISE,
    DEFAULT_TIMEOUT,
    DETAIL,
    MAX_PACKET_SIZE,
    MessageError,
    START_PORT,
    decode_message,
    encode_message,
    make_ack,
    parse_advertisement,
    parse_detail,
    validate_ipv4,
    validate_port,
    validate_timeout,
)


def discover(
    timeout: float = DEFAULT_TIMEOUT,
    *,
    start_port: int = START_PORT,
    bind_host: str = "0.0.0.0",
) -> dict[str, object] | None:
    """Sender 한 대를 발견하고 RTSP endpoint 확인 결과를 반환한다.

    Discover one Sender and return the result of probing its RTSP endpoint.

    최초 유효 ADVERTISE를 보낸 peer가 선택되며, 전체 timeout 안에 교환이
    끝나지 않거나 socket 오류가 발생하면 ``None``을 반환한다.
    The first valid advertiser is selected; an incomplete exchange or socket
    error returns ``None``.
    """

    timeout_value = validate_timeout(timeout)
    listen_port = validate_port(start_port)
    listen_host = validate_ipv4(bind_host)
    deadline = time.monotonic() + timeout_value

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_socket.bind((listen_host, listen_port))

            advertisement_result = _accept_advertisement(udp_socket, deadline)
            if advertisement_result is None:
                return None
            advertisement, sender_peer = advertisement_result

            detail = _accept_detail(
                udp_socket,
                deadline=deadline,
                sender_peer=sender_peer,
                device_id=str(advertisement["device_id"]),
                advertisement_id=str(advertisement["message_id"]),
            )
            if detail is None:
                return None
            return _probe_and_build_result(detail, deadline)
    except (OSError, OverflowError):
        return None


def _receive_message(
    udp_socket: socket.socket,
    deadline: float,
) -> tuple[dict[str, Any], tuple[str, int]] | None:
    """deadline 전에 해석 가능한 다음 메시지와 실제 peer를 반환한다.

    Return the next decodable message and its actual peer before the deadline.
    Invalid UTF-8 or JSON packets are ignored while time remains.
    남은 시간이 있는 동안 잘못된 UTF-8 또는 JSON 패킷은 무시한다.
    """

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            udp_socket.settimeout(remaining)
            payload, peer = udp_socket.recvfrom(MAX_PACKET_SIZE)
        except (socket.timeout, TimeoutError, OSError, OverflowError):
            return None
        try:
            return decode_message(payload), peer
        except MessageError:
            continue


def _accept_advertisement(
    udp_socket: socket.socket,
    deadline: float,
) -> tuple[dict[str, object], tuple[str, int]] | None:
    """유효한 ADVERTISE에 ACK하고 해당 Sender peer를 선택한다.

    Acknowledge a valid ADVERTISE and select its Sender peer.
    """

    while True:
        received = _receive_message(udp_socket, deadline)
        if received is None:
            return None
        message, peer = received
        advertisement = _try_parse_advertisement(message)
        if advertisement is None:
            continue
        if _send_ack(udp_socket, advertisement, ADVERTISE, peer):
            return advertisement, peer


def _accept_detail(
    udp_socket: socket.socket,
    *,
    deadline: float,
    sender_peer: tuple[str, int],
    device_id: str,
    advertisement_id: str,
) -> dict[str, object] | None:
    """선택된 Sender의 새 DETAIL만 수신하여 ACK한다.

    Receive and acknowledge only a new DETAIL from the selected Sender.

    같은 ADVERTISE 재수신에는 ACK를 다시 보내 유실을 복구하지만, 다른
    peer·device·message ID는 현재 교환에 섞이지 않도록 무시한다.
    A repeated selected ADVERTISE is re-acknowledged to recover a lost ACK;
    other peers, devices, and reused IDs are ignored.
    """

    while True:
        received = _receive_message(udp_socket, deadline)
        if received is None:
            return None
        message, peer = received
        if peer != sender_peer:
            continue
        repeated_advertisement = _try_parse_advertisement(message)
        if repeated_advertisement is not None:
            if (
                repeated_advertisement["device_id"] == device_id
                and repeated_advertisement["message_id"] == advertisement_id
            ):
                _send_ack(
                    udp_socket,
                    repeated_advertisement,
                    ADVERTISE,
                    sender_peer,
                )
            continue
        detail = _try_parse_detail(message)
        if (
            detail is None
            or detail["device_id"] != device_id
            or detail["message_id"] == advertisement_id
        ):
            continue
        if _send_ack(udp_socket, detail, DETAIL, sender_peer):
            return detail


def _send_ack(
    udp_socket: socket.socket,
    message: dict[str, object],
    ack_for: str,
    peer: tuple[str, int],
) -> bool:
    """수신 메시지의 ID를 복사한 ACK를 실제 peer로 전송한다.

    Copy the received message ID into an ACK and send it to the actual peer.
    Return ``False`` if the datagram cannot be sent.
    데이터그램을 보낼 수 없으면 ``False``를 반환한다.
    """

    payload = encode_message(
        make_ack(
            str(message["device_id"]),
            ack_for,
            message_id=message["message_id"],
        )
    )
    try:
        udp_socket.sendto(payload, peer)
    except OSError:
        return False
    return True


def _probe_and_build_result(
    detail: dict[str, object],
    deadline: float,
) -> dict[str, object]:
    """남은 timeout으로 DETAIL endpoint를 확인하고 결과 dict를 만든다.

    Probe the DETAIL endpoint with the remaining timeout and build the result
    dictionary.
    """

    device_id = str(detail["device_id"])
    ip = str(detail["ip"])
    rtsp_port = int(detail["rtsp_port"])
    rtsp_path = str(detail["rtsp_path"])
    remaining = max(0.0, deadline - time.monotonic())
    try:
        connected = connecter.probe_rtsp(
            ip,
            rtsp_port,
            rtsp_path,
            timeout=remaining,
        )
    except Exception:
        # FR-RTSP-007: probe 실패는 discover() 밖으로 전파하지 않는다.
        # A probe failure must not escape Receiver.discover().
        connected = False
    return {
        "device_id": device_id,
        "ip": ip,
        "rtsp_port": rtsp_port,
        "rtsp_path": rtsp_path,
        "rtsp_uri": connecter.build_rtsp_uri(ip, rtsp_port, rtsp_path),
        "rtsp_connected": connected,
    }


def _try_parse_advertisement(
    message: dict[str, Any],
) -> dict[str, object] | None:
    """ADVERTISE가 유효하면 정규화된 값, 아니면 ``None``을 반환한다.

    Return normalized ADVERTISE data, or ``None`` when validation fails.
    """

    try:
        return parse_advertisement(message)
    except MessageError:
        return None


def _try_parse_detail(message: dict[str, Any]) -> dict[str, object] | None:
    """DETAIL이 유효하면 정규화된 값, 아니면 ``None``을 반환한다.

    Return normalized DETAIL data, or ``None`` when validation fails.
    """

    try:
        return parse_detail(message)
    except MessageError:
        return None
