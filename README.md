# DJY EV Telemetry

## 실전 검증 완료

**프로젝트 담당자 확인 · 성과 기록: 2026-10-21**

EV 텔레메트리를 활용해 토크벡터링 보정에 기여했고, 대회장에서 실시간 차량 값을 수신했습니다. 테스트 주행 중 BMS 값을 실시간으로 확인하여 배터리 온도의 비정상 상승을 발견했고, 피트인하여 점검할 수 있는 기회를 확보했습니다.

[실전 검증 결과와 현장 사례](docs/FIELD_VALIDATION_20261021.md)

STM·ESP·BMS·휴대폰 GPS 데이터를 수신하고 저장하는 웹 텔레메트리입니다.

피트 화면(`/pit`)의 **차속 환산 설정**에서 기어비와 타이어 지름(cm)을 변경할 수 있습니다.
기본값은 사용자 확인값인 지름 45cm, 감속비 **4.72727272…:1 (52/11)**입니다.
피트 PC 로컬 접속에서 저장하며, 설정은 DB에 보관되어 서버 재시작 후에도 유지됩니다.
변경값은 다음 유효한 좌우 RPM 표본부터 PC에서 환산하는 차속에 적용됩니다. ESP·STM 업로드는 필요하지 않습니다.
STM 제어용 차속은 원본을 유지합니다. ESP 자체 환산값과 PC 설정은 별도이며 PC가 유효한 RPM으로 재계산합니다.
표본의 `speed_geometry`에 기어비·지름(m)·계산 버전을 함께 기록하며, 과거 기록과 CSV는 당시 값을 유지합니다.

## 실행

`start_telemetry.bat`을 실행하면 텔레메트리 서버를 시작하고 브라우저를 엽니다.
ESP 개인 설정과 ngrok이 준비되어 있으면 인터넷 릴레이도 시작합니다. `config.h`가 없거나 예제 설정만 있는 경우, 또는 ngrok이 없는 경우에는 안내를 표시하고 로컬 대시보드(USB·로컬 UDP 수신)를 실행합니다.
Python은 `%USERPROFILE%\telemetry-tools\venv\Scripts\python.exe`, 기존 `telemetry-tools\python\python.exe`, PATH 순으로 찾습니다(Python 3.10 이상).
`start_telemetry.bat -LocalOnly`로 로컬 실행을 명시할 수 있습니다. `-InternetOnly`는 릴레이 설정이 없으면 중단합니다.
인터넷 릴레이 최초 설정은 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure_ev_relay.ps1 -PublicUrl https://실제-ngrok-도메인`으로 실행합니다. ESP `config.h`를 생성하고 릴레이를 켜며, PC와 같은 별도 릴레이 토큰을 설정합니다. Wi-Fi 정보는 기존 `config.h` 또는 예제 파일에서 가져오므로 실제 핫스팟 설정을 확인하세요. ngrok 인증키는 ESP 릴레이 토큰과 별개이며 `telemetry-tools/ngrok/ngrok.yml`에 저장합니다.
PC 릴레이 URL·토큰은 프로젝트 밖의 `%USERPROFILE%\telemetry-tools\ev-relay.json`에 보관합니다. 실행기는 이 설정을 사용하므로 Git에 없는 `config.h`가 빠져도 PC 인터넷 실행이 가능합니다. 프로젝트를 새로 받은 뒤 `scripts/configure_ev_relay.ps1`을 실행하면 같은 URL·토큰으로 ESP 설정을 다시 만들 수 있습니다. 예제 URL·토큰은 사용할 수 없습니다.
실행기는 측정 중이 아닐 때만 이 프로젝트의 기존 서버를 필요한 모드로 재시작합니다. ngrok 연결을 확인한 후 인터넷 주소를 표시하며, 연결 실패 시 로컬 대시보드는 유지하고 재실행으로 연결을 재시도합니다.
ESP 설정 변경 후에는 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_esp_gateway.ps1`로 빌드하고, 보드를 연결해 같은 명령에 `-Upload -Port COM8`을 추가합니다(실제 ESP USB-UART 포트 사용). 기존 핀·UART·수신 전용 설정은 유지됩니다. 새 주소·토큰은 업로드 전까지 보드에 적용되지 않습니다.
실행 BAT는 `start_telemetry.bat` 하나만 사용합니다. 실행 시 프로젝트 경로를 표시하며, 다른 폴더의 서버가 실행 중이면 혼용하지 않고 중단합니다.
ESP USB 전체 표본 수신은 `start_telemetry.bat -EspPort COM8` 또는 `python gateway/ev_gateway.py --serial COM8`로 시작합니다(3000000 baud). [USB 전송·기록 안내](docs/USB_RECORDING_KO.md)를 참고하세요.
후방 STM USB 진단 수신은 PowerShell에서 `./scripts/run_ev_stack.ps1 -Wired -RearPort COM13`, BMS 벤치 연결은 `./scripts/run_bms_pit_dashboard.ps1 -Port COM5`를 사용합니다. 포트는 실제 연결에 맞춥니다.

| 화면 | 주소 |
|---|---|
| 실시간 피트 대시보드 | `http://127.0.0.1:8766/pit` |
| BMS | `http://127.0.0.1:8766/battery` |
| 저장 데이터 분석·CSV | `http://127.0.0.1:8766/records` |
| 지도·출발선·랩타임 | `http://127.0.0.1:8766/map` |
| 휴대폰 GPS 연결 안내 | `http://127.0.0.1:8766/phone` |
| 운전자 화면 | `http://127.0.0.1:8766/driver` |
| 온보드 중계 HUD | `http://127.0.0.1:8766/onboard` |

