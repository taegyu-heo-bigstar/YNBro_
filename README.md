# YNB

YNB는 동일한 IPv4 broadcast domain에서 Sender를 발견하고, Sender가 제공한
RTSP endpoint가 RTSP/2.0 `OPTIONS` 요청에 응답하는지 확인하는 Python
패키지입니다.

현재 버전은 **0.0.1 최소 기능 PoC**입니다. 구현과 이 문서가 충돌하면
[`docs/SRS.md`](docs/SRS.md)를 기준으로 판단합니다. 설계 근거는
[`docs/SDS.md`](docs/SDS.md)를 참고하십시오.

별도 CLI나 상시 실행 daemon은 제공하지 않습니다. Receiver를 먼저 호출해
대기시킨 뒤 `sender.advertise()`와 `receiver.discover()`를 Python에서 직접
사용합니다.

## 1. 지원 범위

YNB 0.0.1의 전체 흐름은 다음과 같습니다.

```text
Sender                         Receiver                    RTSP Server
  |                               |                            |
  |--- ADVERTISE (id=A) broadcast>|                            |
  |<------ ACK (id=A) unicast ----|                            |
  |------- DETAIL (id=D) unicast->|                            |
  |<------ ACK (id=D) unicast ----|                            |
  |                               |------- TCP connect ------->|
  |                               |------- RTSP OPTIONS ------>|
  |                               |<------ RTSP/2.0 2xx -------|
```

- Sender는 `255.255.255.255`의 설정된 UDP start port로 ADVERTISE를
  3초 간격, 최대 10회 broadcast합니다.
- Receiver는 실제 UDP peer로 ACK를 unicast합니다.
- Sender는 첫 ACK의 실제 peer로 DETAIL을 unicast합니다.
- Receiver는 DETAIL ACK를 보낸 뒤 RTSP/2.0 probe를 수행합니다.
- Receiver는 최종 결과를 Python `dict`로 반환합니다.

다음 기능은 0.0.1의 범위가 아닙니다.

- DETAIL 재전송, 중복 제거, replay 방지, 패킷 재정렬
- registry, callback, TTL, 장비 자동 만료
- 비동기 probe, worker thread, thread pool, context manager
- 인증, 암호화, 메시지 서명
- RTSP `DESCRIBE`, `SETUP`, `PLAY`, `TEARDOWN`
- RTP/RTCP 수신과 영상·음성 재생
- 다른 subnet, mDNS, SSDP, ONVIF을 통한 자동 발견

## 2. 실행 환경과 설치

SRS가 보장하는 기준 환경은 **Python 3.11.9**입니다. 패키지 메타데이터는
Python 3.11 이상 설치를 허용하지만, 다른 버전은 이 SRS의 검증 대상이
아닙니다. runtime 의존성은 Python 표준 라이브러리뿐입니다.

프로젝트 루트에서 다음과 같이 설치합니다.

### Windows PowerShell

```powershell
python --version
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -c "import ynb; print(ynb.__version__)"
```

### Linux 또는 macOS

```sh
python3.11 --version
python3.11 -m venv .venv
PY=.venv/bin/python
"$PY" -m pip install -e .
"$PY" -c 'import ynb; print(ynb.__version__)'
```

정상 설치 결과의 버전은 `0.0.1`입니다.

## 3. 공개 Python API

### 3.1 `sender.advertise()`

```python
from ynb import sender

acknowledged = sender.advertise(
    device_id="AA:BB:CC:DD:EE:FF",
    ip="192.168.1.10",
    rtsp_port=8554,
    rtsp_path="/stream",
    start_port=37020,
)
print(acknowledged)
```

실제 signature는 다음과 같습니다.

```python
advertise(
    device_id,
    ip,
    rtsp_port,
    rtsp_path,
    *,
    timeout=30.0,
    start_port=37020,
) -> bool
```

