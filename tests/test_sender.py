from __future__ import annotations

import json
import socket
import threading
import unittest
import uuid
from unittest import mock

from ynb import sender
from ynb._protocol import decode_message

from tests.support import free_udp_port


DEVICE_ID = "DC:A6:32:12:34:56"
MISMATCHED_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class SenderTests(unittest.TestCase):
    def test_advertise_uses_ack_peer_for_detail(self) -> None:
        port = free_udp_port()
        ready = threading.Event()
        received: list[tuple[dict[str, object], tuple[str, int]]] = []
        error: list[BaseException] = []

        def responder() -> None:
            try:
                # ADVERTISE를 받는 discovery socket과 ACK를 보내는 socket의
                # port를 다르게 만든다. Sender가 설정 port를 추측하지 않고
                # recvfrom()의 실제 ACK peer를 쓰는지 검증하기 위해서다.
                with (
                    socket.socket(
                        socket.AF_INET, socket.SOCK_DGRAM
                    ) as discovery_socket,
                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ack_socket,
                ):
                    discovery_socket.bind(("127.0.0.1", port))
                    discovery_socket.settimeout(2.0)
                    ack_socket.bind(("127.0.0.1", 0))
                    ack_socket.settimeout(2.0)
                    ready.set()
                    payload, peer = discovery_socket.recvfrom(65_535)
                    advertisement = json.loads(payload)
                    advertisement_id = advertisement["message_id"]
                    received.append((advertisement, peer))
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": advertisement_id,
                                "device_id": DEVICE_ID,
                                "ack_for": [],
                            }
                        ).encode(),
                        peer,
                    )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": MISMATCHED_ID,
                                "device_id": DEVICE_ID,
                                "ack_for": "ADVERTISE",
                            }
                        ).encode(),
                        peer,
                    )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": advertisement_id,
                                "device_id": "00:11:22:33:44:55",
                                "ack_for": "ADVERTISE",
                            }
                        ).encode(),
                        peer,
                    )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": advertisement_id,
                                "device_id": DEVICE_ID,
                                "ack_for": "DETAIL",
                            }
                        ).encode(),
                        peer,
                    )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": advertisement_id,
                                "device_id": DEVICE_ID,
                                "ack_for": "ADVERTISE",
                            }
                        ).encode(),
                        peer,
                    )
                    payload, detail_peer = ack_socket.recvfrom(65_535)
                    detail = json.loads(payload)
                    detail_id = detail["message_id"]
                    received.append((detail, detail_peer))

                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": detail_id,
                                "device_id": "00:11:22:33:44:55",
                                "ack_for": "DETAIL",
                            }
                        ).encode(),
                        detail_peer,
                    )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": detail_id,
                                "device_id": DEVICE_ID,
                                "ack_for": "ADVERTISE",
                            }
                        ).encode(),
                        detail_peer,
                    )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": MISMATCHED_ID,
                                "device_id": DEVICE_ID,
                                "ack_for": "DETAIL",
                            }
                        ).encode(),
                        detail_peer,
                    )

                    # DETAIL ACK처럼 보이는 패킷을 다른 port에서 먼저 보낸다.
                    # Sender는 최초 ACK peer와 다르므로 이를 무시해야 한다.
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as impostor:
                        impostor.sendto(
                            json.dumps(
                                {
                                    "message_type": "ACK",
                                    "message_id": detail_id,
                                    "device_id": DEVICE_ID,
                                    "ack_for": "DETAIL",
                                }
                            ).encode(),
                            detail_peer,
                        )
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": detail_id,
                                "device_id": DEVICE_ID,
                                "ack_for": "DETAIL",
                            }
                        ).encode(),
                        detail_peer,
                    )
            except BaseException as exc:
                error.append(exc)
                ready.set()

        thread = threading.Thread(target=responder, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(1.0))
        # The public API always broadcasts per SRS.  Patch only the private
        # transport constant so this single-host socket test can use loopback.
        with mock.patch("ynb.sender._BROADCAST_ADDRESS", "127.0.0.1"):
            succeeded = sender.advertise(
                DEVICE_ID,
                "127.0.0.1",
                8554,
                "/stream",
                timeout=1.5,
                start_port=port,
            )
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(error, [])
        self.assertTrue(succeeded)
        advertisement = received[0][0]
        detail = received[1][0]
        advertisement_id = str(advertisement["message_id"])
        detail_id = str(detail["message_id"])
        self.assertEqual(uuid.UUID(advertisement_id).version, 4)
        self.assertEqual(uuid.UUID(detail_id).version, 4)
        self.assertEqual(str(uuid.UUID(advertisement_id)), advertisement_id)
        self.assertEqual(str(uuid.UUID(detail_id)), detail_id)
        self.assertNotEqual(advertisement_id, detail_id)
        self.assertEqual(
            advertisement,
            {
                "message_type": "ADVERTISE",
                "message_id": advertisement_id,
                "device_id": DEVICE_ID,
            },
        )
        self.assertEqual(
            detail,
            {
                "message_type": "DETAIL",
                "message_id": detail_id,
                "device_id": DEVICE_ID,
                "ip": "127.0.0.1",
                "rtsp_port": 8554,
                "rtsp_path": "/stream",
            },
        )
        self.assertEqual(received[0][1], received[1][1])

    def test_detail_ack_from_other_peer_is_ignored(self) -> None:
        """다른 UDP port의 DETAIL ACK만 오면 교환은 성공하지 않아야 한다."""

        port = free_udp_port()
        ready = threading.Event()
        error: list[BaseException] = []

        def responder() -> None:
            try:
                with (
                    socket.socket(
                        socket.AF_INET, socket.SOCK_DGRAM
                    ) as discovery_socket,
                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ack_socket,
                ):
                    discovery_socket.bind(("127.0.0.1", port))
                    discovery_socket.settimeout(1.0)
                    ack_socket.bind(("127.0.0.1", 0))
                    ack_socket.settimeout(1.0)
                    ready.set()

                    advertisement_payload, sender_peer = discovery_socket.recvfrom(
                        65_535
                    )
                    advertisement_id = json.loads(advertisement_payload)["message_id"]
                    ack_socket.sendto(
                        json.dumps(
                            {
                                "message_type": "ACK",
                                "message_id": advertisement_id,
                                "device_id": DEVICE_ID,
                                "ack_for": "ADVERTISE",
                            }
                        ).encode(),
                        sender_peer,
                    )
                    detail_payload, sender_peer = ack_socket.recvfrom(65_535)
                    detail_id = json.loads(detail_payload)["message_id"]

                    # 내용은 맞지만 최초 ACK peer와 port가 다른 위조 ACK다.
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as impostor:
                        impostor.sendto(
                            json.dumps(
                                {
                                    "message_type": "ACK",
                                    "message_id": detail_id,
                                    "device_id": DEVICE_ID,
                                    "ack_for": "DETAIL",
                                }
                            ).encode(),
                            sender_peer,
                        )
            except BaseException as exc:
                error.append(exc)
                ready.set()

        thread = threading.Thread(target=responder, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(1.0))
        with mock.patch("ynb.sender._BROADCAST_ADDRESS", "127.0.0.1"):
            succeeded = sender.advertise(
                DEVICE_ID,
                "127.0.0.1",
                8554,
                "/stream",
                timeout=0.2,
                start_port=port,
            )
        thread.join(1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(error, [])
        self.assertFalse(succeeded)

    def test_default_destination_is_udp_broadcast(self) -> None:
        """기본 ADVERTISE가 limited broadcast 주소로 향하는지 검사한다."""

        udp_socket = mock.MagicMock()
        socket_context = mock.MagicMock()
        socket_context.__enter__.return_value = udp_socket
        socket_context.__exit__.return_value = False

        with mock.patch("ynb.sender.socket.socket", return_value=socket_context):
            succeeded = sender.advertise(
                DEVICE_ID,
                "127.0.0.1",
                8554,
                "/stream",
                timeout=0,
            )

        self.assertFalse(succeeded)
        udp_socket.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET, socket.SO_BROADCAST, 1
        )
        udp_socket.bind.assert_called_once_with(("0.0.0.0", 0))
        first_payload, first_target = udp_socket.sendto.call_args_list[0].args
        self.assertEqual(first_target, ("255.255.255.255", 37_020))
        advertisement = decode_message(first_payload)
        message_id = str(advertisement["message_id"])
        self.assertEqual(uuid.UUID(message_id).version, 4)
        self.assertEqual(str(uuid.UUID(message_id)), message_id)
        self.assertEqual(
            advertisement,
            {
                "message_type": "ADVERTISE",
                "message_id": message_id,
                "device_id": DEVICE_ID,
            },
        )

    def test_advertisement_retries_ten_times_in_three_second_windows(self) -> None:
        """No ACK causes ten broadcasts sharing one ID over the 30s budget."""

        udp_socket = mock.MagicMock()
        socket_context = mock.MagicMock()
        socket_context.__enter__.return_value = udp_socket
        socket_context.__exit__.return_value = False
        monotonic_values = [0.0]
        for attempt in range(10):
            monotonic_values.extend((attempt * 3.0, (attempt + 1) * 3.0))

        with (
            mock.patch("ynb.sender.socket.socket", return_value=socket_context),
            mock.patch("ynb.sender.time.monotonic", side_effect=monotonic_values),
            mock.patch(
                "ynb.sender._wait_for_matching_ack", return_value=None
            ) as wait,
        ):
            succeeded = sender.advertise(
                DEVICE_ID,
                "127.0.0.1",
                8554,
                "/stream",
            )

        self.assertFalse(succeeded)
        self.assertEqual(udp_socket.sendto.call_count, 10)
        payloads = [call.args[0] for call in udp_socket.sendto.call_args_list]
        targets = [call.args[1] for call in udp_socket.sendto.call_args_list]
        self.assertTrue(all(payload == payloads[0] for payload in payloads))
        self.assertEqual(targets, [("255.255.255.255", 37_020)] * 10)
        self.assertEqual(
            [call.kwargs["deadline"] for call in wait.call_args_list],
            [3.0 * attempt for attempt in range(1, 11)],
        )

    def test_platform_timeout_overflow_returns_false(self) -> None:
        """UDP socket이 큰 timeout을 거부해도 False로 끝나야 한다."""

        udp_socket = mock.MagicMock()
        udp_socket.settimeout.side_effect = OverflowError("timeout is too large")
        socket_context = mock.MagicMock()
        socket_context.__enter__.return_value = udp_socket
        socket_context.__exit__.return_value = False

        with mock.patch("ynb.sender.socket.socket", return_value=socket_context):
            self.assertFalse(
                sender.advertise(
                    DEVICE_ID,
                    "127.0.0.1",
                    8554,
                    "/stream",
                    timeout=1e308,
                )
            )

    def test_public_api_cannot_replace_broadcast_with_unicast(self) -> None:
        """CON-004: callers cannot redirect ADVERTISE to a unicast address."""

        with mock.patch("ynb.sender.socket.socket") as socket_factory:
            with self.assertRaises(TypeError):
                sender.advertise(
                    DEVICE_ID,
                    "127.0.0.1",
                    8554,
                    "/stream",
                    broadcast_address="127.0.0.1",  # type: ignore[call-arg]
                )
        socket_factory.assert_not_called()

    def test_invalid_configuration_is_rejected_before_socket_creation(self) -> None:
        invalid_endpoints = (
            ("bad-device", "127.0.0.1", 8554, "/stream"),
            (DEVICE_ID, "localhost", 8554, "/stream"),
            (DEVICE_ID, "127.0.0.1", 0, "/stream"),
            (DEVICE_ID, "127.0.0.1", 65_536, "/stream"),
            (DEVICE_ID, "127.0.0.1", True, "/stream"),
            (DEVICE_ID, "127.0.0.1", 8554, "stream"),
            (DEVICE_ID, "127.0.0.1", 8554, "/line\nbreak"),
            (DEVICE_ID, "127.0.0.1", 8554, "/\ud800"),
        )

        for endpoint in invalid_endpoints:
            with self.subTest(endpoint=endpoint):
                with mock.patch("ynb.sender.socket.socket") as socket_factory:
                    with self.assertRaises(ValueError):
                        sender.advertise(*endpoint)
                socket_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
