# YNB 소프트웨어 설계 명세서 / Software Design Specification

## 1. 문서 정보 / Document information

| 항목 / Item | 내용 / Value |
| --- | --- |
| 문서명 / Document | Software Design Specification (SDS) |
| 대상 시스템 / System | `YNB` (`ynb` Python package) |
| 대상 버전 / Version | 0.0.1 |
| 기준 요구사항 / Requirements | [`SRS.md`](SRS.md) |
| 기준 Python / Python | 3.11.9 |
| 설계 상태 / Status | 최소 기능 PoC / Minimal proof of concept |

이 문서는 `SRS.md`의 요구사항을 현재 코드가 어떤 구조와 알고리즘으로
구현하는지 설명한다. 요구사항과 이 문서가 충돌하면 `SRS.md`가 우선하고,
이 문서와 코드가 충돌하면 실제 동작은 코드를 기준으로 판단한다.

This document explains how the current implementation realizes the requirements
in `SRS.md`. The SRS takes precedence for requirements, while the source code is
the authority for actual runtime behavior.

## 2. 설계 목표 / Design goals

YNB 0.0.1은 동일한 IPv4 broadcast domain에서 RTSP Sender 한 대를 발견하고
그 endpoint가 RTSP/2.0 `OPTIONS` 요청에 응답하는지 확인하는 동기식 Python
라이브러리다.

YNB 0.0.1 is a synchronous Python library that discovers one RTSP Sender in the
same IPv4 broadcast domain and probes its endpoint with RTSP/2.0 `OPTIONS`.

핵심 설계 원칙은 다음과 같다.

- **작은 wire protocol / Small wire protocol:** `ADVERTISE`, `ACK`, `DETAIL`
  세 메시지만 사용한다.
- **엄격한 경계 검증 / Strict boundary validation:** 송신 전 입력과 수신 직후
  wire schema를 검증한다.
- **실제 peer 신뢰 / Actual-peer binding:** 메시지 안의 주소가 아니라
  `recvfrom()`이 반환한 `(ip, port)`로 응답 상대를 선택한다.
- **하나의 시간 예산 / One time budget:** 각 공개 API 호출은 내부 단계 전체가
  하나의 monotonic deadline을 공유한다.
- **실패 격리 / Failure containment:** 예상 가능한 UDP, TCP, timeout, 잘못된
  응답은 공개 API 밖으로 네트워크 예외를 전파하지 않는다.
- **최소 의존성 / Minimal dependencies:** runtime은 Python 표준 라이브러리만
  사용한다.

## 3. 시스템 문맥 / System context

```text
+----------------+       UDP/JSON        +----------------+
| Sender process | <-------------------> | Receiver       |
| sender.py      | broadcast + unicast  | receiver.py    |
+----------------+                       +-------+--------+
                                                |
                                                | TCP + RTSP/2.0 OPTIONS
                                                v
                                        +----------------+
                                        | RTSP endpoint  |
                                        +----------------+
```

Sender와 Receiver는 같은 Python process에 있을 필요가 없다. 일반적인 사용은
Receiver를 먼저 대기시킨 다음 Sender가 광고를 시작하는 방식이다. 공개 API는
동기식이며 자체 worker thread를 만들지 않는다.

Sender and Receiver may run in different processes. The Receiver normally starts
listening before the Sender advertises. Public APIs are synchronous and create no
worker threads.

## 4. 패키지 구조 / Package structure

```text
src/ynb/
├── __init__.py    공개 sender, receiver 모듈 노출 / public exports
├── _protocol.py   wire 메시지 생성·검증·인코딩 / protocol boundary
├── sender.py      Sender 상태 흐름 / sender exchange
├── receiver.py    Receiver 상태 흐름과 결과 생성 / receiver exchange
└── connecter.py   RTSP URI 생성과 OPTIONS probe / RTSP probe
```

| 모듈 / Module | 책임 / Responsibility | 의존 대상 / Depends on |
| --- | --- | --- |
| `ynb._protocol` | 상수, message factory, UTF-8 JSON codec, 필드 검증 | Python standard library |
| `ynb.sender` | ADVERTISE 재전송, ACK 상관관계, DETAIL 전송 | `_protocol`, `socket`, `time` |
| `ynb.receiver` | Sender 선택, ACK 전송, DETAIL 수신, 결과 조립 | `_protocol`, `connecter`, `socket`, `time` |
| `ynb.connecter` | RTSP URI 인코딩, TCP 연결, OPTIONS 응답 판정 | `_protocol`, `socket`, `urllib.parse` |
| `ynb` | `sender`, `receiver`, version 공개 | `sender`, `receiver` |

