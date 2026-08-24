# RTSP 연결정보 부트스트랩 소프트웨어 요구사항 명세서

## 1. 문서 정보

| 항목        | 값                                   |
| --------- | ----------------------------------- |
| 문서명       | Software Requirements Specification |
| 대상 시스템    | `YNB` (`ynb` Python package)        |
| 대상 버전     | 0.0.1                               |
| 기준 Python | 3.11.9                              |
| 대상 RTSP   | RTSP 2.0                            |
| 문서 상태     | 최소 기능 PoC 요구사항                      |

이 문서에서 "하여야 한다"는 검증 가능한 필수 요구사항을 뜻한다.

## 2. 목적과 범위

### 2.1 목적

시스템은 동일 IPv4 LAN에서 UDP broadcast를 통해 송신 장비를 발견하고, 수신기와 최소한의 UDP 정보 교환을 수행한 뒤 전달받은 RTSP endpoint가 RTSP 2.0 요청에 응답하는지 확인하여야 한다.

최종 결과는 Python `dict`로 제공하여야 한다.

### 2.2 기본 동작

0.0.1은 다음 흐름을 구현한다.

```text
Sender                         Receiver                    RTSP Server
  |                               |                            |
  |--- ADVERTISE (id=A) broadcast>|                            |
  |                               |                            |
  |<------ ACK (id=A) unicast ----|                            |
  |                               |                            |
  |------- DETAIL (id=D) unicast->|                            |
  |                               |                            |
  |<------ ACK (id=D) unicast ----|                            |
  |                               |                            |
  |                               |------- TCP connect ------->|
  |                               |------- RTSP OPTIONS ------>|
  |                               |<------ RTSP/2.0 2xx -------|
  |                               |                            |
```

각 단계의 의미는 다음과 같다.

1. Sender가 자신의 존재를 `ADVERTISE`로 broadcast한다.
2. Receiver가 `ADVERTISE`를 수신하면 Sender의 UDP peer 주소로 ACK를 unicast한다.
3. Sender는 ACK를 보낸 Receiver의 주소로 RTSP 연결정보를 포함한 `DETAIL`을 unicast한다.
4. Receiver는 유효한 `DETAIL`을 수신하면 Sender에 ACK를 unicast한다.
5. Receiver는 DETAIL에 포함된 RTSP endpoint로 RTSP/2.0 연결을 확인한다.
6. Receiver는 결과를 Python `dict`로 반환한다.

### 2.3 범위 제외

0.0.1에서는 다음 기능을 구현하지 않는다.

* DETAIL 메시지 또는 DETAIL ACK 재전송
* `message_id`를 이용한 중복 메시지 제거 또는 replay 방지
* UDP 패킷 재정렬 처리
* 장비 상태 registry
* callback
* 성공 TTL
* 비동기 RTSP probe
* worker thread 또는 thread pool
* context manager
* 장비 자동 만료 또는 삭제
* 사용자 인증
* 메시지 인증 및 암호화
* RTSP `DESCRIBE`, `SETUP`, `PLAY`, `TEARDOWN`
* RTP/RTCP 수신
* 영상·음성 디코딩 및 재생
* 인터넷 또는 서로 다른 subnet 사이의 자동 발견
* mDNS, SSDP, ONVIF 탐색

## 3. 용어

| 용어         | 정의                                                 |
| ---------- | -------------------------------------------------- |
| Sender     | 자신의 존재와 RTSP 연결정보를 제공하는 측                          |
| Receiver   | Sender를 발견하고 RTSP 연결 가능 여부를 확인하는 측                 |
| device ID  | Sender를 식별하는 값. 0.0.1에서는 MAC 주소를 사용                |
| message ID | 개별 ADVERTISE 또는 DETAIL과 그 ACK를 연결하는 UUID v4 문자열    |
| peer       | UDP 데이터그램의 실제 발신 `(ip, port)`                      |
| endpoint   | `(ip, rtsp_port, rtsp_path)`로 구성되는 RTSP 접속정보       |
| probe      | endpoint에 RTSP/2.0 `OPTIONS` 요청을 보내 응답 여부를 확인하는 작업 |
| start port | UDP 부트스트랩 메시지를 송수신하는 포트. 기본값은 `37020`              |

## 4. 실행 환경과 제약

| ID      | 요구사항                                            |
| ------- | ----------------------------------------------- |
| CON-001 | 시스템은 Python 3.11.9에서 설치 및 실행 가능하여야 한다.          |
| CON-002 | runtime 기능은 Python 표준 라이브러리만 사용하여야 한다.          |
| CON-003 | 자동 발견은 IPv4 동일 broadcast domain에서 수행하여야 한다.     |
| CON-004 | 최초 ADVERTISE는 UDP broadcast를 사용하여야 한다.          |
| CON-005 | ACK와 DETAIL은 UDP unicast를 사용하여야 한다.             |
| CON-006 | wire 데이터는 하나의 UDP 데이터그램에 담긴 UTF-8 JSON 객체여야 한다. |
| CON-007 | RTSP 연결 확인은 RTSP 2.0을 대상으로 하여야 한다.              |
| CON-008 | `message_id`는 canonical lowercase UUID v4 문자열이어야 한다. |

