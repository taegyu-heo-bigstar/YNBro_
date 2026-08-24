from __future__ import annotations

import inspect
import queue
import threading
import time
import unittest
from unittest import mock

from ynb import receiver, sender
from ynb.connecter import probe_rtsp

from tests.support import FakeRtspServer, free_udp_port


DEVICE_ID = "DC:A6:32:12:34:56"


class EndToEndTests(unittest.TestCase):
    def test_public_network_timeouts_default_to_thirty_seconds(self) -> None:
        for function in (sender.advertise, receiver.discover, probe_rtsp):
            with self.subTest(function=function.__qualname__):
                self.assertEqual(
                    inspect.signature(function).parameters["timeout"].default,
                    30.0,
                )

    def _run_exchange(
        self, status_line: str
    ) -> tuple[bool, dict[str, object] | None, int]:
        """공개 API 두 개와 가짜 RTSP 서버로 전체 교환을 실행한다."""

        discovery_port = free_udp_port()
        results: queue.Queue[dict[str, object] | None] = queue.Queue()

        with FakeRtspServer(status_line) as rtsp_server:
            receiver_thread = threading.Thread(
                target=lambda: results.put(
                    receiver.discover(
                        2.0, start_port=discovery_port, bind_host="127.0.0.1"
                    )
                ),
                daemon=True,
            )
            receiver_thread.start()
            time.sleep(0.05)
            # Preserve a deterministic single-host E2E test without exposing
            # a public unicast escape hatch from the SRS broadcast contract.
            with mock.patch("ynb.sender._BROADCAST_ADDRESS", "127.0.0.1"):
                acknowledged = sender.advertise(
                    DEVICE_ID,
                    "127.0.0.1",
                    rtsp_server.port,
                    "/stream",
                    timeout=1.5,
                    start_port=discovery_port,
                )
            receiver_thread.join(2.0)
            self.assertFalse(receiver_thread.is_alive())
            result = results.get_nowait()

        return acknowledged, result, rtsp_server.port

    def test_public_sender_and_receiver_apis_complete_the_full_flow(self) -> None:
        acknowledged, result, rtsp_port = self._run_exchange("RTSP/2.0 200 OK")

        self.assertTrue(acknowledged)
        self.assertEqual(
            result,
            {
                "device_id": DEVICE_ID,
                "ip": "127.0.0.1",
                "rtsp_port": rtsp_port,
                "rtsp_path": "/stream",
                "rtsp_uri": f"rtsp://127.0.0.1:{rtsp_port}/stream",
                "rtsp_connected": True,
            },
        )

    def test_rtsp_failure_returns_complete_result_with_false(self) -> None:
        """UDP 교환 성공과 RTSP probe 성공은 서로 다른 결과임을 검증한다."""

        acknowledged, result, rtsp_port = self._run_exchange(
            "RTSP/2.0 404 Not Found"
        )

        self.assertTrue(acknowledged)
        self.assertEqual(
            result,
            {
                "device_id": DEVICE_ID,
                "ip": "127.0.0.1",
                "rtsp_port": rtsp_port,
                "rtsp_path": "/stream",
                "rtsp_uri": f"rtsp://127.0.0.1:{rtsp_port}/stream",
                "rtsp_connected": False,
            },
        )

    def test_rtsp_probe_exception_is_contained_in_receiver_result(self) -> None:
        """FR-RTSP-007: a probe failure cannot escape discover()."""

        with mock.patch(
            "ynb.receiver.connecter.probe_rtsp",
            side_effect=OSError("simulated probe failure"),
        ):
            acknowledged, result, rtsp_port = self._run_exchange(
                "RTSP/2.0 200 OK"
            )

        self.assertTrue(acknowledged)
        self.assertEqual(
            result,
            {
                "device_id": DEVICE_ID,
                "ip": "127.0.0.1",
                "rtsp_port": rtsp_port,
                "rtsp_path": "/stream",
                "rtsp_uri": f"rtsp://127.0.0.1:{rtsp_port}/stream",
                "rtsp_connected": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