`_protocol.py`의 앞쪽 밑줄은 패키지 내부 구현임을 뜻한다. 애플리케이션은
주로 `from ynb import sender, receiver`를 사용한다.

The leading underscore marks `_protocol.py` as an internal module. Applications
normally use `from ynb import sender, receiver`.

## 5. 전체 메시지 흐름 / End-to-end message flow

```text
Sender                         Receiver                     RTSP Server
  |                               |                              |
  |-- ADVERTISE (id=A) broadcast->|                              |
  |<- ACK (id=A, for=ADVERTISE) --|                              |
  |                               |                              |
  |-- DETAIL (id=D) unicast ----->|                              |
  |<- ACK (id=D, for=DETAIL) -----|                              |
  |                               |-- TCP connect -------------->|
  |                               |-- OPTIONS ... RTSP/2.0 ----->|
  |                               |<- RTSP/2.0 status + headers --|
  |                               |                              |
```

`A`와 `D`는 서로 다른 canonical lowercase UUID v4다. 각 ACK는 새 ID를 만들지
않고 대상 메시지의 ID를 복사한다. UDP ACK는 RTSP 연결 성공을 뜻하지 않는다.

`A` and `D` are distinct canonical lowercase UUID v4 values. An ACK copies the ID
of its target message. A UDP ACK does not imply a successful RTSP connection.

## 6. Wire protocol 설계 / Wire protocol design

### 6.1 공통 인코딩 / Common encoding

- 한 메시지는 한 UDP datagram 안의 UTF-8 JSON object다.
- JSON은 불필요한 공백 없이 compact form으로 인코딩한다.
- `NaN`, `Infinity`처럼 JSON 표준에 없는 수는 거부한다.
- 최상위 JSON 값은 object여야 한다.
- 필드 집합은 정확히 일치해야 하며 누락 필드와 추가 필드를 모두 거부한다.
- encoded payload는 IPv4 UDP payload 최대값인 65,507 bytes를 넘을 수 없다.

Each message is one compact UTF-8 JSON object in one UDP datagram. Non-standard
numbers, non-object roots, unknown fields, missing fields, and payloads larger
than 65,507 bytes are rejected.

### 6.2 메시지 schema / Message schemas

#### ADVERTISE

```json
{
  "message_type": "ADVERTISE",
  "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "device_id": "DC:A6:32:12:34:56"
}
```

Sender의 존재만 알린다. RTSP endpoint는 broadcast에 포함하지 않는다.
It announces only the Sender's presence; the RTSP endpoint is not broadcast.

#### ACK

```json
{
  "message_type": "ACK",
  "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "device_id": "DC:A6:32:12:34:56",
  "ack_for": "ADVERTISE"
}
```

`ack_for`는 `ADVERTISE` 또는 `DETAIL`이다. `message_id`는 확인 대상에서
복사한다. `ack_for` is either `ADVERTISE` or `DETAIL`, and `message_id` is
copied from that target.

#### DETAIL

```json
{
  "message_type": "DETAIL",
  "message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "device_id": "DC:A6:32:12:34:56",
  "ip": "192.168.0.10",
  "rtsp_port": 8554,
  "rtsp_path": "/stream"
}
```

선택된 Receiver에만 unicast되며 RTSP 연결에 필요한 endpoint를 전달한다.
It is unicasted only to the selected Receiver and carries the RTSP endpoint.

### 6.3 필드 검증 / Field validation

| 필드 / Input | 허용 규칙 / Accepted form | 거부 예 / Rejected example |
| --- | --- | --- |
| `message_id` | canonical lowercase UUID v4 | uppercase UUID, UUID v1, noncanonical text |
| `device_id` | uppercase `AA:BB:CC:DD:EE:FF` | lowercase MAC, missing octet |
| `ip` | canonical dotted-decimal IPv4 | hostname, IPv6, leading-zero form |
| `rtsp_port`, `start_port` | integer `1..65535`, not `bool` | `0`, `65536`, `True` |
| `rtsp_path` | `/`로 시작, 제어 문자 없음, UTF-8 가능 | `stream`, newline, lone surrogate |
| `timeout` | finite number `>= 0`, not `bool` | negative, `NaN`, infinity, `False` |