## 5. Wire 데이터 요구사항

0.0.1에서는 다음 세 종류의 메시지를 사용한다.

* `ADVERTISE`
* `ACK`
* `DETAIL`

### 5.1 ADVERTISE

Sender가 자신의 존재를 알리기 위한 최소 광고 메시지이다.

```json
{
  "message_type": "ADVERTISE",
  "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "device_id": "DC:A6:32:12:34:56"
}
```

| 필드             | 타입     | 설명              |
| -------------- | ------ | --------------- |
| `message_type` | string | 정확히 `ADVERTISE` |
| `message_id`   | string | 이 ADVERTISE를 식별하는 UUID v4 |
| `device_id`    | string | Sender의 MAC 주소  |

`device_id`는 `AA:BB:CC:DD:EE:FF` 형식의 MAC 주소 문자열을 사용한다.

ADVERTISE에는 RTSP endpoint 정보를 포함하지 않는다.

### 5.2 ACK

수신한 메시지를 확인하기 위한 메시지이다.

ADVERTISE에 대한 ACK:

```json
{
  "message_type": "ACK",
  "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "device_id": "DC:A6:32:12:34:56",
  "ack_for": "ADVERTISE"
}
```

DETAIL에 대한 ACK:

```json
{
  "message_type": "ACK",
  "message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "device_id": "DC:A6:32:12:34:56",
  "ack_for": "DETAIL"
}
```

| 필드             | 타입     | 설명                       |
| -------------- | ------ | ------------------------ |
| `message_type` | string | 정확히 `ACK`                |
| `message_id`   | string | ACK 대상 메시지에서 복사한 UUID v4 |
| `device_id`    | string | ACK 대상 Sender의 device ID |
| `ack_for`      | string | `ADVERTISE` 또는 `DETAIL`  |

ACK는 UDP 데이터그램을 실제로 보낸 peer 주소로 unicast하여야 한다.

ACK의 `message_id`는 확인 대상 ADVERTISE 또는 DETAIL의 `message_id`와 정확히
같아야 한다. ACK를 위해 새로운 `message_id`를 생성하지 않는다.

ACK는 RTSP 연결 성공을 의미하지 않는다.

### 5.3 DETAIL

Sender가 Receiver에게 실제 RTSP 접속정보를 전달하기 위한 메시지이다.

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

| 필드             | 타입      | 설명                      |
| -------------- | ------- | ----------------------- |
| `message_type` | string  | 정확히 `DETAIL`            |
| `message_id`   | string  | 이 DETAIL을 식별하는 UUID v4 |
| `device_id`    | string  | Sender의 MAC 주소          |
| `ip`           | string  | RTSP 서버의 IPv4 주소        |
| `rtsp_port`    | integer | RTSP TCP 포트, `1..65535` |
| `rtsp_path`    | string  | `/`로 시작하는 RTSP 경로       |

Sender는 `uuid.uuid4()`와 동등한 방법으로 각 ADVERTISE와 DETAIL에 새
`message_id`를 생성하여야 한다. 동일 교환의 ADVERTISE와 DETAIL도 서로 다른
ID를 사용한다. Receiver는 ACK에 대상 메시지의 ID를 그대로 복사하고 Sender는
해당 ID가 일치하는 ACK만 유효한 것으로 처리한다. 0.0.1은 이 ID를 ACK
상관관계에만 사용한다.

## 6. 기능 요구사항

### 6.1 Sender

| ID         | 요구사항                                                                                         |
| ---------- | -------------------------------------------------------------------------------------------- |
| FR-SND-001 | Sender는 `device_id`와 새 `message_id`를 포함한 ADVERTISE 메시지를 생성하여야 한다.                              |
| FR-SND-002 | Sender는 유효한 ACK을 받을 때까지 동일 ADVERTISE를 설정된 UDP start port로 3초 간격, 최대 10회 broadcast하여야 한다. |
| FR-SND-003 | Sender는 ADVERTISE 전송 후 ACK를 수신할 수 있어야 한다.                                                    |
| FR-SND-004 | `ack_for="ADVERTISE"`이고 `device_id`와 `message_id`가 ADVERTISE와 일치하는 유효한 ACK를 수신하면 해당 ACK의 peer 주소를 Receiver 주소로 사용하여야 한다. |
| FR-SND-005 | Sender는 해당 Receiver에 새 `message_id`, `device_id`, `ip`, `rtsp_port`, `rtsp_path`를 포함한 DETAIL을 unicast하여야 한다. |
| FR-SND-006 | Sender는 `ack_for="DETAIL"`이고 `device_id`와 `message_id`가 DETAIL과 일치하는 ACK를 수신할 수 있어야 한다. |
| FR-SND-007 | 잘못된 MAC 주소, IPv4 주소, RTSP port 또는 RTSP path는 네트워크 전송 전에 거부하여야 한다.                            |
| FR-SND-008 | Sender는 ADVERTISE와 DETAIL에 서로 다른 새 UUID v4 `message_id`를 사용하여야 한다. |

