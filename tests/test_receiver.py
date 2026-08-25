from __future__ import annotations

import json
import queue
import socket
import threading
import time
import unittest
from unittest import mock

from ynb import receiver

from tests.support import FakeRtspServer, free_udp_port


DEVICE_ID = "DC:A6:32:12:34:56"
ADVERTISE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DETAIL_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
IMPOSTOR_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class ReceiverTests(unittest.TestCase):
    def test_malformed_input_is_ignored_and_exchange_is_peer_bound(self) -> None:
        discovery_port = free_udp_port()
        results: queue.Queue[dict[str, object] | None] = queue.Queue()

        with FakeRtspServer() as rtsp_server:
            thread = threading.Thread(
                target=lambda: results.put(
                    receiver.discover(
                        2.0, start_port=discovery_port, bind_host="127.0.0.1"
                    )
                ),
                daemon=True,
            )
            thread.start()
            time.sleep(0.05)

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender_socket:
                sender_socket.bind(("127.0.0.1", 0))
                sender_socket.settimeout(1.0)
                target = ("127.0.0.1", discovery_port)
                sender_socket.sendto(b"not-json", target)
                sender_socket.sendto(
                    json.dumps(
                        {
                            "message_type": "ADVERTISE",
                            "message_id": "not-a-uuid",
                            "device_id": DEVICE_ID,
                        }
                    ).encode(),
                    target,
                )
                sender_socket.sendto(
                    json.dumps(
                        {
                            "message_type": "ADVERTISE",
                            "message_id": ADVERTISE_ID,
                            "device_id": DEVICE_ID,
                        }
                    ).encode(),
                    target,
                )
                payload, ack_peer = sender_socket.recvfrom(65_535)
                self.assertEqual(ack_peer, target)
                self.assertEqual(
                    json.loads(payload),
                    {
                        "message_type": "ACK",
                        "message_id": ADVERTISE_ID,
                        "device_id": DEVICE_ID,
                        "ack_for": "ADVERTISE",
                    },
                )

                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as impostor:
                    impostor.sendto(
                        json.dumps(
                            {
                                "message_type": "DETAIL",
                                "message_id": IMPOSTOR_ID,
                                "device_id": DEVICE_ID,
                                "ip": "127.0.0.1",
                                "rtsp_port": rtsp_server.port,
                                "rtsp_path": "/wrong-peer",
                            }
                        ).encode(),
                        target,
                    )

                # peer가 같아도 device ID가 다르면 선택된 Sender의 DETAIL이
                # 아니다. The same peer with another device ID must be ignored.
                sender_socket.sendto(
                    json.dumps(
                        {
                            "message_type": "DETAIL",
                            "message_id": IMPOSTOR_ID,
                            "device_id": "00:11:22:33:44:55",
                            "ip": "127.0.0.1",
                            "rtsp_port": rtsp_server.port,
                            "rtsp_path": "/wrong-device",
                        }
                    ).encode(),
                    target,
                )
                # DETAIL은 ADVERTISE ID를 재사용할 수 없다.
                # DETAIL must use a new ID rather than reuse ADVERTISE ID.
                sender_socket.sendto(
                    json.dumps(
                        {
                            "message_type": "DETAIL",
                            "message_id": ADVERTISE_ID,
                            "device_id": DEVICE_ID,
                            "ip": "127.0.0.1",
                            "rtsp_port": rtsp_server.port,
                            "rtsp_path": "/reused-id",
                        }
                    ).encode(),
                    target,
                )
                # FR-RCV-004: 아래 패킷은 각각 한 필드만 잘못되어 각 validator를
                # 독립적으로 검사한다. Each packet has exactly one invalid field,
                # so every DETAIL validator is tested independently.
                invalid_details = (
                    {
                        "message_type": "DETAIL",
                        "message_id": "not-a-uuid",
                        "device_id": DEVICE_ID,
                        "ip": "127.0.0.1",
                        "rtsp_port": rtsp_server.port,
                        "rtsp_path": "/bad-message-id",
                    },
                    {
                        "message_type": "DETAIL",
                        "message_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                        "device_id": DEVICE_ID,
                        "ip": "localhost",
                        "rtsp_port": rtsp_server.port,
                        "rtsp_path": "/bad-ip",
                    },
                    {
                        "message_type": "DETAIL",
                        "message_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                        "device_id": DEVICE_ID,
                        "ip": "127.0.0.1",
                        "rtsp_port": 0,
                        "rtsp_path": "/bad-port",
                    },
                    {
                        "message_type": "DETAIL",
                        "message_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
                        "device_id": DEVICE_ID,
                        "ip": "127.0.0.1",
                        "rtsp_port": rtsp_server.port,
                        "rtsp_path": "bad-path",
                    },
                )
                for invalid_detail in invalid_details:
                    sender_socket.sendto(json.dumps(invalid_detail).encode(), target)
                sender_socket.sendto(
                    json.dumps(
                        {
                            "message_type": "DETAIL",
                            "message_id": DETAIL_ID,
                            "device_id": DEVICE_ID,
                            "ip": "127.0.0.1",
                            "rtsp_port": rtsp_server.port,
                            "rtsp_path": "/stream",
                        }
                    ).encode(),
                    target,
                )
                payload, ack_peer = sender_socket.recvfrom(65_535)
                self.assertEqual(ack_peer, target)
                self.assertEqual(
                    json.loads(payload),
                    {
                        "message_type": "ACK",
                        "message_id": DETAIL_ID,
                        "device_id": DEVICE_ID,
                        "ack_for": "DETAIL",
                    },
                )

            thread.join(2.0)
            self.assertFalse(thread.is_alive())
            result = results.get_nowait()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["device_id"], DEVICE_ID)
        self.assertEqual(result["rtsp_path"], "/stream")
        self.assertTrue(result["rtsp_connected"])

    def test_timeout_returns_none(self) -> None:
        started = time.monotonic()
        result = receiver.discover(
            0.05, start_port=free_udp_port(), bind_host="127.0.0.1"
        )
        self.assertIsNone(result)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_first_device_remains_selected_until_timeout(self) -> None:
        """뒤늦게 온 장비가 최초 선택 장비를 대체하지 못하는지 검사한다.

        Verify that a later device cannot replace the first acknowledged Sender.
        """

        discovery_port = free_udp_port()
        results: queue.Queue[dict[str, object] | None] = queue.Queue()
        thread = threading.Thread(
            target=lambda: results.put(
                receiver.discover(
                    0.3,
                    start_port=discovery_port,
                    bind_host="127.0.0.1",
                )
            ),
            daemon=True,
        )
        thread.start()
        time.sleep(0.03)
        target = ("127.0.0.1", discovery_port)

        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as first,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as second,
        ):
            first.settimeout(0.2)
            second.settimeout(0.05)
            first.sendto(
                json.dumps(
                    {
                        "message_type": "ADVERTISE",
                        "message_id": ADVERTISE_ID,
                        "device_id": DEVICE_ID,
                    }
                ).encode(),
                target,
            )
            first.recvfrom(65_535)
            # 같은 광고에는 ACK를 다시 보내 유실을 복구하되 선택은 유지한다.
            # Re-ACK the identical advertisement without changing selection.
            first.sendto(
                json.dumps(
                    {
                        "message_type": "ADVERTISE",
                        "message_id": ADVERTISE_ID,
                        "device_id": DEVICE_ID,
                    }
                ).encode(),
                target,
            )
            repeated_ack, _peer = first.recvfrom(65_535)
            self.assertEqual(json.loads(repeated_ack)["message_id"], ADVERTISE_ID)

            second.sendto(
                json.dumps(
                    {
                        "message_type": "ADVERTISE",
                        "message_id": IMPOSTOR_ID,
                        "device_id": "00:11:22:33:44:55",
                    }
                ).encode(),
                target,
            )
            with self.assertRaises(socket.timeout):
                second.recvfrom(65_535)

        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(results.get_nowait())

    def test_platform_timeout_overflow_returns_none(self) -> None:
        """표현 불가능한 socket timeout이 ``None``으로 격리되는지 검사한다.

        Verify that an unsupported socket timeout is contained as ``None``.
        """

        udp_socket = mock.MagicMock()
        udp_socket.settimeout.side_effect = OverflowError("timeout is too large")
        socket_context = mock.MagicMock()
        socket_context.__enter__.return_value = udp_socket
        socket_context.__exit__.return_value = False

        with mock.patch("ynb.receiver.socket.socket", return_value=socket_context):
            self.assertIsNone(
                receiver.discover(
                    timeout=1e308,
                    start_port=37_020,
                    bind_host="127.0.0.1",
                )
            )


if __name__ == "__main__":
    unittest.main()