검증 함수는 성공한 값을 정규화된 타입으로 반환한다. 로컬 API 입력이 잘못되면
네트워크 작업 전에 `ValueError`를 발생시킨다. 수신한 wire message가 잘못되면
`MessageError`로 변환되며 Sender와 Receiver의 수신 loop에서 무시된다.

Validators return the accepted value in its normalized type. Invalid local API
arguments raise `ValueError` before network activity. Invalid received messages
become `MessageError` and are ignored by receive loops.

## 7. Sender 상세 설계 / Sender detailed design

### 7.1 공개 API

```python
sender.advertise(
    device_id,
    ip,
    rtsp_port,
    rtsp_path,
    *,
    timeout=30.0,
    start_port=37020,
) -> bool
```

반환값은 DETAIL ACK까지 완료했는지를 나타낸다. `True`는 Receiver가 RTSP
endpoint에 실제로 연결했다는 뜻이 아니다.

The return value indicates completion through the DETAIL ACK. `True` does not
mean that the Receiver successfully probed the RTSP endpoint.

### 7.2 상태 흐름

```text
VALIDATE_INPUT
      |
      v
BROADCAST_ADVERTISE -- invalid/unrelated packet --> wait
      | matching ACK
      v
SELECT_RECEIVER_PEER
      |
      v
UNICAST_DETAIL
      |
      v
WAIT_DETAIL_ACK -- wrong peer/ID/type --> wait
      |
      +-- matching ACK --> return True
      +-- timeout/error --> return False
```

1. ADVERTISE와 DETAIL을 socket 생성 전에 검증하고 인코딩한다.
2. 두 메시지의 UUID가 우연히 같으면 DETAIL ID를 다시 생성한다.
3. 임시 local UDP port에 bind하고 broadcast 권한을 설정한다.
4. `255.255.255.255:<start_port>`로 같은 ADVERTISE를 최대 10회 보낸다.
5. 각 시도는 최대 3초 대기하되 호출 전체 deadline을 넘지 않는다.
6. `device_id`, `message_id`, `ack_for`가 일치하는 첫 ACK의 실제 peer를
   Receiver로 선택한다.
7. DETAIL을 선택된 peer에 unicast한다.
8. 같은 peer에서 온 대응 DETAIL ACK만 성공으로 인정한다.

## 8. Receiver 상세 설계 / Receiver detailed design

### 8.1 공개 API

```python
receiver.discover(
    timeout=30.0,
    *,
    start_port=37020,
    bind_host="0.0.0.0",
) -> dict[str, object] | None
```

UDP 교환과 RTSP probe까지 완료하면 결과 dict를 반환한다. Sender를 선택하지
못하거나 DETAIL을 받기 전에 timeout 또는 socket 오류가 발생하면 `None`이다.

The function returns a result dictionary after UDP exchange and RTSP probing.
It returns `None` if no Sender is selected or DETAIL does not arrive in time.

### 8.2 상태 흐름

```text
LISTEN_ADVERTISE
      | first valid ADVERTISE
      v
ACK_AND_LOCK_PEER
      |
      v
WAIT_DETAIL
      |-- same ADVERTISE again --> resend ACK, keep selection
      |-- other peer/device -----> ignore
      |-- reused/invalid ID -----> ignore
      |-- valid DETAIL ----------> send ACK
      v
PROBE_RTSP
      |
      v
BUILD_RESULT
```

Receiver는 첫 유효 ADVERTISE의 실제 peer와 `device_id`를 현재 호출 동안
고정한다. 이 정책은 뒤늦게 온 다른 Sender가 진행 중인 교환을 탈취하는 것을
막는다. 첫 ACK가 유실되어 같은 ADVERTISE가 다시 오면 ACK를 재전송한다.

The Receiver locks onto the first valid advertiser's actual peer and device ID
for the duration of the call. This prevents a later Sender from replacing an
in-progress exchange. An identical repeated ADVERTISE is acknowledged again to
recover from a lost first ACK.

## 9. RTSP probe 상세 설계 / RTSP probe detailed design

### 9.1 URI 생성

