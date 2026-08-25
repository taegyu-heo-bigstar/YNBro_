from __future__ import annotations

import socket
import threading
import time


def free_udp_port() -> int:
    """loopback에서 사용 가능한 UDP port를 구한다.

    Obtain a currently available UDP port on the loopback interface.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("127.0.0.1", 0))
        return int(udp_socket.getsockname()[1])


def free_tcp_port() -> int:
    """loopback에서 사용 가능한 TCP port를 구한다.

    Obtain a currently available TCP port on the loopback interface.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_socket:
        tcp_socket.bind(("127.0.0.1", 0))
        return int(tcp_socket.getsockname()[1])


class FakeRtspServer:
    """OPTIONS 하나에 지정된 응답을 보내는 작은 RTSP 테스트 서버다.

    A small RTSP test server that sends a configured response to one OPTIONS.
    """

    def __init__(
        self,
        status_line: str = "RTSP/2.0 200 OK",
        *,
        response_delay: float = 0.0,
    ) -> None:
        self.status_line = status_line
        self.response_delay = response_delay
        self.request = b""
        self.error: BaseException | None = None
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self.port = int(self._socket.getsockname()[1])
        self._socket.listen(1)
        self._socket.settimeout(2.0)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "FakeRtspServer":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        try:
            self._socket.close()
        finally:
            self._thread.join(2.0)
        if self.error is not None:
            raise AssertionError("fake RTSP server failed") from self.error

    def _serve(self) -> None:
        try:
            connection, _peer = self._socket.accept()
            with connection:
                connection.settimeout(2.0)
                request = bytearray()
                while b"\r\n\r\n" not in request and len(request) < 16_384:
                    chunk = connection.recv(4_096)
                    if not chunk:
                        break
                    request.extend(chunk)
                self.request = bytes(request)
                # TCP 연결 후 RTSP 응답만 늦는 상황으로 client timeout을 검사한다.
                # Delay only the RTSP response to exercise the client timeout.
                if self.response_delay:
                    time.sleep(self.response_delay)
                response = (
                    f"{self.status_line}\r\n"
                    "CSeq: 1\r\n"
                    "Content-Length: 0\r\n"
                    "\r\n"
                ).encode("ascii")
                connection.sendall(response)
        except OSError as exc:
            if self._socket.fileno() != -1:
                self.error = exc
