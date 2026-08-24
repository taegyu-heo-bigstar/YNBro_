"""Receiver-side UDP exchange from the receiver feature branch."""

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
    """Discover one Sender, probe its RTSP endpoint, and return the SRS result."""

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
    """Return the next decodable message and its peer before the deadline."""

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
    """Receive a valid ADVERTISE, acknowledge it, and bind its Sender peer."""

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
    """Receive and acknowledge a new DETAIL from the selected Sender peer."""

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
    """Copy a received message ID into an ACK and send it to its actual peer."""

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
    """Probe the DETAIL endpoint with the remaining budget and build the result."""

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
        # FR-RTSP-007: probe failure must not escape Receiver.discover().
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
    try:
        return parse_advertisement(message)
    except MessageError:
        return None


def _try_parse_detail(message: dict[str, Any]) -> dict[str, object] | None:
    try:
        return parse_detail(message)
    except MessageError:
        return None