| 인자 | 기본값 | 의미 |
| --- | --- | --- |
| `device_id` | 필수 | 대문자 `AA:BB:CC:DD:EE:FF` 형식의 MAC 주소 |
| `ip` | 필수 | Receiver가 접속할 RTSP 서버의 canonical IPv4 |
| `rtsp_port` | 필수 | RTSP TCP port, 정수 `1..65535` |
| `rtsp_path` | 필수 | `/`로 시작하는 RTSP path |
| `timeout` | `30.0` | 광고 재전송과 두 ACK 교환 전체의 최대 시간 |
| `start_port` | `37020` | ADVERTISE 목적지 UDP port |

현재 0.0.1 구현은 ADVERTISE 목적지로 `255.255.255.255` limited broadcast를
사용합니다. 임의의 유니캐스트 또는 directed broadcast 목적지를 지정하는
공개 인자는 없습니다.

유효한 ADVERTISE ACK가 없으면 같은 `message_id`의 ADVERTISE를 3초 간격으로
최대 10회 전송합니다. 첫 유효 ACK가 오면 반복 광고를 중단하고 그 ACK의 실제
peer 하나에만 DETAIL을 전송합니다. 해당 peer의 DETAIL ACK가 전체 timeout 안에
오지 않으면 다른 peer로 교체하지 않고 `False`를 반환합니다.

Sender는 UDP socket을 `0.0.0.0:0`에 bind합니다. Sender의 로컬 UDP port,
즉 ADVERTISE의 source port이자 ACK의 destination port는 운영체제가 선택한
임시 port이며 현재 API로 고정할 수 없습니다. 같은 socket이 ADVERTISE
전송부터 DETAIL ACK 수신까지 사용됩니다.

반환값의 의미는 다음과 같습니다.

| 반환값 | 의미 |
| --- | --- |
| `True` | 올바른 Receiver peer에서 유효한 DETAIL ACK를 받음 |
| `False` | ACK timeout 또는 UDP socket 오류로 교환을 완료하지 못함 |

`True`는 RTSP 접속 성공을 뜻하지 않습니다. RTSP 결과는 Receiver의
`rtsp_connected`에서 확인합니다.

### 3.2 `receiver.discover()`

```python
from pprint import pprint
from ynb import receiver

device = receiver.discover(
    timeout=30,
    start_port=37020,
    bind_host="0.0.0.0",
)
pprint(device)
```

실제 signature는 다음과 같습니다.

```python
discover(
    timeout=30.0,
    *,
    start_port=37020,
    bind_host="0.0.0.0",
) -> dict[str, object] | None
```

| 인자 | 기본값 | 의미 |
| --- | --- | --- |
| `timeout` | `30.0` | UDP 교환과 RTSP probe 전체의 최대 시간 |
| `start_port` | `37020` | ADVERTISE를 받을 UDP port |
| `bind_host` | `0.0.0.0` | 수신 socket을 bind할 canonical IPv4 |

반환값의 의미는 다음과 같습니다.

| 반환값 | 의미 |
| --- | --- |
| `None` | timeout 또는 UDP 오류로 정상 정보 교환을 완료하지 못함 |
| dict + `rtsp_connected=True` | UDP 교환 완료 후 RTSP/2.0 OPTIONS 2xx 수신 |
| dict + `rtsp_connected=False` | UDP 교환은 완료했지만 RTSP probe 실패 |

RTSP timeout, 연결 거부, 잘못된 응답 등 probe 실패는 `discover()` 밖으로
처리되지 않은 예외를 내보내지 않습니다. 대신 완성된 dict에서
`rtsp_connected=False`로 보고합니다.

완료된 결과 dict는 정확히 다음 여섯 키를 가집니다.

```python
{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "ip": "192.168.1.10",
    "rtsp_port": 8554,
    "rtsp_path": "/stream",
    "rtsp_uri": "rtsp://192.168.1.10:8554/stream",
    "rtsp_connected": True,
}
```

### 3.3 RTSP helper

```python
from ynb.connecter import build_rtsp_uri, probe_rtsp

uri = build_rtsp_uri("192.168.1.10", 8554, "/stream")
connected = probe_rtsp("192.168.1.10", 8554, "/stream", timeout=5)
```

