# ESP 송수신 토크벡터링 조정

후속 UI 통일 및 공통 설정 규격: [실시간 차량 설정 규격](LIVE_TUNING_CONTRACT.md).

2026-09-29. 현재 연결된 STM은 UART1 115200 ASCII 텔레메트리이며,
ESP → STM 명령은 같은 UART의 기존 9-byte LIVE_TV / CRC8 프레임이다.
STM과 BMS 펌웨어 및 BMS 설정은 변경하지 않았다.

## 사용

- 피트 PC: http://127.0.0.1:8766/tv
- `/pit`의 차량 조정 링크에서도 접근한다.
- 공개 ngrok `/tv`는 조회 전용이다. 제어 POST의 기존 로컬 제한을 유지한다.
- 강도와 상한은 각각 0~100%, 5% 단위. 허용을 해제하면 차동 강도 0%를 요청한다.
- 강도는 TV 및 전자식 차동의 좌우 출력 차이를 조정한다. 총출력 상한이나 PID 게인이 아니다.
- 슬라이더 설정 뒤 적용 버튼을 누르는 동안 명령을 갱신한다. 키보드 Space/Enter도 지원한다.
- 버튼 해제, 포커스 상실, 탭 숨김, 상태 조회 실패 시 원격 조정을 해제한다.
- 화면 요청값과 실제 STM 적용값/명령 번호/수신 카운터를 별도로 표시한다.
- `/api/tv`는 표시 시험의 가짜 데이터가 섞이지 않은 실제 상태를 사용한다.

## 현재 STM의 복귀 동작

ESP 명령은 500ms가 지나면 송신을 중단한다. STM도 자신의 명령 타임아웃 후
로컬 기본값으로 복귀한다. 실제 보드에서 원격 0% 요청 종료 후 강도/상한이
100%/100%로 돌아오는 것을 확인했다. 마지막 설정은 영구 저장되지 않는다.
0% 명령은 차동 개입 해제이며, 구동 전원 차단 또는 비상정지가 아니다.

전방 보드는 사용자가 의도적으로 꺼둔 상태다. 이때 fault=1이며 서버는
0% 또는 차동 해제 명령만 허용한다. 오류 차단을 우회하지 않았다.
전방 보드를 켜고 정상 입력이 확인되어야 비영(非零) 강도 실기기 시험이 가능하다.

## 펌웨어

- 현재 보드: `C:/Users/ljh/telemetry-tools/esp-ascii-bidirectional`
- COM3 업로드 완료, esptool 쓰기 해시 검증 성공.
- STM UART 115200, ESP USB 진단 921600.
- `EV_REAR_UART_BINARY=0`, `EV_RECEIVE_ONLY=0`, `EV_RELAY_ACCEPT_COMMANDS=1`.
- TLS 인증서 검증 유지. BMS는 기존 조회 기능 그대로다.
- 기존 고속 준비본: `C:/Users/ljh/telemetry-tools/esp-highrate-bidirectional`.
  460800 binary STM으로 전환할 때 사용하는 별도 빌드이며 현재 보드에는 올리지 않았다.
- 두 준비본 모두 이전에 검증한 소스를 동결해 사용한다. 작업 트리의 별도 LR 변경은 포함하지 않는다.

## 검증 결과

- ESP ASCII 양방향 빌드 성공, factory 이미지의 4개 구간과 업로드 파일 일치.
- 호스트 시험: ESP 실제 UART 송신 함수의 읽기 전용 차단, 만료, 주기,
  441개 강도/상한 조합과 CRC 검사 통과.
- 서버 기존 명령/오류/ACK/deadman/relay 시험 5개 통과.
- 새 HTTP 화면/실제 데이터/외부 제어 차단/오류 시 중립 명령/직접 무선 우선 시험 4개 통과.
- Edge 모의 브라우저: 자동 송신 없음, 유지 갱신, 해제, 오류 차단, 연결 상실,
  재접속 시 자동 재개 없음, 포커스 상실, 공개 조회 전용 확인.
- 실기기 USB → ESP → STM → ESP → 서버:
  명령 번호 71·72 응답, 명령 수신 0 → 45, 명령 오류 617 → 617,
  적용 강도 0%, 상한 60% 확인. 617은 시험 전부터 존재한 누적값이다.
- 실기기에서는 양의 강도를 보내지 않았으며 주행 시험은 수행하지 않았다.
- 근거: `C:/Users/ljh/telemetry-tools/esp-ascii-bidirectional/neutral-roundtrip-usb.json`.
- 실제 직접 무선 왕복도 통과: PC → Wi-Fi → ESP → STM → ESP → Wi-Fi → PC.
  서버 입력/수신 경로 WIFI_UDP, USB 명령 경로 없음. 명령 번호 71·72 ACK,
  명령 수신 45 → 90, 오류 645 → 645, 적용 강도 0%·상한 60% 확인.
  ESP 텔레메트리 파싱 오류도 시험 전후 1로 동일했다. 업로드/재시작 전후 누적값과
  시험 중 증가량을 구분한다.
- 무선 근거: `C:/Users/ljh/telemetry-tools/esp-ascii-bidirectional/neutral-roundtrip-wireless.json`.

## 네트워크와 실행

최종 서버는 USB 입력 없이 UDP 9003 수신과 인증된 HTTPS relay를 함께 활성화했다.
명령은 최근 1.5초 내 수신된 직접 Wi-Fi 경로를 HTTPS보다 우선한다. 직접 경로가
없으면 기존 relay로 대체한다. USB 입력을 별도로 켜면 USB 명령 경로가 우선한다.

PC와 ESP는 HyengmInPhone에 연결되어 있다. 현재 PC IP는 172.20.10.14,
ESP는 172.20.10.8이다. 준비본 config.h의 EV_LOCAL_UDP_ENABLED=1,
EV_PIT_HOST="172.20.10.14"를 적용했다. 기존 로컬 UDP 기능을 활성화했으며
ESP는 지정한 피트 IP의 UDP 명령만 받는다. 이는 신뢰하는 같은 핫스팟용 경로다.
PC가 다른 네트워크로 이동하거나 DHCP 주소가 바뀌면 피트 IP 설정을 갱신해야 한다.

ngrok 터널은 유지되어 공개 대시보드 접근 경로는 남아 있다. 다만 ESP 및 PC에서
HTTPS TLS 재접속 실패가 관찰됐으므로 인터넷 왕복의 안정성은 보장하지 않는다.
직접 Wi-Fi 왕복 성공을 ngrok 제어 성공으로 해석하지 않는다.

재시작 시 기존 개인 relay 설정을 읽는 스크립트를 사용한다:

```powershell
.\scripts\start_park_telemetry.ps1
```

USB로 시험할 때는 위 명령에 `-EspPort COM3`를 추가한다.
서버에 보이는 오래된 HTTP 200이나 Wi-Fi 상태만으로 연결을 판단하지 않는다.