`build_rtsp_uri()`는 endpoint를 검증한 뒤 다음 형식으로 조합한다.

```text
rtsp://<IPv4>:<port><percent-encoded-path>
```

경로의 `/`는 유지하고 공백과 비 ASCII 문자는 UTF-8 percent encoding한다.
Path slashes are preserved; spaces and non-ASCII text use UTF-8 percent encoding.

### 9.2 OPTIONS 교환

```text
OPTIONS rtsp://192.168.0.10:8554/stream RTSP/2.0\r\n
CSeq: 1\r\n
User-Agent: ynb/0.0.1\r\n
\r\n
```

TCP connect, 요청 전송, 응답 header 수신은 하나의 monotonic deadline을
공유한다. 응답은 `\r\n\r\n`까지 읽되 최대 16,384 bytes로 제한한다.

TCP connection, request sending, and response-header reading share one monotonic
deadline. Reading stops at `\r\n\r\n` and is capped at 16,384 bytes.

성공 조건은 첫 줄이 아래 정규식과 일치하고 status code가 `200..299`인 경우다.

```text
^RTSP/2\.0 ([0-9]{3}) [\x20-\x7e]+$
```

RTSP/1.0, reason phrase가 없는 응답, 제어 문자가 섞인 응답, 2xx 이외 상태,
연결 거부와 timeout은 모두 `False`다.

RTSP/1.0, missing reason phrases, control characters, non-2xx statuses,
connection refusal, and timeouts all produce `False`.

## 10. Timeout과 오류 처리 / Timeout and error handling

`time.monotonic()`을 사용하는 이유는 시스템 시각이 변경되어도 남은 시간이
갑자기 늘거나 줄지 않게 하기 위해서다. 각 단계는 `deadline - monotonic()`으로
남은 예산을 계산한다.

`time.monotonic()` prevents wall-clock changes from extending or shortening an
operation unexpectedly. Every stage calculates its remaining budget from the
same absolute deadline.

| 위치 / Boundary | 오류 / Failure | 공개 결과 / Public result |
| --- | --- | --- |
| Sender 인자 검증 | invalid value | `ValueError` |
| Sender UDP 작업 | timeout, `OSError`, timeout overflow | `False` |
| Receiver 인자 검증 | invalid value | `ValueError` |
| Receiver UDP 작업 | timeout, `OSError`, timeout overflow | `None` |
| 수신 wire parsing | malformed UDP payload | packet ignored |
| RTSP probe | invalid endpoint, TCP/RTSP error | `False` |
| Receiver 내부 probe | unexpected exception | result with `rtsp_connected=False` |

프로그래밍 오류를 무조건 숨기지 않기 위해 입력 검증 실패는 명시적
`ValueError`로 남긴다. 반면 네트워크에서 흔히 발생하는 실패는 API의 정상
실패 결과로 변환한다.

Invalid caller input remains an explicit `ValueError`, while expected network
failures are converted into normal API failure values.

## 11. 결과 데이터 / Result data

Receiver가 DETAIL을 받고 probe를 시도하면 다음 dict를 반환한다.

```python
{
    "device_id": "DC:A6:32:12:34:56",
    "ip": "192.168.0.10",
    "rtsp_port": 8554,
    "rtsp_path": "/stream",
    "rtsp_uri": "rtsp://192.168.0.10:8554/stream",
    "rtsp_connected": True,
}
```

`rtsp_connected`만 probe 결과에 따라 달라진다. DETAIL이 유효했다면 probe가
실패해도 endpoint 정보는 유지된다.

Only `rtsp_connected` depends on the probe result. Once DETAIL is valid, endpoint
information remains available even when probing fails.

## 12. 자원과 동시성 / Resources and concurrency

- UDP와 TCP socket은 context manager로 닫는다.
- 공개 API는 blocking call이며 내부 thread나 event loop를 만들지 않는다.
- `discover()` 호출 하나는 Sender 한 대만 처리한다.
- 여러 장비를 동시에 처리하려면 상위 애플리케이션이 별도 process 또는 호출
  구조를 설계해야 한다.
- Receiver의 `SO_REUSEADDR`는 같은 주소 재사용 가능성을 높이지만, 여러
  Receiver가 같은 port의 모든 datagram을 받는다는 보장은 아니다.

Sockets are closed through context managers. APIs are blocking, create no worker
threads, and process one Sender per `discover()` call. Multi-device orchestration
belongs to the calling application.