`probe_rtsp()`의 `timeout`을 생략하면 기본값은 `30.0`초입니다.

probe는 다음 요청을 전송합니다.

```text
OPTIONS rtsp://192.168.1.10:8554/stream RTSP/2.0
CSeq: 1
User-Agent: ynb/0.0.1

```

완전한 응답 header의 status line이 `RTSP/2.0`이고 상태 코드가 `200..299`이면
`True`입니다. 연결 거부, timeout, RTSP/1.0, 비-2xx 또는 잘못된 응답은
`False`입니다.

`OPTIONS` 성공은 해당 path의 영상이 실제로 재생된다는 보장이 아닙니다.

## 4. LAN에서 실행

Receiver를 먼저 실행하고, Receiver가 기다리는 동안 Sender를 실행해야
합니다. 두 장비의 `start_port`는 같아야 합니다.

예시 구성:

| 역할 | 예시 주소 |
| --- | --- |
| Sender 및 RTSP 서버 | `192.168.1.10` |
| Receiver | `192.168.1.20` |
| UDP start port | `37020` |
| RTSP endpoint | `192.168.1.10:8554/stream` |

Receiver PC:

```python
from pprint import pprint
from ynb import receiver

pprint(
    receiver.discover(
        timeout=60,
        start_port=37020,
        bind_host="0.0.0.0",
    )
)
```

Sender PC:

```python
from ynb import sender

print(
    sender.advertise(
        device_id="AA:BB:CC:DD:EE:FF",
        ip="192.168.1.10",
        rtsp_port=8554,
        rtsp_path="/stream",
        timeout=15,
        start_port=37020,
    )
)
```

`ip`는 Sender의 UDP peer 주소가 아니라 Receiver가 실제로 접속할 RTSP 서버
주소입니다. RTSP 서버가 다른 장비에 있어도 접근 가능한 canonical IPv4이면
됩니다.

네트워크에서 최소한 다음 통신을 허용해야 합니다.

- Receiver: UDP `37020` inbound
- RTSP 서버: 광고한 RTSP TCP port inbound
- Sender: 운영체제가 선택한 임시 UDP port로 돌아오는 ACK reply traffic

고정된 Sender ACK port는 없습니다. 같은 Wi-Fi 이름을 사용하더라도 guest
network, VLAN 또는 AP client isolation이 켜져 있으면 broadcast가 차단될 수
있습니다.

## 5. Wire 메시지

모든 wire 메시지는 하나의 UDP 데이터그램에 담긴 UTF-8 JSON 객체입니다.
필드는 아래 schema와 정확히 일치해야 하며 `message_id`는 canonical lowercase
UUID v4입니다.

ADVERTISE:

```json
{
  "message_type": "ADVERTISE",
  "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "device_id": "AA:BB:CC:DD:EE:FF"
}
```

ADVERTISE ACK:

```json
{
  "message_type": "ACK",
  "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "device_id": "AA:BB:CC:DD:EE:FF",
  "ack_for": "ADVERTISE"
}
```

DETAIL:

```json
{
  "message_type": "DETAIL",
  "message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "device_id": "AA:BB:CC:DD:EE:FF",
  "ip": "192.168.1.10",
  "rtsp_port": 8554,
  "rtsp_path": "/stream"
}
```

DETAIL ACK:

```json
{
  "message_type": "ACK",
  "message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "device_id": "AA:BB:CC:DD:EE:FF",
  "ack_for": "DETAIL"
}
```

ADVERTISE와 DETAIL은 서로 다른 새 UUID v4를 사용합니다. ACK는 확인 대상의
`message_id`를 그대로 복사합니다. Sender는 예상 peer, `device_id`,
`ack_for`, `message_id`가 모두 맞는 ACK만 인정합니다.

Receiver는 ADVERTISE ACK를 보낸 동일 peer에서 온 DETAIL만 처리합니다. 또한
그 DETAIL의 `device_id`가 ADVERTISE와 같고, `message_id`는 유효한 새 UUID
v4이며 ADVERTISE의 ID와 달라야 합니다.

