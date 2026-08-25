"""RTSP URI 생성과 RTSP/2.0 endpoint 연결 확인을 제공한다.

Provide RTSP URI construction and RTSP/2.0 endpoint probing.
"""

from __future__ import annotations

import re
import socket
import time
from urllib.parse import quote

from ._protocol import (
    DEFAULT_TIMEOUT,
    validate_ipv4,
    validate_port,
    validate_rtsp_path,
    validate_timeout,
)


# FR-RTSP-005/006: status line은 ``RTSP/2.0 3자리코드 설명`` 형식만 허용한다.
# Only ``RTSP/2.0 <three-digit-code> <reason>`` is accepted as a status line.
# 응답 상한은 header 종결자가 없는 상대가 메모리를 계속 쓰게 하는 일을 막는다.
# The header cap prevents unbounded reads from a peer that never terminates it.
_STATUS_LINE = re.compile(rb"^RTSP/2\.0 ([0-9]{3}) [\x20-\x7e]+$")
_MAX_RESPONSE_HEADER = 16_384


def build_rtsp_uri(ip: str, rtsp_port: int, rtsp_path: str) -> str:
    """검증된 DETAIL endpoint를 percent-encoded RTSP URI로 조합한다.

    Build a percent-encoded RTSP URI from a validated DETAIL endpoint.
    """

    host = validate_ipv4(ip)
    port = validate_port(rtsp_port)
    path = validate_rtsp_path(rtsp_path)
    return f"rtsp://{host}:{port}{quote(path, safe='/')}"


def probe_rtsp(
    ip: str,
    rtsp_port: int,
    rtsp_path: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """RTSP/2.0 OPTIONS 요청이 2xx 응답을 받는지 확인한다.

    Return whether an RTSP/2.0 OPTIONS request receives a 2xx response.

    입력, 연결, 송수신, 응답 형식 오류는 모두 ``False``로 정규화한다.
    Input, connection, I/O, and response-format failures are normalized to
    ``False``.
    """

    # FR-RTSP-002~007: TCP 연결부터 응답 수신까지 하나의 timeout 예산을
    # 공유한다. One timeout budget covers connection, request, and response.
    timeout_value = validate_timeout(timeout)
    if timeout_value == 0:
        return False

    try:
        host = validate_ipv4(ip)
        port = validate_port(rtsp_port)
        path = validate_rtsp_path(rtsp_path)
        uri = build_rtsp_uri(host, port, path)
        request = _make_options_request(uri)
    except (ValueError, UnicodeError):
        return False

    deadline = time.monotonic() + timeout_value
    response_header = _exchange_options(host, port, request, deadline)
    return response_header is not None and _is_success_response(response_header)


def _make_options_request(uri: str) -> bytes:
    """probe에 사용할 최소 RTSP/2.0 OPTIONS 요청을 만든다.

    Build the minimal RTSP/2.0 OPTIONS request used by the probe.
    """

    return (
        f"OPTIONS {uri} RTSP/2.0\r\n"
        "CSeq: 1\r\n"
        "User-Agent: ynb/0.0.1\r\n"
        "\r\n"
    ).encode("ascii")


def _exchange_options(
    host: str,
    port: int,
    request: bytes,
    deadline: float,
) -> bytes | None:
    """TCP 연결 후 OPTIONS를 보내고 완전한 RTSP 응답 header를 반환한다.

    Connect over TCP, send OPTIONS, and return one complete RTSP response
    header within the shared deadline.
    """

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        with socket.create_connection(
            (host, port), timeout=remaining
        ) as connection:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            connection.settimeout(remaining)
            connection.sendall(request)
            return _receive_response_header(connection, deadline)
    except (OSError, OverflowError, UnicodeError):
        return None


def _receive_response_header(
    connection: socket.socket,
    deadline: float,
) -> bytes | None:
    """공유 deadline과 크기 상한 안에서 RTSP 응답 header 하나를 읽는다.

    Read one RTSP response header within the shared deadline and size cap.
    """

    response = bytearray()
    while b"\r\n\r\n" not in response and len(response) < _MAX_RESPONSE_HEADER:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        connection.settimeout(remaining)
        chunk = connection.recv(min(4_096, _MAX_RESPONSE_HEADER - len(response)))
        if not chunk:
            break
        response.extend(chunk)
    if b"\r\n\r\n" not in response:
        return None
    return bytes(response).split(b"\r\n\r\n", 1)[0]


def _is_success_response(response_header: bytes) -> bool:
    """응답이 올바른 RTSP/2.0 2xx status line으로 시작하는지 확인한다.

    Return whether the response starts with a valid RTSP/2.0 2xx status line.
    """

    status_line = response_header.split(b"\r\n", 1)[0]
    match = _STATUS_LINE.fullmatch(status_line)
    return bool(match and 200 <= int(match.group(1)) < 300)
