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
        """공개 API와 가짜 RTSP 서버로 전체 교환을 실행한다.

        Run a complete exchange through public APIs and a fake RTSP server.
        """

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
            # 공개 unicast 우회 없이 단일 host E2E를 재현하기 위해 private
            # broadcast 주소만 바꾼다. Patch only the private address for a
            # deterministic single-host E2E while preserving the public contract.
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
        """UDP 교환 성공과 RTSP probe 성공이 독립적인지 검사한다.

        Verify that UDP exchange success and RTSP probe success are independent.
        """

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
        """FR-RTSP-007: probe 실패가 discover() 밖으로 전파되지 않는지 검사한다.

        FR-RTSP-007: Verify that a probe failure cannot escape discover().
        """

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
