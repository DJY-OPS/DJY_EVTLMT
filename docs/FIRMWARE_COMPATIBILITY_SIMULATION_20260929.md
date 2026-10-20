# STM → ESP 펌웨어 호환성 시뮬레이션

실행일: 2026-09-29. GitHub origin/main을 fetch해 확인한 커밋은
`2edace93d4dbd8ccd3c3f4776541c1deb77ef805`이다.
현재 작업 트리의 ESP 수신 코드를 호스트에서 컴파일하고, STM 송신 형식별로
100개 표본을 공급했다. 실행 중 보드 업로드, 제어 명령, 운영 서버 데이터 주입은 하지 않았다.

## 결론

실제 GitHub 소스 사이에 호환되지 않는 조합이 있다. `4a82533` 원본 후방 STM은
USART1 115200/ASCII이고, `e514e18` 및 현재 binary 프로젝트는 USART1
460800/238-byte binary v1이다. 현재 ESP의 기본 `EV_REAR_UART_BINARY=1`은
후자와 호환된다. 커밋 번호 자체가 달라도 wire 규약이 같으면 수신된다.

| 송신 STM / 시험 조건 | 수신 ESP | 정상 수신 |
|---|---|---:|
| 현재 binary STM, 460800 | 현재 binary | 100/100 |
| e514e18 binary STM, 460800 | 현재 binary | 100/100 |
| stm32_rear_uart ASCII, 115200 | ASCII, 115200 | 100/100 |
| tv_stm_esp/stm_back ASCII, 115200 | ASCII, 115200 | 100/100 |
| 4a82533 원본 STM ASCII, 115200 | ASCII, 115200 | 100/100 |
| 4a82533 원본 STM ASCII, 115200 | 현재 binary, 460800 | 0/100 |
| stm32_rear_uart ASCII, 115200 | 현재 binary, 460800 | 0/100 |
| tv_stm_esp/stm_back ASCII, 115200 | 현재 binary, 460800 | 0/100 |
| ASCII, baud만 460800으로 일치 | binary, 460800 | 0/100 |
| binary, baud는 일치 | ASCII | 0/100 |
| 가상 wire version=2, 올바른 CRC | 현재 binary v1 | 0/100 |
| 가상 payload 길이 변경, 올바른 CRC | 현재 binary | 0/100 |
| 데이터 1비트 변경, CRC 불일치 | 현재 binary | 0/100 |
| CRC 오류 100개 뒤 정상 프레임 100개 | 현재 binary | 정상 100/100 |

14개 시나리오 모두 기대 결과와 일치했다. 바이너리 STM과 ESP 프로토콜은
`D5 4A 01 02 E6 00` 헤더, 총 238바이트, little-endian,
CRC16/CCITT-FALSE를 사용한다. 단순히 UART에 바이트가 들어오는 것과
정상 프레임 수신은 다른 조건이다.

## 재현 방법과 검증

프로젝트 루트에서:

```powershell
& "$env:USERPROFILE\telemetry-tools\venv\Scripts\python.exe" -X utf8 scripts/simulate_firmware_compatibility.py
& "$env:USERPROFILE\telemetry-tools\venv\Scripts\python.exe" -X utf8 -m unittest discover -s tests -p test_binary_telemetry.py -v
```

호스트 컴파일러는 기존 tests/host_compiler.py가 지원하는 Zig 0.13.0을
telemetry-tools/host-tests에 설치했다. 최초 테스트 시 CP949 파일 읽기와
첫 컴파일 캐시 생성의 시간 제한 문제를 확인했고, UTF-8로 실행하고 캐시가
준비된 뒤 재검증했다. 최종 binary 테스트 2개 모두 통과했다. 여기에는 실제
STM Telemetry_Publish 및 ESP receiveRearUart/parseRearTelemetry 함수,
손상 복구, 분할 수신, 중복, 누락, STM 시계 초기화 시험이 포함된다.

시뮬레이터도 현재 ESP의 VehicleState, 두 parser 및 receiveRearUart 함수를
그대로 추출해 binary/ASCII 두 모드로 컴파일한다. 바이너리 표본은 해당
GitHub 커밋의 실제 프로토콜 pack 함수로 생성한다. ASCII 표본은 실제
STM snprintf 형식 문자열에 시험 값을 대입한다. 원본 값 자체는 합성 데이터다.

상세 결과와 수신 소스 SHA256은 `.local/firmware-compatibility/results.json`에 있다.

## 실기기 판단에 적용

현재 업로드한 ESP는 binary/460800 모드다. STM에 원본 4a82533 또는
과거 ASCII 프로젝트가 올라가 있으면 이 조합으로는 통신할 수 없다.
STM에 올라간 실제 펌웨어를 확인한 뒤 다음 중 맞는 구성을 선택해야 한다.

- binary 유지: STM `firmware/stm32_rear_binary`와 ESP binary를 함께 사용.
  STM 빌드 스크립트는 `-BinaryRear` 옵션이 필요하다. 기본 대상은 과거 프로젝트다.
- 기존 ASCII STM 유지: ESP private config.h에서 `EV_REAR_UART_BINARY=0`으로
  빌드하면 115200 ASCII 모드가 된다. 센서 항목과 품질 정보의 제공 범위는
  해당 STM 버전에 따라 달라진다.

STM USART2의 USB 진단은 binary 프로젝트에서도 115200 ASCII다.
ESP RX GPIO18에는 STM USART1 TX PA9를 연결해야 하며, USART2 진단 출력을
binary 입력으로 연결해도 정상 프레임은 수신되지 않는다. 공통 GND도 필요하다.

시뮬레이션은 실제 STM에 올라간 버전을 식별하지 않는다. baud 불일치는
이상적인 8N1 선로의 중앙 샘플링으로 모델링했고, 전압·잡음·클록 편차는
시험하지 않았다. 가상 version=2와 길이 변경은 GitHub 출시 버전이 아니다.

ASCII/binary 포맷 불일치에서는 정상 수신 0이어도 binary parser 오류 수가
0일 수 있다. 실제 관측된 UART 오류 수만으로 버전 불일치를 확정할 수 없다.
이 시험에는 Wi-Fi/ngrok 안정성과 BMS CAN 수신이 포함되지 않는다.

## 원본 근거

- [4a82533 원본 STM main.c](https://github.com/DJY-OPS/DJY_EVTLMT/blob/4a82533d07aa7c9c1d70a6d1d6844153abe38923/firmware/stm32_rear_binary/Core/Src/main.c)
- [e514e18 바이너리 프로토콜](https://github.com/DJY-OPS/DJY_EVTLMT/blob/e514e180c4a0734f2fcc5413616e04e936c69ea6/firmware/stm32_rear_binary/Core/Inc/djy_telemetry_protocol.h)
- [확인한 현재 커밋](https://github.com/DJY-OPS/DJY_EVTLMT/commit/2edace93d4dbd8ccd3c3f4776541c1deb77ef805)
- [바이너리 빌드 및 호환성 안내](https://github.com/DJY-OPS/DJY_EVTLMT/blob/2edace93d4dbd8ccd3c3f4776541c1deb77ef805/docs/BINARY_UART_KO.md)
