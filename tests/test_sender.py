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
                # ADVERTISE 수신 port와 ACK 송신 port를 다르게 하여 Sender가
                # 설정값이 아닌 recvfrom()의 실제 peer를 쓰는지 확인한다.
                # Different ports prove that Sender uses the actual ACK peer
                # returned by recvfrom(), not the configured discovery port.
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

                    # 다른 port의 위조 DETAIL ACK는 최초 ACK peer와 다르므로
                    # 무시해야 한다. A forged DETAIL ACK from another port must
                    # be ignored because it is not the selected peer.
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
        # 공개 API의 broadcast 계약은 유지하고, 단일 host 테스트를 위해 private
        # 주소만 loopback으로 바꾼다. Keep the public broadcast contract intact;
        # patch only the private address for this single-host socket test.
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
        """다른 port의 ACK만으로 성공하지 않는지 검사한다.

        Verify that an ACK from a different UDP port cannot complete exchange.
        """

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

                    # 내용이 맞아도 최초 peer와 port가 다르면 위조 ACK다.
                    # Matching fields do not make an ACK valid from another port.
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
        """기본 ADVERTISE의 limited broadcast 사용을 검사한다.

        Verify that ADVERTISE uses the limited-broadcast address by default.
        """

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
        """ACK가 없을 때 같은 ID로 30초 동안 10회 광고하는지 검사한다.

        Verify ten broadcasts with one ID over the 30-second budget without ACK.
        """

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
        """표현 불가능한 socket timeout이 ``False``로 격리되는지 검사한다.

        Verify that an unsupported socket timeout is contained as ``False``.
        """

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
        """CON-004: 호출자가 ADVERTISE를 unicast로 바꿀 수 없는지 검사한다.

        CON-004: Verify callers cannot redirect ADVERTISE to unicast.
        """

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