Receiver는 첫 유효 ADVERTISE의 `device_id`와 실제 peer에 고정됩니다. 선택된
장비가 같은 ADVERTISE를 재전송하면 ACK 유실 복구를 위해 다시 ACK하지만, 다른
장비의 ADVERTISE와 DETAIL은 timeout까지 무시합니다.

## 6. 입력 검증과 오류 처리

- MAC 주소는 대문자 `AA:BB:CC:DD:EE:FF` 형식이어야 합니다.
- IPv4는 hostname이 아닌 canonical 숫자 형식이어야 합니다.
- port는 `bool`이 아닌 정수 `1..65535`여야 합니다.
- RTSP path는 `/`로 시작하고 제어 문자가 없어야 하며 유효한 UTF-8
  문자열이어야 합니다.
- timeout은 `bool`이 아닌 유한한 0 이상의 수여야 합니다.
- `start_port`도 정수 `1..65535`여야 합니다.

Sender에 문서화된 인자의 값 검증이 실패하면 socket 생성 전에 `ValueError`로
거부됩니다. 필수 인자를 생략하거나 알 수 없는 keyword를 넘기는 Python 호출
형식 오류는 `TypeError`입니다.
잘못된 수신 UDP 패킷은 Sender와 Receiver 내부에서 무시되며 전체 처리를
종료시키지 않습니다.

Receiver의 잘못된 `timeout`, `start_port`, `bind_host`는 `ValueError`입니다.
문법상 유효하지만 이 PC에 할당되지 않은 `bind_host` 때문에 bind가 실패하면
`None`입니다.

## 7. 테스트

Python 3.11.9에서 전체 테스트를 실행합니다.

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

설치된 Windows 가상환경을 사용할 때:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

영역별 실행:

```powershell
python -m unittest tests.test_protocol -v
python -m unittest tests.test_sender -v
python -m unittest tests.test_receiver -v
python -m unittest tests.test_connecter -v
python -m unittest tests.test_e2e -v
```

현재 테스트는 총 **29개**입니다.

| 파일 | 테스트 수 | 검증 영역 |
| --- | ---: | --- |
| `tests/test_protocol.py` | 7 | wire schema, UUID v4, UTF-8 JSON, 입력 경계 |
| `tests/test_sender.py` | 7 | 강제 broadcast, 3초×10회 광고, ACK 상관관계, peer |
| `tests/test_receiver.py` | 4 | malformed 입력, 첫 peer/device 고정, 재ACK, timeout |
| `tests/test_connecter.py` | 7 | URI, OPTIONS/CSeq, 2xx 경계, 오류와 timeout |
| `tests/test_e2e.py` | 4 | 전체 성공, RTSP 실패, probe 예외, 기본 timeout |

자동 E2E는 실제 loopback UDP/TCP socket과 가짜 RTSP 서버를 사용합니다.
공개 API의 broadcast 계약을 바꾸지 않기 위해 테스트 내부에서만 private
broadcast 목적지를 loopback으로 대체합니다. 따라서 이 테스트는 실제 NIC,
방화벽 또는 LAN broadcast 전달성을 증명하지 않습니다.

## 8. 알려진 제한과 보안

- UDP 데이터그램 전달과 ACK 수신을 보장하지 않습니다.
- ADVERTISE ACK가 없으면 같은 광고를 3초 간격으로 최대 10회 재전송합니다.
- DETAIL 또는 DETAIL ACK 유실 시에는 자동 재전송하지 않습니다.
- 중복 메시지와 순서 변경을 별도로 처리하지 않습니다.
- `message_id`는 ACK 상관관계에만 사용합니다.
- Receiver 호출 한 번은 장비 한 대만 반환합니다.
- MAC 주소가 바뀌면 같은 장비도 다른 `device_id`로 인식될 수 있습니다.
- 인증과 암호화를 제공하지 않습니다.
- RTSP `OPTIONS` 성공은 실제 영상 재생 성공을 보장하지 않습니다.

신뢰할 수 있는 동일 IPv4 LAN에서만 사용하고, 방화벽 범위를 필요한 subnet과
port로 제한하십시오.