### 6.2 Receiver

| ID         | 요구사항                                                                           |
| ---------- | ------------------------------------------------------------------------------ |
| FR-RCV-001 | Receiver는 설정된 UDP start port에서 ADVERTISE를 수신하여야 한다.                            |
| FR-RCV-002 | Receiver는 유효한 ADVERTISE를 수신하면 ADVERTISE의 `message_id`를 복사한 ACK를 데이터그램의 실제 peer 주소로 unicast하여야 한다. |
| FR-RCV-003 | Receiver는 ACK를 보낸 Sender로부터 DETAIL을 수신하여야 한다.                                  |
| FR-RCV-004 | Receiver는 DETAIL의 `message_id`, `device_id`, `ip`, `rtsp_port`, `rtsp_path`를 검증하여야 한다. |
| FR-RCV-005 | Receiver는 유효한 DETAIL을 수신하면 DETAIL의 `message_id`를 복사한 ACK를 Sender의 peer 주소로 unicast하여야 한다. |
| FR-RCV-006 | DETAIL ACK 전송 후 광고된 endpoint에 RTSP probe를 수행하여야 한다.                            |
| FR-RCV-007 | `discover(timeout)`은 지정된 시간 안에 정상적인 정보 교환이 완료되지 않으면 결과 없음으로 종료하여야 한다.          |
| FR-RCV-008 | 잘못된 UDP 입력은 Receiver 전체를 종료시키지 않아야 한다.                                         |
| FR-RCV-009 | Receiver는 첫 유효 ADVERTISE의 `device_id`와 실제 peer만 선택하고, 완료 또는 timeout까지 다른 장비를 무시하여야 한다. 동일한 선택 ADVERTISE가 재수신되면 ACK를 다시 전송하여야 한다. |

## 7. RTSP probe 요구사항

| ID          | 요구사항                                                                 |
| ----------- | -------------------------------------------------------------------- |
| FR-RTSP-001 | 시스템은 DETAIL의 endpoint를 `rtsp://<ip>:<port><path>` 형태의 URI로 구성하여야 한다. |
| FR-RTSP-002 | Receiver는 DETAIL의 IP와 RTSP port로 TCP 연결을 시도하여야 한다.                   |
| FR-RTSP-003 | TCP 연결 성공 후 해당 URI에 `OPTIONS ... RTSP/2.0` 요청을 전송하여야 한다.             |
| FR-RTSP-004 | 요청에는 `CSeq` header를 포함하여야 한다.                                        |
| FR-RTSP-005 | 응답 status line이 `RTSP/2.0`이고 상태 코드가 `2xx`이면 probe 성공으로 판단하여야 한다.     |
| FR-RTSP-006 | timeout, 연결 거부 또는 잘못된 RTSP 응답은 probe 실패로 처리하여야 한다.                   |
| FR-RTSP-007 | RTSP probe 실패는 처리되지 않은 예외로 Receiver 외부에 전파되지 않아야 한다.                 |

## 8. 공개 Python API

| ID         | 요구사항                                                                  |
| ---------- | --------------------------------------------------------------------- |
| FR-API-001 | 패키지는 `from ynb import sender, receiver` 형태의 import를 지원하여야 한다.         |
| FR-API-002 | `sender.py`는 ADVERTISE → ACK → DETAIL → ACK 교환을 수행하는 공개 기능을 제공하여야 한다. |
| FR-API-003 | `receiver.py`는 `discover(timeout)` 형태의 발견 기능을 제공하여야 한다.               |
| FR-API-004 | RTSP URI 생성과 RTSP probe 기능은 `connecter.py`에 배치하여야 한다.                 |
| FR-API-005 | Sender, Receiver, RTSP probe의 timeout 기본값은 `30`초여야 한다.                    |

## 9. 결과 데이터 요구사항

Receiver는 DETAIL 수신과 RTSP probe를 완료한 경우 다음 형태의 Python `dict`를 반환하여야 한다.