인터넷에서는 ngrok 도메인 뒤에 같은 경로를 사용합니다.

### AWS와 운전자 화면 (2026-10-04 점검)

현재 AWS 운전자 화면은 https://13.209.166.150/driver 이며 `/pit`, `/map`, `/records`, `/battery`도 같은 서버를 사용합니다.
AWS는 `nginx → 127.0.0.1:8766 → Python gateway → /var/lib/djy-telemetry/ev_telemetry.db` 구조입니다.
PC의 `start_telemetry.bat`은 별도 로컬 서버를 실행합니다. PC DB와 AWS DB는 자동 동기화되지 않으므로 주행 중에는 ESP·휴대폰 GPS·대시보드가 같은 AWS 주소를 사용해야 합니다.

- `/driver`는 현재·전·최고 랩과 **완료 랩 − 그 전 랩** 델타를 표시합니다. 두 랩 이상 완료되면 단축은 초록 ▲, 지연은 빨강 ▼로 3.5초 점멸합니다. 같은 기록은 중립 표시이며, 새로고침이나 새 측정 시작 때 과거 랩 알림을 재생하지 않습니다. 실시간 동일 지점 대비 델타는 아닙니다.
- 수신 지연 시 마지막 차량 값을 흐리게 표시하고, 마지막 차량 표본 또는 서버 응답이 10초 이상 오래되면 오프라인으로 표시합니다. 개별 센서 미수신도 10초 유예하지만 명시적인 고장 코드는 즉시 표시합니다. 제어·기록 데이터의 기존 유효성 기준은 그대로 적용합니다.
- Wi-Fi 4칸은 **ESP ↔ 핫스팟** RSSI입니다. -55/-67/-75 dBm 경계로 매우 좋음/좋음/보통/약함을 표시하고, 값이 없거나 오래되면 미확인으로 표시합니다. 아이폰의 LTE/5G 신호강도를 나타내지 않습니다.
- 주행 전 `/map`에서 출발선과 방향을 설정하고 랩 측정을 시작해야 합니다. GPS 수신만으로 랩이 저장되지는 않습니다. 차량 센서 전체 기록은 `/pit`의 **측정 시작**을 별도로 눌러야 합니다. 완료 랩·GPS는 `gnss_runs/gnss_fixes/gnss_laps`, 차량 표본은 `measurement_sessions/telemetry_samples`에 저장됩니다.
- 서버가 재시작되면 기존 랩 기록은 보존되고 진행 중 랩 측정은 `INTERRUPTED`로 종료됩니다. 다시 시작해야 합니다. 차량 표본 기록 세션은 재개됩니다. GPS 3초 초과 공백은 현재 랩의 연속성을 끊습니다. 10초 화면 유예는 기록 무손실이나 랩 유효성을 보장하는 시간이 아닙니다.
- AWS Nginx 설정은 [deploy/aws/nginx.conf](deploy/aws/nginx.conf)에 보관합니다. `/api/gnss/control`을 게이트웨이로 전달하여 출발선·시작·종료가 동작하며, 보정 및 차량 제어의 기존 로컬 접근 검사는 유지합니다.

