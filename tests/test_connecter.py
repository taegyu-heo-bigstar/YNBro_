from __future__ import annotations

import time
import unittest
from unittest import mock

from ynb.connecter import build_rtsp_uri, probe_rtsp

from tests.support import FakeRtspServer, free_tcp_port


class ConnecterTests(unittest.TestCase):
    def test_build_uri_percent_encodes_non_ascii_path(self) -> None:
        self.assertEqual(
            build_rtsp_uri("127.0.0.1", 8554, "/카메라 1"),
            "rtsp://127.0.0.1:8554/%EC%B9%B4%EB%A9%94%EB%9D%BC%201",
        )

    def test_rtsp_2xx_is_success_and_request_has_cseq(self) -> None:
        with FakeRtspServer() as server:
            self.assertTrue(probe_rtsp("127.0.0.1", server.port, "/stream", 1.0))
        self.assertIn(
            f"OPTIONS rtsp://127.0.0.1:{server.port}/stream RTSP/2.0\r\n".encode(),
            server.request,
        )
        self.assertIn(b"CSeq: 1\r\n", server.request)

    def test_wrong_protocol_and_connection_refusal_are_false(self) -> None:
        with FakeRtspServer("RTSP/1.0 200 OK") as server:
            self.assertFalse(probe_rtsp("127.0.0.1", server.port, "/stream", 1.0))
        self.assertFalse(probe_rtsp("127.0.0.1", free_tcp_port(), "/stream", 0.2))

    def test_rtsp_status_code_boundaries(self) -> None:
        cases = (
            ("RTSP/2.0 199 Informational", False),
            ("RTSP/2.0 200 OK", True),
            ("RTSP/2.0 299 Success", True),
            ("RTSP/2.0 300 Redirect", False),
        )

        for status_line, expected in cases:
            with self.subTest(status_line=status_line):
                with FakeRtspServer(status_line) as server:
                    self.assertEqual(
                        probe_rtsp("127.0.0.1", server.port, "/stream", 1.0),
                        expected,
                    )

    def test_malformed_status_lines_are_false(self) -> None:
        """reason phrase 누락과 제어 문자가 있는 status line을 거부한다.

        Reject status lines with no reason phrase or with control characters.
        """

        for status_line in (
            "RTSP/2.0 200",
            "RTSP/2.0 200\tOK",
            "RTSP/2.0 200 \x00",
        ):
            with self.subTest(status_line=status_line):
                with FakeRtspServer(status_line) as server:
                    self.assertFalse(
                        probe_rtsp("127.0.0.1", server.port, "/stream", 1.0)
                    )

    def test_response_timeout_is_false(self) -> None:
        """늦은 RTSP 응답이 예외 대신 ``False``가 되는지 검사한다.

        Verify that a late RTSP response returns ``False`` instead of raising.
        """

        with FakeRtspServer(response_delay=0.2) as server:
            started = time.monotonic()
            self.assertFalse(
                probe_rtsp("127.0.0.1", server.port, "/stream", timeout=0.05)
            )
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.2)

    def test_platform_timeout_overflow_is_false(self) -> None:
        """OS가 표현할 수 없는 timeout을 외부 예외로 전파하지 않는다.

        Contain a timeout value that the operating system cannot represent.
        """

        with mock.patch(
            "ynb.connecter.socket.create_connection",
            side_effect=OverflowError("timeout is too large"),
        ):
            self.assertFalse(
                probe_rtsp("127.0.0.1", 8554, "/stream", timeout=1e308)
            )


if __name__ == "__main__":
    unittest.main()