| 키                | 타입      | 설명              |
| ---------------- | ------- | --------------- |
| `device_id`      | string  | Sender의 MAC 주소  |
| `ip`             | string  | RTSP 서버 IPv4 주소 |
| `rtsp_port`      | integer | RTSP TCP port   |
| `rtsp_path`      | string  | RTSP path       |
| `rtsp_uri`       | string  | 조합된 RTSP URI    |
| `rtsp_connected` | boolean | RTSP probe 결과   |

예:

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

## 10. 최소 테스트 요구사항

### 10.1 ADVERTISE / ACK 테스트

다음을 검증하여야 한다.

```text
Sender
  |
  | ADVERTISE (message_id=A) broadcast
  v
Receiver
  |
  | ACK (message_id=A) unicast
  v
Sender
```

Receiver가 받은 `device_id`가 Sender가 전송한 MAC 주소와 동일하여야 한다.

ADVERTISE의 `message_id`가 UUID v4 형식이어야 하며 ADVERTISE ACK의
`message_id`가 이 값과 동일하여야 한다. Sender는 다른 `message_id`를 가진
ACK를 무시하여야 한다.

Sender는 ACK를 보낸 Receiver의 UDP peer 주소를 확인할 수 있어야 한다.

### 10.2 DETAIL / ACK 테스트

다음을 검증하여야 한다.

```text
Sender
  |
  | DETAIL (message_id=D) unicast
  v
Receiver
  |
  | ACK (message_id=D) unicast
  v
Sender
```

Receiver가 받은 다음 값이 Sender가 보낸 값과 동일하여야 한다.

* `device_id`
* `ip`
* `rtsp_port`
* `rtsp_path`

DETAIL의 `message_id`가 ADVERTISE의 `message_id`와 달라야 하며 DETAIL ACK의
`message_id`는 DETAIL의 값과 동일하여야 한다. Sender는 다른 `message_id`를
가진 ACK를 무시하여야 한다.

### 10.3 RTSP probe 테스트

가짜 TCP 서버가 다음 응답을 반환하도록 구성한다.

```text
RTSP/2.0 200 OK
CSeq: 1

```

정상 응답에서는 probe 결과가 `True`여야 한다.

연결 거부 또는 timeout에서는 `False`여야 한다.

### 10.4 End-to-End 테스트

다음 전체 흐름을 검증하여야 한다.

```text
Sender                         Receiver                  Fake RTSP Server
  |                               |                           |
  |--- ADVERTISE (id=A) broadcast>|                           |
  |<------ ACK (id=A) unicast ----|                           |
  |------- DETAIL (id=D) unicast->|                           |
  |<------ ACK (id=D) unicast ----|                           |
  |                               |------ OPTIONS ----------->|
  |                               |<----- RTSP/2.0 200 -------|
  |                               |                           |
```

최종 결과의 `rtsp_connected`는 `True`여야 한다.

## 11. 완료 기준

0.0.1은 다음 조건을 모두 만족하면 완료된 것으로 간주한다.

1. Python 3.11.9에서 패키지를 import할 수 있다.
2. Sender가 최소 ADVERTISE를 UDP broadcast할 수 있다.
3. Receiver가 ADVERTISE를 수신하고 ACK를 unicast할 수 있다.
4. Sender가 ACK peer로 DETAIL을 unicast할 수 있다.
5. Receiver가 DETAIL을 수신하고 ACK를 unicast할 수 있다.
6. Receiver가 전달받은 endpoint에 RTSP/2.0 `OPTIONS` 요청을 보낼 수 있다.
7. RTSP 성공 또는 실패 결과를 `rtsp_connected`로 반환할 수 있다.
8. 두 ACK가 각각 대상 ADVERTISE와 DETAIL의 `message_id`와 일치할 때만 인정된다.
9. 최소 End-to-End 테스트가 통과한다.

## 12. 알려진 제한

* UDP 데이터그램의 전달 성공을 보장하지 않는다.
* ADVERTISE ACK가 없으면 동일 ADVERTISE를 3초 간격으로 최대 10회 재전송한다.
* DETAIL 또는 DETAIL ACK 유실 시 자동 재전송하지 않는다.
* 메시지 중복 및 순서 변경을 별도로 처리하지 않는다.
* `message_id`는 ACK 상관관계와 동일 ADVERTISE 재전송 식별에만 사용하며 중복 제거 또는 replay 방지를 제공하지 않는다.
* 장비의 장기 상태를 유지하지 않는다.
* MAC 주소 변경 시 동일 장비도 다른 `device_id`로 인식될 수 있다.
* RTSP `OPTIONS` 성공은 실제 영상 재생 성공을 보장하지 않는다.
* 인증 및 암호화를 제공하지 않으므로 신뢰할 수 있는 로컬 네트워크에서 사용하는 것을 전제로 한다.