아이폰 17 운용 시 다른 사람의 연결 허용과 호환성 최대화를 켜고 ESP 연결을 확인합니다. 유선 전원과 통풍을 확보하고 직사광선을 피하세요. 사용하지 않는 핫스팟의 자동 연결 해제와 과열 시 무선 성능 저하는 Apple이 안내하는 동작입니다. ESP 소스에는 Wi-Fi 절전 해제·자동 재연결·15초 간격 재시도·1 Hz heartbeat가 있지만 90분 무중단을 보장하지는 않습니다. 실제 아이폰의 화면 잠금·충전·장착 상태로 사전 주행 시험이 필요합니다. [Apple 핫스팟](https://support.apple.com/ko-kr/guide/iphone/iph45447ca6/ios), [연결 문제](https://support.apple.com/ko-kr/119837), [온도 관리](https://support.apple.com/ko-kr/118431).

검증: `python -m unittest discover -s tests -v`, `python scripts/check_driver_browser.py`, `python scripts/check_aws_telemetry.py`. 90분 GPS 재생 검사는 가상 시각의 5,400개 위치·490개 랩 저장과 재시작 보존을 확인하며, 실제 90분 무선 주행 시험과는 구분합니다.

`/onboard`는 F1 온보드 스타일로 차속·좌우 모터 RPM·스로틀·IMU G-force·yaw·조향각을 표시합니다.
기어 자리의 POWER는 실제 `vehicle_power_on` 불리언 신호가 있을 때만 ON/OFF를 표시합니다. 현재 펌웨어에는 이 신호가 없어 미확인으로 표시하며, 연결 끊김을 전원 OFF로 간주하지 않습니다.
`/onboard?demo=1`은 디자인 확인용 가상 데이터입니다. 화면에 DEMO가 표시되고 실제 수신·저장·제어 API를 사용하지 않습니다.
OBS 브라우저 소스에 `http://127.0.0.1:8766/onboard?overlay=1`을 넣고 1920×1080으로 설정하면 투명 HUD를 영상 위에 올릴 수 있습니다. 카메라 영상 수신과 방송 송출은 OBS의 별도 영상 소스에서 설정하세요.
일반 화면의 **영상 선택**은 PC의 MP4/WebM을 브라우저 안에서 미리 재생합니다. 파일을 서버에 업로드하지 않으며, 영상과 텔레메트리의 시간 동기화 기능은 없습니다.
HUD는 `/api/onboard`를 약 10Hz로 조회합니다. 기존 유효성·수신 시간 검증을 따르며 미수신·불량·오래된 값을 0 대신 `—`로 표시합니다. 화면 테스트용 오버라이드 값은 중계하지 않습니다. RPM 막대는 0–8,000 눈금이고 G-force 원은 ±2 G 범위입니다. 범위를 넘는 수치는 그대로 표시합니다.
`/pit`은 차량·센서·고장 상태, 토크벡터링 제어(통합 그래프 1개), 운전자 입력·차량 응답(2개), 배터리 요약(통합 그래프 1개)을 표시합니다.
`/battery`는 팩 전압·전류·전력, 셀 전압·편차, 온도, 잔량의 4개 그래프와 셀·MOS·알람 상세를 표시합니다.
관련 신호의 선은 같은 그래프에 겹쳐 표시합니다. 시간축을 공유하며, 좌우 눈금은 단위별로 독립적입니다. 같은 단위는 같은 축을 사용하고 체크박스로 선을 선택합니다.
현재 PC의 모든 실제 링크는 설정 스크립트가 생성하는 [접속 주소 모음](.local/connection-links.md)에 있습니다. `start_telemetry.bat`도 각 화면의 인터넷 주소와 ESP/GPS 수신 주소를 출력합니다.
서버 실행 후 `python scripts/check_ev_relay.py`로 ESP 소스·PC의 URL/토큰 일치, 실제 외부 페이지, 릴레이 인증을 확인할 수 있습니다. 이 검사는 차량 표본이나 제어 명령을 전송하지 않습니다. Python은 위 `telemetry-tools` 가상환경을 사용하세요.
기록 시작·종료와 출발선 설정·랩 측정은 인터넷에서도 가능합니다.
차량 제어 명령과 GPS 기준점 보정에는 기존 로컬 접근 제한이 적용됩니다.

## 드라이버 소통

`/comms`에서 YELLOW·RED·BOX BOX·GREEN·STOP·CHEQUERED와 CLEAR를 발령합니다. AWS HTTPS 주소로 접속한 휴대폰에서도 사용할 수 있으며, 운전자는 **같은 서버의 `/driver`**를 열어두어야 합니다. `/pit`과 `/test`에 이동 링크가 있습니다.

- 플래그와 일반 메시지는 서버 발령부터 4초간 점멸한 뒤 자동으로 사라집니다. 같은 플래그를 다시 누르면 4초간 재표시하며 CLEAR는 즉시 해제용입니다. 일반 메시지와 TV GAIN·REGEN 알림은 최대 48자이며, 플래그가 사라지거나 해제된 뒤 보낼 수 있습니다.
- 표시용 소통이며 차량 설정을 변경하지 않습니다. 펌웨어 업로드나 DB 변경은 필요 없습니다. 발령은 메모리에 보관하며 재시작 뒤 다시 발령해야 합니다.
- 상태는 서버 반영 기준이며 운전자의 확인 응답을 뜻하지 않습니다. 통신이 끊겨도 운전자 화면에서 4초 뒤 자동으로 사라지며, 만료된 플래그와 메시지는 재접속 시 재생하지 않습니다.
- `/api/driver-message`의 GET/POST만 공개하며 기존 `/api/test`와 차량 제어 제한은 유지합니다. 현재 다른 공개 운영 버튼과 같이 별도 로그인이 없으므로, AWS 주소를 아는 사용자는 발령할 수 있습니다.

검증: `python -m unittest discover -s tests -p test_driver_messages.py`, `python scripts/check_comms_browser.py`.

## 저장·분석

`/battery`의 **실시간 남은 주행시간 · 전비** 패널은 1초마다 갱신됩니다. 서버를 재시작한 뒤 BMS 전압·전류가 10초 이상 수신되면 최근 30초의 평균 순방전전류로 예비 SOC까지 남은 시간을 계산합니다. 기본 예비량은 10%이고, 피트 PC에서 설정할 수 있습니다.

- 시간: `(BMS 잔여 Ah − 설정용량 × 예비 SOC/100) / 평균 순방전 A × 60`. 잔여 Ah가 없으면 `SOC × 설정용량`을 사용하고 참고 추정으로 표시합니다. 10·30·120초 평균으로 계산한 최소·최대 시간도 표시하며, 확률 신뢰구간은 아닙니다.
- 전비: 최근 30초의 순소비 Wh / 주행 km. 낮을수록 효율적입니다. 직전 최대 120초 구간과 평균 속도가 20% 이내일 때 소비가 10% 넘게 줄면 **좋음**, 늘면 **나쁨**, 그 사이는 **비슷함**입니다. 속도·거리 자료가 부족하거나 정차·회생 우세일 때는 별도로 표시합니다.
- EVDATA의 2026-09-20 기록에서 확인한 약 **80Ah의 BMS 설정용량 관계와 음수 방전**을 초기값으로 사용합니다. 실측 배터리 건강도나 새 팩 용량을 의미하지 않습니다. 설정은 DB에 보관됩니다.
- 통신 공백·중복·역순 표본은 연결해 적산하지 않습니다. BMS 용량 변화가 전류 적산과 크게 다르면 시간 예측을 보류합니다. BMS 경보가 있으면 계산값의 한계를 함께 표시합니다. 전압·온도 차단이나 앞으로의 경사·부하까지 예측하는 모델은 아닙니다.
- 그래프의 과거 선택·일시정지와 독립적으로 **현재 주행**을 표시합니다. 서버 재시작·설정 변경 시 소비 구간을 다시 수집하며, 기록 시작 버튼을 누르지 않아도 동작합니다. 펌웨어 업로드는 필요하지 않습니다.

검증: `python -m unittest discover -s tests -p "test_battery*.py"`, `python scripts/check_battery_browser.py`. 기존 CSV 재생은 `python scripts/check_battery_prediction_data.py "EVDATA 폴더 경로"`로 수행하며 실제 DB나 차량에 기록하지 않습니다.

- 측정 시작부터 수신 데이터를 `data/ev_telemetry.db`에 저장합니다.
- 분석창은 세션 전체를 불러오며, 카테고리 체크박스·구간 확대·정확한 표본값·CSV 내보내기를 제공합니다.
- 전력은 W/kW로 전환할 수 있으며 저장 원본은 유지합니다.
- BMS 팩 전력은 전압×전류입니다. 모터 출력 요구값은 실측 출력과 구분합니다.
- GPS가 함께 저장된 표본은 지도에서 위치와 전력을 확인할 수 있습니다.
- 아이폰 백그라운드 GPS는 Traccar Client와 `/api/gnss/native`를 사용합니다.
- 출발선의 첫 정방향 통과에서 타이머가 시작되고 다음 정상 통과에서 랩을 완료합니다.

## 프로젝트 구조

```text
start_telemetry.bat   서버·ngrok 시작
gateway/              Python 수신·저장·웹 서버
web/                  현재 사용하는 웹 화면과 지도 라이브러리
firmware/             STM·ESP 펌웨어 및 통신 정의
bin/                  보존된 ESP 펌웨어 바이너리
data/                 실제 주행 기록 DB
scripts/              실행·계측·검증 도구
tests/                서버·통신·펌웨어 검증
docs/                 기능 안내와 과거 진단 기록
_archive/             이전 작업물 보관 (Git 제외)
```

C++/ImGui 대시보드와 구형 기록 HTML은 작업 경로에서 제외했습니다.
`native`, `ev-direct`, `rebuild` 실행 모드는 더 이상 제공하지 않습니다.
STM·ESP 펌웨어의 C/C++ 소스는 차량용이므로 유지합니다.
과거 진단 문서에는 당시 구성과 경로가 남아 있을 수 있습니다.

## 상세 안내

- [기록 분석](docs/RECORD_ANALYSIS_20260920.md)
- [BMS 전력 화면](docs/BMS_POWER_DASHBOARD_20260920.md)
- [아이폰 GPS](docs/IPHONE_BACKGROUND_GPS_20260920.md)
- [STM·ESP 통합 안내](firmware/tv_stm_esp/README.md)
- [배선도](docs/TORQUEVECTORING_PINMAP.svg)

## 고속 텔레메트리와 그래프 값 선택

- 실제 후방 STM → ESP는 238바이트 CRC16 바이너리, 460800 baud, 최대 100 Hz입니다. BMS CAN 전압·전류 요청은 10 ms이며 실제 응답 간격을 별도로 기록합니다.
- ESP는 수신 이벤트를 FIFO에 담아 최대 64개/64 KiB씩 전달합니다. 서버는 표본별 취득 시각을 보존하고 한 묶음을 한 트랜잭션으로 기록합니다.
- 피트 13개 / BMS 11개 그래프는 클릭한 표본의 값만 고정하며 계속 갱신됩니다. 별도 일시정지 버튼으로 화면을 정지할 수 있습니다.
- 과거 세션 CSV는 전체 표본을 내려받습니다. `/records`의 원본 JSONL은 로컬 피트 PC에서 이용할 수 있습니다. 기존 인터넷 기록 조작·지도·휴대폰 GPS 기능은 유지합니다.

실차 후방 프로젝트는 [`firmware/stm32_rear_binary`](firmware/stm32_rear_binary/README.md)입니다. 별도로 제공된 `stm_back.zip`을 반입한 뒤 송신 변경을 적용했으며, 과거 `firmware/tv_stm_esp/stm_back`은 그대로 보관합니다. 빌드는 `./scripts/build_stm32_rear_uart.ps1 -BinaryRear`로 선택합니다. **새 ESP 기본 바이너리 모드는 이 후방 펌웨어와 함께 적용해야 합니다.**

버퍼 한계와 적용 순서는 [고속 수집 안내](docs/HIGH_RATE_TELEMETRY_KO.md), 패킷 정의는 [바이너리 UART](docs/BINARY_UART_KO.md), 검증 결과는 [PR 검증 기록](docs/PR_VALIDATION_20260921.md)을 참고하세요. 실측 피드백에 의한 10 kW 초과 차단은 이번 변경에 포함되지 않습니다.