## 13. 보안 고려사항 / Security considerations

현재 protocol에는 인증, 무결성 검증, 암호화, replay 방지가 없다. 같은 LAN의
다른 host가 유효한 형식의 메시지를 위조할 수 있다. 실제 peer 고정과 ID
상관관계는 교환 혼선을 줄이지만 보안 인증 수단은 아니다.

The protocol provides no authentication, integrity protection, encryption, or
replay prevention. Peer locking and ID correlation reduce accidental mixing but
do not authenticate a Sender or Receiver.

따라서 신뢰할 수 있는 LAN에서만 사용하고 방화벽에서 UDP start port와 필요한
RTSP TCP port를 제한해야 한다. 운영 확장 시에는 message authentication,
nonce/replay cache, key 관리와 접근 제어를 별도 설계해야 한다.

Use only on a trusted LAN and restrict the UDP start port and RTSP TCP ports with
a firewall. A production extension requires explicit authentication, replay
protection, key management, and access control.

## 14. 요구사항 추적성 / Requirements traceability

| SRS 요구사항 / Requirement | 주요 구현 / Primary implementation |
| --- | --- |
| CON-004, FR-SND-001~004 | `sender.advertise()`, `_broadcast_until_ack()`, `_wait_for_matching_ack()` |
| FR-SND-005~008 | `_prepare_detail()`, `make_detail()`, DETAIL ACK matching |
| FR-RCV-001~003, FR-RCV-009 | `receiver.discover()`, `_accept_advertisement()`, `_accept_detail()` |
| FR-RCV-004~005 | `parse_detail()`, `_send_ack()` |
| FR-RCV-006~008 | `_probe_and_build_result()`, `_receive_message()` |
| CON-006, CON-008 | `_protocol.encode_message()`, `decode_message()`, validators |
| FR-RTSP-001 | `connecter.build_rtsp_uri()` |
| FR-RTSP-002~007 | `connecter.probe_rtsp()` and private RTSP helpers |
| FR-API-001~005 | `ynb.__init__`, public function signatures, `DEFAULT_TIMEOUT` |

## 15. 테스트 설계 / Test design

| 테스트 파일 / Test file | 설계 검증 대상 / Design coverage |
| --- | --- |
| `tests/test_protocol.py` | strict schema, UUID, JSON/UTF-8, validation boundaries |
| `tests/test_sender.py` | broadcast policy, retry timing, ACK ID/type/peer correlation |
| `tests/test_receiver.py` | malformed input, first-peer lock, repeated ACK, timeout |
| `tests/test_connecter.py` | URI encoding, OPTIONS request, status parsing, errors |
| `tests/test_e2e.py` | complete UDP exchange and RTSP probe result |

자동 End-to-End 테스트는 실제 loopback UDP/TCP socket과 작은 가짜 RTSP
서버를 사용한다. 테스트에서만 private broadcast 주소를 loopback으로 바꾸며
공개 API에는 unicast 목적지 인자를 추가하지 않는다.

The automated end-to-end test uses real loopback UDP/TCP sockets and a small fake
RTSP server. Only the private broadcast constant is patched for deterministic
local testing; the public API exposes no unicast destination override.

## 16. 알려진 제한과 확장 지점 / Limitations and extension points

현재 버전은 다음을 제공하지 않는다.

- DETAIL 또는 DETAIL ACK 재전송
- 일반적인 중복 제거, 패킷 재정렬, replay cache
- 여러 Sender registry와 수명 관리
- 비동기 API, callback, worker pool
- 인증·암호화
- RTSP 인증과 `DESCRIBE`/`SETUP`/`PLAY`
- RTP/RTCP 수신 또는 미디어 decoding
- subnet을 넘는 discovery

향후 확장 시 wire schema 변경에는 protocol version 협상이 필요하다. 재전송을
추가한다면 중복 수신 시 부작용이 없도록 idempotent 처리와 retry backoff를 함께
설계해야 한다. 여러 Sender를 지원하려면 단일 peer lock 대신 교환별 상태와
만료 시간을 가진 registry가 필요하다.

Future wire-schema changes require protocol-version negotiation. Retries require
idempotent duplicate handling and backoff. Multi-Sender support requires a
registry of per-exchange state and expiration times instead of one peer lock.
